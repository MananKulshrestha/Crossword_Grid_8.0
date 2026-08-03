"""Deterministic Tier-1 recovery and Tier-2 planner boundaries."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from .catalog import DeterministicCatalog
from .contracts import (
    ApprovedExpansion,
    CompatibilityTuple,
    ConfidenceDecision,
    ConstrainedRepairPlan,
    QueryState,
    RecoveryConstraint,
    RecoveryEventRecord,
    RecoveryPlan,
    RetrievalComparison,
    RetrievalRun,
    SearchRequest,
    SearchResult,
)
from .determinism import hard_filter_hash, normalize_text, stable_id, tokenize, unique_sorted


def assess_retrieval_confidence(
    result: SearchResult, query_state: QueryState
) -> ConfidenceDecision:
    top_score = float(result.confidence_signals.get("top_score", 0.0))
    unknown_terms = list(result.unknown_terms)
    if result.status == "NO_MATCH":
        return ConfidenceDecision(
            status="RECOVER" if unknown_terms else "NO_MATCH",
            confidence=0.0,
            reasons=["NO_ELIGIBLE_RESULTS"],
            unknown_terms=unknown_terms,
        )
    if unknown_terms and top_score < 0.9:
        return ConfidenceDecision(
            status="RECOVER",
            confidence=top_score,
            reasons=["UNKNOWN_TERMS", "LOW_TOP_SCORE"],
            unknown_terms=unknown_terms,
        )
    if top_score < 0.25:
        return ConfidenceDecision(
            status="CLARIFY",
            confidence=top_score,
            reasons=["LOW_CONFIDENCE"],
            unknown_terms=unknown_terms,
        )
    if query_state.hard_filters and not result.entries:
        return ConfidenceDecision(
            status="CLARIFY",
            confidence=top_score,
            reasons=["HARD_FILTER_NO_MATCH"],
            unknown_terms=unknown_terms,
        )
    return ConfidenceDecision(
        status="ACCEPT",
        confidence=top_score,
        reasons=["SUFFICIENT_MATCH"],
        unknown_terms=unknown_terms,
    )


def lookup_approved_expansions(
    normalized_terms: Sequence[str],
    expansions: Iterable[ApprovedExpansion],
    query_state: QueryState | None = None,
    compatibility: CompatibilityTuple | None = None,
    limit: int = 8,
) -> list[ApprovedExpansion]:
    pinned = compatibility or CompatibilityTuple()
    terms = {normalize_text(term) for term in normalized_terms}
    values = [
        expansion
        for expansion in expansions
        if normalize_text(expansion.surface_form) in terms
        and expansion.lexicon_version == pinned.lexicon_version
        and (query_state is None or expansion.scope in {"global", query_state.category_id or ""})
    ]
    return sorted(values, key=lambda item: (normalize_text(item.surface_form), item.target_id))[
        :limit
    ]


def get_recovery_constraints(
    query_state: QueryState,
    unknown_terms: Sequence[str],
    expansions: Iterable[ApprovedExpansion] = (),
    compatibility: CompatibilityTuple | None = None,
    limit: int = 8,
) -> list[RecoveryConstraint]:
    pinned = compatibility or CompatibilityTuple()
    known = {normalize_text(item.canonical_term): item for item in expansions}
    constraints: list[RecoveryConstraint] = []
    for term in unique_sorted(unknown_terms):
        expansion = known.get(normalize_text(term))
        if expansion is not None:
            constraints.append(
                RecoveryConstraint(
                    constraint_id=stable_id("rc", {"term": term, "target": expansion.target_id}),
                    field="canonical_term",
                    allowed_values=[expansion.canonical_term],
                    source="approved_lexicon",
                    compatibility=pinned,
                )
            )
    if query_state.category_id:
        constraints.append(
            RecoveryConstraint(
                constraint_id=stable_id("rc", {"category": query_state.category_id}),
                field="category_id",
                allowed_values=[query_state.category_id],
                source="current_query_state",
                compatibility=pinned,
            )
        )
    return constraints[:limit]


def plan_constrained_repair(
    context: dict[str, object], timeout_ms: int = 300
) -> ConstrainedRepairPlan:
    """Choose one bounded recovery outcome from supplied concepts only.

    This is the deterministic fallback for the feature-flagged planner call.
    It cannot invent IDs, relax filters, call retrieval, or select a tool.
    """

    del timeout_ms
    hard_hash = str(context.get("hard_filter_hash", ""))
    allowed_concepts = [
        item for item in context.get("allowed_concepts", []) if isinstance(item, dict)
    ]
    options = [
        item for item in context.get("allowed_clarification_options", []) if isinstance(item, dict)
    ]
    if len(allowed_concepts) == 1:
        concept = allowed_concepts[0]
        concept_id = str(concept.get("concept_id", ""))
        label = str(concept.get("label", ""))
        if concept_id and label:
            return ConstrainedRepairPlan(
                action="REWRITE_WITH_ALLOWED_CONCEPTS",
                added_concept_ids=[concept_id],
                interpretation_label=label,
                preserved_hard_filter_hash=hard_hash,
            )
    if options:
        option_ids = [str(item.get("option_id", "")) for item in options]
        option_ids = [item for item in option_ids if item]
        if option_ids:
            return ConstrainedRepairPlan(
                action="ASK_CLARIFICATION",
                option_ids=option_ids[:4],
                target_field=str(context.get("target_field", "category")),
                reason_code="AMBIGUOUS_ALLOWED_INTERPRETATION",
            )
    return ConstrainedRepairPlan(
        action="NO_SAFE_RECOVERY",
        reason_code="NO_COMPATIBLE_INTERPRETATION",
    )


class RecoveryEventStore:
    """Non-authoritative deterministic event sink for local contract tests."""

    def __init__(self) -> None:
        self.events: list[RecoveryEventRecord] = []

    def record(self, event: RecoveryEventRecord) -> RecoveryEventRecord:
        self.events.append(event)
        return event


def record_recovery_event(
    store: RecoveryEventStore,
    outcome: str,
    reason_codes: Sequence[str],
    hard_filter_hash_value: str,
    compatibility: CompatibilityTuple,
    planner_used: bool = False,
) -> RecoveryEventRecord:
    event = RecoveryEventRecord(
        event_id=stable_id(
            "recovery-event",
            {
                "outcome": outcome,
                "reasons": list(reason_codes),
                "hard_filter_hash": hard_filter_hash_value,
                "compatibility": compatibility,
                "planner_used": planner_used,
            },
        ),
        outcome=outcome,
        reason_codes=sorted(set(reason_codes)),
        hard_filter_hash=hard_filter_hash_value,
        compatibility=compatibility,
        created_at=datetime(1970, 1, 1, tzinfo=UTC),
        planner_used=planner_used,
    )
    return store.record(event)


def apply_recovery_plan(query_state: QueryState, plan: RecoveryPlan) -> QueryState:
    # QueryState intentionally does not own the compatibility tuple. Callers
    # validate the tuple with validate_recovery_plan before applying this plan.
    if plan.hard_filter_hash != hard_filter_hash(query_state):
        raise ValueError("HARD_FILTER_CHANGED")
    if plan.action != "REWRITE":
        return query_state
    existing = list(query_state.normalized_terms)
    additions = [
        term
        for term in plan.add_terms
        if normalize_text(term) not in {normalize_text(item) for item in existing}
    ]
    removals = {normalize_text(term) for term in plan.remove_terms}
    # Recovery may not remove explicit hard constraints. It can only rewrite
    # soft query terms, and the planner does not receive hard-filter values.
    current_terms = [term for term in existing if normalize_text(term) not in removals]
    return query_state.model_copy(
        update={"normalized_terms": unique_sorted([*current_terms, *additions])}
    )


def validate_recovery_plan(
    plan: RecoveryPlan, query_state: QueryState, compatibility: CompatibilityTuple
) -> list[str]:
    errors: list[str] = []
    if plan.compatibility != compatibility:
        errors.append("COMPATIBILITY_MISMATCH")
    if plan.hard_filter_hash != hard_filter_hash(query_state):
        errors.append("HARD_FILTER_CHANGED")
    if len(plan.add_terms) > 8 or len(plan.remove_terms) > 8:
        errors.append("TERM_BUDGET_EXCEEDED")
    if plan.action == "CLARIFY" and not plan.clarification_field:
        errors.append("CLARIFICATION_FIELD_REQUIRED")
    return errors


def compare_retrieval_runs(baseline: RetrievalRun, candidate: RetrievalRun) -> RetrievalComparison:
    baseline_ids = {item.binding.offer_id for item in baseline.result.entries}
    candidate_ids = {item.binding.offer_id for item in candidate.result.entries}
    new_ids = candidate_ids - baseline_ids
    new_bindings = [
        item.binding for item in candidate.result.entries if item.binding.offer_id in new_ids
    ]
    if candidate.result.status == "NO_MATCH":
        status = "KEEP_BASELINE"
        reasons = ["CANDIDATE_NO_MATCH"]
    elif len(candidate.result.entries) > len(baseline.result.entries) and new_bindings:
        status = "ACCEPT_RECOVERY"
        reasons = ["MORE_ELIGIBLE_RESULTS", "NEW_EXACT_BINDINGS"]
    elif candidate.result.entries and not baseline.result.entries:
        status = "ACCEPT_RECOVERY"
        reasons = ["BASELINE_EMPTY"]
    else:
        status = "CLARIFY" if candidate.result.unknown_terms else "KEEP_BASELINE"
        reasons = ["NO_CONFIRMED_IMPROVEMENT"]
    return RetrievalComparison(
        status=status,
        baseline_run_id=baseline.run_id,
        candidate_run_id=candidate.run_id,
        baseline_count=len(baseline.result.entries),
        candidate_count=len(candidate.result.entries),
        new_bindings=new_bindings,
        reasons=reasons,
    )


def resolve_query_state(message: str, session_state: QueryState | None = None) -> QueryState:
    """Tier-3-compatible deterministic seam; it never relaxes existing hard filters."""

    base = session_state or QueryState()
    terms = unique_sorted([*base.normalized_terms, *tokenize(message)])
    return base.model_copy(update={"text": message[:2000], "normalized_terms": terms})


class DeterministicRecoveryAdapter:
    """Adapter shape compatible with the shopper worktree's RecoveryPort."""

    def __init__(
        self, catalog: DeterministicCatalog, expansions: Iterable[ApprovedExpansion] = ()
    ) -> None:
        self.catalog = catalog
        self.expansions = list(expansions)

    def assess(self, result: SearchResult, query_state: QueryState) -> ConfidenceDecision:
        return assess_retrieval_confidence(result, query_state)

    def recover(
        self,
        result: SearchResult,
        query_state: QueryState,
        compatibility: CompatibilityTuple,
        deadline_ms: int = 75,
    ) -> SearchResult:
        del deadline_ms
        if not result.unknown_terms:
            return result
        matches = lookup_approved_expansions(
            result.unknown_terms, self.expansions, query_state, compatibility
        )
        if not matches:
            return result
        recovered = query_state.model_copy(
            update={
                "normalized_terms": unique_sorted(
                    [*query_state.normalized_terms, *(item.canonical_term for item in matches)]
                )
            }
        )
        return self.catalog.search(
            SearchRequest(query_state=recovered, compatibility=compatibility), deadline_ms=75
        )
