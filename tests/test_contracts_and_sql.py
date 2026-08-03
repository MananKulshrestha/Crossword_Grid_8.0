from __future__ import annotations

import json
import sqlite3
import unittest

from fkgrid.query_recovery.adapters.in_memory import FixedClock
from fkgrid.query_recovery.adapters.sqlite import SqliteRecoveryRepository, install_recovery_schema
from fkgrid.query_recovery.domain import (
    CompatibilityTuple,
    RecoveryEvent,
    RecoveryOutcome,
    HardConstraint,
    QueryState,
)
from fkgrid.query_recovery.validation import canonical_hash, canonical_json, hard_filter_hash


class ContractAndSqlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.compatibility = CompatibilityTuple(
            contract_schema_version="recovery-contract-v1",
            catalog_version="catalog-1",
            index_version="index-1",
            taxonomy_version="taxonomy-1",
            category_schema_version="schema-1",
            lexicon_version="lexicon-1",
            rank_policy_version="rank-1",
            gate_policy_version="gate-1",
            recovery_policy_version="recovery-policy-v1",
            recovery_prompt_version="recovery-v2",
            recovery_model_alias="fake",
        )
        self.state = QueryState(
            hard_constraints=[
                HardConstraint(
                    field_id="price",
                    operator="LTE",
                    values=[150000],
                    provenance_turn_id="turn-a",
                )
            ],
            query_terms=["shirt"],
            taxonomy_scope_id="shirts",
            catalog_version="catalog-1",
            index_version="index-1",
            taxonomy_version="taxonomy-1",
            category_schema_version="schema-1",
            lexicon_version="lexicon-1",
        )

    def test_hard_filter_hash_excludes_provenance_but_changes_on_semantics(self) -> None:
        equivalent = self.state.model_copy(deep=True)
        equivalent.hard_constraints[0].provenance_turn_id = "turn-b"
        self.assertEqual(hard_filter_hash(self.state), hard_filter_hash(equivalent))
        changed = self.state.model_copy(deep=True)
        changed.hard_constraints[0].values = [160000]
        self.assertNotEqual(hard_filter_hash(self.state), hard_filter_hash(changed))

    def test_canonical_json_rejects_non_finite_numbers(self) -> None:
        with self.assertRaises(ValueError):
            canonical_json({"value": float("nan")})
        self.assertEqual(len(canonical_hash({"b": 2, "a": 1})), 64)

    def test_sql_queries_are_version_and_scope_pinned(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        install_recovery_schema(connection)
        connection.execute(
            """
            CREATE TABLE lexicon_mappings (
                mapping_id TEXT PRIMARY KEY, normalized_phrase TEXT NOT NULL,
                original_phrase TEXT NOT NULL, canonical_target_id TEXT NOT NULL,
                canonical_label TEXT NOT NULL, concept_type TEXT NOT NULL,
                mapping_type TEXT NOT NULL, expansion_action TEXT NOT NULL,
                locale TEXT NOT NULL, taxonomy_scope_id TEXT,
                attribute_id TEXT, lexicon_version TEXT NOT NULL,
                catalog_version TEXT NOT NULL, taxonomy_version TEXT NOT NULL,
                category_schema_version TEXT NOT NULL, evidence_band TEXT NOT NULL,
                priority INTEGER NOT NULL, status TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO lexicon_mappings VALUES
            ('map-1', 'trainers', 'trainers', 'footwear', 'Footwear', 'TAXONOMY',
             'ALIAS', 'CANONICAL_SYNONYM', 'en-IN', 'shirts', NULL,
             'lexicon-1', 'catalog-1', 'taxonomy-1', 'schema-1', 'APPROVED_HIGH', 10, 'APPROVED')
            """
        )
        connection.execute(
            """
            INSERT INTO recovery_allowed_concepts VALUES
            ('catalog-1', 'taxonomy-1', 'schema-1', 'lexicon-1', 'footwear', 'TAXONOMY',
             'Footwear', 'footwear', 'shirts', NULL, NULL, 'en-IN', 1, '2026-08-02T00:00:00Z')
            """
        )
        connection.commit()
        repository = SqliteRecoveryRepository(connection)
        expansions = repository.lookup(
            normalized_terms=["trainers"],
            query_state=self.state,
            compatibility=self.compatibility,
            limit=3,
        )
        self.assertEqual([item.mapping_id for item in expansions], ["map-1"])
        concepts = repository.get_constraints(
            query_state=self.state,
            unknown_terms=["trainers"],
            compatibility=self.compatibility,
            limit=20,
        )
        self.assertEqual([item.concept_id for item in concepts], ["footwear"])

    def test_recovery_event_insert_is_append_only_and_sanitized(self) -> None:
        connection = sqlite3.connect(":memory:")
        install_recovery_schema(connection)
        repository = SqliteRecoveryRepository(connection)
        event = RecoveryEvent(
            event_id="event-1",
            session_id="session-1",
            turn_id="turn-1",
            trace_id="trace-1",
            outcome=RecoveryOutcome.RECOVERED_TIER2,
            terminal_state="ANSWERED_WITH_GROUNDED_RESULTS",
            trigger_reasons=[],
            hard_filter_hash_before="a" * 64,
            hard_filter_hash_after="a" * 64,
            baseline_run_id="baseline",
            generative_run_id="generative",
            planner_called=True,
            planner_validation_codes=["OK"],
            comparator_decisions=[],
            retrieval_run_count=2,
            budget_ms=1800,
            budget_used_ms=120,
            model_prompt_version="recovery-v2",
            model_alias="fake",
            planner_input_hash="b" * 64,
            planner_token_count=12,
            planner_latency_ms=7,
            allowed_concept_count=2,
            baseline_eligible_count=1,
            baseline_popular_result_count=0,
            selected_eligible_count=5,
            selected_popular_result_count=3,
            compatibility=self.compatibility,
            warnings=["safe-warning"],
            created_at=FixedClock().now_utc(),
        )
        repository.record(event)
        row = connection.execute(
            "SELECT outcome, planner_called, retrieval_run_count, planner_token_count, "
            "planner_latency_ms, allowed_concept_count, compatibility_json FROM recovery_events"
        ).fetchone()
        self.assertEqual(row[0], "RECOVERED_TIER2")
        self.assertEqual(row[1], 1)
        self.assertEqual(row[2], 2)
        self.assertEqual(row[3:6], (12, 7, 2))
        self.assertEqual(json.loads(row[6])["catalog_version"], "catalog-1")
        counts = connection.execute(
            "SELECT baseline_eligible_count, baseline_popular_result_count, "
            "selected_eligible_count, selected_popular_result_count "
            "FROM recovery_events WHERE event_id = ?",
            ("event-1",),
        ).fetchone()
        self.assertEqual(tuple(counts), (1, 0, 5, 3))
        with self.assertRaises(sqlite3.IntegrityError):
            repository.record(event)


if __name__ == "__main__":
    unittest.main()
