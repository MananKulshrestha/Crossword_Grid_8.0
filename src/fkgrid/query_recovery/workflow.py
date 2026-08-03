"""Explicit bounded Tier 1/Tier 2 recovery state machine."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .confidence import compare_retrieval_runs
from .domain import (
    ApprovedExpansion,
    ComparatorDecision,
    ConceptType,
    EvidenceBand,
    ExpansionAction,
    GateDecision,
    MappingType,
    PlannerAction,
    RecoveryClarificationPlan,
    RecoveryConstraint,
    RecoveryEvent,
    RecoveryNoSafePlan,
    RecoveryOutcome,
    RecoveryPlan,
    RecoveryRequest,
    RecoveryResponse,
    RecoverySuggestion,
    RetrievalRun,
    ToolReceipt,
)
from .ports import ClockPort, IdPort, RecoveryCircuitPort, RecoveryPlanCachePort
from .tools import RecoveryTools
from .validation import (
    hard_filter_hash,
    recovery_cache_key,
    validate_internal_rewrite_plan,
    validate_planner_plan,
    validate_recovery_context,
)

PLANNER_POST_CALL_RESERVE_MS = 250


class QueryRecoveryWorkflow:
    """Run the only permitted recovery graph.

    A baseline run is supplied by the normal retrieval owner. This workflow can
    add at most one direct rerun, one planner call, and one generative rerun.
    """

    def __init__(
        self,
        *,
        tools: RecoveryTools,
        clock: ClockPort,
        ids: IdPort,
        circuit: RecoveryCircuitPort,
        cache: RecoveryPlanCachePort | None = None,
    ) -> None:
        self.tools = tools
        self.clock = clock
        self.ids = ids
        self.circuit = circuit
        self.cache = cache

    def run(self, request: RecoveryRequest) -> RecoveryResponse:
        started_ms = self.clock.monotonic_ms()
        warnings: list[str] = []
        validation_codes = validate_recovery_context(request)
        before_hash = hard_filter_hash(request.query_state)
        if validation_codes:
            warnings.extend(validation_codes)
            return self._finish(
                request,
                started_ms=started_ms,
                outcome=RecoveryOutcome.NO_SAFE_RECOVERY,
                terminal_state="NO_SAFE_RECOVERY",
                selected_run=None,
                warnings=warnings,
                validation_codes=validation_codes,
            )

        if request.gate.decision is GateDecision.CONFIDENT:
            return self._finish(
                request,
                started_ms=started_ms,
                outcome=RecoveryOutcome.RECOVERY_SKIPPED_CONFIDENT,
                terminal_state="ANSWERED_WITH_GROUNDED_RESULTS",
                selected_run=request.baseline_run,
                warnings=warnings,
                validation_codes=[],
            )

        if request.gate.decision is not GateDecision.RECOVERY_ELIGIBLE:
            outcome = RecoveryOutcome.RECOVERY_SKIPPED_UNSAFE_GATE
            terminal = "NO_ELIGIBLE_MATCH"
            gate_warnings = ["RECOVERY_GATE_NOT_AUTHORIZED"]
            if request.gate.decision is GateDecision.CLARIFICATION_REQUIRED:
                gate_warnings.append("CLARIFICATION_SUPPRESSED_AS_NON_CHAT_RECOVERY")
            return self._finish(
                request,
                started_ms=started_ms,
                outcome=outcome,
                terminal_state=terminal,
                selected_run=request.baseline_run,
                warnings=gate_warnings,
                validation_codes=["GATE_NOT_RECOVERY_ELIGIBLE"],
            )

        if not request.baseline_run.is_scope_safe:
            return self._finish(
                request,
                started_ms=started_ms,
                outcome=RecoveryOutcome.NO_SAFE_RECOVERY,
                terminal_state="NO_SAFE_RECOVERY",
                selected_run=None,
                warnings=["BASELINE_SCOPE_UNSAFE"],
                validation_codes=["BASELINE_SCOPE_UNSAFE"],
            )

        direct_run: RetrievalRun | None = None
        generative_run: RetrievalRun | None = None
        direct_plan: RecoveryPlan | None = None
        selected_comparator = None
        comparator_decisions: list[ComparatorDecision] = []
        mapping_ids: list[str] = []
        planner_action: PlannerAction | None = None
        planner_called = False
        planner_codes: list[str] = []
        concepts_by_id: dict[str, RecoveryConstraint] = {}
        allowed_concepts: list[RecoveryConstraint] = []
        cache_key = recovery_cache_key(request) if self.cache is not None else None

        # Tier 1: exact approved mappings only.
        try:
            expansions = self.tools.lookup_approved_expansions(request)
        except Exception as exc:  # provider/DB failures must preserve baseline
            expansions = []
            warnings.append("LEXICON_LOOKUP_UNAVAILABLE")
            planner_codes.append(f"LEXICON_LOOKUP:{type(exc).__name__}")

        direct_expansions = self._select_direct_expansions(request, expansions)
        if direct_expansions:
            concepts_by_id = {
                expansion.canonical_target_id: self._constraint_from_expansion(expansion)
                for expansion in direct_expansions
            }
            direct_plan = RecoveryPlan(
                source="DIRECT",
                mapping_ids=[item.mapping_id for item in direct_expansions],
                added_concept_ids=[item.canonical_target_id for item in direct_expansions],
                added_query_terms=[item.canonical_label for item in direct_expansions],
                interpretation_label=(
                    "Approved semantic expansion: "
                    + ", ".join(item.canonical_label for item in direct_expansions)
                ),
                preserved_hard_filter_hash=before_hash,
            )
            issues = validate_internal_rewrite_plan(direct_plan, request, concepts_by_id)
            if issues:
                warnings.extend(issues)
                direct_plan = None
            else:
                direct_state = self._apply_plan(request, direct_plan)
                if direct_state is not None:
                    try:
                        direct_run = self.tools.retrieve(
                            request,
                            state=direct_state,
                            run_kind="TIER1_DIRECT",
                            remaining_ms=self._remaining(request, started_ms),
                        )
                    except Exception as exc:
                        warnings.append("TIER1_RETRIEVAL_UNAVAILABLE")
                        planner_codes.append(f"TIER1_RETRIEVAL:{type(exc).__name__}")
                    if direct_run is not None:
                        comparison = compare_retrieval_runs(
                            baseline=request.baseline_run,
                            candidate=direct_run,
                            policy=request.policy,
                        )
                        comparator_decisions.append(comparison.decision)
                        if comparison.decision is ComparatorDecision.ACCEPTED:
                            self._cache_put(cache_key, direct_plan, warnings)
                            return self._finish(
                                request,
                                started_ms=started_ms,
                                outcome=RecoveryOutcome.RECOVERED_TIER1,
                                terminal_state="ANSWERED_WITH_GROUNDED_RESULTS",
                                selected_run=direct_run,
                                plan=direct_plan,
                                comparator=comparison,
                                direct_run=direct_run,
                                mapping_ids=direct_plan.mapping_ids,
                                warnings=warnings,
                                validation_codes=planner_codes,
                            )
                        selected_comparator = comparison
                        mapping_ids = list(direct_plan.mapping_ids)

        # Replay only an already accepted immutable plan. The cache contains
        # no session response, reference, authorization, or catalog facts.
        if self.cache is not None and direct_run is None and cache_key is not None:
            cached_plan = self.cache.get(cache_key)
            if cached_plan is not None:
                cached_concepts = self._cached_concepts(request, cached_plan)
                cache_issues = validate_internal_rewrite_plan(cached_plan, request, cached_concepts)
                cached_state = self._apply_plan(request, cached_plan) if not cache_issues else None
                if cached_state is not None:
                    try:
                        cached_run = self.tools.retrieve(
                            request,
                            state=cached_state,
                            run_kind="CACHED_PLAN",
                            remaining_ms=self._remaining(request, started_ms),
                        )
                    except Exception:
                        cached_run = None
                    if cached_run is not None:
                        cached_comparison = compare_retrieval_runs(
                            baseline=request.baseline_run,
                            candidate=cached_run,
                            policy=request.policy,
                        )
                        if cached_comparison.decision is ComparatorDecision.ACCEPTED:
                            return self._finish(
                                request,
                                started_ms=started_ms,
                                outcome=(
                                    RecoveryOutcome.RECOVERED_TIER1
                                    if cached_plan.source == "DIRECT"
                                    else RecoveryOutcome.RECOVERED_TIER2
                                ),
                                terminal_state="ANSWERED_WITH_GROUNDED_RESULTS",
                                selected_run=cached_run,
                                plan=cached_plan,
                                comparator=cached_comparison,
                                direct_run=cached_run if cached_plan.source == "DIRECT" else None,
                                generative_run=cached_run if cached_plan.source == "GENERATIVE" else None,
                                mapping_ids=cached_plan.mapping_ids,
                                cache_hit=True,
                                warnings=[*warnings, "RECOVERY_PLAN_CACHE_HIT"],
                                validation_codes=cache_issues,
                            )

        # Tier 2 is disabled by policy, circuit, or remaining deadline unless
        # direct recovery already returned an accepted result above.
        if not request.policy.tier2_enabled:
            return self._baseline_or_no_safe(
                request,
                started_ms,
                direct_run=direct_run,
                direct_plan=direct_plan,
                comparator=selected_comparator,
                mapping_ids=mapping_ids,
                comparator_decisions=comparator_decisions,
                warnings=[*warnings, "TIER2_DISABLED"],
                validation_codes=planner_codes,
            )
        if self.circuit.is_open():
            return self._baseline_or_no_safe(
                request,
                started_ms,
                direct_run=direct_run,
                direct_plan=direct_plan,
                comparator=selected_comparator,
                mapping_ids=mapping_ids,
                comparator_decisions=comparator_decisions,
                warnings=[*warnings, "TIER2_CIRCUIT_OPEN"],
                validation_codes=planner_codes,
            )
        remaining_ms = self._remaining(request, started_ms)
        if remaining_ms < request.policy.tier2_min_remaining_ms:
            return self._baseline_or_no_safe(
                request,
                started_ms,
                direct_run=direct_run,
                direct_plan=direct_plan,
                comparator=selected_comparator,
                mapping_ids=mapping_ids,
                comparator_decisions=comparator_decisions,
                warnings=[*warnings, "TIER2_DEADLINE_BLOCKED"],
                validation_codes=[*planner_codes, "RECOVERY_BUDGET_EXHAUSTED"],
            )

        try:
            allowed_concepts = self.tools.get_recovery_constraints(request)
        except Exception as exc:
            return self._baseline_or_no_safe(
                request,
                started_ms,
                direct_run=direct_run,
                direct_plan=direct_plan,
                comparator=selected_comparator,
                mapping_ids=mapping_ids,
                comparator_decisions=comparator_decisions,
                warnings=[*warnings, "RECOVERY_CONSTRAINTS_UNAVAILABLE"],
                validation_codes=[*planner_codes, f"CONSTRAINT_LOOKUP:{type(exc).__name__}"],
            )

        concepts_by_id.update({concept.concept_id: concept for concept in allowed_concepts})
        context = self.tools.build_context(
            request,
            allowed_concepts=allowed_concepts,
            approved_suggestions=expansions[:3],
        )
        planner_called = True
        planner_timeout = max(
            1,
            self._remaining(request, started_ms) - PLANNER_POST_CALL_RESERVE_MS,
        )
        try:
            planner_output, planner_codes, _tokens, _latency = self.tools.plan_constrained_repair(
                context,
                timeout_ms=planner_timeout,
            )
        except Exception as exc:
            planner_output = None
            planner_codes = [*planner_codes, f"PLANNER:{type(exc).__name__}"]
        if planner_output is None:
            return self._planner_failure_or_clarification(
                request,
                started_ms,
                allowed_concepts=allowed_concepts,
                concepts_by_id=concepts_by_id,
                direct_run=direct_run,
                direct_plan=direct_plan,
                comparator=selected_comparator,
                mapping_ids=mapping_ids,
                comparator_decisions=comparator_decisions,
                planner_action=None,
                planner_called=planner_called,
                planner_codes=planner_codes,
                warnings=warnings,
            )

        planner_action = PlannerAction(planner_output.action)
        plan_issues = validate_planner_plan(planner_output, context, concepts_by_id)
        if plan_issues:
            return self._planner_failure_or_clarification(
                request,
                started_ms,
                allowed_concepts=allowed_concepts,
                concepts_by_id=concepts_by_id,
                direct_run=direct_run,
                direct_plan=direct_plan,
                comparator=selected_comparator,
                mapping_ids=mapping_ids,
                comparator_decisions=comparator_decisions,
                planner_action=planner_action,
                planner_called=planner_called,
                planner_codes=[*planner_codes, *plan_issues],
                warnings=warnings,
            )

        if isinstance(planner_output, RecoveryClarificationPlan):
            clarification = self.tools.build_clarification(
                request,
                option_ids=planner_output.option_ids,
                target_field=planner_output.target_field,
                reason_code="RECOVERY_PLANNER_CLARIFICATION",
                concepts_by_id=concepts_by_id,
            )
            if clarification is not None:
                return self._finish(
                    request,
                    started_ms=started_ms,
                    outcome=RecoveryOutcome.CLARIFICATION_REQUIRED,
                    terminal_state="CLARIFICATION_REQUIRED",
                    selected_run=request.baseline_run,
                    clarification=clarification,
                    direct_run=direct_run,
                    mapping_ids=mapping_ids,
                    planner_action=planner_action,
                    planner_called=planner_called,
                    planner_validation_codes=planner_codes,
                    comparator_decisions=comparator_decisions,
                    warnings=warnings,
                )
            return self._baseline_or_no_safe(
                request,
                started_ms,
                direct_run=direct_run,
                direct_plan=direct_plan,
                comparator=selected_comparator,
                mapping_ids=mapping_ids,
                comparator_decisions=comparator_decisions,
                warnings=[*warnings, "PLANNER_CLARIFICATION_OPTIONS_UNAVAILABLE"],
                validation_codes=[*planner_codes, "CLARIFICATION_OPTIONS_UNAVAILABLE"],
                planner_action=planner_action,
                planner_called=planner_called,
            )

        if isinstance(planner_output, RecoveryNoSafePlan):
            return self._baseline_or_no_safe(
                request,
                started_ms,
                direct_run=direct_run,
                direct_plan=direct_plan,
                comparator=selected_comparator,
                mapping_ids=mapping_ids,
                comparator_decisions=comparator_decisions,
                warnings=[*warnings, "PLANNER_ABSTAINED"],
                validation_codes=planner_codes,
                planner_action=planner_action,
                planner_called=planner_called,
            )

        generative_plan = self.tools.make_internal_rewrite_plan(
            request,
            source="GENERATIVE",
            output=planner_output,
            concepts_by_id=concepts_by_id,
        )
        plan_issues = validate_internal_rewrite_plan(generative_plan, request, concepts_by_id)
        if plan_issues:
            return self._planner_failure_or_clarification(
                request,
                started_ms,
                allowed_concepts=allowed_concepts,
                concepts_by_id=concepts_by_id,
                direct_run=direct_run,
                direct_plan=direct_plan,
                comparator=selected_comparator,
                mapping_ids=mapping_ids,
                comparator_decisions=comparator_decisions,
                planner_action=planner_action,
                planner_called=planner_called,
                planner_codes=[*planner_codes, *plan_issues],
                warnings=warnings,
            )
        generative_state = self._apply_plan(request, generative_plan)
        if generative_state is None:
            return self._planner_failure_or_clarification(
                request,
                started_ms,
                allowed_concepts=allowed_concepts,
                concepts_by_id=concepts_by_id,
                direct_run=direct_run,
                direct_plan=direct_plan,
                comparator=selected_comparator,
                mapping_ids=mapping_ids,
                comparator_decisions=comparator_decisions,
                planner_action=planner_action,
                planner_called=planner_called,
                planner_codes=[*planner_codes, "RECOVERY_PLAN_APPLICATION_FAILED"],
                warnings=warnings,
            )
        try:
            generative_run = self.tools.retrieve(
                request,
                state=generative_state,
                run_kind="TIER2_GENERATIVE",
                remaining_ms=self._remaining(request, started_ms),
            )
        except Exception as exc:
            return self._baseline_or_no_safe(
                request,
                started_ms,
                direct_run=direct_run,
                direct_plan=direct_plan,
                comparator=selected_comparator,
                mapping_ids=mapping_ids,
                comparator_decisions=comparator_decisions,
                warnings=[*warnings, "TIER2_RETRIEVAL_UNAVAILABLE"],
                validation_codes=[*planner_codes, f"TIER2_RETRIEVAL:{type(exc).__name__}"],
                planner_action=planner_action,
                planner_called=planner_called,
            )
        comparison = compare_retrieval_runs(
            baseline=request.baseline_run,
            candidate=generative_run,
            policy=request.policy,
        )
        comparator_decisions.append(comparison.decision)
        if comparison.decision is ComparatorDecision.ACCEPTED:
            self._cache_put(cache_key, generative_plan, warnings)
            return self._finish(
                request,
                started_ms=started_ms,
                outcome=RecoveryOutcome.RECOVERED_TIER2,
                terminal_state="ANSWERED_WITH_GROUNDED_RESULTS",
                selected_run=generative_run,
                plan=generative_plan,
                comparator=comparison,
                direct_run=direct_run,
                generative_run=generative_run,
                mapping_ids=mapping_ids,
                planner_action=planner_action,
                planner_called=planner_called,
                planner_validation_codes=planner_codes,
                comparator_decisions=comparator_decisions,
                warnings=warnings,
            )
        if comparison.decision is ComparatorDecision.AMBIGUOUS:
            clarification = self._clarification_from_constraints(
                request,
                allowed_concepts=allowed_concepts,
                concepts_by_id=concepts_by_id,
                reason_code="RECOVERY_RUNS_AMBIGUOUS",
            )
            if clarification is not None:
                return self._finish(
                    request,
                    started_ms=started_ms,
                    outcome=RecoveryOutcome.CLARIFICATION_REQUIRED,
                    terminal_state="CLARIFICATION_REQUIRED",
                    selected_run=request.baseline_run,
                    comparator=comparison,
                    clarification=clarification,
                    direct_run=direct_run,
                    generative_run=generative_run,
                    mapping_ids=mapping_ids,
                    planner_action=planner_action,
                    planner_called=planner_called,
                    planner_validation_codes=planner_codes,
                    comparator_decisions=comparator_decisions,
                    warnings=warnings,
                )
        return self._baseline_or_no_safe(
            request,
            started_ms,
            direct_run=direct_run,
            direct_plan=direct_plan,
            comparator=comparison,
            mapping_ids=mapping_ids,
            comparator_decisions=comparator_decisions,
            warnings=[*warnings, "TIER2_REWRITE_NOT_ACCEPTED"],
            validation_codes=planner_codes,
            planner_action=planner_action,
            planner_called=planner_called,
            generative_run=generative_run,
        )

    def _select_direct_expansions(
        self,
        request: RecoveryRequest,
        expansions: list[ApprovedExpansion],
    ) -> list[ApprovedExpansion]:
        grouped: dict[str, list[ApprovedExpansion]] = defaultdict(list)
        for expansion in expansions:
            if expansion.lexicon_version != request.compatibility.lexicon_version:
                continue
            if expansion.catalog_version != request.compatibility.catalog_version:
                continue
            if expansion.taxonomy_version != request.compatibility.taxonomy_version:
                continue
            if expansion.category_schema_version != request.compatibility.category_schema_version:
                continue
            if expansion.evidence_band is not EvidenceBand.APPROVED_HIGH:
                continue
            if expansion.expansion_action not in {
                ExpansionAction.CANONICAL_SYNONYM,
                ExpansionAction.CANONICAL_ATTRIBUTE,
                ExpansionAction.CANONICAL_VALUE,
            }:
                continue
            if expansion.taxonomy_scope_id not in {None, request.query_state.taxonomy_scope_id}:
                continue
            grouped[expansion.normalized_form].append(expansion)

        selected: list[ApprovedExpansion] = []
        for term in sorted(grouped):
            candidates = sorted(
                grouped[term],
                key=lambda item: (
                    item.taxonomy_scope_id != request.query_state.taxonomy_scope_id,
                    -item.priority,
                    item.mapping_id,
                ),
            )
            if not candidates:
                continue
            best = candidates[0]
            same_priority = [
                item
                for item in candidates
                if item.taxonomy_scope_id == best.taxonomy_scope_id and item.priority == best.priority
            ]
            if len(same_priority) > 1:
                is_bounded_compound = (
                    all(item.mapping_type is MappingType.COMPOUND for item in same_priority)
                    and len({item.canonical_target_id for item in same_priority}) == len(same_priority)
                )
                if not is_bounded_compound:
                    continue
                remaining = request.policy.max_direct_mappings - len(selected)
                selected.extend(same_priority[:remaining])
            elif len(same_priority) == 1:
                selected.append(best)
            else:
                continue
            if len(selected) >= request.policy.max_direct_mappings:
                break
        return selected

    @staticmethod
    def _constraint_from_expansion(expansion: ApprovedExpansion) -> RecoveryConstraint:
        return RecoveryConstraint(
            concept_id=expansion.canonical_target_id,
            concept_type=expansion.concept_type,
            label=expansion.canonical_label,
            canonical_term=expansion.canonical_label,
            taxonomy_scope_id=expansion.taxonomy_scope_id,
            attribute_id=expansion.attribute_id,
            value_id=expansion.canonical_target_id if expansion.concept_type is ConceptType.VALUE else None,
            catalog_version=expansion.catalog_version,
            taxonomy_version=expansion.taxonomy_version,
            category_schema_version=expansion.category_schema_version,
            lexicon_version=expansion.lexicon_version,
        )

    @staticmethod
    def _cached_concepts(request: RecoveryRequest, plan: RecoveryPlan) -> dict[str, RecoveryConstraint]:
        return {
            concept_id: RecoveryConstraint(
                concept_id=concept_id,
                concept_type=ConceptType.TAXONOMY,
                label=term,
                canonical_term=term,
                taxonomy_scope_id=request.query_state.taxonomy_scope_id,
                catalog_version=request.compatibility.catalog_version,
                taxonomy_version=request.compatibility.taxonomy_version,
                category_schema_version=request.compatibility.category_schema_version,
                lexicon_version=request.compatibility.lexicon_version,
            )
            for concept_id, term in zip(plan.added_concept_ids, plan.added_query_terms, strict=True)
        }

    def _cache_put(self, key: str | None, plan: RecoveryPlan, warnings: list[str]) -> None:
        if self.cache is None or key is None:
            return
        try:
            self.cache.put(key, plan)
        except Exception:
            warnings.append("RECOVERY_PLAN_CACHE_WRITE_FAILED")

    @staticmethod
    def _apply_plan(request: RecoveryRequest, plan: RecoveryPlan):
        current_hash = hard_filter_hash(request.query_state)
        if current_hash != plan.preserved_hard_filter_hash:
            return None
        terms = list(request.query_state.query_terms)
        for term in plan.added_query_terms:
            if term not in terms:
                terms.append(term)
        return request.query_state.model_copy(update={"query_terms": terms})

    def _remaining(self, request: RecoveryRequest, started_ms: int) -> int:
        return max(0, request.policy.recovery_deadline_ms - (self.clock.monotonic_ms() - started_ms))

    def _baseline_or_no_safe(
        self,
        request: RecoveryRequest,
        started_ms: int,
        *,
        direct_run: RetrievalRun | None,
        direct_plan: RecoveryPlan | None,
        comparator,
        mapping_ids: list[str],
        comparator_decisions: list[ComparatorDecision],
        warnings: list[str],
        validation_codes: list[str] | None = None,
        planner_action: PlannerAction | None = None,
        planner_called: bool = False,
        generative_run: RetrievalRun | None = None,
    ) -> RecoveryResponse:
        if request.baseline_run.eligible_count > 0:
            outcome = RecoveryOutcome.BASELINE_PRESERVED
            terminal = "ANSWERED_WITH_GROUNDED_RESULTS"
            selected = request.baseline_run
        else:
            outcome = RecoveryOutcome.NO_SAFE_RECOVERY
            terminal = "NO_SAFE_RECOVERY"
            selected = None
        return self._finish(
            request,
            started_ms=started_ms,
            outcome=outcome,
            terminal_state=terminal,
            selected_run=selected,
            plan=direct_plan,
            comparator=comparator,
            direct_run=direct_run,
            generative_run=generative_run,
            mapping_ids=mapping_ids,
            planner_action=planner_action,
            planner_called=planner_called,
            planner_validation_codes=validation_codes,
            comparator_decisions=comparator_decisions,
            warnings=warnings,
        )

    def _planner_failure_or_clarification(
        self,
        request: RecoveryRequest,
        started_ms: int,
        *,
        allowed_concepts: list[RecoveryConstraint],
        concepts_by_id: dict[str, RecoveryConstraint],
        direct_run: RetrievalRun | None,
        direct_plan: RecoveryPlan | None,
        comparator,
        mapping_ids: list[str],
        comparator_decisions: list[ComparatorDecision],
        planner_action: PlannerAction | None,
        planner_called: bool,
        planner_codes: list[str],
        warnings: list[str],
    ) -> RecoveryResponse:
        clarification = self._clarification_from_constraints(
            request,
            allowed_concepts=allowed_concepts,
            concepts_by_id=concepts_by_id,
            reason_code="RECOVERY_PLANNER_INVALID_OR_UNAVAILABLE",
        )
        if clarification is not None:
            return self._finish(
                request,
                started_ms=started_ms,
                outcome=RecoveryOutcome.CLARIFICATION_REQUIRED,
                terminal_state="CLARIFICATION_REQUIRED",
                selected_run=request.baseline_run,
                clarification=clarification,
                comparator=comparator,
                direct_run=direct_run,
                mapping_ids=mapping_ids,
                planner_action=planner_action,
                planner_called=planner_called,
                planner_validation_codes=planner_codes,
                comparator_decisions=comparator_decisions,
                warnings=[*warnings, "PLANNER_FALLBACK_TO_CLARIFICATION"],
            )
        return self._baseline_or_no_safe(
            request,
            started_ms,
            direct_run=direct_run,
            direct_plan=direct_plan,
            comparator=comparator,
            mapping_ids=mapping_ids,
            comparator_decisions=comparator_decisions,
            warnings=[*warnings, "PLANNER_NO_SAFE_FALLBACK"],
            validation_codes=planner_codes,
            planner_action=planner_action,
            planner_called=planner_called,
        )

    def _clarification_from_constraints(
        self,
        request: RecoveryRequest,
        *,
        allowed_concepts: list[RecoveryConstraint],
        concepts_by_id: dict[str, RecoveryConstraint],
        reason_code: str,
    ):
        if len(allowed_concepts) < 2:
            return None
        option_ids = [concept.concept_id for concept in allowed_concepts[:4]]
        target_field = next(
            (concept.attribute_id for concept in allowed_concepts if concept.attribute_id),
            "category",
        )
        return self.tools.build_clarification(
            request,
            option_ids=option_ids,
            target_field=target_field,
            reason_code=reason_code,
            concepts_by_id=concepts_by_id,
        )

    def _finish(
        self,
        request: RecoveryRequest,
        *,
        started_ms: int,
        outcome: RecoveryOutcome,
        terminal_state: str,
        selected_run: RetrievalRun | None,
        warnings: list[str],
        validation_codes: list[str] | None = None,
        plan: RecoveryPlan | None = None,
        comparator=None,
        clarification=None,
        direct_run: RetrievalRun | None = None,
        generative_run: RetrievalRun | None = None,
        mapping_ids: list[str] | None = None,
        planner_action: PlannerAction | None = None,
        planner_called: bool = False,
        planner_validation_codes: list[str] | None = None,
        comparator_decisions: list[ComparatorDecision] | None = None,
        cache_hit: bool = False,
    ) -> RecoveryResponse:
        suggestions = self._suggestions_from_plan(plan)
        if clarification is not None:
            suggestions = self._suggestions_from_clarification(clarification)
            clarification = None
            if outcome is RecoveryOutcome.CLARIFICATION_REQUIRED:
                outcome = RecoveryOutcome.RECOVERY_SUGGESTIONS
                terminal_state = "RECOVERY_SUGGESTIONS_AVAILABLE"
            warnings = [*warnings, "CLARIFICATION_SUPPRESSED_AS_SUGGESTIONS"]
        used_ms = max(0, self.clock.monotonic_ms() - started_ms)
        before_hash = hard_filter_hash(request.query_state)
        after_hash = selected_run.hard_filter_hash if selected_run is not None else before_hash
        mutation_count = 0 if before_hash == after_hash else 1
        final_codes = [*(validation_codes or []), *(planner_validation_codes or [])]
        event = RecoveryEvent(
            event_id=self.ids.new_id("recovery-event"),
            session_id=request.session_id,
            turn_id=request.turn_id,
            trace_id=request.trace_id,
            outcome=outcome,
            terminal_state=terminal_state,
            trigger_reasons=list(request.gate.reasons),
            original_terms=list(request.unresolved_terms),
            hard_filter_hash_before=before_hash,
            hard_filter_hash_after=after_hash,
            baseline_run_id=request.baseline_run.run_id,
            direct_run_id=direct_run.run_id if direct_run else None,
            generative_run_id=generative_run.run_id if generative_run else None,
            mapping_ids=list(mapping_ids or (plan.mapping_ids if plan else [])),
            planner_action=planner_action,
            planner_called=planner_called,
            planner_validation_codes=sorted(set(final_codes))[:20],
            comparator_decisions=list(comparator_decisions or []),
            tool_calls=self._tool_calls(
                request=request,
                direct_run=direct_run,
                generative_run=generative_run,
                planner_called=planner_called,
                planner_validation_codes=final_codes,
                clarification=clarification,
                suggestions=suggestions,
                cache_hit=cache_hit,
            ),
            retrieval_run_count=1 + int(direct_run is not None) + int(generative_run is not None),
            hard_filter_mutation_count=mutation_count,
            added_latency_ms=used_ms,
            budget_ms=request.policy.recovery_deadline_ms,
            budget_used_ms=used_ms,
            model_prompt_version=request.compatibility.recovery_prompt_version if planner_called else None,
            model_alias=request.compatibility.recovery_model_alias if planner_called else None,
            compatibility=request.compatibility,
            cache_hit=cache_hit,
            warnings=list(dict.fromkeys(warnings))[:12],
            created_at=self.clock.now_utc(),
        )
        try:
            self.tools.events.record(event)
        except Exception:
            event = event.model_copy(update={"warnings": [*event.warnings, "EVENT_RECORD_FAILED"][:12]})
        return RecoveryResponse(
            outcome=outcome,
            terminal_state=terminal_state,
            baseline_run=request.baseline_run,
            selected_run=selected_run,
            plan=plan,
            comparator=comparator,
            clarification=clarification,
            suggestions=suggestions,
            interpretation_label=plan.interpretation_label if plan else None,
            warnings=list(dict.fromkeys([*warnings, *final_codes]))[:16],
            event=event,
        )

    @staticmethod
    def _tool_calls(
        *,
        request: RecoveryRequest,
        direct_run: RetrievalRun | None,
        generative_run: RetrievalRun | None,
        planner_called: bool,
        planner_validation_codes: list[str],
        clarification: Any,
        suggestions: list[RecoverySuggestion],
        cache_hit: bool,
    ) -> list[ToolReceipt]:
        calls = [ToolReceipt(tool_name="retrieve_candidates:baseline", status="OK")]
        if request.gate.decision is GateDecision.RECOVERY_ELIGIBLE:
            calls.append(ToolReceipt(tool_name="lookup_approved_expansions", status="OK"))
        if direct_run is not None:
            calls.append(ToolReceipt(tool_name="retrieve_candidates:tier1", status="OK"))
            calls.append(ToolReceipt(tool_name="compare_retrieval_runs:tier1", status="OK"))
        if planner_called:
            calls.append(ToolReceipt(tool_name="get_recovery_constraints", status="OK"))
            calls.append(
                ToolReceipt(
                    tool_name="plan_constrained_repair",
                    status="REJECTED" if planner_validation_codes else "OK",
                    validation_codes=sorted(set(planner_validation_codes))[:20],
                )
            )
        if generative_run is not None:
            calls.append(ToolReceipt(tool_name="retrieve_candidates:tier2", status="OK"))
            calls.append(ToolReceipt(tool_name="compare_retrieval_runs:tier2", status="OK"))
        if cache_hit:
            calls.append(ToolReceipt(tool_name="recovery_plan_cache", status="OK"))
        if clarification is not None:
            calls.append(ToolReceipt(tool_name="build_clarification", status="OK"))
        if suggestions:
            calls.append(ToolReceipt(tool_name="build_suggestions", status="OK"))
        calls.append(ToolReceipt(tool_name="record_recovery_event", status="OK"))
        return calls[:12]

    @staticmethod
    def _suggestions_from_plan(plan: RecoveryPlan | None) -> list[RecoverySuggestion]:
        if plan is None:
            return []
        source = "APPROVED_LEXICON" if plan.source == "DIRECT" else "ALLOWED_CONCEPT"
        return [
            RecoverySuggestion(
                suggestion_id=f"recovery-{index + 1}-{concept_id}",
                label=term.title(),
                query_terms=[term],
                concept_ids=[concept_id],
                source=source,
            )
            for index, (concept_id, term) in enumerate(
                zip(plan.added_concept_ids, plan.added_query_terms, strict=True)
            )
        ][:3]

    @staticmethod
    def _suggestions_from_clarification(clarification) -> list[RecoverySuggestion]:
        return [
            RecoverySuggestion(
                suggestion_id=f"recovery-option-{option.option_id}",
                label=option.label,
                query_terms=[option.label.lower()],
                concept_ids=[option.concept_id],
                source="ALLOWED_CONCEPT",
            )
            for option in clarification.options
        ][:3]
