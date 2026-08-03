"""Small actathon-facing input adapter for the canonical recovery workflow."""

from __future__ import annotations

from uuid import uuid4

from pydantic import Field, field_validator

from ..query_recovery.adapters.deepinfra import DEEPINFRA_GEMMA_4_26B_A4B_IT
from ..query_recovery.confidence import assess_retrieval_confidence
from ..query_recovery.domain import (
    CompatibilityTuple,
    HardConstraint,
    QueryState,
    RecoveryPolicy,
    RecoveryRequest,
    RecoveryResponse,
    RetrievalRun,
    StrictModel,
)
from ..query_recovery.validation import hard_filter_hash, normalize_term, query_state_hash


class DemoRecoveryInput(StrictModel):
    """Human-sized form input; callers do not need to construct RecoveryRequest."""

    query: str = Field(min_length=1, max_length=2_000)
    in_stock_only: bool = False
    cotton_only: bool = False
    under_2000: bool = False

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must contain at least one non-space character")
        return value.strip()


class DemoRecoveryResponse(StrictModel):
    """Friendly result envelope for the demo UI and its small JSON endpoint."""

    query: str
    filters: list[str]
    hard_filter_hash: str
    result: RecoveryResponse


_DEMO_VERSIONS = {
    "catalog_version": "catalog-swagger-demo",
    "index_version": "index-swagger-demo",
    "taxonomy_version": "taxonomy-swagger-demo",
    "category_schema_version": "schema-swagger-demo",
    "lexicon_version": "lexicon-swagger-demo",
}


def build_demo_recovery_request(payload: DemoRecoveryInput) -> RecoveryRequest:
    """Translate the three visible filters into explicit hard constraints."""

    turn_id = f"actathon-turn-{uuid4().hex}"
    normalized_query = normalize_term(payload.query)
    query_terms = normalized_query.split()[:20]
    constraints = _build_hard_constraints(payload, turn_id)
    state = QueryState(
        hard_constraints=constraints,
        query_terms=query_terms,
        **_DEMO_VERSIONS,
    )
    compatibility = CompatibilityTuple(
        contract_schema_version="recovery-contract-v1",
        rank_policy_version="rank-swagger-demo",
        gate_policy_version="gate-swagger-demo",
        recovery_policy_version="recovery-policy-v1",
        recovery_prompt_version="recovery-v2",
        recovery_model_alias=DEEPINFRA_GEMMA_4_26B_A4B_IT,
        **_DEMO_VERSIONS,
    )
    baseline = RetrievalRun(
        run_id=f"baseline-{turn_id}",
        query_state_hash=query_state_hash(state),
        hard_filter_hash=hard_filter_hash(state),
        compatibility=compatibility,
        eligible_count=0,
        required_criteria_coverage=None,
    )
    gate = assess_retrieval_confidence(
        run=baseline,
        query_state=state,
        unknown_terms=query_terms[:10],
        policy_version=compatibility.gate_policy_version,
    )
    return RecoveryRequest(
        session_id="actathon-demo-session",
        turn_id=turn_id,
        trace_id=f"actathon-trace-{uuid4().hex}",
        query_state=state,
        baseline_run=baseline,
        gate=gate,
        unresolved_terms=query_terms[:10],
        compatibility=compatibility,
        policy=RecoveryPolicy(policy_version=compatibility.recovery_policy_version),
    )


def build_demo_recovery_response(
    payload: DemoRecoveryInput,
    result: RecoveryResponse,
) -> DemoRecoveryResponse:
    return DemoRecoveryResponse(
        query=payload.query,
        filters=selected_filter_labels(payload),
        hard_filter_hash=result.event.hard_filter_hash_before,
        result=result,
    )


def selected_filter_labels(payload: DemoRecoveryInput) -> list[str]:
    labels: list[str] = []
    if payload.in_stock_only:
        labels.append("In stock only")
    if payload.cotton_only:
        labels.append("Cotton")
    if payload.under_2000:
        labels.append("Under ₹2,000")
    return labels


def _build_hard_constraints(payload: DemoRecoveryInput, turn_id: str) -> list[HardConstraint]:
    constraints: list[HardConstraint] = []
    if payload.in_stock_only:
        constraints.append(
            HardConstraint(
                field_id="availability",
                operator="EQ",
                values=["IN_STOCK"],
                provenance_turn_id=turn_id,
            )
        )
    if payload.cotton_only:
        constraints.append(
            HardConstraint(
                field_id="material",
                operator="EQ",
                values=["cotton"],
                provenance_turn_id=turn_id,
            )
        )
    if payload.under_2000:
        constraints.append(
            HardConstraint(
                field_id="price",
                operator="LTE",
                values=[2000],
                provenance_turn_id=turn_id,
            )
        )
    return constraints


__all__ = [
    "DemoRecoveryInput",
    "DemoRecoveryResponse",
    "build_demo_recovery_request",
    "build_demo_recovery_response",
    "selected_filter_labels",
]
