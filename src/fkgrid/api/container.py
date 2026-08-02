"""Dependency assembly for the FastAPI catalog-language API.

The API accepts a fully assembled container so production callers can inject
database, retrieval, model, review, and activation adapters without changing
HTTP contracts.  The default container is a deterministic demo only; it never
connects to a database or external provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from fkgrid.adapters.catalog_language.fakes import (
    ApprovingReview,
    CompareAndSwapActivation,
    FakeActiveLexicon,
    FakeClock,
    FakeEvidenceAggregation,
    FakeVocabulary,
    InMemoryTargetRetriever,
    InMemoryTrace,
    PassingRegression,
    PassingShadow,
    SequentialIds,
)
from fkgrid.adapters.catalog_language.runtime import (
    DeterministicLexiconLookup,
    RuntimeLexiconSnapshot,
)
from fkgrid.adapters.model.fake import FakeCatalogLanguageModel
from fkgrid.domain.catalog_language import (
    CanonicalVocabularySnapshot,
    EvidenceBand,
    EvidenceGroup,
    EvidenceSourceClass,
    ExpansionAction,
    LexiconCompatibility,
    LexiconMapping,
    LexiconScope,
    MappingDirection,
    MappingKind,
    MappingStatus,
    TargetType,
    VocabularyItem,
)
from fkgrid.ports.catalog_language import LexiconLookupPort
from fkgrid.workflows.catalog_language import CatalogLanguageTier2Workflow

ApiMode = Literal["demo", "configured"]


@dataclass(frozen=True, slots=True)
class CatalogLanguageApiContainer:
    """Resolved dependencies for API routes.

    ``mode=\"configured\"`` is intended for the DB/provider owner’s assembly
    code.  The API does not inspect or construct those adapters itself.
    """

    workflow: CatalogLanguageTier2Workflow
    lookup: LexiconLookupPort
    mode: ApiMode
    ready: bool = True
    readiness_detail: str = "configured adapters are available"


def demo_compatibility() -> LexiconCompatibility:
    return LexiconCompatibility(
        catalog_version="cat-demo-1",
        taxonomy_version="tax-demo-1",
        category_schema_version="schema-demo-1",
        lexicon_version="lex-demo-1",
        normalizer_version="normalizer-v1",
        mapping_schema_version="mapping-v1",
        rank_policy_version="rank-v1",
    )


def demo_vocabulary(compatibility: LexiconCompatibility) -> CanonicalVocabularySnapshot:
    return CanonicalVocabularySnapshot(
        catalog_version=compatibility.catalog_version,
        taxonomy_version=compatibility.taxonomy_version,
        category_schema_version=compatibility.category_schema_version,
        normalizer_version=compatibility.normalizer_version,
        checksum="d" * 64,
        items=[
            VocabularyItem(
                target_type=TargetType.TAXONOMY_NODE,
                target_id="athletic-shoes",
                canonical_name="Athletic trainers",
                normalized_name="athletic trainers",
                scope=LexiconScope(locale="en-IN", taxonomy_node_id="footwear"),
            ),
            VocabularyItem(
                target_type=TargetType.TAXONOMY_NODE,
                target_id="running-shoes",
                canonical_name="Running shoes",
                normalized_name="running shoes",
                scope=LexiconScope(locale="en-IN", taxonomy_node_id="footwear"),
            ),
        ],
    )


def demo_evidence() -> EvidenceGroup:
    return EvidenceGroup(
        group_id="demo-group-trainers",
        normalized_term="trainers",
        observed_surface_forms=["trainers", "trainer"],
        locale="en-IN",
        taxonomy_node_id="footwear",
        support_count=8,
        distinct_source_groups=8,
        source_classes=[EvidenceSourceClass.ZERO_RESULT, EvidenceSourceClass.RECOVERY],
        source_concentration=0.25,
        recovery_success_count=4,
        contradiction_count=0,
        first_observed_at=datetime(2026, 7, 2, tzinfo=UTC),
        last_observed_at=datetime(2026, 7, 30, tzinfo=UTC),
    )


def demo_mapping(compatibility: LexiconCompatibility) -> LexiconMapping:
    return LexiconMapping(
        mapping_id="demo-mapping-trainers",
        surface_form="trainers",
        normalized_form="trainers",
        locale="en-IN",
        mapping_kind=MappingKind.SYNONYM,
        target_type=TargetType.TAXONOMY_NODE,
        target_id="athletic-shoes",
        scope=LexiconScope(locale="en-IN", taxonomy_node_id="footwear"),
        direction=MappingDirection.QUERY_TO_CANONICAL,
        expansion_action=ExpansionAction.CANONICAL_SYNONYM,
        evidence_band=EvidenceBand.APPROVED_HIGH,
        origin="TIER_1_CURATED",
        evidence_ids=["demo-curated-evidence"],
        status=MappingStatus.APPROVED,
        compatibility=compatibility,
        created_at=datetime(2026, 8, 2, tzinfo=UTC),
    )


def create_demo_container() -> CatalogLanguageApiContainer:
    """Assemble a safe, deterministic container for local Swagger exploration."""

    compatibility = demo_compatibility()
    vocabulary = demo_vocabulary(compatibility)
    workflow = CatalogLanguageTier2Workflow(
        evidence=FakeEvidenceAggregation([demo_evidence()]),
        vocabulary=FakeVocabulary(vocabulary),
        targets=InMemoryTargetRetriever(),
        active_lexicon=FakeActiveLexicon(),
        model=FakeCatalogLanguageModel(),
        regression=PassingRegression(),
        shadow=PassingShadow(),
        review=ApprovingReview(),
        activation=CompareAndSwapActivation(compatibility.lexicon_version),
        clock=FakeClock(datetime(2026, 8, 2, tzinfo=UTC)),
        ids=SequentialIds(),
        trace_sink=InMemoryTrace(),
    )
    lookup = DeterministicLexiconLookup(
        RuntimeLexiconSnapshot(
            lexicon_version=compatibility.lexicon_version,
            compatibility=compatibility,
            mappings=(demo_mapping(compatibility),),
        )
    )
    return CatalogLanguageApiContainer(
        workflow=workflow,
        lookup=lookup,
        mode="demo",
        readiness_detail="deterministic demo adapters active; no database or provider configured",
    )
