"""Turn handling: extract -> enhance -> route -> followups -> update memory.

Any failure at any stage (LLM invalid/timeout, reranker timeout/error,
MySQL error, unresolved reference) returns an explicit ERROR TurnResult
immediately. No retries, no cascading fallbacks, no cached/stale data
returned in place of a real answer - a failure is a hard pause.

Every reranker result and every sku_id a reference resolves to is checked
against MySQL directly (single source of truth for what actually exists),
never trusted from the model. Results are recorded on TurnResult.trace
(stage name, exact input, exact output, ok/found flags) so a caller - the
CLI in cli.py, or Swagger's raw response - can see precisely what happened
at each step, including which sku_ids were hallucinated by the reranker or
the extractor.
"""

from __future__ import annotations

import uuid

from . import cart, catalog, enhancer, followups, llm, memory
from .catalog import CatalogError, RerankerError
from .config import ConfigError, load_multi_product_config, load_search_mode
from .contracts import (
    Action,
    CartOperationDraft,
    MultiProductBudgetIntent,
    MultiProductCandidateGroup,
    MultiProductRecommendation,
    MultiProductResult,
    Reference,
    Role,
    SearchEntry,
    TraceStep,
    TurnRequest,
    TurnResult,
    TurnStatus,
    UpdateCartRequest,
)
from .llm import LLMError
from .sarvam import (
    ENGLISH_LANGUAGE_CODE,
    SarvamClient,
    SarvamError,
    TranslationResult,
    default_client,
    is_english_language,
    is_probably_english,
)


class _Tracer:
    def __init__(self) -> None:
        self.steps: list[TraceStep] = []
        self.language_code = ENGLISH_LANGUAGE_CODE
        self.sarvam_client: SarvamClient | None = None

    def record(self, stage: str, input_: dict, output: dict, ok: bool = True) -> None:
        self.steps.append(TraceStep(stage=stage, input=input_, output=output, ok=ok))

    def localize(self, text: str, *, strict: bool = False) -> str:
        if not text or is_english_language(self.language_code):
            return text
        if self.sarvam_client is None:
            return text
        try:
            return self.sarvam_client.translate(
                text,
                source_language_code=ENGLISH_LANGUAGE_CODE,
                target_language_code=self.language_code,
            ).text
        except SarvamError:
            # A provider failure must not leak an exception or credentials in
            # a trace. The caller still receives the safe error code. A
            # successful non-English turn uses strict mode so English is
            # never silently returned as the localized answer.
            if strict:
                raise
            return text


def _error(tracer: _Tracer, message: str, code: str) -> TurnResult:
    return TurnResult(
        status=TurnStatus.ERROR,
        message=tracer.localize(message),
        language_code=tracer.language_code,
        error_code=code,
        trace=tracer.steps,
    )


def _translate_input(message: str, client: SarvamClient | None) -> TranslationResult:
    if client is None:
        if not is_probably_english(message):
            raise SarvamError("SARVAM_API_KEY_REQUIRED")
        return TranslationResult(
            text=message.strip(),
            source_language_code=ENGLISH_LANGUAGE_CODE,
            target_language_code=ENGLISH_LANGUAGE_CODE,
        )
    return client.translate(
        message,
        source_language_code="auto",
        target_language_code=ENGLISH_LANGUAGE_CODE,
    )


def _localize_result(tracer: _Tracer, result: TurnResult) -> TurnResult:
    """Translate generated prose while leaving catalog facts untouched."""

    if is_english_language(tracer.language_code):
        result.language_code = tracer.language_code
        return result
    result.message = tracer.localize(result.message, strict=True)
    for followup in result.followups:
        followup.label = tracer.localize(followup.label, strict=True)
    if result.comparison is not None and result.comparison.summary:
        result.comparison.summary = tracer.localize(result.comparison.summary, strict=True)
    if result.multi_product_result is not None:
        if result.multi_product_result.analysis_summary:
            result.multi_product_result.analysis_summary = tracer.localize(
                result.multi_product_result.analysis_summary,
                strict=True,
            )
        for recommendation in result.multi_product_result.recommendations:
            recommendation.rationale = tracer.localize(recommendation.rationale, strict=True)
    result.language_code = tracer.language_code
    return result


class BundleIntentError(ValueError):
    """Raised when a shared-budget intent cannot be safely executed."""


def _bundle_budget_paise(intent: MultiProductBudgetIntent, usd_to_inr: float | None) -> int:
    if intent.total_budget_paise is not None:
        if intent.total_budget_paise <= 0:
            raise BundleIntentError("BUNDLE_BUDGET_INVALID")
        currency = (intent.budget_currency or "INR").strip().upper()
        if currency not in {"INR", "₹", "RUPEE", "RUPEES"}:
            raise BundleIntentError("BUNDLE_CURRENCY_MISMATCH")
        return intent.total_budget_paise

    if intent.budget_amount is None or intent.budget_amount <= 0:
        raise BundleIntentError("BUNDLE_BUDGET_MISSING")
    currency = (intent.budget_currency or "INR").strip().upper()
    if currency in {"INR", "₹", "RUPEE", "RUPEES"}:
        return round(intent.budget_amount * 100)
    if currency in {"USD", "$", "DOLLAR", "DOLLARS"} and usd_to_inr is not None:
        return round(intent.budget_amount * usd_to_inr * 100)
    raise BundleIntentError("BUNDLE_CURRENCY_UNSUPPORTED")


def _normalise_bundle_items(intent: MultiProductBudgetIntent, max_item_types: int) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for raw_item in intent.item_queries:
        item = " ".join(raw_item.split()).strip()
        if item and item.casefold() not in seen:
            seen.add(item.casefold())
            items.append(item)
    if len(items) < 2:
        raise BundleIntentError("BUNDLE_ITEMS_INVALID")
    if len(items) > max_item_types:
        raise BundleIntentError("BUNDLE_TOO_MANY_ITEM_TYPES")
    return items


def _materialise_bundle_result(
    candidate_groups: list[MultiProductCandidateGroup],
    draft: llm.BundleAnalysisDraft,
    item_queries: list[str],
    budget_paise: int,
) -> MultiProductResult:
    entries_by_item = {
        group.item_query: {entry.sku_id: entry for entry in group.candidates}
        for group in candidate_groups
    }
    recommendations: list[MultiProductRecommendation] = []
    for index, draft_recommendation in enumerate(draft.recommendations, start=1):
        items: list[SearchEntry] = []
        total = 0
        for item_query in item_queries:
            entry = entries_by_item[item_query].get(draft_recommendation.selections[item_query])
            if entry is None or entry.price_paise is None:
                raise LLMError("BUNDLE_ANALYSIS_CANDIDATE_INVALID")
            items.append(entry)
            total += entry.price_paise
        if total > budget_paise:
            raise LLMError("BUNDLE_ANALYSIS_OVER_BUDGET")
        recommendations.append(
            MultiProductRecommendation(
                set_id=f"bundle-{index}",
                items=items,
                total_price_paise=total,
                rationale=draft_recommendation.rationale,
            )
        )
    return MultiProductResult(
        budget_paise=budget_paise,
        item_queries=item_queries,
        candidate_groups=candidate_groups,
        recommendations=recommendations,
        analysis_summary=draft.summary,
    )


def _from_last_results(reference: Reference | None, last_results: list[SearchEntry]) -> SearchEntry | None:
    if reference is None:
        return None
    if reference.sku_id:
        return next((entry for entry in last_results if entry.sku_id == reference.sku_id), None)
    if reference.ordinal:
        index = reference.ordinal - 1
        if 0 <= index < len(last_results):
            return last_results[index]
    return None


def _expand_reference(reference: Reference | None, last_results: list[SearchEntry]) -> list[Reference]:
    """Turns an "all"- or "count"-flagged reference into one per-ordinal
    reference per matching entry in the last search results, so downstream
    resolution/verification works exactly as it does for a single explicit
    reference."""

    if reference is not None and reference.all:
        return [Reference(ordinal=index + 1) for index in range(len(last_results))]
    if reference is not None and reference.count is not None:
        n = max(0, min(reference.count, len(last_results)))
        return [Reference(ordinal=index + 1) for index in range(n)]
    return [reference]


def _resolve_entry(
    tracer: _Tracer, reference: Reference | None, last_results: list[SearchEntry]
) -> SearchEntry | None:
    """Resolve a reference to a catalog entry, verifying against MySQL directly
    when the reference names an explicit sku_id that isn't in the last search
    results (e.g. "compare SKU123 to SKU456" for skus never searched this
    session). Records one trace step either way so the CLI can show
    green/red per sku_id."""

    entry = _from_last_results(reference, last_results)
    if entry is not None:
        tracer.record(
            "resolve_reference",
            {"reference": reference.model_dump()},
            {"sku_id": entry.sku_id, "source": "session_results", "found_in_mysql": True},
            ok=True,
        )
        return entry

    if reference is not None and reference.sku_id:
        details = catalog.get_details(reference.sku_id)
        if details.found and details.entry is not None:
            tracer.record(
                "resolve_reference",
                {"reference": reference.model_dump()},
                {"sku_id": reference.sku_id, "source": "mysql_lookup", "found_in_mysql": True},
                ok=True,
            )
            return details.entry
        tracer.record(
            "resolve_reference",
            {"reference": reference.model_dump()},
            {"sku_id": reference.sku_id, "source": "mysql_lookup", "found_in_mysql": False},
            ok=False,
        )
        return None

    tracer.record(
        "resolve_reference",
        {"reference": reference.model_dump() if reference else None},
        {"sku_id": None, "source": "none", "found_in_mysql": False},
        ok=False,
    )
    return None


def handle_turn(
    request: TurnRequest,
    *,
    sarvam_client: SarvamClient | None = None,
) -> TurnResult:
    tracer = _Tracer()

    client = default_client() if sarvam_client is None else sarvam_client
    try:
        translated_input = _translate_input(request.message, client)
    except SarvamError as exc:
        tracer.record(
            "sarvam_translate_input",
            {"text_length": len(request.message), "source_language_code": "auto"},
            {"error": exc.code},
            ok=False,
        )
        return _error(
            tracer,
            "This language is temporarily unavailable. Please try again in English.",
            exc.code,
        )

    tracer.sarvam_client = client
    tracer.language_code = translated_input.source_language_code
    tracer.record(
        "sarvam_translate_input",
        {"text_length": len(request.message), "source_language_code": "auto"},
        {
            "source_language_code": translated_input.source_language_code,
            "target_language_code": translated_input.target_language_code,
            "translated": translated_input.text != request.message.strip(),
        },
    )

    session = memory.get_session(request.session_id)
    if session is None:
        session = memory.create_session(request.session_id)

    memory.set_language(request.session_id, translated_input.source_language_code)
    memory.append_turn(
        request.session_id,
        Role.USER,
        request.message,
        language_code=translated_input.source_language_code,
        canonical_content=translated_input.text,
    )
    session = memory.get_session(request.session_id)

    history_in = {
        "message": translated_input.text,
        "language_code": translated_input.source_language_code,
        "history_len": len(session.chat_history),
    }
    try:
        extraction = llm.extract_query(translated_input.text, session.chat_history)
    except LLMError as exc:
        tracer.record("query_extractor", history_in, {"error": str(exc)}, ok=False)
        return _error(tracer, "Could not interpret the message.", str(exc))
    tracer.record("query_extractor", history_in, extraction.model_dump(mode="json"))

    if extraction.action == Action.CHITCHAT:
        canonical_message = extraction.reply or "Hi! How can I help you shop today?"
        result = TurnResult(
            status=TurnStatus.OK,
            message=canonical_message,
            language_code=tracer.language_code,
            action=Action.CHITCHAT,
        )
        session = memory.get_session(request.session_id)
        result.followups = followups.build(result, session)
        tracer.record("followups", {"action": Action.CHITCHAT.value},
                       {"followups": [f.model_dump(mode="json") for f in result.followups]})
        try:
            result = _localize_result(tracer, result)
        except SarvamError as exc:
            tracer.record(
                "sarvam_translate_output",
                {"source_language_code": ENGLISH_LANGUAGE_CODE},
                {"error": exc.code},
                ok=False,
            )
            return _error(tracer, "Could not translate the response.", exc.code)
        memory.append_turn(
            request.session_id,
            Role.ASSISTANT,
            result.message,
            language_code=tracer.language_code,
            canonical_content=canonical_message,
        )
        result.trace = tracer.steps
        return result

    enhance_in = {
        "message": translated_input.text,
        "extraction": extraction.model_dump(mode="json"),
        "prior_reranker_request": (
            session.last_reranker_request.model_dump(mode="json") if session.last_reranker_request else None
        ),
    }
    reranker_request = enhancer.build_reranker_request(translated_input.text, extraction, session)
    tracer.record("query_enhancer", enhance_in, reranker_request.model_dump(mode="json"))

    action = extraction.action

    if extraction.multi_product_budget.enabled:
        try:
            bundle_config = load_multi_product_config()
            item_queries = _normalise_bundle_items(
                extraction.multi_product_budget, bundle_config.max_item_types
            )
            budget_paise = _bundle_budget_paise(
                extraction.multi_product_budget, bundle_config.usd_to_inr
            )
            candidate_groups = catalog.fast_multi_product_candidates(
                item_queries=item_queries,
                budget_paise=budget_paise,
                candidate_cap_per_item=bundle_config.candidate_cap_per_item,
                hard_constraints=reranker_request.hard_constraints,
            )
            tracer.record(
                "multi_product_fast_candidates",
                {
                    "item_queries": item_queries,
                    "budget_paise": budget_paise,
                    "candidate_cap_per_item": bundle_config.candidate_cap_per_item,
                },
                {
                    "candidate_counts": [len(group.candidates) for group in candidate_groups],
                    "candidate_groups": [group.model_dump(mode="json") for group in candidate_groups],
                    "search_mode": "fast",
                },
            )
            draft = llm.analyze_multi_product_sets(candidate_groups, budget_paise)
            tracer.record(
                "multi_product_gemma_analysis",
                {"budget_paise": budget_paise, "item_queries": item_queries},
                draft.model_dump(mode="json"),
            )
            multi_product_result = _materialise_bundle_result(
                candidate_groups, draft, item_queries, budget_paise
            )
        except BundleIntentError as exc:
            tracer.record(
                "multi_product_budget",
                extraction.multi_product_budget.model_dump(mode="json"),
                {"error": str(exc)},
                ok=False,
            )
            return _error(tracer, "The shared bundle budget or item list is invalid.", str(exc))
        except ConfigError as exc:
            tracer.record("multi_product_config", {}, {"error": str(exc)}, ok=False)
            return _error(tracer, "The bundle workflow configuration is invalid.", str(exc))
        except LLMError as exc:
            tracer.record("multi_product_gemma_analysis", {}, {"error": str(exc)}, ok=False)
            return _error(
                tracer,
                "The bundle analysis service returned an unusable result.",
                str(exc),
            )
        except CatalogError as exc:
            tracer.record("multi_product_fast_candidates", {}, {"error": str(exc)}, ok=False)
            return _error(tracer, "The catalog is unavailable for bundle search.", str(exc))

        result = TurnResult(
            status=TurnStatus.OK,
            message="Here are the best complete sets within your shared budget.",
            action=action,
            multi_product_result=multi_product_result,
        )

    elif action in (Action.SEARCH, Action.REFINE):
        try:
            search_mode = load_search_mode()
            if search_mode == "fast":
                search_result = catalog.fast_search(reranker_request)
                tracer.record(
                    "fast_sql_bm25_search",
                    reranker_request.model_dump(mode="json"),
                    search_result.model_dump(mode="json"),
                )
            else:
                search_result = catalog.search(reranker_request)
                tracer.record(
                    "reranker_search",
                    reranker_request.model_dump(mode="json"),
                    search_result.model_dump(mode="json"),
                )
        except RerankerError as exc:
            tracer.record("reranker_search", reranker_request.model_dump(mode="json"), {"error": str(exc)}, ok=False)
            return _error(tracer, "The search service is unavailable.", str(exc))
        except CatalogError as exc:
            tracer.record("catalog_search", reranker_request.model_dump(mode="json"), {"error": str(exc)}, ok=False)
            return _error(tracer, "The catalog is unavailable.", str(exc))
        except ConfigError as exc:
            tracer.record("search_mode", {}, {"error": str(exc)}, ok=False)
            return _error(tracer, "Search mode configuration is invalid.", str(exc))

        # The relevance LLM is part of the existing normal reranker flow.
        # Fast mode remains retrieval-only after extraction and BM25 ranking.
        if search_mode == "normal" and search_result.entries:
            candidates = [
                {
                    "sku_id": entry.sku_id,
                    "title": entry.title,
                    "brand": entry.brand,
                    "category": entry.category,
                }
                for entry in search_result.entries
            ]
            try:
                keep_ids = set(
                    llm.filter_relevant_sku_ids(
                        reranker_request.soft_query_text, reranker_request.hard_constraints, candidates
                    )
                )
                dropped = [c["sku_id"] for c in candidates if c["sku_id"] not in keep_ids]
                search_result.entries = [entry for entry in search_result.entries if entry.sku_id in keep_ids]
                tracer.record(
                    "relevance_filter",
                    {"soft_query_text": reranker_request.soft_query_text, "candidate_sku_ids": [c["sku_id"] for c in candidates]},
                    {"kept_sku_ids": [entry.sku_id for entry in search_result.entries], "dropped_sku_ids": dropped},
                )
            except LLMError as exc:
                # Best-effort - a filter failure must not fail the whole search,
                # keep every reranker result unfiltered.
                tracer.record(
                    "relevance_filter",
                    {"soft_query_text": reranker_request.soft_query_text, "candidate_sku_ids": [c["sku_id"] for c in candidates]},
                    {"error": str(exc)},
                    ok=False,
                )
        # Every sku_id the reranker returned, checked against MySQL directly -
        # a dedicated stage so it's obvious in the trace/CLI which ones were
        # real catalog rows and which were reranker hallucinations.
        tracer.record(
            "sku_verification",
            {"candidate_sku_ids": search_result.verified_sku_ids + search_result.hallucinated_sku_ids},
            {"verified_sku_ids": search_result.verified_sku_ids, "hallucinated_sku_ids": search_result.hallucinated_sku_ids},
            ok=not search_result.hallucinated_sku_ids,
        )
        memory.set_last_results(request.session_id, search_result.entries, reranker_request)
        result = TurnResult(
            status=TurnStatus.OK,
            message=f"Found {len(search_result.entries)} result(s).",
            action=action,
            search_result=search_result,
        )

    elif action == Action.PRODUCT_DETAILS:
        entry = _resolve_entry(tracer, extraction.references[0] if extraction.references else None, session.last_results)
        if entry is None:
            return _error(tracer, "Could not resolve which product you mean.", "REFERENCE_UNRESOLVED")
        details = catalog.get_details(entry.sku_id)
        tracer.record("catalog_get_details", {"sku_id": entry.sku_id}, details.model_dump(mode="json"))
        result = TurnResult(status=TurnStatus.OK, message="Here are the details.", action=action,
                             product_details=details)

    elif action == Action.COMPARE:
        expanded_refs = [
            expanded
            for ref in extraction.references
            for expanded in _expand_reference(ref, session.last_results)
        ]
        entries = [_resolve_entry(tracer, ref, session.last_results) for ref in expanded_refs]
        unresolved = [ref for ref, entry in zip(expanded_refs, entries) if entry is None]
        if len(entries) < 2 or unresolved:
            return _error(
                tracer,
                "Could not resolve which products to compare - one or more sku_ids were not found in MySQL.",
                "REFERENCE_UNRESOLVED",
            )
        sku_ids = [entry.sku_id for entry in entries]
        comparison = catalog.compare(sku_ids)
        tracer.record("catalog_compare", {"sku_ids": sku_ids}, comparison.model_dump(mode="json"))
        try:
            comparison.summary = llm.summarize_comparison(comparison)
            tracer.record("comparison_summary", {"sku_ids": sku_ids}, {"summary": comparison.summary})
        except LLMError as exc:
            # Best-effort - the raw comparison rows are the authoritative
            # answer, a summarizer failure must not fail the whole turn.
            tracer.record("comparison_summary", {"sku_ids": sku_ids}, {"error": str(exc)}, ok=False)
        result = TurnResult(status=TurnStatus.OK, message="Here is the comparison.", action=action,
                             comparison=comparison)

    elif action == Action.CHECK_AVAILABILITY:
        entry = _resolve_entry(tracer, extraction.references[0] if extraction.references else None, session.last_results)
        if entry is None:
            return _error(tracer, "Could not resolve which product you mean.", "REFERENCE_UNRESOLVED")
        availability = catalog.check_availability(entry.sku_id)
        tracer.record("catalog_check_availability", {"sku_id": entry.sku_id}, availability.model_dump(mode="json"))
        result = TurnResult(status=TurnStatus.OK, message="Here is the availability.", action=action,
                             availability=availability)

    elif action == Action.SHOW_CART:
        snapshot = cart.show_cart(request.session_id)
        tracer.record("cart_show", {"session_id": request.session_id}, snapshot.model_dump(mode="json"))
        result = TurnResult(status=TurnStatus.OK, message="Here is your cart.", action=action, cart=snapshot)

    elif action == Action.UPDATE_CART:
        resolved_operations: list[CartOperationDraft] = []
        for operation in extraction.cart_operations:
            if operation.type.value == "ADD_ITEM":
                for ref in _expand_reference(operation.reference, session.last_results):
                    entry = _resolve_entry(tracer, ref, session.last_results)
                    if entry is None:
                        return _error(tracer, "Could not resolve which product to add - sku_id not found in MySQL.",
                                       "REFERENCE_UNRESOLVED")
                    resolved_operations.append(
                        operation.model_copy(update={
                            "reference": ref,
                            "sku_id": entry.sku_id,
                            "product_id": entry.product_id,
                            "offer_id": entry.offer_id,
                        })
                    )
            else:
                resolved_operations.append(operation)

        if not resolved_operations:
            tracer.record("cart_update", {"cart_operations": []}, {"error": "CART_OPERATION_REQUIRED"}, ok=False)
            return _error(tracer, "No cart operations were specified.", "CART_OPERATION_REQUIRED")

        cart_request = UpdateCartRequest(
            session_id=request.session_id,
            idempotency_key=uuid.uuid4().hex,
            expected_cart_version=session.cart_version,
            operations=resolved_operations,
        )
        cart_result = cart.update_cart(cart_request)
        tracer.record("cart_update", cart_request.model_dump(mode="json"), cart_result.model_dump(mode="json"),
                       ok=cart_result.status == "UPDATED")
        if cart_result.status != "UPDATED":
            return _error(tracer, f"Cart update failed: {cart_result.reason}", cart_result.reason or cart_result.status)
        memory.bump_cart_version(request.session_id, cart_result.cart.cart_version)
        result = TurnResult(status=TurnStatus.OK, message="Cart updated.", action=action, cart=cart_result.cart)

    else:
        tracer.record("route", {"action": action}, {"error": "ACTION_NOT_SUPPORTED"}, ok=False)
        return _error(tracer, f"Unsupported action: {action}", "ACTION_NOT_SUPPORTED")

    session = memory.get_session(request.session_id)
    result.followups = followups.build(result, session)
    tracer.record("followups", {"action": action.value}, {"followups": [f.model_dump(mode="json") for f in result.followups]})
    canonical_message = result.message
    try:
        result = _localize_result(tracer, result)
    except SarvamError as exc:
        tracer.record(
            "sarvam_translate_output",
            {"source_language_code": ENGLISH_LANGUAGE_CODE},
            {"error": exc.code},
            ok=False,
        )
        return _error(tracer, "Could not translate the response.", exc.code)
    memory.append_turn(
        request.session_id,
        Role.ASSISTANT,
        result.message,
        language_code=tracer.language_code,
        canonical_content=canonical_message,
    )
    result.trace = tracer.steps
    return result
