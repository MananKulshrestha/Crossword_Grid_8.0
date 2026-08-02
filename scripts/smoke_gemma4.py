"""Live, redacted Gemma 4 26B planner smoke test.

Run with ``PYTHONPATH=src`` and ``FKGRID_GEMINI_API_KEY`` or the official
``GEMINI_API_KEY`` environment variable set. The key is never printed or
written by this script.
"""

from __future__ import annotations

import os

from fkgrid.query_recovery.adapters.gemini import build_gemma4_recovery_planner
from fkgrid.query_recovery.domain import (
    BaselineSignals,
    CompatibilityTuple,
    ConceptType,
    QueryState,
    RecoveryConstraint,
    RecoveryContext,
    RecoveryTriggerReason,
)
from fkgrid.query_recovery.validation import canonical_hash, hard_filter_hash


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
    concept = RecoveryConstraint(
        concept_id="concept-shirts",
        concept_type=ConceptType.TAXONOMY,
        label="Shirts",
        canonical_term="shirts",
        catalog_version="catalog-smoke",
        taxonomy_version="taxonomy-smoke",
        category_schema_version="schema-smoke",
        lexicon_version="lexicon-smoke",
    )
    base = {
        "unresolved_terms": ["formal wear"],
        "query_state": state,
        "hard_filter_hash": hard_filter_hash(state),
        "gate_reasons": [RecoveryTriggerReason.UNKNOWN_IMPORTANT_TERM],
        "baseline_run_id": "baseline-smoke",
        "baseline_summary": BaselineSignals(eligible_count=0, unknown_terms=["formal wear"]),
        "allowed_concepts": [concept],
        "approved_suggestions": [],
        "compatibility": compatibility,
        "locale": "en-IN",
    }
    return RecoveryContext(**base, planner_input_hash=canonical_hash(base))


def main() -> int:
    planner = build_gemma4_recovery_planner()
    timeout_ms = max(500, min(int(os.environ.get("FKGRID_GEMMA_SMOKE_TIMEOUT_MS", "5000")), 30000))
    output, codes, token_count, latency_ms = planner.plan(
        context=make_context(), timeout_ms=timeout_ms
    )
    if output is None:
        print(f"GEMMA4_PLANNER_FAILED codes={','.join(codes[:8]) or 'UNKNOWN'}")
        return 1
    print(
        f"GEMMA4_PLANNER_OK action={output.action} "
        f"tokens={token_count} latency_ms={latency_ms}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
