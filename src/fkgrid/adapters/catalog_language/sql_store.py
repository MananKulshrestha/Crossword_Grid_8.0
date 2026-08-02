"""Thin row-mapping adapter for the SQL query catalog.

The database owner supplies a transaction-aware ``SqlExecutor``.  This adapter
does not open connections, run migrations, or decide transaction boundaries.
Activation must execute RETIRE_ACTIVE, PROMOTE_CANDIDATE, and ACTIVATE_CAS in
one short transaction and require the retire/promote row counts to be exactly one.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from fkgrid.adapters.catalog_language.sql import CatalogLanguageQueries, SqlQuery
from fkgrid.catalog_language.serialization import canonical_json_bytes, sha256_hex
from fkgrid.domain.catalog_language import (
    CanonicalVocabularySnapshot,
    EvidenceGroup,
    EvidenceWindow,
    LexiconCompatibility,
    LexiconMapping,
    RegressionReport,
    ReviewDecision,
    ShadowReport,
)
from fkgrid.ports.catalog_language import (
    ActiveLexiconPort,
    CanonicalVocabularyPort,
    EvidenceAggregationPort,
)


class SqlExecutor(Protocol):
    def fetch_all(
        self, sql: str, parameters: Mapping[str, object]
    ) -> Sequence[Mapping[str, object]]: ...

    def execute(self, sql: str, parameters: Mapping[str, object]) -> int: ...


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _json_text(value: object) -> str:
    return canonical_json_bytes(value).decode("utf-8")


class SqlCatalogLanguageRepository(
    EvidenceAggregationPort,
    CanonicalVocabularyPort,
    ActiveLexiconPort,
):
    """Port implementation that only depends on an injected SQL executor."""

    def __init__(self, executor: SqlExecutor) -> None:
        self.executor = executor

    @staticmethod
    def _rows(query: SqlQuery, executor: SqlExecutor, parameters: dict[str, object]):
        query.validate_parameters(parameters)
        return executor.fetch_all(query.sql, parameters)

    def aggregate_query_gap_events(
        self, window: EvidenceWindow, compatibility: LexiconCompatibility
    ) -> list[EvidenceGroup]:
        rows = self._rows(
            CatalogLanguageQueries.LOAD_EVIDENCE_GROUPS,
            self.executor,
            {
                "catalog_version": compatibility.catalog_version,
                "taxonomy_version": compatibility.taxonomy_version,
                "category_schema_version": compatibility.category_schema_version,
                "window_start": _iso(window.window_start),
                "window_end": _iso(window.window_end),
            },
        )
        groups: list[EvidenceGroup] = []
        for row in rows:
            record = dict(row)
            record["observed_surface_forms"] = json.loads(str(record.pop("surface_forms_json")))
            record["source_classes"] = json.loads(str(record.pop("source_classes_json")))
            record["privacy_safe"] = bool(record["privacy_safe"])
            groups.append(EvidenceGroup.model_validate_json(canonical_json_bytes(record)))
        return groups

    def load_canonical_vocabulary(
        self, catalog_version: str, taxonomy_version: str, schema_version: str
    ) -> CanonicalVocabularySnapshot:
        rows = self._rows(
            CatalogLanguageQueries.LOAD_VOCABULARY,
            self.executor,
            {
                "catalog_version": catalog_version,
                "taxonomy_version": taxonomy_version,
                "category_schema_version": schema_version,
            },
        )
        items = []
        for row in rows:
            record = dict(row)
            record["active"] = bool(record["active"])
            items.append(record)
        payload = {
            "catalog_version": catalog_version,
            "taxonomy_version": taxonomy_version,
            "category_schema_version": schema_version,
            "normalizer_version": rows[0].get("normalizer_version", "normalizer-v1")
            if rows
            else "normalizer-v1",
            "items": items,
        }
        payload["checksum"] = sha256_hex(payload["items"])
        return CanonicalVocabularySnapshot.model_validate_json(canonical_json_bytes(payload))

    def load_active_mappings(
        self, lexicon_version: str, compatibility: LexiconCompatibility
    ) -> list[LexiconMapping]:
        rows = self._rows(
            CatalogLanguageQueries.LOAD_ACTIVE_MAPPINGS,
            self.executor,
            {"lexicon_version": lexicon_version},
        )
        mappings: list[LexiconMapping] = []
        for row in rows:
            record = dict(row)
            record["scope"] = json.loads(str(record.pop("scope_json")))
            record["evidence_ids"] = json.loads(str(record.pop("evidence_ids_json")))
            record["compatibility"] = json.loads(str(record.pop("compatibility_json")))
            mappings.append(LexiconMapping.model_validate_json(canonical_json_bytes(record)))
        if any(mapping.compatibility != compatibility for mapping in mappings):
            raise ValueError("active lexicon rows contain mixed compatibility tuples")
        return mappings

    def execute_query(self, query: SqlQuery, parameters: dict[str, object]) -> int:
        query.validate_parameters(parameters)
        return self.executor.execute(query.sql, parameters)

    def record_regression(self, report: RegressionReport, created_at: datetime) -> int:
        report_json = canonical_json_bytes(report)
        return self.execute_query(
            CatalogLanguageQueries.INSERT_REGRESSION,
            {
                "regression_run_id": report.report_id,
                "candidate_version": report.candidate_version,
                "policy_version": report.policy_version,
                "report_json": report_json.decode("utf-8"),
                "report_checksum": sha256_hex(report),
                "passed": int(report.passed),
                "created_at": _iso(created_at),
            },
        )

    def record_shadow(self, report: ShadowReport, created_at: datetime) -> int:
        report_json = canonical_json_bytes(report)
        return self.execute_query(
            CatalogLanguageQueries.INSERT_SHADOW,
            {
                "shadow_run_id": report.report_id,
                "candidate_version": report.candidate_version,
                "policy_version": report.policy_version,
                "report_json": report_json.decode("utf-8"),
                "report_checksum": sha256_hex(report),
                "passed": int(report.passed),
                "created_at": _iso(created_at),
            },
        )

    def record_review(self, decision: ReviewDecision) -> int:
        return self.execute_query(
            CatalogLanguageQueries.RECORD_REVIEW,
            {
                "review_id": f"review-{decision.candidate_version}-{decision.reviewer_id}",
                "candidate_version": decision.candidate_version,
                "reviewer_id": decision.reviewer_id,
                "approved": int(decision.approved),
                "approved_mapping_ids_json": _json_text(decision.approved_mapping_ids),
                "rejected_mapping_ids_json": _json_text(decision.rejected_mapping_ids),
                "decision_reason_code": decision.decision_reason_code,
                "decided_at": _iso(decision.decided_at),
            },
        )
