"""SQLite implementation of the recovery-owned DB ports.

The SQL is intentionally plain and parameterized so the future SQLAlchemy/
PostgreSQL owner can port the statements without changing domain contracts.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..domain import (
    ApprovedExpansion,
    CompatibilityTuple,
    ConceptType,
    ExpansionAction,
    EvidenceBand,
    MappingType,
    RecoveryConstraint,
    RecoveryEvent,
    QueryState,
)
from ..validation import canonical_json, normalize_term


LEXICON_LOOKUP_SQL = """
SELECT
    m.mapping_id,
    m.normalized_phrase,
    m.original_phrase,
    m.canonical_target_id,
    m.canonical_label,
    m.concept_type,
    m.mapping_type,
    m.expansion_action,
    m.locale,
    m.taxonomy_scope_id,
    m.attribute_id,
    m.lexicon_version,
    m.catalog_version,
    m.taxonomy_version,
    m.category_schema_version,
    m.evidence_band,
    m.priority,
    m.status
FROM lexicon_mappings AS m
WHERE m.normalized_phrase IN ({placeholders})
  AND m.locale = ?
  AND m.status = 'APPROVED'
  AND m.lexicon_version = ?
  AND m.catalog_version = ?
  AND m.taxonomy_version = ?
  AND m.category_schema_version = ?
  AND (m.taxonomy_scope_id IS NULL OR m.taxonomy_scope_id = ?)
ORDER BY
  CASE WHEN m.taxonomy_scope_id = ? THEN 0 ELSE 1 END,
  m.priority DESC,
  m.mapping_id ASC
LIMIT ?
"""


RECOVERY_CONSTRAINTS_SQL = """
SELECT
    c.concept_id,
    c.concept_type,
    c.label,
    c.canonical_term,
    c.taxonomy_scope_id,
    c.attribute_id,
    c.value_id,
    c.locale,
    c.catalog_version,
    c.taxonomy_version,
    c.category_schema_version,
    c.lexicon_version,
    c.active
FROM recovery_allowed_concepts AS c
WHERE c.locale = ?
  AND c.catalog_version = ?
  AND c.taxonomy_version = ?
  AND c.category_schema_version = ?
  AND c.lexicon_version = ?
  AND c.active = 1
  AND (c.taxonomy_scope_id IS NULL OR c.taxonomy_scope_id = ?)
ORDER BY c.concept_type ASC, c.label ASC, c.concept_id ASC
LIMIT ?
"""


RECOVERY_EVENT_INSERT_SQL = """
INSERT INTO recovery_events (
    event_id, session_id, turn_id, trace_id, outcome, terminal_state,
    trigger_reasons_json, original_terms_json, hard_filter_hash_before, hard_filter_hash_after,
    baseline_run_id, direct_run_id, generative_run_id, mapping_ids_json,
    planner_action, planner_called, planner_validation_codes_json,
    comparator_decisions_json, tool_calls_json, retrieval_run_count, hard_filter_mutation_count,
    added_latency_ms, budget_ms, budget_used_ms, model_prompt_version,
    model_alias, compatibility_json, cache_hit, warnings_json, created_at
) VALUES (
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
)
"""


def _json_list(value: Any) -> str:
    return canonical_json(list(value))


class SqliteRecoveryRepository:
    """Read/write adapter for the recovery-owned tables.

    The caller owns connection lifecycle and transaction boundaries. `record`
    performs one insert; the application transaction owner may call it inside
    the same transaction as the authoritative turn/outbox commit.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def lookup(
        self,
        *,
        normalized_terms: list[str],
        query_state: QueryState,
        compatibility: CompatibilityTuple,
        limit: int,
    ) -> list[ApprovedExpansion]:
        if not normalized_terms:
            return []
        terms = [normalize_term(term) for term in normalized_terms]
        placeholders = ",".join("?" for _ in terms)
        statement = LEXICON_LOOKUP_SQL.format(placeholders=placeholders)
        scope = query_state.taxonomy_scope_id
        parameters: list[Any] = [
            *terms,
            "en-IN",
            compatibility.lexicon_version,
            compatibility.catalog_version,
            compatibility.taxonomy_version,
            compatibility.category_schema_version,
            scope,
            scope,
            limit,
        ]
        rows = self.connection.execute(statement, parameters).fetchall()
        return [
            ApprovedExpansion(
                mapping_id=row[0],
                normalized_form=row[1],
                original_form=row[2],
                canonical_target_id=row[3],
                canonical_label=row[4],
                concept_type=ConceptType(row[5]),
                mapping_type=MappingType(row[6]),
                expansion_action=ExpansionAction(row[7]),
                locale=row[8],
                taxonomy_scope_id=row[9],
                attribute_id=row[10],
                lexicon_version=row[11],
                catalog_version=row[12],
                taxonomy_version=row[13],
                category_schema_version=row[14],
                evidence_band=EvidenceBand(row[15]),
                priority=int(row[16]),
                status=row[17],
            )
            for row in rows
        ]

    def get_constraints(
        self,
        *,
        query_state: QueryState,
        unknown_terms: list[str],
        compatibility: CompatibilityTuple,
        limit: int,
    ) -> list[RecoveryConstraint]:
        scope = query_state.taxonomy_scope_id
        rows = self.connection.execute(
            RECOVERY_CONSTRAINTS_SQL,
            (
                "en-IN",
                compatibility.catalog_version,
                compatibility.taxonomy_version,
                compatibility.category_schema_version,
                compatibility.lexicon_version,
                scope,
                limit,
            ),
        ).fetchall()
        return [
            RecoveryConstraint(
                concept_id=row[0],
                concept_type=ConceptType(row[1]),
                label=row[2],
                canonical_term=row[3],
                taxonomy_scope_id=row[4],
                attribute_id=row[5],
                value_id=row[6],
                locale=row[7],
                catalog_version=row[8],
                taxonomy_version=row[9],
                category_schema_version=row[10],
                lexicon_version=row[11],
                active=bool(row[12]),
            )
            for row in rows
        ]

    def record(self, event: RecoveryEvent) -> None:
        self.connection.execute(
            RECOVERY_EVENT_INSERT_SQL,
            (
                event.event_id,
                event.session_id,
                event.turn_id,
                event.trace_id,
                event.outcome.value,
                event.terminal_state,
                _json_list(reason.value for reason in event.trigger_reasons),
                _json_list(event.original_terms),
                event.hard_filter_hash_before,
                event.hard_filter_hash_after,
                event.baseline_run_id,
                event.direct_run_id,
                event.generative_run_id,
                _json_list(event.mapping_ids),
                event.planner_action.value if event.planner_action else None,
                int(event.planner_called),
                _json_list(event.planner_validation_codes),
                _json_list(decision.value for decision in event.comparator_decisions),
                _json_list(call.model_dump(mode="json") for call in event.tool_calls),
                event.retrieval_run_count,
                event.hard_filter_mutation_count,
                event.added_latency_ms,
                event.budget_ms,
                event.budget_used_ms,
                event.model_prompt_version,
                event.model_alias,
                canonical_json(event.compatibility),
                int(event.cache_hit),
                _json_list(event.warnings),
                event.created_at.isoformat().replace("+00:00", "Z"),
            ),
        )


def install_recovery_schema(connection: sqlite3.Connection) -> None:
    """Install only recovery-owned tables for adapter/contract tests.

    Production migrations should be run by the repository owner. This helper is
    intentionally limited to the SQL in `migrations/versions/0001_query_recovery.sql`.
    """

    from pathlib import Path

    migration = Path(__file__).resolve().parents[4] / "migrations" / "versions" / "0001_query_recovery.sql"
    connection.executescript(migration.read_text(encoding="utf-8"))
