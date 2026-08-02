"""In-memory adapters used by contract tests; no database or provider setup."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from fkgrid.catalog_language.normalization import tokenize
from fkgrid.domain.catalog_language import (
    ActivationReceipt,
    ActivationRequest,
    CanonicalVocabularySnapshot,
    EvidenceGroup,
    EvidenceWindow,
    LexiconCandidateVersion,
    LexiconCompatibility,
    LexiconMapping,
    MappingStatus,
    RegressionCase,
    RegressionReport,
    ReviewDecision,
    ShadowCase,
    ShadowReport,
    SurfaceFormCluster,
    TargetCandidate,
)
from fkgrid.ports.catalog_language import (
    ActivationPort,
    ActiveLexiconPort,
    CanonicalVocabularyPort,
    ClockPort,
    EvidenceAggregationPort,
    IdGeneratorPort,
    RegressionPort,
    ReviewPort,
    ShadowEvaluationPort,
    TargetRetrievalPort,
    TraceSinkPort,
)


class FakeClock(ClockPort):
    def __init__(self, current: datetime | None = None) -> None:
        self.current = current or datetime(2026, 8, 2, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current


class SequentialIds(IdGeneratorPort):
    def __init__(self) -> None:
        self._counter = 0

    def new_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}-{self._counter:04d}"


class InMemoryTrace(TraceSinkPort):
    def __init__(self) -> None:
        self.events: list[object] = []

    def emit(self, event: object) -> None:
        self.events.append(event)


class FakeEvidenceAggregation(EvidenceAggregationPort):
    def __init__(self, groups: Iterable[EvidenceGroup] = ()) -> None:
        self.groups = list(groups)
        self.calls: list[tuple[EvidenceWindow, LexiconCompatibility]] = []

    def aggregate_query_gap_events(
        self, window: EvidenceWindow, compatibility: LexiconCompatibility
    ) -> list[EvidenceGroup]:
        self.calls.append((window, compatibility))
        return list(self.groups)


class FakeVocabulary(CanonicalVocabularyPort):
    def __init__(self, snapshot: CanonicalVocabularySnapshot) -> None:
        self.snapshot = snapshot
        self.calls: list[tuple[str, str, str]] = []

    def load_canonical_vocabulary(
        self, catalog_version: str, taxonomy_version: str, schema_version: str
    ) -> CanonicalVocabularySnapshot:
        self.calls.append((catalog_version, taxonomy_version, schema_version))
        return self.snapshot


class InMemoryTargetRetriever(TargetRetrievalPort):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def retrieve_candidate_targets(
        self,
        cluster: SurfaceFormCluster,
        vocabulary: CanonicalVocabularySnapshot,
        limit: int,
    ) -> list[TargetCandidate]:
        self.calls.append(cluster.normalized_form)
        term_tokens = set(tokenize(cluster.normalized_form))
        candidates: list[TargetCandidate] = []
        for item in vocabulary.items:
            if not item.active or item.scope.locale != cluster.locale:
                continue
            if cluster.taxonomy_node_id and item.scope.taxonomy_node_id not in {
                None,
                cluster.taxonomy_node_id,
            }:
                continue
            target_tokens = set(tokenize(item.normalized_name))
            overlap = len(term_tokens & target_tokens) / max(1, len(term_tokens | target_tokens))
            if overlap == 0.0 and item.scope.taxonomy_node_id != cluster.taxonomy_node_id:
                continue
            candidates.append(
                TargetCandidate(
                    target=item,
                    lexical_overlap=overlap,
                    scope_rank=3 if item.scope.taxonomy_node_id == cluster.taxonomy_node_id else 1,
                    evidence_refs=list(cluster.evidence_group_ids),
                )
            )
        candidates.sort(
            key=lambda candidate: (
                -candidate.scope_rank,
                -candidate.lexical_overlap,
                candidate.target.target_id,
            )
        )
        return candidates[:limit]


class FakeActiveLexicon(ActiveLexiconPort):
    def __init__(self, mappings: Iterable[LexiconMapping] = ()) -> None:
        self.mappings = list(mappings)
        self.calls = 0

    def load_active_mappings(
        self, lexicon_version: str, compatibility: LexiconCompatibility
    ) -> list[LexiconMapping]:
        self.calls += 1
        return [
            mapping
            for mapping in self.mappings
            if mapping.status == MappingStatus.APPROVED
            and mapping.compatibility == compatibility
            and mapping.compatibility.lexicon_version == lexicon_version
        ]


class PassingRegression(RegressionPort):
    def __init__(self, report: RegressionReport | None = None) -> None:
        self.report = report
        self.calls = 0

    def run_lexicon_regression(
        self,
        candidate: LexiconCandidateVersion,
        active_mappings: list[LexiconMapping],
        cases: list[RegressionCase],
        policy_version: str,
    ) -> RegressionReport:
        self.calls += 1
        return self.report or RegressionReport(
            report_id=f"regression-{candidate.candidate_version}",
            candidate_version=candidate.candidate_version,
            cases_total=len(cases),
            cases_passed=len(cases),
            expansion_precision=1.0,
            expected_recovery_rate=1.0,
            protected_regressions=0,
            scope_leakage=0,
            invalid_targets=0,
            passed=True,
            policy_version=policy_version,
        )


class PassingShadow(ShadowEvaluationPort):
    def __init__(self, report: ShadowReport | None = None) -> None:
        self.report = report
        self.calls = 0

    def shadow_evaluate_lexicon(
        self,
        candidate: LexiconCandidateVersion,
        active_mappings: list[LexiconMapping],
        cases: list[ShadowCase],
        policy_version: str,
    ) -> ShadowReport:
        self.calls += 1
        return self.report or ShadowReport(
            report_id=f"shadow-{candidate.candidate_version}",
            candidate_version=candidate.candidate_version,
            cases_total=len(cases),
            cases_improved=len(cases),
            cases_regressed=0,
            irrelevant_result_increase=0.0,
            passed=True,
            policy_version=policy_version,
        )


class ApprovingReview(ReviewPort):
    def __init__(self, approve: bool = True) -> None:
        self.approve = approve
        self.calls = 0

    def review_lexicon_diff(self, candidate: LexiconCandidateVersion) -> ReviewDecision:
        self.calls += 1
        mapping_ids = [mapping.mapping_id for mapping in candidate.mappings]
        return ReviewDecision(
            candidate_version=candidate.candidate_version,
            approved=self.approve,
            approved_mapping_ids=mapping_ids if self.approve else [],
            rejected_mapping_ids=[] if self.approve else mapping_ids,
            reviewer_id="reviewer-test",
            decision_reason_code="FAKE_APPROVAL" if self.approve else "FAKE_REJECTION",
            decided_at=datetime(2026, 8, 2, tzinfo=UTC),
        )


class CompareAndSwapActivation(ActivationPort):
    def __init__(self, active_version: str) -> None:
        self.active_version = active_version
        self.calls: list[ActivationRequest] = []

    def activate_lexicon_version(self, request: ActivationRequest) -> ActivationReceipt:
        self.calls.append(request)
        if request.expected_active_version != self.active_version:
            raise RuntimeError("STALE_ACTIVE_LEXICON_VERSION")
        self.active_version = request.candidate_version
        return ActivationReceipt(
            activated=True,
            active_lexicon_version=self.active_version,
            activated_mapping_ids=request.approved_mapping_ids,
            event_id=f"activation-{request.candidate_version}",
            activated_at=datetime(2026, 8, 2, tzinfo=UTC),
        )


class InMemoryLookup(ActiveLexiconPort):
    """Compatibility-only active mapping port used by runtime tests."""

    def __init__(self, mappings: Iterable[LexiconMapping]) -> None:
        self.mappings = list(mappings)

    def load_active_mappings(
        self, lexicon_version: str, compatibility: LexiconCompatibility
    ) -> list[LexiconMapping]:
        return [
            mapping
            for mapping in self.mappings
            if mapping.status == MappingStatus.APPROVED
            and mapping.compatibility == compatibility
            and mapping.compatibility.lexicon_version == lexicon_version
        ]
