"""Live, redacted Gemma 4 26B planner/workflow smoke test.

Run with ``PYTHONPATH=src`` and ``FKGRID_GEMINI_API_KEY`` or the official
``GEMINI_API_KEY`` environment variable set. The key is never printed or
written by this script. Diagnostic mode uses a longer timeout to prove the
provider path; production mode exercises the binding 1,800 ms safe fallback.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from fkgrid.query_recovery.adapters.in_memory import (
    InMemoryApprovedExpansions,
    InMemoryRecoveryConstraints,
    InMemoryRecoveryEvents,
    ScriptedRetrieval,
    SequenceIds,
)
from fkgrid.query_recovery.adapters.gemini import build_gemma4_recovery_planner
from fkgrid.query_recovery.domain import (
    BaselineSignals,
    CompatibilityTuple,
    ConceptType,
    GateDecision,
    QueryState,
    RecoveryConstraint,
    RecoveryContext,
    RecoveryGate,
    RecoveryPolicy,
    RecoveryRequest,
    RecoveryTriggerReason,
    RetrievalRun,
)
from fkgrid.query_recovery.ports import ClosedCircuitPort
from fkgrid.query_recovery.tools import RecoveryTools
from fkgrid.query_recovery.validation import canonical_hash, hard_filter_hash
from fkgrid.query_recovery.workflow import QueryRecoveryWorkflow


class LiveClock:
    def monotonic_ms(self) -> int:
        return time.monotonic_ns() // 1_000_000

    def now_utc(self) -> datetime:
        return datetime.now(timezone.utc)


def make_context() -> RecoveryContext:
    compatibility = CompatibilityTuple(
        contract_schema_version="recovery-contract-v1",
        catalog_version="catalog-smoke",
        index_version="index-smoke",
        taxonomy_version="taxonomy-smoke",
        category_schema_version="schema-smoke",
        lexicon_version="lexicon-smoke",
        rank_policy_version="rank-smoke",
        gate_policy_version="gate-smoke",
        recovery_policy_version="recovery-policy-v1",
        recovery_prompt_version="recovery-v1",
        recovery_model_alias="gemma-4-26b-a4b-it",
    )
    state = QueryState(
        query_terms=["formal wear"],
        catalog_version="catalog-smoke",
        index_version="index-smoke",
        taxonomy_version="taxonomy-smoke",
        category_schema_version="schema-smoke",
        lexicon_version="lexicon-smoke",
    )
    shirt_concept = RecoveryConstraint(
        concept_id="concept-shirts",
        concept_type=ConceptType.TAXONOMY,
        label="Shirts",
        canonical_term="shirts",
        catalog_version="catalog-smoke",
        taxonomy_version="taxonomy-smoke",
        category_schema_version="schema-smoke",
        lexicon_version="lexicon-smoke",
    )
    blazer_concept = shirt_concept.model_copy(
        update={
            "concept_id": "concept-blazers",
            "label": "Blazers",
            "canonical_term": "blazers",
        }
    )
    base = {
        "unresolved_terms": ["formal wear"],
        "query_state": state,
        "hard_filter_hash": hard_filter_hash(state),
        "gate_reasons": [RecoveryTriggerReason.UNKNOWN_IMPORTANT_TERM],
        "baseline_run_id": "baseline-smoke",
        "baseline_summary": BaselineSignals(eligible_count=0, unknown_terms=["formal wear"]),
        "allowed_concepts": [shirt_concept, blazer_concept],
        "approved_suggestions": [],
        "compatibility": compatibility,
        "locale": "en-IN",
    }
    return RecoveryContext(**base, planner_input_hash=canonical_hash(base))


def main() -> int:
    context = make_context()
    planner = build_gemma4_recovery_planner()
    production_mode = os.environ.get("FKGRID_GEMMA_SMOKE_MODE", "diagnostic") == "production"
    default_timeout = "1800" if production_mode else "5000"
    timeout_ms = max(
        500,
        min(int(os.environ.get("FKGRID_GEMMA_SMOKE_TIMEOUT_MS", default_timeout)), 30000),
    )
    recovery_deadline_ms = (
        1800 if production_mode else min(10000, max(5000, timeout_ms + 500))
    )
    baseline = RetrievalRun(
        run_id="baseline-smoke",
        query_state_hash=canonical_hash(context.query_state),
        hard_filter_hash=context.hard_filter_hash,
        compatibility=context.compatibility,
        eligible_count=0,
        required_criteria_coverage=None,
    )
    gate = RecoveryGate(
        decision=GateDecision.RECOVERY_ELIGIBLE,
        reasons=context.gate_reasons,
        unknown_terms=context.unresolved_terms,
        signals=context.baseline_summary,
        hard_filter_hash=context.hard_filter_hash,
        gate_policy_version=context.compatibility.gate_policy_version,
    )
    request = RecoveryRequest(
        session_id="session-smoke",
        turn_id="turn-smoke",
        trace_id="trace-smoke",
        query_state=context.query_state,
        baseline_run=baseline,
        gate=gate,
        unresolved_terms=context.unresolved_terms,
        compatibility=context.compatibility,
        policy=RecoveryPolicy(
            policy_version="recovery-policy-v1",
            recovery_deadline_ms=recovery_deadline_ms,
            tier2_min_remaining_ms=450 if production_mode else 50,
        ),
    )
    recovered_run = RetrievalRun(
        run_id="tier2-smoke",
        query_state_hash=canonical_hash(context.query_state),
        hard_filter_hash=context.hard_filter_hash,
        compatibility=context.compatibility,
        eligible_count=1,
        top_score=0.80,
        top_score_margin=0.20,
        required_criteria_coverage=1.0,
        result_product_ids=["product-smoke"],
    )
    clock = LiveClock()
    events = InMemoryRecoveryEvents()
    tools = RecoveryTools(
        expansions=InMemoryApprovedExpansions(),
        constraints=InMemoryRecoveryConstraints(context.allowed_concepts),
        retrieval=ScriptedRetrieval([recovered_run]),
        planner=planner,
        events=events,
        clock=clock,
    )
    response = QueryRecoveryWorkflow(
        tools=tools,
        clock=clock,
        ids=SequenceIds(),
        circuit=ClosedCircuitPort(),
    ).run(request)
    event = response.event
    if production_mode:
        safe_provider_codes = {
            "PROVIDER_TIMEOUT",
            "RATE_LIMITED",
            "AUTH_REJECTED",
            "PROVIDER_UNAVAILABLE",
            "NETWORK_ERROR",
        }
        if not event.planner_called or not set(event.planner_validation_codes) <= safe_provider_codes:
            print(
                "GEMMA4_PRODUCTION_FAILED "
                f"codes={','.join(event.planner_validation_codes[:8]) or 'PLANNER_NOT_CALLED'}"
            )
            return 1
        print(
            f"GEMMA4_PRODUCTION_SAFE_FALLBACK outcome={response.outcome.value} "
            f"planner_called={event.planner_called} retrieval_runs={event.retrieval_run_count} "
            f"latency_ms={event.added_latency_ms} codes={','.join(event.planner_validation_codes)}"
        )
        return 0
    if not event.planner_called or event.planner_validation_codes:
        print(
            "GEMMA4_WORKFLOW_FAILED "
            f"codes={','.join(event.planner_validation_codes[:8]) or 'PLANNER_NOT_CALLED'}"
        )
        return 1
    if event.retrieval_run_count > 3:
        print("GEMMA4_WORKFLOW_FAILED codes=RETRIEVAL_RUN_BUDGET")
        return 1
    print(
        f"GEMMA4_WORKFLOW_OK outcome={response.outcome.value} "
        f"planner_called={event.planner_called} retrieval_runs={event.retrieval_run_count} "
        f"latency_ms={event.added_latency_ms}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
