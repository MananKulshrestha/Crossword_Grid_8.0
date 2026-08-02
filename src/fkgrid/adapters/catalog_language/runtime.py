"""Deterministic bounded runtime lookup over an approved immutable mapping set."""

from __future__ import annotations

from dataclasses import dataclass

from fkgrid.catalog_language.normalization import normalize_surface_form
from fkgrid.domain.catalog_language import (
    ExpansionResult,
    LexiconCompatibility,
    LexiconLookupRequest,
    LexiconLookupResult,
    LexiconMapping,
    MappingStatus,
)
from fkgrid.ports.catalog_language import LexiconLookupPort


@dataclass(frozen=True, slots=True)
class RuntimeLexiconSnapshot:
    lexicon_version: str
    compatibility: LexiconCompatibility
    mappings: tuple[LexiconMapping, ...]


class DeterministicLexiconLookup(LexiconLookupPort):
    """Lookup only approved, compatible mappings; no model/history/retrieval calls."""

    def __init__(self, snapshot: RuntimeLexiconSnapshot) -> None:
        self.snapshot = snapshot
        self._by_key: dict[tuple[str, str], tuple[LexiconMapping, ...]] = {}
        buckets: dict[tuple[str, str], list[LexiconMapping]] = {}
        for mapping in snapshot.mappings:
            if mapping.status == MappingStatus.APPROVED:
                buckets.setdefault((mapping.locale, mapping.normalized_form), []).append(mapping)
        self._by_key = {
            key: tuple(sorted(value, key=self._mapping_sort_key)) for key, value in buckets.items()
        }

    @staticmethod
    def _mapping_sort_key(mapping: LexiconMapping) -> tuple[int, int, str]:
        scope_rank = 0 if mapping.scope.taxonomy_node_id or mapping.scope.attribute_id else 1
        evidence_rank = {"APPROVED_HIGH": 0, "MEDIUM": 1, "LOW": 2}[mapping.evidence_band.value]
        return scope_rank, evidence_rank, mapping.mapping_id

    @staticmethod
    def _scope_match(mapping: LexiconMapping, request: LexiconLookupRequest) -> str | None:
        if mapping.locale != request.locale:
            return None
        scope = mapping.scope
        if scope.attribute_id and scope.attribute_id == request.attribute_id:
            return "EXACT"
        if scope.taxonomy_node_id and scope.taxonomy_node_id == request.taxonomy_node_id:
            return "EXACT"
        if (
            scope.parent_taxonomy_node_id
            and scope.parent_taxonomy_node_id == request.parent_taxonomy_node_id
        ):
            return "ANCESTOR"
        if scope.taxonomy_node_id is None and scope.attribute_id is None:
            return "GLOBAL"
        return None

    def lookup_expansions(self, request: LexiconLookupRequest) -> LexiconLookupResult:
        normalized = normalize_surface_form(request.term, request.locale)
        if request.lexicon_version != self.snapshot.lexicon_version:
            return LexiconLookupResult(
                normalized_term=normalized,
                compatibility_ok=False,
                warnings=["LEXICON_VERSION_MISMATCH"],
            )
        candidates: list[tuple[str, LexiconMapping]] = []
        for mapping in self._by_key.get((request.locale, normalized), ()):
            scope_match = self._scope_match(mapping, request)
            if scope_match is not None:
                candidates.append((scope_match, mapping))
        if not candidates:
            return LexiconLookupResult(normalized_term=normalized)
        scope_rank = {"EXACT": 0, "ANCESTOR": 1, "GLOBAL": 2}
        candidates.sort(key=lambda item: (scope_rank[item[0]], self._mapping_sort_key(item[1])))
        top_scope = scope_rank[candidates[0][0]]
        top = [item for item in candidates if scope_rank[item[0]] == top_scope]
        target_ids = sorted({mapping.target_id for _, mapping in top})
        if len(target_ids) > 1:
            return LexiconLookupResult(
                normalized_term=normalized,
                ambiguous=True,
                ambiguity_target_ids=target_ids,
                warnings=["INCOMPATIBLE_TOP_SENSES"],
            )
        results = [
            ExpansionResult(
                mapping_id=mapping.mapping_id,
                target_type=mapping.target_type,
                target_id=mapping.target_id,
                expansion_action=mapping.expansion_action,
                scope_match=scope,
                interpretation_label=mapping.target_id,
                evidence_band=mapping.evidence_band,
            )
            for scope, mapping in candidates[: request.max_mappings]
        ]
        return LexiconLookupResult(normalized_term=normalized, mappings=results)
