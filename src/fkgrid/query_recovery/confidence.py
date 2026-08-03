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
    minimum_result_count: int = 2,
    minimum_popular_result_count: int = 1,
    low_popularity_enabled: bool = True,
) -> RecoveryGate:
    """Build structural recovery signals without asking a model to judge quality."""

    reasons: list[RecoveryTriggerReason] = []
    details: dict[str, str] = {}
    normalized_unknown = sorted({term for term in unknown_terms if term.strip()})
    if run.eligible_count == 0:
        reasons.append(RecoveryTriggerReason.NO_ELIGIBLE_RESULTS)
        details[RecoveryTriggerReason.NO_ELIGIBLE_RESULTS.value] = "no eligible catalog result"
    if run.eligible_count < minimum_result_count:
        reasons.append(RecoveryTriggerReason.LOW_RESULT_COUNT)
        details[RecoveryTriggerReason.LOW_RESULT_COUNT.value] = (
            f"fewer than {minimum_result_count} eligible catalog results"
        )
    if (
        low_popularity_enabled
        and run.popular_result_count is not None
        and run.popular_result_count < minimum_popular_result_count
    ):
        reasons.append(RecoveryTriggerReason.LOW_POPULARITY)
        details[RecoveryTriggerReason.LOW_POPULARITY.value] = "no eligible result meets the popularity floor"
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
        or run.eligible_count < minimum_result_count
        or (
            low_popularity_enabled
            and run.popular_result_count is not None
            and run.popular_result_count < minimum_popular_result_count
        )
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
        popular_result_count=run.popular_result_count,
        top_popularity_score=run.top_popularity_score,
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
    popular_delta = (
        candidate.popular_result_count - baseline.popular_result_count
        if candidate.popular_result_count is not None
        and baseline.popular_result_count is not None
        else None
    )
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
    baseline_ids = list(baseline.result_product_ids)
    candidate_ids = list(candidate.result_product_ids)
    baseline_results_preserved = set(baseline_ids).issubset(set(candidate_ids))
    baseline_results_first = not baseline_ids or candidate_ids[: len(baseline_ids)] == baseline_ids
    reasons: list[str] = []

    if not hash_equal:
        reasons.append("HARD_FILTER_HASH_CHANGED")
    if not versions_equal:
        reasons.append("COMPATIBILITY_TUPLE_CHANGED")
    if not candidate.is_scope_safe:
        reasons.append("RECOVERED_RUN_SCOPE_UNSAFE")
    if not baseline_results_preserved:
        reasons.append("BASELINE_RESULTS_NOT_PRESERVED")
    elif not baseline_results_first:
        reasons.append("BASELINE_RESULTS_NOT_FIRST")
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
            popular_result_count_delta=popular_delta,
            top_score_delta=score_delta,
            coverage_delta=coverage_delta,
            diversity_delta=diversity_delta,
            baseline_results_preserved=baseline_results_preserved,
            baseline_results_first=baseline_results_first,
            hard_filter_hash_equal=hash_equal,
            compatibility_equal=versions_equal,
        )

    if baseline.eligible_count == 0 and candidate.eligible_count > 0:
        return ComparatorResult(
            decision=ComparatorDecision.ACCEPTED,
            rule_id="recovery-policy-v1-zero-to-eligible",
            reasons=["BASELINE_ZERO_ELIGIBLE", "RECOVERED_ELIGIBLE"],
            eligible_count_delta=eligible_delta,
            popular_result_count_delta=popular_delta,
            top_score_delta=score_delta,
            coverage_delta=coverage_delta,
            diversity_delta=diversity_delta,
            baseline_results_preserved=baseline_results_preserved,
            baseline_results_first=baseline_results_first,
            hard_filter_hash_equal=hash_equal,
            compatibility_equal=versions_equal,
        )

    materially_better = bool(
        (score_delta is not None and score_delta >= policy.min_improvement_margin)
        or (coverage_delta is not None and coverage_delta >= policy.min_improvement_margin)
        or (popular_delta is not None and popular_delta >= policy.min_popularity_gain)
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
        if popular_delta is not None and popular_delta >= policy.min_popularity_gain:
            reasons.append("POPULARITY_SIGNAL_IMPROVED")
        return ComparatorResult(
            decision=ComparatorDecision.ACCEPTED,
            rule_id="recovery-policy-v1-material-improvement",
            reasons=reasons,
            eligible_count_delta=eligible_delta,
            popular_result_count_delta=popular_delta,
            top_score_delta=score_delta,
            coverage_delta=coverage_delta,
            diversity_delta=diversity_delta,
            baseline_results_preserved=baseline_results_preserved,
            baseline_results_first=baseline_results_first,
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
            popular_result_count_delta=popular_delta,
            top_score_delta=score_delta,
            coverage_delta=coverage_delta,
            diversity_delta=diversity_delta,
            baseline_results_preserved=baseline_results_preserved,
            baseline_results_first=baseline_results_first,
            hard_filter_hash_equal=hash_equal,
            compatibility_equal=versions_equal,
        )

    return ComparatorResult(
        decision=ComparatorDecision.REJECTED,
        rule_id="recovery-policy-v1-no-material-improvement",
        reasons=["NO_MATERIAL_IMPROVEMENT"],
        eligible_count_delta=eligible_delta,
        popular_result_count_delta=popular_delta,
        top_score_delta=score_delta,
        coverage_delta=coverage_delta,
        diversity_delta=diversity_delta,
        baseline_results_preserved=baseline_results_preserved,
        baseline_results_first=baseline_results_first,
        hard_filter_hash_equal=hash_equal,
        compatibility_equal=versions_equal,
    )
