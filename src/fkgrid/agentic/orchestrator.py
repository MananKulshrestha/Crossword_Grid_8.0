"""Explicit bounded shopper-turn state machine.

The orchestrator is ordinary typed Python. Models propose interpretations; the
router and ports own authorization boundaries, exact IDs, version checks,
grounding, and terminal states.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Sequence

from .contracts import (
    Action,
    AddItemOperation,
    ActiveResultBinding,
    CartSnapshot,
    ClarificationDraft,
    ClarificationOption,
    ClarificationPacket,
    CommitCommand,
    CompatibilityTuple,
    FallbackState,
    IntentDeltaV1,
    ModelCallType,
    ModelStatus,
    PublicTrace,
    QueryState,
    ReferenceDraft,
    ShopperResponse,
    StateDeltaSummary,
    TerminalState,
    TraceEvent,
    TurnRequest,
    TurnResult,
    UpdateCartRequest,
    ValidatedResearch,
    ToolStatus,
    SearchRequest,
    ResearchSynthesisV1,
    SetComparativeOperation,
    SetHardOperation,
    SetSoftOperation,
    RemoveHardOperation,
    RemoveSoftOperation,
    AddScopeOperation,
    RemoveScopeOperation,
    ClearSearchStateOperation,
    ClearCartOperation,
    ProductBinding,
    Money,
    PendingClarification,
    SearchResult,
    ProductDetails,
    Comparison,
    Availability,
)
from .gateway import PromptRegistry, StructuredModelGateway
from .capabilities import SHOPPER_CAPABILITIES
from .ports import (
    CartPort,
    CatalogSearchPort,
    ClockPort,
    IdSourcePort,
    IntentSemanticValidatorPort,
    MarkdownPipelinePort,
    QueryEnhancementPort,
    RecoveryPort,
    ReferenceResolverPort,
    ResearchPort,
    SessionStatePort,
    SuggestionPort,
    TraceSinkPort,
)
from .validation import (
    canonical_hash,
    merge_query_state,
    normalize_action,
    parse_intent_payload,
    validate_clarifying_question,
    validate_intent_semantics,
)


@dataclass(frozen=True)
class OrchestratorConfig:
    model_alias: str = "Qwen/Qwen3.6-27B-Instruct"
    intent_budget_ms: int = 1800
    enhancement_budget_ms: int = 75
    local_tool_budget_ms: int = 300
    recovery_budget_ms: int = 800
    research_budget_ms: int = 6500
    suggestion_budget_ms: int = 75
    turn_budget_ms: int = 3000
    enable_clarification_model: bool = False


class TurnOrchestrator:
    def __init__(
        self,
        *,
        state: SessionStatePort,
        enhancer: QueryEnhancementPort,
        gateway: StructuredModelGateway,
        catalog: CatalogSearchPort,
        recovery: RecoveryPort,
        references: ReferenceResolverPort,
        cart: CartPort,
        research: ResearchPort,
        suggestions: SuggestionPort,
        markdown: MarkdownPipelinePort,
        clock: ClockPort,
        ids: IdSourcePort,
        trace_sink: TraceSinkPort | None = None,
        prompts: PromptRegistry | None = None,
        validator: IntentSemanticValidatorPort | None = None,
        config: OrchestratorConfig | None = None,
    ) -> None:
        self.state = state
        self.enhancer = enhancer
        self.gateway = gateway
        self.catalog = catalog
        self.recovery = recovery
        self.references = references
        self.cart = cart
        self.research = research
        self.suggestions = suggestions
        self.markdown = markdown
        self.clock = clock
        self.ids = ids
        self.trace_sink = trace_sink
        self.prompts = prompts or PromptRegistry()
        self.validator = validator
        self.config = config or OrchestratorConfig()

    def handle(self, request: TurnRequest) -> TurnResult:
        trace_id = self.ids.new_id("trace")
        events: list[TraceEvent] = []
        compatibility: CompatibilityTuple | None = None
        action: Action | None = None
        try:
            request_hash = canonical_hash(
                {
                    "session_id": request.session_id,
                    "client_turn_id": request.client_turn_id,
                    "expected_state_version": request.expected_state_version,
                    "expected_cart_version": request.expected_cart_version,
                    "locale": request.locale,
                    "message": request.message,
                    "ui_action": request.ui_action,
                    "ignore_history": request.ignore_history,
                }
            )
            self._event(events, trace_id, request.client_turn_id, "RECEIVED", "reserve_idempotency", "OK")
            reservation = self.state.reserve(request, request_hash, trace_id)
            if reservation.status == "REPLAY" and reservation.stored_response:
                trace = self._trace(
                    trace_id,
                    request.client_turn_id,
                    reservation.stored_response.action,
                    reservation.stored_response.terminal_state,
                    events,
                    reservation.stored_response.compatibility_tuple,
                )
                return TurnResult(status="COMPLETED", http_status=200, response=reservation.stored_response, trace=trace)
            if reservation.status == "IN_PROGRESS":
                # No provider or tool work is performed for a matching pending key.
                compatibility = self._compatibility_for_in_progress(request)
                trace = self._trace(
                    trace_id,
                    request.client_turn_id,
                    None,
                    TerminalState.STATE_CONFLICT,
                    events,
                    compatibility,
                )
                return TurnResult(status="IN_PROGRESS", http_status=202, trace=trace, status_ref=reservation.status_ref)
            if reservation.status == "CONFLICT":
                compatibility = self._compatibility_for_in_progress(request)
                trace = self._trace(
                    trace_id,
                    request.client_turn_id,
                    None,
                    TerminalState.STATE_CONFLICT,
                    events,
                    compatibility,
                )
                return TurnResult(status="REJECTED", http_status=409, trace=trace)

            snapshot = self.state.load_snapshot(request.session_id, request.expected_state_version)
            compatibility = snapshot.compatibility_tuple
            self._event(events, trace_id, request.client_turn_id, "SESSION_LOADED", "load_snapshot", "OK")

            projection = None
            fallback = FallbackState.NONE
            if request.message is not None:
                enhanced = self.enhancer.enhance(request, snapshot, self.config.enhancement_budget_ms)
                projection = enhanced.projection
                fallback = enhanced.fallback
                self._event(
                    events,
                    trace_id,
                    request.client_turn_id,
                    "QUERY_ENHANCED",
                    "enhance_chat_query",
                    "OK",
                    latency_ms=enhanced.latency_ms,
                    fallback=enhanced.fallback,
                    safe_metadata={
                        "token_count": enhanced.token_count,
                        "context_hash": enhanced.envelope.context_hash,
                        "memory_used": bool(enhanced.envelope.persistent_memory_candidates),
                        "warnings": enhanced.warnings,
                    },
                )
                intent, intent_issues = self._resolve_intent(projection, compatibility, trace_id, events, request)
                if intent is None:
                    response = self._interpretation_unavailable(snapshot, compatibility, trace_id, intent_issues)
                    return self._commit_and_finish(
                        request,
                        snapshot,
                        reservation.reservation_id,
                        response,
                        snapshot.state,
                        False,
                        trace_id,
                        events,
                    )
            else:
                intent = self._typed_action_to_intent(request.ui_action, snapshot)
                self._event(events, trace_id, request.client_turn_id, "INTENT_RESOLVED", "typed_ui_action", "OK")

            assert intent is not None
            action = normalize_action(intent.primary_action)
            intent = intent.model_copy(update={"primary_action": action})
            allowed_owned_ids = {
                entry.result_entry_id
                for entry in snapshot.acknowledged_entries
            } | {
                item.cart_item_id for item in snapshot.cart.items
            }
            allowed_owned_ids.update(
                entry.context_ref
                for entry in snapshot.acknowledged_entries
                if entry.context_ref
            )
            semantic_issues = (
                self.validator.validate(intent, projection, snapshot)
                if self.validator and projection
                else validate_intent_semantics(
                    intent,
                    projection,
                    allowed_owned_ids,
                )
                if projection
                else []
            )
            if semantic_issues:
                response = self._interpretation_unavailable(snapshot, compatibility, trace_id, semantic_issues)
                self._event(
                    events,
                    trace_id,
                    request.client_turn_id,
                    "DELTA_VALIDATED",
                    "validate_intent",
                    "INVALID",
                    validation_codes=semantic_issues,
                    fallback=FallbackState.DETERMINISTIC_TEMPLATE,
                )
                return self._commit_and_finish(
                    request,
                    snapshot,
                    reservation.reservation_id,
                    response,
                    snapshot.state,
                    False,
                    trace_id,
                    events,
                )
            self._event(events, trace_id, request.client_turn_id, "DELTA_VALIDATED", "validate_intent", "OK")
            proposed_state, delta_summary = merge_query_state(snapshot.state, intent, request.client_turn_id)
            self._event(events, trace_id, request.client_turn_id, "STATE_MERGED", "merge_query_state", "OK")

            clarification = self._blocking_clarification(intent, action, snapshot, proposed_state)
            if clarification is not None:
                response = self._clarification_response(
                    action,
                    clarification,
                    compatibility,
                    trace_id,
                    delta_summary,
                    request,
                    snapshot,
                )
                self._event(
                    events,
                    trace_id,
                    request.client_turn_id,
                    "CLARIFICATION_REQUIRED",
                    "detect_follow_up_need",
                    "OK",
                    safe_metadata={"reason_code": clarification[0]},
                )
                return self._commit_and_finish(
                    request,
                    snapshot,
                    reservation.reservation_id,
                    response,
                    proposed_state,
                    False,
                    trace_id,
                    events,
                )

            response, cart_changed = self._route(
                action,
                intent,
                request,
                snapshot,
                proposed_state,
                delta_summary,
                compatibility,
                trace_id,
                events,
            )
            return self._commit_and_finish(
                request,
                snapshot,
                reservation.reservation_id,
                response,
                proposed_state,
                cart_changed,
                trace_id,
                events,
            )
        except KeyError:
            compatibility = compatibility or self._compatibility_for_in_progress(request)
            trace = self._trace(
                trace_id,
                request.client_turn_id,
                action,
                TerminalState.ACTION_FAILED_WITH_REASON,
                events,
                compatibility,
            )
            return TurnResult(status="REJECTED", http_status=404, trace=trace)
        except ValueError as exc:
            compatibility = compatibility or self._compatibility_for_in_progress(request)
            if str(exc) in {"STATE_CONFLICT", "CART_VERSION_CONFLICT"}:
                terminal = TerminalState.STATE_CONFLICT
                status_code = 409
            else:
                terminal = TerminalState.ACTION_FAILED_WITH_REASON
                status_code = 422
            self._event(
                events,
                trace_id,
                request.client_turn_id,
                "FAILED",
                "turn_boundary",
                terminal.value,
                validation_codes=[str(exc)],
            )
            trace = self._trace(trace_id, request.client_turn_id, action, terminal, events, compatibility)
            return TurnResult(status="REJECTED", http_status=status_code, trace=trace)
        except Exception:
            compatibility = compatibility or self._compatibility_for_in_progress(request)
            self._event(events, trace_id, request.client_turn_id, "FAILED", "turn_boundary", "INTERNAL_ERROR")
            trace = self._trace(
                trace_id,
                request.client_turn_id,
                action,
                TerminalState.ACTION_FAILED_WITH_REASON,
                events,
                compatibility,
            )
            return TurnResult(status="REJECTED", http_status=500, trace=trace)

    def _resolve_intent(
        self,
        projection: Any,
        compatibility: CompatibilityTuple,
        trace_id: str,
        events: list[TraceEvent],
        request: TurnRequest,
    ) -> tuple[IntentDeltaV1 | None, list[str]]:
        payload = projection.model_dump(mode="json", exclude_none=False)
        model_request = self.prompts.build_request(
            ModelCallType.RESOLVE_INTENT_AND_DELTA,
            payload,
            compatibility,
            self.ids.new_id("model_call"),
            self.config.intent_budget_ms,
            compatibility.intent_model_alias or self.config.model_alias,
        )
        model_response = self.gateway.complete(model_request)
        self._event(
            events,
            trace_id,
            request.client_turn_id,
            "INTENT_RESOLVED",
            ModelCallType.RESOLVE_INTENT_AND_DELTA.value,
            model_response.status.value,
            latency_ms=model_response.latency_ms,
            input_hash=model_response.input_hash,
            output_hash=model_response.output_hash,
            validation_codes=[issue.code for issue in model_response.validation_issues],
            safe_metadata={"model_alias": model_response.model_alias, "provider": model_response.provider_name},
        )
        intent, issues = self._parse_and_validate_model_intent(model_response, projection)
        if intent is not None:
            return intent, []

        # Exactly one repair call. It receives sanitized validation codes, never
        # the raw invalid output or provider error text.
        repair_payload = {
            **payload,
            "repair_validation_codes": issues[:20],
            "repair_attempt": True,
        }
        repair_request = self.prompts.build_request(
            ModelCallType.RESOLVE_INTENT_AND_DELTA,
            repair_payload,
            compatibility,
            self.ids.new_id("model_call"),
            self.config.intent_budget_ms,
            compatibility.intent_model_alias or self.config.model_alias,
        )
        repair_response = self.gateway.complete(repair_request)
        self._event(
            events,
            trace_id,
            request.client_turn_id,
            "INTENT_REPAIR",
            ModelCallType.RESOLVE_INTENT_AND_DELTA.value,
            repair_response.status.value,
            latency_ms=repair_response.latency_ms,
            input_hash=repair_response.input_hash,
            output_hash=repair_response.output_hash,
            validation_codes=[issue.code for issue in repair_response.validation_issues],
            safe_metadata={"repair": True},
        )
        intent, repair_issues = self._parse_and_validate_model_intent(repair_response, projection)
        if intent is not None:
            return intent, []
        fallback = self._deterministic_exact_grammar(request.message or "")
        if fallback is not None:
            self._event(
                events,
                trace_id,
                request.client_turn_id,
                "INTENT_FALLBACK",
                "deterministic_exact_grammar",
                "OK",
                fallback=FallbackState.DETERMINISTIC_EXACT_GRAMMAR,
            )
            return fallback, []
        return None, (issues + repair_issues)[:20]

    def _parse_and_validate_model_intent(self, model_response: Any, projection: Any) -> tuple[IntentDeltaV1 | None, list[str]]:
        if model_response.status is not ModelStatus.OK or model_response.output_payload is None:
            return None, [f"MODEL_{model_response.status.value}"]
        intent, issues = parse_intent_payload(model_response.output_payload)
        if intent is None:
            return None, issues
        allowed = {
            entry.result_entry_id for entry in projection.active_result_bindings
        } | set(projection.cart_summary.item_ids)
        issues = validate_intent_semantics(intent, projection, allowed)
        return (intent, []) if not issues else (None, issues)

    def _deterministic_exact_grammar(self, message: str) -> IntentDeltaV1 | None:
        lower = message.casefold().strip()
        if lower in {"show cart", "show my cart", "cart"}:
            return IntentDeltaV1(primary_action=Action.SHOW_CART)
        if lower in {"help", "what can you do"}:
            return IntentDeltaV1(primary_action=Action.HELP)
        return None

    def _typed_action_to_intent(self, ui_action: Any, snapshot: Any) -> IntentDeltaV1:
        if ui_action is None:
            raise ValueError("TYPED_ACTION_REQUIRED")
        action = normalize_action(ui_action.action)
        payload = dict(ui_action.payload)
        raw_references = payload.pop("references", [])
        if not isinstance(raw_references, list):
            raise ValueError("TYPED_REFERENCES_INVALID")
        try:
            references = [ReferenceDraft.model_validate(item) for item in raw_references]
        except Exception as exc:
            raise ValueError("TYPED_REFERENCES_INVALID") from exc
        return IntentDeltaV1(
            primary_action=action,
            references=references,
            action_parameters=payload,
        )

    def _blocking_clarification(
        self,
        intent: IntentDeltaV1,
        action: Action,
        snapshot: Any,
        proposed_state: QueryState,
    ) -> tuple[str, str, list[ClarificationOption]] | None:
        if snapshot.pending_clarification and action not in {Action.HELP, Action.RESET_SEARCH}:
            return (
                snapshot.pending_clarification.reason_code,
                "clarification_choice",
                [ClarificationOption(choice_id=item, label=item) for item in snapshot.pending_clarification.choice_ids],
            )
        if action in {Action.PRODUCT_DETAILS, Action.CHECK_AVAILABILITY, Action.UPDATE_CART} and not intent.references:
            if action is Action.UPDATE_CART and intent.action_parameters.get("operations"):
                return None
            return ("REFERENCE_REQUIRED", "result_entry_id", [])
        if action in {Action.PRODUCT_DETAILS, Action.CHECK_AVAILABILITY} and len(intent.references) > 1:
            return ("REFERENCE_COUNT", "result_entry_id", [])
        if action is Action.COMPARE and len(intent.references) < 2:
            return ("COMPARE_NEEDS_TWO_REFERENCES", "result_entry_id", [])
        if action is Action.COMPARE and len(intent.references) > 4:
            return ("COMPARE_TOO_MANY_REFERENCES", "result_entry_id", [])
        if action is Action.UPDATE_CART and not intent.action_parameters.get("operations"):
            return ("CART_OPERATION_REQUIRED", "cart_operation", [])
        return None

    def _route(
        self,
        action: Action,
        intent: IntentDeltaV1,
        request: TurnRequest,
        snapshot: Any,
        proposed_state: QueryState,
        delta_summary: StateDeltaSummary,
        compatibility: CompatibilityTuple,
        trace_id: str,
        events: list[TraceEvent],
    ) -> tuple[ShopperResponse, bool]:
        if action not in SHOPPER_CAPABILITIES:
            return self._action_failed(action, compatibility, trace_id, delta_summary, "CAPABILITY_NOT_ALLOWED"), False
        if action in {Action.SEARCH, Action.REFINE}:
            result = self.catalog.search(
                SearchRequest(
                    session_id=request.session_id,
                    query_state=proposed_state,
                    top_k=5,
                    exclusions=[],
                    compatibility_tuple=compatibility,
                ),
                self.config.local_tool_budget_ms,
            )
            self._event(
                events,
                trace_id,
                request.client_turn_id,
                "TOOL_EXECUTED",
                "search_catalog",
                result.status.value,
                safe_metadata={"entries": len(result.entries), "hard_filter_hash": result.hard_filter_hash},
            )
            # Retrieval adapters should pin this ID. Keep a safe compatibility
            # fallback for an older adapter and mark that degradation in trace.
            if result.result_set_id is None:
                result = result.model_copy(update={"result_set_id": self.ids.new_id("result_set")})
                self._event(
                    events,
                    trace_id,
                    request.client_turn_id,
                    "RESULT_SET_PINNED",
                    "search_result_set",
                    "DEGRADED_ADAPTER",
                    fallback=FallbackState.DETERMINISTIC_TEMPLATE,
                )
            confidence = self.recovery.assess(result, proposed_state)
            self._event(
                events,
                trace_id,
                request.client_turn_id,
                "RETRIEVAL_ASSESSED",
                "assess_retrieval_confidence",
                confidence.decision,
                safe_metadata={"reasons": confidence.reasons},
            )
            if confidence.decision == "RECOVER":
                result = self.recovery.recover(
                    result,
                    proposed_state,
                    compatibility,
                    self.config.recovery_budget_ms,
                )
                self._event(
                    events,
                    trace_id,
                    request.client_turn_id,
                    "RECOVERY_EXECUTED",
                    "bounded_recovery",
                    result.status.value,
                )
            terminal = TerminalState.ANSWERED_WITH_GROUNDED_RESULTS if result.entries else TerminalState.NO_ELIGIBLE_MATCH
            return (
                ShopperResponse(
                    response_id=self.ids.new_id("response"),
                    action=action,
                    terminal_state=terminal,
                    summary=(
                        f"I found {len(result.entries)} prototype catalog result(s)."
                        if result.entries
                        else "No eligible prototype catalog result matched the preserved constraints."
                    ),
                    search_entries=result.entries,
                    result_set_id=result.result_set_id,
                    state_delta=delta_summary,
                    warnings=result.warnings,
                    compatibility_tuple=compatibility,
                    trace_id=trace_id,
                ),
                False,
            )

        if action in {Action.PRODUCT_DETAILS, Action.CHECK_AVAILABILITY, Action.COMPARE}:
            resolution = self.references.resolve(request.session_id, intent.references, action, snapshot)
            self._event(
                events,
                trace_id,
                request.client_turn_id,
                "TOOL_EXECUTED",
                "resolve_reference",
                resolution.status,
                safe_metadata={"resolved_count": len(resolution.references)},
            )
            if resolution.status != "RESOLVED":
                response = self._clarification_response(
                    action,
                    (resolution.reason_code or "REFERENCE_UNRESOLVED", "result_entry_id", resolution.options),
                    compatibility,
                    trace_id,
                    delta_summary,
                    request,
                    snapshot,
                )
                return response, False
            bindings = [item.binding for item in resolution.references]
            if action is Action.PRODUCT_DETAILS:
                eligibility = self.catalog.check_eligibility(bindings[0], "DETAILS", self.config.local_tool_budget_ms)
                if not eligibility.eligible:
                    return self._action_failed(action, compatibility, trace_id, delta_summary, "POLICY_BLOCKED"), False
                details = self.catalog.get_details(bindings[0], compatibility, self.config.local_tool_budget_ms)
                self._event(events, trace_id, request.client_turn_id, "TOOL_EXECUTED", "get_product_details", details.status.value)
                return self._details_response(action, details, compatibility, trace_id, delta_summary), False
            if action is Action.COMPARE:
                comparison = self.catalog.compare(bindings[:4], compatibility, self.config.local_tool_budget_ms)
                self._event(events, trace_id, request.client_turn_id, "TOOL_EXECUTED", "compare_products", comparison.status.value)
                return self._comparison_response(action, comparison, compatibility, trace_id, delta_summary), False
            availability = self.catalog.check_availability(bindings[0], compatibility, self.config.local_tool_budget_ms)
            self._event(events, trace_id, request.client_turn_id, "TOOL_EXECUTED", "check_availability", availability.status.value)
            return self._availability_response(action, availability, compatibility, trace_id, delta_summary), False

        if action is Action.SHOW_CART:
            cart = self.cart.show_cart(request.session_id, snapshot.cart_version, self.config.local_tool_budget_ms)
            self._event(events, trace_id, request.client_turn_id, "TOOL_EXECUTED", "show_cart", "OK")
            return (
                ShopperResponse(
                    response_id=self.ids.new_id("response"),
                    action=action,
                    terminal_state=TerminalState.CART_SHOWN,
                    summary="Prototype cart snapshot.",
                    cart=cart,
                    state_delta=delta_summary,
                    compatibility_tuple=compatibility,
                    trace_id=trace_id,
                ),
                False,
            )

        if action is Action.UPDATE_CART:
            cart_request = self._cart_request_from_intent(intent, request, snapshot)
            if cart_request is None:
                return self._action_failed(action, compatibility, trace_id, delta_summary, "CART_OPERATION_INVALID"), False
            for operation in cart_request.operations:
                binding = operation.binding if isinstance(operation, AddItemOperation) else None
                if binding is None and hasattr(operation, "cart_item_id"):
                    cart_item = next(
                        (item for item in snapshot.cart.items if item.cart_item_id == operation.cart_item_id),
                        None,
                    )
                    binding = cart_item.binding if cart_item else None
                if binding is not None:
                    eligibility = self.catalog.check_eligibility(
                        binding,
                        "CART_UPDATE",
                        self.config.local_tool_budget_ms,
                    )
                    if not eligibility.eligible:
                        return self._action_failed(
                            action,
                            compatibility,
                            trace_id,
                            delta_summary,
                            "COMMERCE_POLICY_BLOCKED",
                        ), False
            update = self.cart.update_cart(cart_request, self.config.local_tool_budget_ms)
            self._event(
                events,
                trace_id,
                request.client_turn_id,
                "TOOL_EXECUTED",
                "update_cart",
                update.status,
                safe_metadata={"operations": update.applied_operation_ids},
            )
            if update.status == "UPDATED":
                return (
                    ShopperResponse(
                        response_id=self.ids.new_id("response"),
                        action=action,
                        terminal_state=TerminalState.CART_UPDATED,
                        summary="Prototype cart updated. Amounts are item-price-only mock catalog values.",
                        cart=update.cart,
                        state_delta=delta_summary,
                        warnings=update.warnings,
                        compatibility_tuple=compatibility,
                        trace_id=trace_id,
                    ),
                    True,
                )
            terminal = TerminalState.ACTION_FAILED_WITH_REASON
            return (
                ShopperResponse(
                    response_id=self.ids.new_id("response"),
                    action=action,
                    terminal_state=terminal,
                    summary="The prototype cart was not changed.",
                    cart=update.cart,
                    state_delta=delta_summary,
                    warnings=update.warnings + [update.status],
                    compatibility_tuple=compatibility,
                    trace_id=trace_id,
                ),
                False,
            )

        if action is Action.RESEARCH_EXTERNAL:
            selected_ids = [reference.value for reference in intent.references if reference.kind == "OWNED_ID"]
            decision = self.research.detect_need(intent, request.message or "", selected_ids)
            self._event(events, trace_id, request.client_turn_id, "RESEARCH_EXECUTED", "detect_research_need", decision.decision)
            if decision.decision != "REQUIRED":
                return (
                    ShopperResponse(
                        response_id=self.ids.new_id("response"),
                        action=action,
                        terminal_state=TerminalState.RESEARCH_UNAVAILABLE,
                        summary="External research is not enabled for this request; the catalog path remains available.",
                        state_delta=delta_summary,
                        compatibility_tuple=compatibility,
                        trace_id=trace_id,
                    ),
                    False,
                )
            search_result = self.research.search(decision, self.config.research_budget_ms)
            self._event(events, trace_id, request.client_turn_id, "RESEARCH_EXECUTED", "online_search", search_result.status)
            if not search_result.sources:
                research = ValidatedResearch(status="UNAVAILABLE", warnings=search_result.warnings + [search_result.status])
            else:
                model_request = self.prompts.build_request(
                    ModelCallType.SYNTHESIZE_RESEARCH_ANSWER,
                    {
                        "question": decision.canonical_query,
                        "selected_entity_ids": selected_ids,
                        "sources": [source.model_dump(mode="json") for source in search_result.sources[:5]],
                    },
                    compatibility,
                    self.ids.new_id("model_call"),
                    self.config.research_budget_ms,
                    compatibility.research_model_alias or self.config.model_alias,
                )
                model_response = self.gateway.complete(model_request)
                synthesis = None
                if model_response.status is ModelStatus.OK and model_response.output_payload:
                    try:
                        from .validation import canonical_json

                        synthesis = ResearchSynthesisV1.model_validate_json(
                            canonical_json(model_response.output_payload)
                        )
                    except Exception:
                        synthesis = None
                research = self.research.validate(decision, search_result, synthesis)
                self._event(
                    events,
                    trace_id,
                    request.client_turn_id,
                    "RESEARCH_EXECUTED",
                    ModelCallType.SYNTHESIZE_RESEARCH_ANSWER.value,
                    model_response.status.value,
                    latency_ms=model_response.latency_ms,
                    validation_codes=[issue.code for issue in model_response.validation_issues],
                )
            terminal = (
                TerminalState.ANSWERED_WITH_EXTERNAL_RESEARCH
                if research.status in {"VALID", "PARTIAL"}
                else TerminalState.RESEARCH_UNAVAILABLE
            )
            return (
                ShopperResponse(
                    response_id=self.ids.new_id("response"),
                    action=action,
                    terminal_state=terminal,
                    summary=research.summary or "External research could not be verified within the bounded research path.",
                    research=research,
                    state_delta=delta_summary,
                    compatibility_tuple=compatibility,
                    trace_id=trace_id,
                ),
                False,
            )

        if action is Action.RESET_SEARCH:
            return (
                ShopperResponse(
                    response_id=self.ids.new_id("response"),
                    action=action,
                    terminal_state=TerminalState.ANSWERED_WITH_GROUNDED_RESULTS,
                    summary="Search state reset. The prototype cart was preserved.",
                    state_delta=delta_summary,
                    compatibility_tuple=compatibility,
                    trace_id=trace_id,
                ),
                False,
            )
        if action is Action.HELP:
            return (
                ShopperResponse(
                    response_id=self.ids.new_id("response"),
                    action=action,
                    terminal_state=TerminalState.ANSWERED_WITH_GROUNDED_RESULTS,
                    summary="I can search, refine, inspect, compare, check prototype availability, show or update the prototype cart, and run explicitly requested cited research.",
                    state_delta=delta_summary,
                    compatibility_tuple=compatibility,
                    trace_id=trace_id,
                ),
                False,
            )
        return self._action_failed(action, compatibility, trace_id, delta_summary, "ACTION_NOT_IMPLEMENTED"), False

    def _cart_request_from_intent(self, intent: IntentDeltaV1, request: TurnRequest, snapshot: Any) -> UpdateCartRequest | None:
        raw_operations = intent.action_parameters.get("operations")
        if not isinstance(raw_operations, list):
            return None
        operations: list[Any] = []
        from .contracts import (
            AddItemOperation,
            DecrementItemOperation,
            IncrementItemOperation,
            RemoveItemOperation,
            SetQuantityOperation,
        )

        allowed_entries = {entry.result_entry_id: entry for entry in snapshot.acknowledged_entries}
        if not 1 <= len(raw_operations) <= 10:
            return None
        allowed_cart_items = {item.cart_item_id for item in snapshot.cart.items}
        seen_operation_ids: set[str] = set()
        for raw in raw_operations:
            if not isinstance(raw, dict) or "type" not in raw or "operation_id" not in raw:
                return None
            if raw["operation_id"] in seen_operation_ids:
                return None
            seen_operation_ids.add(raw["operation_id"])
            operation_type = raw["type"]
            try:
                if operation_type == "ADD_ITEM":
                    entry_id = raw.get("result_entry_id")
                    entry = allowed_entries.get(entry_id)
                    if entry is None:
                        return None
                    operations.append(
                        AddItemOperation(
                            operation_id=raw["operation_id"],
                            result_entry_id=entry.result_entry_id,
                            binding=entry.binding,
                            selected_variant_hash=raw.get("selected_variant_hash", canonical_hash(entry.binding)),
                            quantity=raw.get("quantity", 1),
                            expected_unit_price=(
                                Money.model_validate_json(canonical_json(raw["expected_unit_price"]))
                                if raw.get("expected_unit_price") is not None
                                else None
                            ),
                        )
                    )
                elif operation_type == "SET_QUANTITY":
                    if raw.get("cart_item_id") not in allowed_cart_items:
                        return None
                    operations.append(SetQuantityOperation.model_validate(raw))
                elif operation_type == "INCREMENT_ITEM":
                    if raw.get("cart_item_id") not in allowed_cart_items:
                        return None
                    operations.append(IncrementItemOperation.model_validate(raw))
                elif operation_type == "DECREMENT_ITEM":
                    if raw.get("cart_item_id") not in allowed_cart_items:
                        return None
                    operations.append(DecrementItemOperation.model_validate(raw))
                elif operation_type == "REMOVE_ITEM":
                    if raw.get("cart_item_id") not in allowed_cart_items:
                        return None
                    operations.append(RemoveItemOperation.model_validate(raw))
                elif operation_type == "CLEAR_CART":
                    if not raw.get("confirmation_token"):
                        return None
                    operations.append(ClearCartOperation.model_validate(raw))
                else:
                    return None
            except Exception:
                return None
        if not operations:
            return None
        try:
            return UpdateCartRequest(
                session_id=request.session_id,
                expected_state_version=request.expected_state_version,
                expected_cart_version=request.expected_cart_version,
                idempotency_key=request.idempotency_key,
                operations=operations,
            )
        except Exception:
            return None

    def _commit_and_finish(
        self,
        request: TurnRequest,
        snapshot: Any,
        reservation_id: str,
        response: ShopperResponse,
        proposed_state: QueryState,
        cart_changed: bool,
        trace_id: str,
        events: list[TraceEvent],
    ) -> TurnResult:
        acknowledged_result_set_id = snapshot.acknowledged_result_set_id
        acknowledged_entries = list(snapshot.acknowledged_entries)
        commit_state = proposed_state
        if response.result_set_id is not None:
            acknowledged_result_set_id = response.result_set_id
            acknowledged_entries = [
                ActiveResultBinding(
                    result_entry_id=entry.result_entry_id,
                    display_position=entry.display_position,
                    binding=entry.binding,
                )
                for entry in response.search_entries
            ]
            commit_state = proposed_state.model_copy(
                update={
                    "latest_result_set_id": response.result_set_id,
                    "selected_result_entry_ids": [],
                }
            )
        elif response.action is Action.RESET_SEARCH:
            acknowledged_result_set_id = None
            acknowledged_entries = []
            commit_state = proposed_state.model_copy(
                update={"latest_result_set_id": None, "selected_result_entry_ids": []}
            )
        if response.terminal_state is TerminalState.CLARIFICATION_REQUIRED and response.clarification:
            if commit_state.pending_clarification is None:
                commit_state = commit_state.model_copy(
                    update={
                        "pending_clarification": PendingClarification(
                            clarification_id=self.ids.new_id("clarification"),
                            reason_code=response.clarification_reason_code or "CLARIFICATION_REQUIRED",
                            choice_ids=response.clarification.choice_ids,
                            state_hash=canonical_hash(commit_state),
                            expires_at=self.clock.now_utc() + timedelta(minutes=10),
                        )
                    }
                )
        commit = self.state.commit(
            CommitCommand(
                reservation_id=reservation_id,
                session_id=request.session_id,
                expected_state_version=snapshot.state_version,
                expected_cart_version=snapshot.cart_version,
                response=response,
                proposed_state=commit_state,
                cart_changed=cart_changed,
                acknowledged_result_set_id=acknowledged_result_set_id,
                acknowledged_entries=acknowledged_entries,
            )
        )
        if not commit.committed:
            conflict_response = response.model_copy(
                update={
                    "terminal_state": TerminalState.STATE_CONFLICT,
                    "summary": "The session changed while this turn was running. Please retry from the current state.",
                    "warnings": response.warnings + [commit.safe_reason or "STATE_CONFLICT"],
                    "suggestions": None,
                }
            )
            self._event(
                events,
                trace_id,
                request.client_turn_id,
                "STATE_COMMITTED",
                "commit_turn",
                "STATE_CONFLICT",
            )
            trace = self._trace(
                trace_id,
                request.client_turn_id,
                response.action,
                TerminalState.STATE_CONFLICT,
                events,
                response.compatibility_tuple,
            )
            return TurnResult(status="REJECTED", http_status=409, response=conflict_response, trace=trace)
        committed_response = commit.response or response
        self._event(events, trace_id, request.client_turn_id, "STATE_COMMITTED", "commit_turn", "COMMITTED")
        try:
            post_commit_snapshot = self.state.load_snapshot(request.session_id, commit.state_version)
        except Exception:
            post_commit_snapshot = snapshot
        suggestion_set = self.suggestions.build_and_store(
            committed_response,
            post_commit_snapshot,
            trace_id,
            self.config.suggestion_budget_ms,
        )
        if suggestion_set:
            committed_response = committed_response.model_copy(update={"suggestions": suggestion_set})
            self._event(
                events,
                trace_id,
                request.client_turn_id,
                "FOLLOW_UPS_ATTACHED",
                "build_follow_up_candidates",
                "OK",
                safe_metadata={"count": len(suggestion_set.suggestions)},
            )
        else:
            self._event(
                events,
                trace_id,
                request.client_turn_id,
                "FOLLOW_UPS_ATTACHED",
                "build_follow_up_candidates",
                "OMITTED",
                fallback=FallbackState.OMITTED,
            )
        self.state.attach_final_response(committed_response)
        handoff = self.markdown.handoff(committed_response, self._trace(
            trace_id,
            request.client_turn_id,
            committed_response.action,
            committed_response.terminal_state,
            events,
            committed_response.compatibility_tuple,
        ))
        self._event(
            events,
            trace_id,
            request.client_turn_id,
            "RESPONSE_COMPOSED",
            "markdown_pipeline_handoff",
            handoff.status,
            safe_metadata={"handoff_ref": handoff.handoff_ref},
        )
        trace = self._trace(
            trace_id,
            request.client_turn_id,
            committed_response.action,
            committed_response.terminal_state,
            events,
            committed_response.compatibility_tuple,
        )
        return TurnResult(status="COMPLETED", http_status=200, response=committed_response, trace=trace)

    def _clarification_response(
        self,
        action: Action,
        clarification: tuple[str, str, Sequence[ClarificationOption]],
        compatibility: CompatibilityTuple,
        trace_id: str,
        delta_summary: StateDeltaSummary,
        request: TurnRequest,
        snapshot: Any,
    ) -> ShopperResponse:
        reason, target, options = clarification
        if not options:
            if reason == "COMPARE_NEEDS_TWO_REFERENCES":
                options = [
                    ClarificationOption(choice_id="choose_two", label="Choose two acknowledged results to compare."),
                ]
            elif reason == "REFERENCE_REQUIRED":
                options = [ClarificationOption(choice_id="choose_result", label="Choose an acknowledged result." )]
            elif reason == "CART_OPERATION_REQUIRED":
                options = [ClarificationOption(choice_id="choose_cart_operation", label="Choose a cart operation." )]
            else:
                options = [ClarificationOption(choice_id="retry", label="Please provide one more specific choice.")]
        packet = ClarificationPacket(
            reason_code=reason,
            target_field=target,
            preserved_state_summary=snapshot.state.compact_goal_summary,
            options=list(options)[:4],
        )
        draft = ClarificationDraft(
            question=(
                "Which acknowledged result should I use?"
                if target == "result_entry_id"
                else "What would you like me to do next?"
            ),
            choice_ids=[option.choice_id for option in packet.options],
            mentioned_values=[],
        )
        if validate_clarifying_question(draft, packet):
            draft = ClarificationDraft(
                question="Please choose one of the available options.",
                choice_ids=packet.options and [option.choice_id for option in packet.options] or ["retry"],
                mentioned_values=[],
            )
        return ShopperResponse(
            response_id=self.ids.new_id("response"),
            action=action,
            terminal_state=TerminalState.CLARIFICATION_REQUIRED,
            summary="I need one focused clarification before I can continue.",
            clarification=draft,
            clarification_reason_code=reason,
            clarification_target_field=target,
            state_delta=delta_summary,
            compatibility_tuple=compatibility,
            trace_id=trace_id,
        )

    def _interpretation_unavailable(
        self,
        snapshot: Any,
        compatibility: CompatibilityTuple,
        trace_id: str,
        issues: Sequence[str],
    ) -> ShopperResponse:
        return ShopperResponse(
            response_id=self.ids.new_id("response"),
            action=Action.HELP,
            terminal_state=TerminalState.INTERPRETATION_UNAVAILABLE,
            summary="I could not safely interpret that request. Your current search state was preserved.",
            warnings=list(issues)[:12],
            compatibility_tuple=compatibility,
            trace_id=trace_id,
        )

    def _action_failed(
        self,
        action: Action,
        compatibility: CompatibilityTuple,
        trace_id: str,
        delta_summary: StateDeltaSummary,
        reason: str,
    ) -> ShopperResponse:
        return ShopperResponse(
            response_id=self.ids.new_id("response"),
            action=action,
            terminal_state=TerminalState.ACTION_FAILED_WITH_REASON,
            summary="The requested action could not be verified, so no state was changed.",
            warnings=[reason],
            state_delta=delta_summary,
            compatibility_tuple=compatibility,
            trace_id=trace_id,
        )

    def _details_response(self, action: Action, details: ProductDetails, compatibility: CompatibilityTuple, trace_id: str, delta: StateDeltaSummary) -> ShopperResponse:
        terminal = TerminalState.ANSWERED_WITH_PRODUCT_DETAILS if details.status is ToolStatus.OK else TerminalState.ACTION_FAILED_WITH_REASON
        return ShopperResponse(
            response_id=self.ids.new_id("response"), action=action, terminal_state=terminal,
            summary="Verified prototype catalog details." if details.status is ToolStatus.OK else "Details could not be verified.",
            details=details, state_delta=delta, warnings=details.warnings,
            compatibility_tuple=compatibility, trace_id=trace_id,
        )

    def _comparison_response(self, action: Action, comparison: Comparison, compatibility: CompatibilityTuple, trace_id: str, delta: StateDeltaSummary) -> ShopperResponse:
        terminal = TerminalState.ANSWERED_WITH_COMPARISON if comparison.status is ToolStatus.OK else TerminalState.ACTION_FAILED_WITH_REASON
        return ShopperResponse(
            response_id=self.ids.new_id("response"), action=action, terminal_state=terminal,
            summary="Deterministic comparison of the acknowledged products." if comparison.status is ToolStatus.OK else "Comparison could not be verified.",
            comparison=comparison, state_delta=delta, warnings=comparison.warnings,
            compatibility_tuple=compatibility, trace_id=trace_id,
        )

    def _availability_response(self, action: Action, availability: Availability, compatibility: CompatibilityTuple, trace_id: str, delta: StateDeltaSummary) -> ShopperResponse:
        terminal = TerminalState.ANSWERED_WITH_AVAILABILITY if availability.status is ToolStatus.OK else TerminalState.ACTION_FAILED_WITH_REASON
        return ShopperResponse(
            response_id=self.ids.new_id("response"), action=action, terminal_state=terminal,
            summary="Prototype catalog availability; not live inventory or delivery." if availability.status is ToolStatus.OK else "Availability could not be verified.",
            availability=availability, state_delta=delta, warnings=availability.warnings,
            compatibility_tuple=compatibility, trace_id=trace_id,
        )

    def _compatibility_for_in_progress(self, request: TurnRequest) -> CompatibilityTuple:
        alias = self.config.model_alias
        return CompatibilityTuple(
            contract_schema_version="agentic-contracts-v1",
            catalog_version="unavailable",
            index_version="unavailable",
            taxonomy_version="unavailable",
            category_schema_version="unavailable",
            lexicon_version="unavailable",
            rank_policy_version="unavailable",
            gate_policy_version="unavailable",
            intent_prompt_version="1",
            intent_model_alias=alias,
            response_template_version="1",
            commerce_policy_version="1",
            research_policy_version="1",
            suggestion_policy_version="1",
            memory_schema_version="1",
            query_enhancement_policy_version="1",
        )

    def _event(
        self,
        events: list[TraceEvent],
        trace_id: str,
        turn_id: str,
        stage: str,
        logical_name: str,
        status: str,
        *,
        latency_ms: int = 0,
        input_hash: str | None = None,
        output_hash: str | None = None,
        validation_codes: Sequence[str] = (),
        fallback: FallbackState = FallbackState.NONE,
        safe_metadata: dict[str, Any] | None = None,
    ) -> None:
        event = TraceEvent(
            event_name="shopper_agentic_stage",
            trace_id=trace_id,
            turn_id=turn_id,
            stage=stage,
            logical_name=logical_name,
            status=status,
            latency_ms=max(0, latency_ms),
            input_hash=input_hash,
            output_hash=output_hash,
            validation_codes=list(validation_codes)[:20],
            fallback=fallback,
            safe_metadata=safe_metadata or {},
        )
        events.append(event)
        if self.trace_sink:
            self.trace_sink.append(event)

    def _trace(
        self,
        trace_id: str,
        turn_id: str,
        action: Action | None,
        terminal: TerminalState,
        events: list[TraceEvent],
        compatibility: CompatibilityTuple,
    ) -> PublicTrace:
        return PublicTrace(
            trace_id=trace_id,
            action=action,
            terminal_state=terminal,
            events=list(events),
            compatibility_tuple=compatibility,
        )
