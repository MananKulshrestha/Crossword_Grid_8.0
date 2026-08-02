"""Deterministic regression and shadow harnesses for approved lexicon changes."""

from __future__ import annotations

from fkgrid.adapters.catalog_language.runtime import (
    DeterministicLexiconLookup,
    RuntimeLexiconSnapshot,
)
from fkgrid.catalog_language.serialization import sha256_hex
from fkgrid.domain.catalog_language import (
    LexiconCandidateVersion,
    LexiconLookupRequest,
    LexiconMapping,
    MappingStatus,
    RegressionCase,
    RegressionReport,
    ShadowCase,
    ShadowReport,
)
from fkgrid.ports.catalog_language import RegressionPort, ShadowEvaluationPort


def _lookup(
    mappings: list[LexiconMapping],
    lexicon_version: str,
    compatibility,
    query: str,
    locale: str,
    taxonomy_node_id: str | None,
    attribute_id: str | None,
) -> tuple[str, ...]:
    result = DeterministicLexiconLookup(
        RuntimeLexiconSnapshot(
            lexicon_version=lexicon_version,
            compatibility=compatibility,
            mappings=tuple(mappings),
        )
    ).lookup_expansions(
        LexiconLookupRequest(
            term=query,
            locale=locale,
            taxonomy_node_id=taxonomy_node_id,
            attribute_id=attribute_id,
            lexicon_version=lexicon_version,
        )
    )
    return tuple(mapping.target_id for mapping in result.mappings)


class DeterministicRegressionRunner(RegressionPort):
    def __init__(self, minimum_precision: float = 0.80, minimum_recovery: float = 0.50) -> None:
        self.minimum_precision = minimum_precision
        self.minimum_recovery = minimum_recovery

    def run_lexicon_regression(
        self,
        candidate: LexiconCandidateVersion,
        active_mappings: list[LexiconMapping],
        cases: list[RegressionCase],
        policy_version: str,
    ) -> RegressionReport:
        active_compatibility = (
            active_mappings[0].compatibility
            if active_mappings
            else candidate.compatibility.model_copy(
                update={"lexicon_version": candidate.parent_lexicon_version}
            )
        )
        returned_total = 0
        hits_total = 0
        expected_total = 0
        expected_hits = 0
        protected_regressions = 0
        scope_leakage = 0
        invalid_targets = 0
        candidate_target_ids = {mapping.target_id for mapping in candidate.mappings}
        candidate_mappings = [
            mapping.model_copy(update={"status": MappingStatus.APPROVED})
            for mapping in candidate.mappings
        ]
        for case in cases:
            candidate_ids = _lookup(
                candidate_mappings,
                candidate.candidate_version,
                candidate.compatibility,
                case.query,
                case.locale,
                case.taxonomy_node_id,
                case.attribute_id,
            )
            baseline_ids = _lookup(
                active_mappings,
                candidate.parent_lexicon_version,
                active_compatibility,
                case.query,
                case.locale,
                case.taxonomy_node_id,
                case.attribute_id,
            )
            returned_total += len(candidate_ids)
            expected = set(case.expected_target_ids)
            hits = [target_id for target_id in candidate_ids if target_id in expected]
            hits_total += len(hits)
            if case.expects_expansion and expected:
                expected_total += 1
                expected_hits += int(bool(expected.intersection(candidate_ids)))
            if case.protected and candidate_ids != baseline_ids:
                protected_regressions += 1
            if set(candidate_ids).intersection(case.forbidden_target_ids):
                scope_leakage += 1
            invalid_targets += sum(
                target_id not in candidate_target_ids for target_id in candidate_ids
            )
        expansion_precision = hits_total / returned_total if returned_total else 1.0
        expected_recovery_rate = expected_hits / expected_total if expected_total else 1.0
        failures: list[str] = []
        if protected_regressions:
            failures.append("PROTECTED_QUERY_REGRESSION")
        if scope_leakage:
            failures.append("SCOPE_LEAKAGE")
        if invalid_targets:
            failures.append("INVALID_TARGET")
        if expansion_precision < self.minimum_precision:
            failures.append("EXPANSION_PRECISION_BELOW_THRESHOLD")
        if expected_recovery_rate < self.minimum_recovery:
            failures.append("EXPECTED_RECOVERY_BELOW_THRESHOLD")
        return RegressionReport(
            report_id=f"regression-{sha256_hex([candidate.candidate_version, cases])[:16]}",
            candidate_version=candidate.candidate_version,
            cases_total=len(cases),
            cases_passed=max(0, len(cases) - len(set(failures))),
            expansion_precision=expansion_precision,
            expected_recovery_rate=expected_recovery_rate,
            protected_regressions=protected_regressions,
            scope_leakage=scope_leakage,
            invalid_targets=invalid_targets,
            passed=not failures,
            policy_version=policy_version,
            failure_codes=failures,
        )


class DeterministicShadowEvaluator(ShadowEvaluationPort):
    def shadow_evaluate_lexicon(
        self,
        candidate: LexiconCandidateVersion,
        active_mappings: list[LexiconMapping],
        cases: list[ShadowCase],
        policy_version: str,
    ) -> ShadowReport:
        improved = 0
        regressed = 0
        irrelevant_sum = 0.0
        candidate_mappings = [
            mapping.model_copy(update={"status": MappingStatus.APPROVED})
            for mapping in candidate.mappings
        ]
        for case in cases:
            candidate_ids = _lookup(
                candidate_mappings,
                candidate.candidate_version,
                candidate.compatibility,
                case.query,
                case.locale,
                case.taxonomy_node_id,
                case.attribute_id,
            )
            baseline_ids = tuple(case.baseline_target_ids)
            expected = set(case.expected_target_ids)
            candidate_relevant = bool(expected.intersection(candidate_ids))
            baseline_relevant = bool(expected.intersection(baseline_ids))
            improved += int(candidate_relevant and not baseline_relevant)
            regressed += int(baseline_relevant and not candidate_relevant)
            if candidate_ids:
                irrelevant_sum += sum(
                    target_id not in expected for target_id in candidate_ids
                ) / len(candidate_ids)
        irrelevant_increase = irrelevant_sum / len(cases) if cases else 0.0
        failures: list[str] = []
        if regressed:
            failures.append("SHADOW_RECOVERY_REGRESSION")
        if irrelevant_increase > 0.05:
            failures.append("IRRELEVANT_RESULT_INCREASE")
        return ShadowReport(
            report_id=f"shadow-{sha256_hex([candidate.candidate_version, cases])[:16]}",
            candidate_version=candidate.candidate_version,
            cases_total=len(cases),
            cases_improved=improved,
            cases_regressed=regressed,
            irrelevant_result_increase=irrelevant_increase,
            passed=not failures,
            policy_version=policy_version,
            failure_codes=failures,
        )
