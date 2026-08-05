"""Turn handling: extract -> enhance -> route -> followups -> update memory.

Any failure at any stage (LLM invalid/timeout, reranker timeout/error,
MySQL error, unresolved reference) returns an explicit ERROR TurnResult
immediately. No retries, no cascading fallbacks, no cached/stale data
returned in place of a real answer - a failure is a hard pause.

Every stage is recorded onto TurnResult.trace (stage name, exact input,
exact output) so a caller - the CLI in cli.py, or Swagger's raw response -
can see precisely what each step did.
"""

from __future__ import annotations

import uuid

from . import catalog, cart, followups, llm, memory
from .catalog import CatalogError, RerankerError
from .contracts import (
    Action,
    CartOperationDraft,
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


class _Tracer:
    def __init__(self) -> None:
        self.steps: list[TraceStep] = []

    def record(self, stage: str, input_: dict, output: dict, ok: bool = True) -> None:
        self.steps.append(TraceStep(stage=stage, input=input_, output=output, ok=ok))


def _error(tracer: _Tracer, message: str, code: str) -> TurnResult:
    return TurnResult(status=TurnStatus.ERROR, message=message, error_code=code, trace=tracer.steps)


def _resolve_reference(reference: Reference | None, last_results: list[SearchEntry]) -> SearchEntry | None:
    if reference is None:
        return None
    if reference.sku_id:
        return next((entry for entry in last_results if entry.sku_id == reference.sku_id), None)
    if reference.ordinal:
        index = reference.ordinal - 1
        if 0 <= index < len(last_results):
            return last_results[index]
    return None


def handle_turn(request: TurnRequest) -> TurnResult:
    tracer = _Tracer()

    session = memory.get_session(request.session_id)
    if session is None:
        session = memory.create_session(request.session_id)

    memory.append_turn(request.session_id, Role.USER, request.message)

    history_in = {"message": request.message, "history_len": len(session.chat_history)}
    try:
        extraction = llm.extract_query(request.message, session.chat_history)
    except LLMError as exc:
        tracer.record("query_extractor", history_in, {"error": str(exc)}, ok=False)
        return _error(tracer, "Could not interpret the message.", str(exc))
    tracer.record("query_extractor", history_in, extraction.model_dump(mode="json"))

    enhance_in = {
        "extraction": extraction.model_dump(mode="json"),
        "prior_reranker_request": (
            session.last_reranker_request.model_dump(mode="json") if session.last_reranker_request else None
        ),
    }
    try:
        reranker_request = llm.enhance_query(extraction, session)
    except LLMError as exc:
        tracer.record("query_enhancer", enhance_in, {"error": str(exc)}, ok=False)
        return _error(tracer, "Could not build a search request.", str(exc))
    tracer.record("query_enhancer", enhance_in, reranker_request.model_dump(mode="json"))

    action = extraction.action

    if action in (Action.SEARCH, Action.REFINE):
        try:
            search_result = catalog.search(reranker_request)
        except RerankerError as exc:
            tracer.record("reranker_search", reranker_request.model_dump(mode="json"), {"error": str(exc)}, ok=False)
            return _error(tracer, "The search service is unavailable.", str(exc))
        except CatalogError as exc:
            tracer.record("reranker_search", reranker_request.model_dump(mode="json"), {"error": str(exc)}, ok=False)
            return _error(tracer, "The catalog is unavailable.", str(exc))
        tracer.record("reranker_search", reranker_request.model_dump(mode="json"), search_result.model_dump(mode="json"))
        memory.set_last_results(request.session_id, search_result.entries, reranker_request)
        result = TurnResult(
            status=TurnStatus.OK,
            message=f"Found {len(search_result.entries)} result(s).",
            action=action,
            search_result=search_result,
        )

    elif action == Action.PRODUCT_DETAILS:
        entry = _resolve_reference(extraction.references[0] if extraction.references else None, session.last_results)
        if entry is None:
            tracer.record("resolve_reference", {"references": [r.model_dump() for r in extraction.references]},
                           {"error": "REFERENCE_UNRESOLVED"}, ok=False)
            return _error(tracer, "Could not resolve which product you mean.", "REFERENCE_UNRESOLVED")
        details = catalog.get_details(entry.sku_id)
        tracer.record("catalog_get_details", {"sku_id": entry.sku_id}, details.model_dump(mode="json"))
        result = TurnResult(status=TurnStatus.OK, message="Here are the details.", action=action,
                             product_details=details)

    elif action == Action.COMPARE:
        entries = [_resolve_reference(ref, session.last_results) for ref in extraction.references]
        if len(entries) < 2 or any(entry is None for entry in entries):
            tracer.record("resolve_reference", {"references": [r.model_dump() for r in extraction.references]},
                           {"error": "REFERENCE_UNRESOLVED"}, ok=False)
            return _error(tracer, "Could not resolve which products to compare.", "REFERENCE_UNRESOLVED")
        sku_ids = [entry.sku_id for entry in entries]
        comparison = catalog.compare(sku_ids)
        tracer.record("catalog_compare", {"sku_ids": sku_ids}, comparison.model_dump(mode="json"))
        result = TurnResult(status=TurnStatus.OK, message="Here is the comparison.", action=action,
                             comparison=comparison)

    elif action == Action.CHECK_AVAILABILITY:
        entry = _resolve_reference(extraction.references[0] if extraction.references else None, session.last_results)
        if entry is None:
            tracer.record("resolve_reference", {"references": [r.model_dump() for r in extraction.references]},
                           {"error": "REFERENCE_UNRESOLVED"}, ok=False)
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
                entry = _resolve_reference(operation.reference, session.last_results)
                if entry is None:
                    tracer.record("resolve_reference", {"reference": operation.reference.model_dump() if operation.reference else None},
                                   {"error": "REFERENCE_UNRESOLVED"}, ok=False)
                    return _error(tracer, "Could not resolve which product to add.", "REFERENCE_UNRESOLVED")
                resolved_operations.append(
                    operation.model_copy(update={
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
    memory.append_turn(request.session_id, Role.ASSISTANT, result.message)
    result.trace = tracer.steps
    return result
