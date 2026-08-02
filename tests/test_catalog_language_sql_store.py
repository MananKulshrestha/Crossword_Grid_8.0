from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from fkgrid.adapters.catalog_language.sql_store import SqlCatalogLanguageRepository
from fkgrid.domain.catalog_language import LexiconCompatibility


class RecordingExecutor:
    def __init__(self, rows: Sequence[Mapping[str, object]] = ()) -> None:
        self.rows = list(rows)
        self.fetches: list[tuple[str, dict[str, object]]] = []
        self.executions: list[tuple[str, dict[str, object]]] = []

    def fetch_all(
        self, sql: str, parameters: Mapping[str, object]
    ) -> Sequence[Mapping[str, object]]:
        self.fetches.append((sql, dict(parameters)))
        return self.rows

    def execute(self, sql: str, parameters: Mapping[str, object]) -> int:
        self.executions.append((sql, dict(parameters)))
        return 1


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


def test_sql_repository_maps_approved_rows_without_opening_a_database() -> None:
    compat = compatibility()
    row = {
        "mapping_id": "mapping-1",
        "surface_form": "trainers",
        "normalized_form": "trainers",
        "locale": "en-IN",
        "mapping_kind": "SYNONYM",
        "target_type": "TAXONOMY_NODE",
        "target_id": "athletic-shoes",
        "scope_json": json.dumps({"locale": "en-IN", "taxonomy_node_id": "footwear"}),
        "direction": "QUERY_TO_CANONICAL",
        "expansion_action": "CANONICAL_SYNONYM",
        "compound_semantics": "NONE",
        "evidence_band": "APPROVED_HIGH",
        "origin": "TIER_2_EVIDENCE",
        "evidence_ids_json": json.dumps(["group-1"]),
        "status": "APPROVED",
        "compatibility_json": compat.model_dump_json(),
        "created_at": "2026-08-02T00:00:00Z",
        "reviewed_at": None,
    }
    executor = RecordingExecutor([row])
    repository = SqlCatalogLanguageRepository(executor)

    mappings = repository.load_active_mappings("lex-1", compat)

    assert mappings[0].target_id == "athletic-shoes"
    assert executor.fetches[0][1] == {"lexicon_version": "lex-1"}
