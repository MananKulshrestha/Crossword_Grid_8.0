"""Deterministic confidence-gate and retrieval comparator policies."""

from __future__ import annotations

from .domain import (
    BaselineSignals,
    ComparatorDecision,
    ComparatorResult,
    GateDecision,
    QueryState,
    RecoveryGate,
    RecoveryPolicy,
    RecoveryTriggerReason,
    RetrievalRun,
)
from .validation import hard_filter_hash, compatibility_equal


def assess_retrieval_confidence(
    *,
    run: RetrievalRun,
    query_state: QueryState,
    unknown_terms: list[str],
    policy_version: str,
    calibrated: bool = False,
) -> RecoveryGate:
    """Build structural recovery signals without asking a model to judge quality."""

    reasons: list[RecoveryTriggerReason] = []
    details: dict[str, str] = {}
    normalized_unknown = sorted({term for term in unknown_terms if term.strip()})
    if run.eligible_count == 0:
        reasons.append(RecoveryTriggerReason.NO_ELIGIBLE_RESULTS)
        details[RecoveryTriggerReason.NO_ELIGIBLE_RESULTS.value] = "no eligible catalog result"
    if normalized_unknown:
        reasons.append(RecoveryTriggerReason.UNKNOWN_IMPORTANT_TERM)
        details[RecoveryTriggerReason.UNKNOWN_IMPORTANT_TERM.value] = "unrecognized query term"
    if run.category_scope_consistent is False or run.hard_filter_violations > 0:
        reasons.append(RecoveryTriggerReason.CATEGORY_SCOPE_LEAKAGE)
        details[RecoveryTriggerReason.CATEGORY_SCOPE_LEAKAGE.value] = "retrieval scope diagnostic failed"
    if run.required_criteria_coverage is not None and calibrated and run.required_criteria_coverage < 0.50:
        reasons.append(RecoveryTriggerReason.LOW_COVERAGE)
        details[RecoveryTriggerReason.LOW_COVERAGE.value] = "calibrated required-criteria coverage is low"

    expected_hash = hard_filter_hash(query_state)
    if run.hard_filter_hash != expected_hash:
        reasons.append(RecoveryTriggerReason.CONSTRAINT_CONFLICT)
        details[RecoveryTriggerReason.CONSTRAINT_CONFLICT.value] = "baseline hash does not match state"

    # A genuine zero-result query with no unresolved/ambiguous cause is honest
    # inventory absence, not authorization for a generative rewrite.
    recoverable = bool(
        normalized_unknown
        or run.category_scope_consistent is False
        or (calibrated and run.required_criteria_coverage is not None and run.required_criteria_coverage < 0.50)
    )
    if not reasons:
        decision = GateDecision.CONFIDENT
    elif not recoverable:
        decision = GateDecision.NO_ELIGIBLE_MATCH
    elif RecoveryTriggerReason.CONSTRAINT_CONFLICT in reasons:
        decision = GateDecision.CLARIFICATION_REQUIRED
    else:
        decision = GateDecision.RECOVERY_ELIGIBLE

    signals = BaselineSignals(
        eligible_count=run.eligible_count,
        top_score=run.top_score,
        top_score_margin=run.top_score_margin,
        required_criteria_coverage=run.required_criteria_coverage,
        unknown_terms=normalized_unknown,
        parser_ambiguous=False,
        constraint_conflict=RecoveryTriggerReason.CONSTRAINT_CONFLICT in reasons,
        category_scope_consistent=run.category_scope_consistent,
        hard_filter_violations=run.hard_filter_violations,
        protected_exclusion_violations=run.protected_exclusion_violations,
    )
    return RecoveryGate(
        decision=decision,
        reasons=reasons,
        unknown_terms=normalized_unknown,
        signals=signals,
        hard_filter_hash=expected_hash,
        gate_policy_version=policy_version,
        calibrated=calibrated,
        reason_details=details,
    )


def compare_retrieval_runs(
    *,
    baseline: RetrievalRun,
    candidate: RetrievalRun,
    policy: RecoveryPolicy,
) -> ComparatorResult:
    """Compare safety and quality in the frozen plan-09 order."""

    hash_equal = baseline.hard_filter_hash == candidate.hard_filter_hash
    versions_equal = compatibility_equal(baseline.compatibility, candidate.compatibility)
    eligible_delta = candidate.eligible_count - baseline.eligible_count
    score_delta = (
        candidate.top_score - baseline.top_score
        if candidate.top_score is not None and baseline.top_score is not None
        else None
    )
    coverage_delta = (
        candidate.required_criteria_coverage - baseline.required_criteria_coverage
        if candidate.required_criteria_coverage is not None
        and baseline.required_criteria_coverage is not None
        else None
    )
    diversity_delta = len(set(candidate.result_product_ids)) - len(set(baseline.result_product_ids))
    reasons: list[str] = []

    if not hash_equal:
        reasons.append("HARD_FILTER_HASH_CHANGED")
    if not versions_equal:
        reasons.append("COMPATIBILITY_TUPLE_CHANGED")
    if not candidate.is_scope_safe:
        reasons.append("RECOVERED_RUN_SCOPE_UNSAFE")
    if candidate.eligible_count == 0:
        reasons.append("RECOVERED_RUN_HAS_NO_ELIGIBLE_RESULTS")
    if score_delta is not None and score_delta < -policy.max_top_score_drop:
        reasons.append("TOP_SCORE_REGRESSION")
    if coverage_delta is not None and coverage_delta < 0:
        reasons.append("REQUIRED_COVERAGE_REGRESSION")

    if reasons:
        return ComparatorResult(
            decision=ComparatorDecision.REJECTED,
            rule_id="recovery-policy-v1-safety-first",
            reasons=reasons,
            eligible_count_delta=eligible_delta,
            top_score_delta=score_delta,
            coverage_delta=coverage_delta,
            diversity_delta=diversity_delta,
            hard_filter_hash_equal=hash_equal,
            compatibility_equal=versions_equal,
        )

    if baseline.eligible_count == 0 and candidate.eligible_count > 0:
        return ComparatorResult(
            decision=ComparatorDecision.ACCEPTED,
            rule_id="recovery-policy-v1-zero-to-eligible",
            reasons=["BASELINE_ZERO_ELIGIBLE", "RECOVERED_ELIGIBLE"],
            eligible_count_delta=eligible_delta,
            top_score_delta=score_delta,
            coverage_delta=coverage_delta,
            diversity_delta=diversity_delta,
            hard_filter_hash_equal=hash_equal,
            compatibility_equal=versions_equal,
        )

    materially_better = bool(
        (score_delta is not None and score_delta >= policy.min_improvement_margin)
        or (coverage_delta is not None and coverage_delta >= policy.min_improvement_margin)
    )
    close_but_more_diverse = bool(
        score_delta is not None
        and abs(score_delta) <= policy.close_margin
        and diversity_delta >= policy.close_diversity_gain
    )
    if materially_better or close_but_more_diverse:
        reasons.extend(
            [
                "QUALITY_SIGNAL_IMPROVED" if materially_better else "CLOSE_SCORE_DIVERSITY_GAIN",
                "ELIGIBILITY_AND_SCOPE_PRESERVED",
            ]
        )
        return ComparatorResult(
            decision=ComparatorDecision.ACCEPTED,
            rule_id="recovery-policy-v1-material-improvement",
            reasons=reasons,
            eligible_count_delta=eligible_delta,
            top_score_delta=score_delta,
            coverage_delta=coverage_delta,
            diversity_delta=diversity_delta,
            hard_filter_hash_equal=hash_equal,
            compatibility_equal=versions_equal,
        )

    if (
        score_delta is not None
        and abs(score_delta) <= policy.close_margin
        and baseline.interpretation_family
        and candidate.interpretation_family
        and baseline.interpretation_family != candidate.interpretation_family
    ):
        return ComparatorResult(
            decision=ComparatorDecision.AMBIGUOUS,
            rule_id="recovery-policy-v1-close-incompatible-interpretations",
            reasons=["CLOSE_INCOMPATIBLE_INTERPRETATIONS"],
            eligible_count_delta=eligible_delta,
            top_score_delta=score_delta,
            coverage_delta=coverage_delta,
            diversity_delta=diversity_delta,
            hard_filter_hash_equal=hash_equal,
            compatibility_equal=versions_equal,
        )

    return ComparatorResult(
        decision=ComparatorDecision.REJECTED,
        rule_id="recovery-policy-v1-no-material-improvement",
        reasons=["NO_MATERIAL_IMPROVEMENT"],
        eligible_count_delta=eligible_delta,
        top_score_delta=score_delta,
        coverage_delta=coverage_delta,
        diversity_delta=diversity_delta,
        hard_filter_hash_equal=hash_equal,
        compatibility_equal=versions_equal,
    )
