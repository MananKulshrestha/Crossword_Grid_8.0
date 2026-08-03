"""Provider-neutral structured model gateway with deterministic local behavior."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from .catalog_language import propose_canonical_mapping
from .catalog_operations import classify_taxonomy, extract_supported_attributes
from .contracts import (
    CandidateTerm,
    CompatibilityTuple,
    EvidencePacket,
    IntentDelta,
    ModelCallRequest,
    ModelCallResponse,
    QueryState,
    RecoveryPlan,
)
from .determinism import hard_filter_hash, stable_id
from .quality import classify_quality_issue
from .recovery import plan_constrained_repair as deterministic_plan_constrained_repair
from .research import synthesize_research_answer
from .runtime import generate_clarifying_question, resolve_intent_and_delta
from .suggestions import generate_follow_up_suggestions


class StructuredModelGateway(Protocol):
    def complete_json(self, request: ModelCallRequest) -> ModelCallResponse: ...


class DeterministicModelGateway:
    """A no-network gateway used for contract tests and safe local development."""

    provider = "deterministic"

    def complete_json(self, request: ModelCallRequest) -> ModelCallResponse:
        try:
            output = self._dispatch(request.call_name, request.payload)
        except (KeyError, TypeError, ValueError) as exc:
            return ModelCallResponse(
                status="INVALID",
                call_name=request.call_name,
                schema_version=request.schema_version,
                provider=self.provider,
                warnings=[type(exc).__name__],
            )
        if output is None:
            return ModelCallResponse(
                status="ABSTAIN",
                call_name=request.call_name,
                schema_version=request.schema_version,
                provider=self.provider,
                warnings=["NO_DETERMINISTIC_DECISION"],
            )
        if hasattr(output, "model_dump"):
            output = output.model_dump(mode="json")
        return ModelCallResponse(
            status="OK",
            call_name=request.call_name,
            schema_version=request.schema_version,
            provider=self.provider,
            output=dict(output),
        )

    def _dispatch(self, call_name: str, payload: Mapping[str, object]) -> object | None:
        if call_name == "resolve_intent_and_delta":
            return resolve_intent_and_delta(
                str(payload.get("text", "")),
                QueryState.model_validate(payload["query_state"])
                if payload.get("query_state")
                else None,
            )
        if call_name == "generate_clarifying_question":
            state = QueryState.model_validate(payload.get("query_state", {}))
            return generate_clarifying_question(state, str(payload.get("reason", "")))
        if call_name == "plan_constrained_repair":
            if payload.get("allowed_concepts") or payload.get("allowed_clarification_options"):
                return deterministic_plan_constrained_repair(dict(payload))
            state = QueryState.model_validate(payload.get("query_state", {}))
            compatibility = CompatibilityTuple.model_validate(payload.get("compatibility", {}))
            unknown_terms = [str(term) for term in payload.get("unknown_terms", [])]
            allowed = {str(term) for term in payload.get("allowed_terms", [])}
            additions = sorted(set(unknown_terms) & allowed)
            action = (
                "REWRITE"
                if additions
                else "CLARIFY"
                if payload.get("clarification_field")
                else "ABSTAIN"
            )
            return RecoveryPlan(
                plan_id=stable_id(
                    "plan",
                    {"state": state, "unknown_terms": unknown_terms, "allowed": sorted(allowed)},
                ),
                action=action,
                add_terms=additions[:8],
                clarification_field=str(payload["clarification_field"])
                if payload.get("clarification_field")
                else None,
                hard_filter_hash=hard_filter_hash(state),
                compatibility=compatibility,
            )
        if call_name == "propose_canonical_mapping":
            term = CandidateTerm.model_validate(payload["term"])
            return propose_canonical_mapping(
                term, [str(item) for item in payload.get("allowed_targets", [])]
            )
        if call_name == "extract_supported_attributes":
            return extract_supported_attributes(
                payload.get("candidate", {}), payload.get("schema", {})
            )
        if call_name == "classify_taxonomy":
            return classify_taxonomy(
                payload.get("candidate", {}),
                [str(item) for item in payload.get("allowed_children", [])],
            )
        if call_name == "classify_quality_issue":
            return classify_quality_issue(EvidencePacket.model_validate(payload["packet"]))
        if call_name == "synthesize_research_answer":
            sources = payload.get("sources", [])
            return synthesize_research_answer(str(payload.get("query", "")), sources)
        if call_name == "generate_follow_up_suggestions":
            return generate_follow_up_suggestions(
                payload.get("candidates", []),
                str(payload.get("locale", "en-IN")),
                str(payload.get("tone", "")),
            )
        return None


def resolve_intent_via_gateway(
    gateway: StructuredModelGateway, text: str, query_state: QueryState | None = None
) -> IntentDelta:
    response = gateway.complete_json(
        ModelCallRequest(
            call_name="resolve_intent_and_delta",
            payload={
                "text": text,
                "query_state": query_state.model_dump(mode="json") if query_state else {},
            },
        )
    )
    if response.status != "OK":
        return IntentDelta(action="SEARCH", query_text=text, confidence=0.0)
    return IntentDelta.model_validate(response.output)
