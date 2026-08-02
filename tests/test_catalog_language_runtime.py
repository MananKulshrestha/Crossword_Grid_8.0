from __future__ import annotations

from datetime import UTC, datetime

import pytest

from fkgrid.adapters.catalog_language.runtime import (
    DeterministicLexiconLookup,
    RuntimeLexiconSnapshot,
)
from fkgrid.domain.catalog_language import (
    EvidenceBand,
    ExpansionAction,
    LexiconCompatibility,
    LexiconLookupRequest,
    LexiconMapping,
    LexiconScope,
    MappingDirection,
    MappingKind,
    MappingStatus,
    TargetType,
)


def compatibility() -> LexiconCompatibility:
    return LexiconCompatibility(
        catalog_version="cat-1",
        taxonomy_version="tax-1",
        category_schema_version="schema-1",
        lexicon_version="lex-1",
        normalizer_version="normalizer-v1",
        mapping_schema_version="mapping-v1",
        rank_policy_version="rank-v1",
    )


def mapping(mapping_id: str, target_id: str, scope: LexiconScope) -> LexiconMapping:
    return LexiconMapping(
        mapping_id=mapping_id,
        surface_form="trainers",
        normalized_form="trainers",
        locale="en-IN",
        mapping_kind=MappingKind.SYNONYM,
        target_type=TargetType.TAXONOMY_NODE,
        target_id=target_id,
        scope=scope,
        direction=MappingDirection.QUERY_TO_CANONICAL,
        expansion_action=ExpansionAction.CANONICAL_SYNONYM,
        evidence_band=EvidenceBand.APPROVED_HIGH,
        origin="TIER_2_EVIDENCE",
        evidence_ids=["group-1"],
        status=MappingStatus.APPROVED,
        compatibility=compatibility(),
        created_at=datetime(2026, 8, 2, tzinfo=UTC),
    )


def test_lookup_prefers_exact_scope_and_returns_bounded_mapping() -> None:
    lookup = DeterministicLexiconLookup(
        RuntimeLexiconSnapshot(
            lexicon_version="lex-1",
            compatibility=compatibility(),
            mappings=(
                mapping("global", "global-shoes", LexiconScope()),
                mapping("footwear", "athletic-shoes", LexiconScope(taxonomy_node_id="footwear")),
            ),
        )
    )

    result = lookup.lookup_expansions(
        LexiconLookupRequest(
            term="Trainers",
            locale="en-IN",
            taxonomy_node_id="footwear",
            lexicon_version="lex-1",
        )
    )

    assert result.ambiguous is False
    assert result.mappings[0].mapping_id == "footwear"
    assert result.mappings[0].scope_match == "EXACT"


def test_lookup_abstains_on_incompatible_top_global_senses() -> None:
    lookup = DeterministicLexiconLookup(
        RuntimeLexiconSnapshot(
            lexicon_version="lex-1",
            compatibility=compatibility(),
            mappings=(
                mapping("global-a", "athletic-shoes", LexiconScope()),
                mapping("global-b", "formal-shoes", LexiconScope()),
            ),
        )
    )

    result = lookup.lookup_expansions(
        LexiconLookupRequest(term="trainers", locale="en-IN", lexicon_version="lex-1")
    )

    assert result.ambiguous is True
    assert result.mappings == []
    assert result.ambiguity_target_ids == ["athletic-shoes", "formal-shoes"]


def test_lookup_rejects_stale_lexicon_version() -> None:
    lookup = DeterministicLexiconLookup(
        RuntimeLexiconSnapshot(lexicon_version="lex-1", compatibility=compatibility(), mappings=())
    )

    result = lookup.lookup_expansions(
        LexiconLookupRequest(term="trainers", locale="en-IN", lexicon_version="lex-old")
    )

    assert result.compatibility_ok is False
    assert result.warnings == ["LEXICON_VERSION_MISMATCH"]


def test_lookup_does_not_expand_quoted_or_negated_input() -> None:
    lookup = DeterministicLexiconLookup(
        RuntimeLexiconSnapshot(
            lexicon_version="lex-1",
            compatibility=compatibility(),
            mappings=(
                mapping("footwear", "athletic-shoes", LexiconScope(taxonomy_node_id="footwear")),
            ),
        )
    )

    result = lookup.lookup_expansions(
        LexiconLookupRequest(
            term='"trainers"',
            locale="en-IN",
            taxonomy_node_id="footwear",
            lexicon_version="lex-1",
        )
    )

    assert result.mappings == []
    assert result.warnings == ["PROTECTED_OR_NEGATED_TERM"]


def test_lookup_rejects_mixed_compatibility_snapshot() -> None:
    mixed_mapping = mapping(
        "mixed",
        "athletic-shoes",
        LexiconScope(taxonomy_node_id="footwear"),
    ).model_copy(
        update={"compatibility": compatibility().model_copy(update={"lexicon_version": "lex-2"})}
    )

    with pytest.raises(ValueError, match="mixed compatibility"):
        DeterministicLexiconLookup(
            RuntimeLexiconSnapshot(
                lexicon_version="lex-1",
                compatibility=compatibility(),
                mappings=(mixed_mapping,),
            )
        )
