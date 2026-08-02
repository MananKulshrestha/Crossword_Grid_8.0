"""Provider/database/artifact seams for Catalog Language Tier 2.

No implementation in this module imports SQLAlchemy, a model SDK, or a web
framework.  The eventual database owner can implement these protocols against
SQLite/PostgreSQL without changing workflow or runtime contracts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from fkgrid.domain.catalog_language import (
    ActivationReceipt,
    ActivationRequest,
    CanonicalVocabularySnapshot,
    CriticDraft,
    EvidenceGroup,
    EvidenceWindow,
    LexiconCandidateVersion,
    LexiconCompatibility,
    LexiconLookupRequest,
    LexiconLookupResult,
    LexiconMapping,
    MappingDraft,
    ModelCallRequest,
    ModelCallResponse,
    RegressionCase,
    RegressionReport,
    ReviewDecision,
    ShadowCase,
    ShadowReport,
    SurfaceFormCluster,
    TargetCandidate,
)


class ClockPort(Protocol):
    def now(self) -> datetime: ...


class IdGeneratorPort(Protocol):
    def new_id(self, prefix: str) -> str: ...


class EvidenceAggregationPort(Protocol):
    def aggregate_query_gap_events(
        self, window: EvidenceWindow, compatibility: LexiconCompatibility
    ) -> list[EvidenceGroup]: ...


class CanonicalVocabularyPort(Protocol):
    def load_canonical_vocabulary(
        self, catalog_version: str, taxonomy_version: str, schema_version: str
    ) -> CanonicalVocabularySnapshot: ...


class TargetRetrievalPort(Protocol):
    def retrieve_candidate_targets(
        self,
        cluster: SurfaceFormCluster,
        vocabulary: CanonicalVocabularySnapshot,
        limit: int,
    ) -> list[TargetCandidate]: ...


class ActiveLexiconPort(Protocol):
    def load_active_mappings(
        self, lexicon_version: str, compatibility: LexiconCompatibility
    ) -> list[LexiconMapping]: ...


class CatalogLanguageModelPort(Protocol):
    def propose_canonical_mapping(
        self, request: ModelCallRequest
    ) -> ModelCallResponse: ...

    def critique_mapping(self, request: ModelCallRequest) -> ModelCallResponse: ...


class RegressionPort(Protocol):
    def run_lexicon_regression(
        self,
        candidate: LexiconCandidateVersion,
        active_mappings: list[LexiconMapping],
        cases: list[RegressionCase],
        policy_version: str,
    ) -> RegressionReport: ...


class ShadowEvaluationPort(Protocol):
    def shadow_evaluate_lexicon(
        self,
        candidate: LexiconCandidateVersion,
        active_mappings: list[LexiconMapping],
        cases: list[ShadowCase],
        policy_version: str,
    ) -> ShadowReport: ...


class ReviewPort(Protocol):
    def review_lexicon_diff(self, candidate: LexiconCandidateVersion) -> ReviewDecision: ...


class ActivationPort(Protocol):
    def activate_lexicon_version(self, request: ActivationRequest) -> ActivationReceipt: ...


class TraceSinkPort(Protocol):
    def emit(self, event: object) -> None: ...


class LexiconLookupPort(Protocol):
    def lookup_expansions(self, request: LexiconLookupRequest) -> LexiconLookupResult: ...

