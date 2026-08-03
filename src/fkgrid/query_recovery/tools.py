"""Typed recovery tools and the static capability inventory."""

from __future__ import annotations

from dataclasses import dataclass

from .domain import (
    ApprovedExpansion,
    ClarificationOption,
    ClarificationPacket,
    RecoveryConstraint,
    RecoveryContext,
    RecoveryPlan,
    RecoveryPlannerOutput,
    RecoveryRequest,
    RecoveryRewritePlan,
    RetrievalRun,
    ToolReceipt,
)
from .ports import (
    ApprovedExpansionPort,
    CatalogRetrievalPort,
    ClockPort,
    RecoveryConstraintPort,
    RecoveryEventPort,
    RecoveryPlannerPort,
)
from .validation import canonical_hash, hard_filter_hash, query_state_hash, normalize_term


RECOVERY_CAPABILITIES = frozenset(
    {
        "lookup_approved_expansions",
        "assess_retrieval_confidence",
        "apply_recovery_plan",
        "compare_retrieval_runs",
        "get_recovery_constraints",
        "plan_constrained_repair",
        "validate_recovery_plan",
        "build_clarification",
        "record_recovery_event",
    }
)


@dataclass(frozen=True)
class ToolCall:
    name: str
    status: str
    latency_ms: int = 0
    validation_codes: tuple[str, ...] = ()

    def receipt(self) -> ToolReceipt:
        return ToolReceipt(
            tool_name=self.name,
            status=self.status,  # type: ignore[arg-type]
            latency_ms=max(0, self.latency_ms),
            validation_codes=list(self.validation_codes),
        )


class RecoveryTools:
    """Adapter-neutral implementations of the recovery tool boundary.

    The workflow owns ordering and budgets. This class only invokes explicitly
    injected ports and builds safe planner/clarification packets.
    """

    def __init__(
        self,
        *,
        expansions: ApprovedExpansionPort,
        constraints: RecoveryConstraintPort,
        retrieval: CatalogRetrievalPort,
        planner: RecoveryPlannerPort,
        events: RecoveryEventPort,
        clock: ClockPort,
    ) -> None:
        self.expansions = expansions
        self.constraints = constraints
        self.retrieval = retrieval
        self.planner = planner
        self.events = events
        self.clock = clock

    def lookup_approved_expansions(self, request: RecoveryRequest) -> list[ApprovedExpansion]:
        terms = sorted({normalize_term(term) for term in request.unresolved_terms if term.strip()})
        return self.expansions.lookup(
            normalized_terms=terms[: request.policy.max_lookup_terms],
            query_state=request.query_state,
            compatibility=request.compatibility,
            limit=request.policy.max_direct_mappings,
        )

    def get_recovery_constraints(self, request: RecoveryRequest) -> list[RecoveryConstraint]:
        candidates = self.constraints.get_constraints(
            query_state=request.query_state,
            unknown_terms=request.unresolved_terms[: request.policy.max_lookup_terms],
            compatibility=request.compatibility,
            limit=20,
        )
        selected: list[RecoveryConstraint] = []
        seen: set[str] = set()
        for concept in candidates:
            if concept.concept_id in seen:
                continue
            if not concept.active or concept.locale != request.query_state.locale:
                continue
            if concept.catalog_version != request.compatibility.catalog_version:
                continue
            if concept.taxonomy_version != request.compatibility.taxonomy_version:
                continue
            if concept.category_schema_version != request.compatibility.category_schema_version:
                continue
            if concept.lexicon_version != request.compatibility.lexicon_version:
                continue
            if concept.taxonomy_scope_id not in {None, request.query_state.taxonomy_scope_id}:
                continue
            seen.add(concept.concept_id)
            selected.append(concept)
        return selected[:20]

    def retrieve(
        self,
        request: RecoveryRequest,
        *,
        state,
        run_kind: str,
        remaining_ms: int,
    ) -> RetrievalRun:
        return self.retrieval.search(
            query_state=state,
            query_terms=list(state.query_terms),
            compatibility=request.compatibility,
            run_kind=run_kind,
            remaining_ms=max(1, remaining_ms),
        )

    def build_context(
        self,
        request: RecoveryRequest,
        *,
        allowed_concepts: list[RecoveryConstraint],
        approved_mappings: list[ApprovedExpansion],
    ) -> RecoveryContext:
        unresolved_terms = (
            request.unresolved_terms
            or request.gate.unknown_terms
            or request.query_state.query_terms[: request.policy.max_lookup_terms]
        )
        base = {
            "unresolved_terms": unresolved_terms,
            "query_state": request.query_state,
            "hard_filter_hash": hard_filter_hash(request.query_state),
            "gate_reasons": request.gate.reasons,
            "baseline_run_id": request.baseline_run.run_id,
            "baseline_summary": request.gate.signals,
            "allowed_concepts": allowed_concepts,
            "approved_mappings": approved_mappings[:3],
            "compatibility": request.compatibility,
            "locale": "en-IN",
        }
        return RecoveryContext(
            **base,
            planner_input_hash=canonical_hash(base),
        )

    def plan_constrained_repair(
        self,
        context: RecoveryContext,
        *,
        timeout_ms: int,
    ) -> tuple[RecoveryPlannerOutput | None, list[str], int, int]:
        return self.planner.plan(context=context, timeout_ms=max(1, timeout_ms))

    def make_internal_rewrite_plan(
        self,
        request: RecoveryRequest,
        *,
        source: str,
        output: RecoveryRewritePlan,
        concepts_by_id: dict[str, RecoveryConstraint],
        mapping_ids: list[str] | None = None,
    ) -> RecoveryPlan:
        concepts = [concepts_by_id[concept_id] for concept_id in output.added_concept_ids]
        return RecoveryPlan(
            source=source,  # type: ignore[arg-type]
            mapping_ids=mapping_ids or [],
            added_concept_ids=output.added_concept_ids,
            added_query_terms=[concept.canonical_term for concept in concepts],
            interpretation_label=(
                "Allowed catalog concepts: " + ", ".join(concept.label for concept in concepts)
            ),
            preserved_hard_filter_hash=output.preserved_hard_filter_hash,
        )

    def build_clarification(
        self,
        request: RecoveryRequest,
        *,
        option_ids: list[str],
        target_field: str,
        reason_code: str,
        concepts_by_id: dict[str, RecoveryConstraint],
    ) -> ClarificationPacket | None:
        concepts = [concepts_by_id[concept_id] for concept_id in option_ids if concept_id in concepts_by_id]
        if len(concepts) < 2 or len(concepts) > 4:
            return None
        options = [
            ClarificationOption(
                option_id=concept.concept_id,
                label=concept.label,
                concept_id=concept.concept_id,
                concept_type=concept.concept_type,
            )
            for concept in concepts
        ]
        if len(options) == 2:
            question_labels = f"{options[0].label} or {options[1].label}"
        else:
            question_labels = (
                ", ".join(option.label for option in options[:-1])
                + f", or {options[-1].label}"
            )
        return ClarificationPacket(
            question=f"Did you mean {question_labels}?",
            reason_code=reason_code,
            target_field=target_field,
            options=options,
            preserved_hard_filter_hash=hard_filter_hash(request.query_state),
            state_hash=query_state_hash(request.query_state),
        )
