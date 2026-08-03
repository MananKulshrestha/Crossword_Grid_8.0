from __future__ import annotations

import unittest

from fkgrid.query_recovery.adapters.in_memory import (
    FakePlanner,
    FixedClock,
    InMemoryApprovedExpansions,
    InMemoryRecoveryConstraints,
    InMemoryRecoveryEvents,
    ScriptedRetrieval,
    SequenceIds,
)
from fkgrid.query_recovery.adapters.cache import InMemoryRecoveryPlanCache
from fkgrid.query_recovery.confidence import assess_retrieval_confidence
from fkgrid.query_recovery.domain import (
    ApprovedExpansion,
    CompatibilityTuple,
    ConceptType,
    EvidenceBand,
    ExpansionAction,
    MappingType,
    RecoveryClarificationPlan,
    RecoveryConstraint,
    RecoveryOutcome,
    RecoveryPolicy,
    RecoveryRewritePlan,
    QueryState,
    RetrievalRun,
)
from fkgrid.query_recovery.ports import ClosedCircuitPort
from fkgrid.query_recovery.tools import RecoveryTools
from fkgrid.query_recovery.validation import hard_filter_hash, query_state_hash
from fkgrid.query_recovery.workflow import QueryRecoveryWorkflow


class RecoveryWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.compatibility = CompatibilityTuple(
            contract_schema_version="recovery-contract-v1",
            catalog_version="catalog-1",
            index_version="index-1",
            taxonomy_version="taxonomy-1",
            category_schema_version="schema-1",
            lexicon_version="lexicon-1",
            rank_policy_version="rank-1",
            gate_policy_version="gate-1",
            recovery_policy_version="recovery-policy-v1",
            recovery_prompt_version="recovery-v2",
            recovery_model_alias="fake-recovery",
        )
        self.state = QueryState(
            hard_constraints=[],
            soft_preferences=[],
            query_terms=["formal shirt"],
            taxonomy_scope_id="shirts",
            catalog_version="catalog-1",
            index_version="index-1",
            taxonomy_version="taxonomy-1",
            category_schema_version="schema-1",
            lexicon_version="lexicon-1",
        )
        self.policy = RecoveryPolicy(policy_version="recovery-policy-v1")
        self.clock = FixedClock()
        self.events = InMemoryRecoveryEvents()
        self.ids = SequenceIds()

    def run_request(
        self,
        *,
        baseline: RetrievalRun,
        unknown_terms: list[str],
        expansions=(),
        constraints=(),
        planner_output=None,
        retrieval_runs=(),
        tier2_enabled=True,
        cache=None,
        planner_codes=(),
        planner_tokens=0,
        planner_latency_ms=0,
    ):
        policy = self.policy.model_copy(update={"tier2_enabled": tier2_enabled})
        gate = assess_retrieval_confidence(
            run=baseline,
            query_state=self.state,
            unknown_terms=unknown_terms,
            policy_version=policy.policy_version,
        )
        request = self._request(baseline, gate, unknown_terms, policy)
        retrieval = ScriptedRetrieval(retrieval_runs)
        planner = FakePlanner(
            planner_output,
            codes=planner_codes,
            tokens=planner_tokens,
            latency_ms=planner_latency_ms,
        )
        tools = RecoveryTools(
            expansions=InMemoryApprovedExpansions(expansions),
            constraints=InMemoryRecoveryConstraints(constraints),
            retrieval=retrieval,
            planner=planner,
            events=self.events,
            clock=self.clock,
        )
        workflow = QueryRecoveryWorkflow(
            tools=tools,
            clock=self.clock,
            ids=self.ids,
            circuit=ClosedCircuitPort(),
            cache=cache,
        )
        response = workflow.run(request)
        return response, retrieval, planner

    def _request(self, baseline, gate, unknown_terms, policy):
        from fkgrid.query_recovery.domain import RecoveryRequest

        return RecoveryRequest(
            session_id="session-1",
            turn_id="turn-1",
            trace_id="trace-1",
            query_state=self.state,
            baseline_run=baseline,
            gate=gate,
            unresolved_terms=unknown_terms,
            compatibility=self.compatibility,
            policy=policy,
        )

    def run_summary(
        self,
        *,
        run_id: str,
        eligible: int,
        score: float | None,
        coverage: float | None = 1.0,
        products=(),
        interpretation: str | None = None,
        state=None,
    ) -> RetrievalRun:
        state = state or self.state
        return RetrievalRun(
            run_id=run_id,
            query_state_hash=query_state_hash(state),
            hard_filter_hash=hard_filter_hash(state),
            compatibility=self.compatibility,
            eligible_count=eligible,
            top_score=score,
            top_score_margin=0.2 if eligible else None,
            required_criteria_coverage=coverage,
            result_product_ids=list(products),
            interpretation_family=interpretation,
        )

    def expansion(self, *, target: str = "athletic-shoes") -> ApprovedExpansion:
        return ApprovedExpansion(
            mapping_id="mapping-1",
            normalized_form="trainers",
            original_form="trainers",
            canonical_target_id=target,
            canonical_label="athletic shoes",
            concept_type=ConceptType.TAXONOMY,
            mapping_type=MappingType.ALIAS,
            expansion_action=ExpansionAction.CANONICAL_SYNONYM,
            taxonomy_scope_id="shirts",
            lexicon_version="lexicon-1",
            catalog_version="catalog-1",
            taxonomy_version="taxonomy-1",
            category_schema_version="schema-1",
            evidence_band=EvidenceBand.APPROVED_HIGH,
            priority=10,
        )

    def constraint(self, concept_id: str, label: str) -> RecoveryConstraint:
        return RecoveryConstraint(
            concept_id=concept_id,
            concept_type=ConceptType.TAXONOMY,
            label=label,
            canonical_term=label,
            taxonomy_scope_id="shirts",
            catalog_version="catalog-1",
            taxonomy_version="taxonomy-1",
            category_schema_version="schema-1",
            lexicon_version="lexicon-1",
        )

    def test_confident_query_never_calls_recovery(self) -> None:
        baseline = self.run_summary(run_id="baseline", eligible=2, score=0.8, products=["p1", "p2"])
        response, retrieval, planner = self.run_request(baseline=baseline, unknown_terms=[])
        self.assertEqual(response.outcome, RecoveryOutcome.RECOVERY_SKIPPED_CONFIDENT)
        self.assertEqual(retrieval.calls, [])
        self.assertEqual(planner.calls, 0)
        self.assertEqual(response.event.retrieval_run_count, 1)

    def test_tier1_direct_recovery_is_accepted_without_planner(self) -> None:
        baseline = self.run_summary(run_id="baseline", eligible=0, score=None, coverage=None)
        direct_state = self.state.model_copy(update={"query_terms": ["formal shirt", "athletic shoes"]})
        direct = self.run_summary(
            run_id="direct",
            eligible=2,
            score=0.72,
            products=["p1", "p2"],
            state=direct_state,
        )
        response, retrieval, planner = self.run_request(
            baseline=baseline,
            unknown_terms=["trainers"],
            expansions=[self.expansion()],
            retrieval_runs=[direct],
        )
        self.assertEqual(response.outcome, RecoveryOutcome.RECOVERED_TIER1)
        self.assertEqual(retrieval.calls, ["TIER1_DIRECT"])
        self.assertEqual(planner.calls, 0)
        self.assertEqual(response.event.hard_filter_mutation_count, 0)
        self.assertEqual(response.event.mapping_ids, ["mapping-1"])

    def test_tier2_rewrite_is_one_call_and_one_rerun(self) -> None:
        baseline = self.run_summary(run_id="baseline", eligible=0, score=None, coverage=None)
        concept = self.constraint("footwear", "footwear")
        planner_output = RecoveryRewritePlan(
            added_concept_ids=[concept.concept_id],
            interpretation_label="Footwear interpretation",
            preserved_hard_filter_hash=hard_filter_hash(self.state),
        )
        recovered_state = self.state.model_copy(update={"query_terms": ["formal shirt", "footwear"]})
        recovered = self.run_summary(
            run_id="generative",
            eligible=2,
            score=0.80,
            products=["p1", "p2"],
            state=recovered_state,
        )
        response, retrieval, planner = self.run_request(
            baseline=baseline,
            unknown_terms=["unknown-item"],
            constraints=[concept],
            planner_output=planner_output,
            retrieval_runs=[recovered],
        )
        self.assertEqual(response.outcome, RecoveryOutcome.RECOVERED_TIER2)
        self.assertEqual(planner.calls, 1)
        self.assertEqual(planner.timeouts, [1550])
        self.assertEqual(retrieval.calls, ["TIER2_GENERATIVE"])
        self.assertTrue(response.event.planner_called)
        self.assertEqual(response.event.retrieval_run_count, 2)

    def test_tier2_event_records_bounded_planner_telemetry(self) -> None:
        baseline = self.run_summary(run_id="baseline", eligible=0, score=None, coverage=None)
        concept = self.constraint("footwear", "footwear")
        planner_output = RecoveryRewritePlan(
            added_concept_ids=[concept.concept_id],
            interpretation_label="Footwear interpretation",
            preserved_hard_filter_hash=hard_filter_hash(self.state),
        )
        recovered_state = self.state.model_copy(update={"query_terms": ["formal shirt", "footwear"]})
        recovered = self.run_summary(
            run_id="generative",
            eligible=2,
            score=0.80,
            products=["p1", "p2"],
            state=recovered_state,
        )
        response, _retrieval, planner = self.run_request(
            baseline=baseline,
            unknown_terms=["unknown-item"],
            constraints=[concept],
            planner_output=planner_output,
            retrieval_runs=[recovered],
            planner_tokens=73,
            planner_latency_ms=41,
        )
        self.assertEqual(response.event.planner_token_count, 73)
        self.assertEqual(response.event.planner_latency_ms, 41)
        self.assertEqual(response.event.allowed_concept_count, 1)
        self.assertEqual(response.event.planner_input_hash, planner.last_context.planner_input_hash)
        self.assertEqual(len(response.event.planner_input_hash or ""), 64)

    def test_arbitrary_planner_id_is_rejected_without_rerun(self) -> None:
        baseline = self.run_summary(run_id="baseline", eligible=0, score=None, coverage=None)
        allowed = self.constraint("allowed", "allowed")
        planner_output = RecoveryRewritePlan(
            added_concept_ids=["not-allowed"],
            interpretation_label="Injected",
            preserved_hard_filter_hash=hard_filter_hash(self.state),
        )
        response, retrieval, planner = self.run_request(
            baseline=baseline,
            unknown_terms=["ignore filters"],
            constraints=[allowed],
            planner_output=planner_output,
        )
        self.assertEqual(response.outcome, RecoveryOutcome.NO_SAFE_RECOVERY)
        self.assertEqual(planner.calls, 1)
        self.assertEqual(retrieval.calls, [])
        self.assertIn("CONCEPT_ID_NOT_ALLOWED", response.event.planner_validation_codes)

    def test_planner_clarification_is_taxonomy_backed(self) -> None:
        baseline = self.run_summary(run_id="baseline", eligible=0, score=None, coverage=None)
        first = self.constraint("category-a", "Category A")
        second = self.constraint("category-b", "Category B")
        planner_output = RecoveryClarificationPlan(
            option_ids=[first.concept_id, second.concept_id],
            target_field="category",
            reason="Ambiguous category",
            preserved_hard_filter_hash=hard_filter_hash(self.state),
        )
        response, retrieval, planner = self.run_request(
            baseline=baseline,
            unknown_terms=["ambiguous"],
            constraints=[first, second],
            planner_output=planner_output,
        )
        self.assertEqual(response.outcome, RecoveryOutcome.CLARIFICATION_REQUIRED)
        self.assertIsNotNone(response.clarification)
        self.assertEqual(
            {option.option_id for option in response.clarification.options},
            {"category-a", "category-b"},
        )
        self.assertEqual(retrieval.calls, [])
        self.assertEqual(planner.calls, 1)

    def test_planner_no_safe_is_honest_and_does_not_turn_every_concept_into_chat(self) -> None:
        baseline = self.run_summary(run_id="baseline", eligible=0, score=None, coverage=None)
        first = self.constraint("category-a", "Category A")
        second = self.constraint("category-b", "Category B")
        from fkgrid.query_recovery.domain import RecoveryNoSafePlan

        planner_output = RecoveryNoSafePlan(
            reason_code="NO_COMPATIBLE_INTERPRETATION",
            preserved_hard_filter_hash=hard_filter_hash(self.state),
        )
        response, retrieval, planner = self.run_request(
            baseline=baseline,
            unknown_terms=["moon-boots"],
            constraints=[first, second],
            planner_output=planner_output,
        )
        self.assertEqual(response.outcome, RecoveryOutcome.NO_SAFE_RECOVERY)
        self.assertIsNone(response.clarification)
        self.assertEqual(retrieval.calls, [])
        self.assertEqual(planner.calls, 1)

    def test_invalid_or_unavailable_planner_falls_back_to_allowed_clarification(self) -> None:
        baseline = self.run_summary(run_id="baseline", eligible=0, score=None, coverage=None)
        first = self.constraint("category-a", "Category A")
        second = self.constraint("category-b", "Category B")
        response, retrieval, planner = self.run_request(
            baseline=baseline,
            unknown_terms=["ambiguous"],
            constraints=[first, second],
            planner_output=None,
            planner_codes=["PROVIDER_TIMEOUT"],
        )
        self.assertEqual(response.outcome, RecoveryOutcome.CLARIFICATION_REQUIRED)
        self.assertEqual(response.clarification.question, "Did you mean Category A or Category B?")
        self.assertEqual(retrieval.calls, [])
        self.assertEqual(planner.calls, 1)
        self.assertIn("PLANNER_FALLBACK_TO_CLARIFICATION", response.warnings)

    def test_retrieval_result_for_wrong_state_is_rejected_before_comparison(self) -> None:
        baseline = self.run_summary(run_id="baseline", eligible=0, score=None, coverage=None)
        concept = self.constraint("footwear", "footwear")
        planner_output = RecoveryRewritePlan(
            added_concept_ids=[concept.concept_id],
            interpretation_label="Footwear interpretation",
            preserved_hard_filter_hash=hard_filter_hash(self.state),
        )
        # The adapter returns a baseline-state hash even though Tier 2 asked for a rewrite.
        wrong_run = self.run_summary(run_id="wrong-state", eligible=2, score=0.90, products=["p1"])
        response, retrieval, _planner = self.run_request(
            baseline=baseline,
            unknown_terms=["unknown-item"],
            constraints=[concept],
            planner_output=planner_output,
            retrieval_runs=[wrong_run],
        )
        self.assertEqual(response.outcome, RecoveryOutcome.NO_SAFE_RECOVERY)
        self.assertEqual(retrieval.calls, ["TIER2_GENERATIVE"])
        self.assertIn("RETRIEVAL_QUERY_STATE_HASH_MISMATCH", response.event.planner_validation_codes)

    def test_hard_filter_hash_change_in_baseline_fails_closed(self) -> None:
        baseline = self.run_summary(run_id="baseline", eligible=0, score=None, coverage=None)
        broken = baseline.model_copy(update={"hard_filter_hash": "0" * 64})
        response, retrieval, planner = self.run_request(
            baseline=broken,
            unknown_terms=["unknown"],
        )
        self.assertEqual(response.outcome, RecoveryOutcome.NO_SAFE_RECOVERY)
        self.assertEqual(retrieval.calls, [])
        self.assertEqual(planner.calls, 0)
        self.assertIn("BASELINE_HARD_FILTER_HASH_MISMATCH", response.event.planner_validation_codes)

    def test_tier2_disable_preserves_baseline_and_planner_is_not_called(self) -> None:
        baseline = self.run_summary(run_id="baseline", eligible=1, score=0.2, coverage=0.3, products=["p1"])
        response, retrieval, planner = self.run_request(
            baseline=baseline,
            unknown_terms=["unknown"],
            tier2_enabled=False,
        )
        self.assertEqual(response.outcome, RecoveryOutcome.BASELINE_PRESERVED)
        self.assertEqual(planner.calls, 0)
        self.assertEqual(retrieval.calls, [])

    def test_planner_scope_leakage_is_rejected(self) -> None:
        baseline = self.run_summary(run_id="baseline", eligible=0, score=None, coverage=None)
        concept = self.constraint("other-category", "Other category").model_copy(
            update={"taxonomy_scope_id": "other-category"}
        )
        planner_output = RecoveryRewritePlan(
            added_concept_ids=[concept.concept_id],
            interpretation_label="scope leak",
            preserved_hard_filter_hash=hard_filter_hash(self.state),
        )
        response, retrieval, planner = self.run_request(
            baseline=baseline,
            unknown_terms=["scope-leak"],
            constraints=[concept],
            planner_output=planner_output,
        )
        self.assertEqual(response.outcome, RecoveryOutcome.NO_SAFE_RECOVERY)
        self.assertEqual(retrieval.calls, [])
        self.assertIn("CONCEPT_ID_NOT_ALLOWED", response.event.planner_validation_codes)

    def test_cache_replays_only_an_accepted_plan_and_reruns_retrieval(self) -> None:
        cache = InMemoryRecoveryPlanCache(clock=self.clock)
        baseline = self.run_summary(run_id="baseline", eligible=0, score=None, coverage=None)
        direct_state = self.state.model_copy(update={"query_terms": ["formal shirt", "athletic shoes"]})
        direct = self.run_summary(
            run_id="direct",
            eligible=2,
            score=0.72,
            products=["p1", "p2"],
            state=direct_state,
        )
        first, _retrieval, _planner = self.run_request(
            baseline=baseline,
            unknown_terms=["trainers"],
            expansions=[self.expansion()],
            retrieval_runs=[direct],
            cache=cache,
        )
        self.assertEqual(first.outcome, RecoveryOutcome.RECOVERED_TIER1)
        second_baseline = self.run_summary(run_id="baseline-2", eligible=0, score=None, coverage=None)
        second, retrieval, planner = self.run_request(
            baseline=second_baseline,
            unknown_terms=["trainers"],
            expansions=[],
            retrieval_runs=[direct.model_copy(update={"run_id": "cached-run"})],
            cache=cache,
        )
        self.assertEqual(second.outcome, RecoveryOutcome.RECOVERED_TIER1)
        self.assertTrue(second.event.cache_hit)
        self.assertEqual(retrieval.calls, ["CACHED_PLAN"])
        self.assertEqual(planner.calls, 0)


if __name__ == "__main__":
    unittest.main()
