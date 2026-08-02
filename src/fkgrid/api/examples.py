"""Canonical Swagger example requests built from the same domain contracts."""

from __future__ import annotations

from ..query_recovery.adapters.deepinfra import DEEPINFRA_GEMMA_4_26B_A4B_IT
from ..query_recovery.confidence import assess_retrieval_confidence
from ..query_recovery.domain import (
    CompatibilityTuple,
    QueryState,
    RecoveryPolicy,
    RecoveryRequest,
    RetrievalRun,
)
from ..query_recovery.validation import hard_filter_hash, query_state_hash


def build_example_request() -> RecoveryRequest:
    compatibility = CompatibilityTuple(
        contract_schema_version="recovery-contract-v1",
        catalog_version="catalog-swagger-demo",
        index_version="index-swagger-demo",
        taxonomy_version="taxonomy-swagger-demo",
        category_schema_version="schema-swagger-demo",
        lexicon_version="lexicon-swagger-demo",
        rank_policy_version="rank-swagger-demo",
        gate_policy_version="gate-swagger-demo",
        recovery_policy_version="recovery-policy-v1",
        recovery_prompt_version="recovery-v1",
        recovery_model_alias=DEEPINFRA_GEMMA_4_26B_A4B_IT,
    )
    state = QueryState(
        query_terms=["formal wear"],
        catalog_version=compatibility.catalog_version,
        index_version=compatibility.index_version,
        taxonomy_version=compatibility.taxonomy_version,
        category_schema_version=compatibility.category_schema_version,
        lexicon_version=compatibility.lexicon_version,
    )
    baseline = RetrievalRun(
        run_id="baseline-swagger-demo",
        query_state_hash=query_state_hash(state),
        hard_filter_hash=hard_filter_hash(state),
        compatibility=compatibility,
        eligible_count=0,
        required_criteria_coverage=None,
    )
    policy = RecoveryPolicy(policy_version=compatibility.recovery_policy_version)
    gate = assess_retrieval_confidence(
        run=baseline,
        query_state=state,
        unknown_terms=["formal wear"],
        policy_version=compatibility.gate_policy_version,
    )
    return RecoveryRequest(
        session_id="swagger-session",
        turn_id="swagger-turn-1",
        trace_id="swagger-trace-1",
        query_state=state,
        baseline_run=baseline,
        gate=gate,
        unresolved_terms=["formal wear"],
        compatibility=compatibility,
        policy=policy,
    )


__all__ = ["build_example_request"]
