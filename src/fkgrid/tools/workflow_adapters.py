"""Explicit adapters for specialist-worktree port shapes.

These adapters intentionally accept ``Any`` at the edge: the four existing
worktrees own different Pydantic classes.  Inputs are converted into the
shared deterministic contracts, and outputs are projected to the exact field
names used by the specialist ports.  A caller may pass a local Pydantic model
class to validate the projection without importing that worktree.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .catalog_language import (
    aggregate_query_gap_events,
    load_canonical_vocabulary,
    lookup_approved_expansions,
    retrieve_candidate_targets,
    run_lexicon_regression,
    shadow_evaluate_lexicon,
)
from .catalog_operations import (
    CatalogVersionStore,
    build_and_smoke_test_index,
    build_catalog_diff,
    compare_with_current,
    publish_catalog_version,
    record_review_decision,
    rollback_catalog_version,
)
from .contracts import (
    CandidateTerm,
    CatalogRecord,
    CompatibilityTuple,
    LexiconCandidate,
    MappingProposal,
    QualityAssessment,
    QualitySignal,
    ReviewDecision,
    ValidationResult,
)
from .determinism import canonical_hash, normalize_text, stable_id
from .interop import payload_of, validate_with_worktree_model
from .quality import (
    assemble_case_evidence,
    classify_quality_issue,
    close_or_reopen_case,
    get_catalog_snapshot,
    ingest_quality_signal,
    qualify_signal_group,
    record_human_case_decision,
    route_quality_case,
    validate_quality_assessment,
)


def _project(payload: Mapping[str, Any], model_type: type[Any] | None) -> Any:
    return validate_with_worktree_model(payload, model_type) if model_type else dict(payload)


@dataclass(frozen=True)
class LanguageModelTypes:
    evidence_group: type[Any] | None = None
    vocabulary_snapshot: type[Any] | None = None
    target_candidate: type[Any] | None = None
    lexicon_mapping: type[Any] | None = None
    regression_report: type[Any] | None = None
    shadow_report: type[Any] | None = None
    review_decision: type[Any] | None = None
    activation_receipt: type[Any] | None = None
    lookup_result: type[Any] | None = None
    artifact_manifest: type[Any] | None = None


class CatalogLanguagePortAdapter:
    """Matches the query-expansion worktree's offline/runtime ports."""

    def __init__(
        self,
        records: Iterable[CatalogRecord] = (),
        events: Iterable[Mapping[str, object]] = (),
        models: LanguageModelTypes | None = None,
        lexicon_store: Any | None = None,
    ) -> None:
        from .catalog_language import LexiconStore

        self.records = list(records)
        self.events = [dict(event) for event in events]
        self.models = models or LanguageModelTypes()
        self.lexicon_store = lexicon_store or LexiconStore()

    def aggregate_query_gap_events(self, window: Any, compatibility: Any) -> list[Any]:
        del window, compatibility
        terms = aggregate_query_gap_events(self.events, minimum_count=1)
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        result = []
        for term in terms:
            result.append(
                _project(
                    {
                        "group_id": stable_id("evidence-group", term.model_dump(mode="json")),
                        "normalized_term": term.normalized_form,
                        "observed_surface_forms": [term.surface_form],
                        "locale": term.locale,
                        "taxonomy_node_id": None,
                        "attribute_id": None,
                        "support_count": term.count,
                        "distinct_source_groups": max(1, len(term.source_ids)),
                        "source_classes": ["ZERO_RESULT"],
                        "source_concentration": 1.0,
                        "recovery_success_count": 0,
                        "contradiction_count": 0,
                        "first_observed_at": epoch,
                        "last_observed_at": epoch,
                        "privacy_safe": True,
                    },
                    self.models.evidence_group,
                )
            )
        return result

    def load_canonical_vocabulary(
        self, catalog_version: str, taxonomy_version: str, schema_version: str
    ) -> Any:
        vocabulary = load_canonical_vocabulary(self.records, catalog_version)
        items: list[dict[str, Any]] = []
        for category in vocabulary.categories:
            items.append(
                {
                    "target_type": "TAXONOMY_NODE",
                    "target_id": category,
                    "canonical_name": category,
                    "normalized_name": normalize_text(category),
                    "scope": {"locale": "en-IN"},
                    "active": True,
                }
            )
        for brand in vocabulary.brands:
            if brand:
                items.append(
                    {
                        "target_type": "BRAND",
                        "target_id": brand,
                        "canonical_name": brand,
                        "normalized_name": normalize_text(brand),
                        "scope": {"locale": "en-IN"},
                        "active": True,
                    }
                )
        for attribute, values in vocabulary.attributes.items():
            for value in values:
                items.append(
                    {
                        "target_type": "CONTROLLED_VALUE",
                        "target_id": f"{attribute}:{value}",
                        "canonical_name": value,
                        "normalized_name": normalize_text(value),
                        "scope": {"locale": "en-IN", "attribute_id": attribute},
                        "active": True,
                    }
                )
        return _project(
            {
                "catalog_version": catalog_version,
                "taxonomy_version": taxonomy_version,
                "category_schema_version": schema_version,
                "normalizer_version": "deterministic-normalizer-v1",
                "items": items,
                "checksum": canonical_hash(items, length=64),
            },
            self.models.vocabulary_snapshot,
        )

    def retrieve_candidate_targets(self, cluster: Any, vocabulary: Any, limit: int) -> list[Any]:
        cluster_data = payload_of(cluster)
        term = CandidateTerm(
            surface_form=str(
                cluster_data.get("surface_form", cluster_data.get("normalized_form", ""))
            ),
            normalized_form=str(cluster_data.get("normalized_form", "")),
            count=max(1, int(cluster_data.get("support_count", cluster_data.get("count", 1)))),
        )
        vocabulary_data = payload_of(vocabulary)
        from .contracts import Vocabulary

        shared_vocabulary = Vocabulary(
            catalog_version=str(vocabulary_data.get("catalog_version", "catalog-demo-v1")),
            categories=[
                str(item.get("target_id"))
                for item in vocabulary_data.get("items", [])
                if item.get("target_type") == "TAXONOMY_NODE"
            ],
            brands=[
                str(item.get("target_id"))
                for item in vocabulary_data.get("items", [])
                if item.get("target_type") == "BRAND"
            ],
            attributes={},
        )
        target_ids = retrieve_candidate_targets(term, shared_vocabulary, limit)
        result = []
        for target_id in target_ids:
            target_type = "TAXONOMY_NODE" if target_id in shared_vocabulary.categories else "BRAND"
            result.append(
                _project(
                    {
                        "target": {
                            "target_type": target_type,
                            "target_id": target_id,
                            "canonical_name": target_id,
                            "normalized_name": normalize_text(target_id),
                            "scope": {"locale": "en-IN"},
                            "active": True,
                        },
                        "lexical_overlap": 1.0
                        if normalize_text(target_id) == term.normalized_form
                        else 0.5,
                        "scope_rank": 0,
                        "evidence_refs": [],
                    },
                    self.models.target_candidate,
                )
            )
        return result

    def load_active_mappings(self, lexicon_version: str, compatibility: Any) -> list[Any]:
        pinned = (
            compatibility
            if isinstance(compatibility, CompatibilityTuple)
            else CompatibilityTuple(lexicon_version=lexicon_version)
        )
        candidate = self.lexicon_store.active
        if (
            candidate is None
            or candidate.version != lexicon_version
            or pinned.lexicon_version != lexicon_version
        ):
            return []
        return [
            _project(
                {
                    "mapping_id": stable_id("mapping", mapping.model_dump(mode="json")),
                    "surface_form": mapping.surface_form,
                    "normalized_form": mapping.normalized_form,
                    "locale": "en-IN",
                    "mapping_kind": "SYNONYM",
                    "target_type": "CONTROLLED_VALUE",
                    "target_id": mapping.target_id or "unknown",
                    "scope": {"locale": "en-IN"},
                    "direction": "QUERY_TO_CANONICAL",
                    "expansion_action": "CANONICAL_SYNONYM",
                    "compound_semantics": "NONE",
                    "evidence_band": "APPROVED_HIGH",
                    "origin": "TIER_1_CURATED",
                    "evidence_ids": [ref.evidence_id for ref in mapping.evidence_refs]
                    or ["curated"],
                    "status": "APPROVED",
                    "compatibility": {
                        "catalog_version": pinned.catalog_version,
                        "taxonomy_version": pinned.taxonomy_version,
                        "category_schema_version": pinned.category_schema_version,
                        "lexicon_version": lexicon_version,
                        "normalizer_version": "deterministic-normalizer-v1",
                        "mapping_schema_version": "mapping-v1",
                        "rank_policy_version": pinned.rank_policy_version,
                    },
                    "created_at": datetime(1970, 1, 1, tzinfo=UTC),
                    "reviewed_at": datetime(1970, 1, 1, tzinfo=UTC),
                },
                self.models.lexicon_mapping,
            )
            for mapping in candidate.mappings
            if mapping.status == "PROPOSED"
        ]

    @staticmethod
    def _shared_candidate(candidate: Any) -> LexiconCandidate:
        if isinstance(candidate, LexiconCandidate):
            return candidate
        data = payload_of(candidate)
        mappings = []
        for item in data.get("mappings", []):
            mapping = payload_of(item)
            status = (
                "PROPOSED"
                if str(mapping.get("status", "PROPOSED")) in {"PROPOSED", "APPROVED", "DRAFT"}
                else "ABSTAIN"
            )
            mappings.append(
                MappingProposal(
                    surface_form=str(mapping.get("surface_form", "")),
                    normalized_form=str(mapping.get("normalized_form", "")),
                    target_id=mapping.get("target_id"),
                    canonical_term=mapping.get("canonical_term", mapping.get("target_id")),
                    status=status,
                    evidence_refs=[],
                )
            )
        return LexiconCandidate(
            version=str(data.get("candidate_version", data.get("version", "lexicon-candidate"))),
            catalog_version=str(
                data.get("compatibility", {}).get(
                    "catalog_version", data.get("catalog_version", "catalog-demo-v1")
                )
            ),
            mappings=mappings,
        )

    def run_lexicon_regression(
        self, candidate: Any, active_mappings: list[Any], cases: list[Any], policy_version: str
    ) -> Any:
        del active_mappings
        report = run_lexicon_regression(
            self._shared_candidate(candidate), [payload_of(case) for case in cases]
        )
        data = {
            "report_id": stable_id("lexicon-regression", report.model_dump(mode="json")),
            "candidate_version": report.candidate_version,
            "cases_total": report.cases,
            "cases_passed": round(report.precision * report.cases),
            "expansion_precision": report.precision,
            "expected_recovery_rate": report.precision,
            "protected_regressions": report.changed_protected_cases,
            "scope_leakage": report.leakage_count,
            "invalid_targets": 0,
            "passed": report.status == "PASS",
            "policy_version": policy_version,
            "failure_codes": report.reasons,
        }
        return _project(data, self.models.regression_report)

    def shadow_evaluate_lexicon(
        self, candidate: Any, active_mappings: list[Any], cases: list[Any], policy_version: str
    ) -> Any:
        del active_mappings
        shared = self._shared_candidate(candidate)
        raw = shadow_evaluate_lexicon(
            shared, [str(payload_of(case).get("query", case)) for case in cases]
        )
        total = int(raw["replay_cases"])
        data = {
            "report_id": stable_id("lexicon-shadow", raw),
            "candidate_version": shared.version,
            "cases_total": total,
            "cases_improved": int(raw["expansion_hits"]),
            "cases_regressed": 0,
            "irrelevant_result_increase": 0.0,
            "passed": int(raw["leakage_count"]) == 0,
            "policy_version": policy_version,
            "failure_codes": [],
        }
        return _project(data, self.models.shadow_report)

    def review_lexicon_diff(self, candidate: Any) -> Any:
        shared = self._shared_candidate(candidate)
        data = {
            "candidate_version": shared.version,
            "approved": False,
            "approved_mapping_ids": [],
            "rejected_mapping_ids": [],
            "reviewer_id": "system-pending-human",
            "decision_reason_code": "HUMAN_APPROVAL_REQUIRED",
            "decided_at": datetime(1970, 1, 1, tzinfo=UTC),
        }
        return _project(data, self.models.review_decision)

    def activate_lexicon_version(self, request: Any) -> Any:
        data = payload_of(request)
        version = str(data.get("candidate_version", data.get("version", "")))
        try:
            candidate = self.lexicon_store.activate_lexicon_version(version)
            receipt = {
                "activated": True,
                "active_lexicon_version": candidate.version,
                "activated_mapping_ids": [],
                "event_id": stable_id("lexicon-activation", candidate.version),
                "activated_at": datetime(1970, 1, 1, tzinfo=UTC),
            }
        except ValueError:
            receipt = {
                "activated": False,
                "active_lexicon_version": str(data.get("expected_active_version", "none")),
                "activated_mapping_ids": [],
                "event_id": stable_id("lexicon-activation-rejected", version),
                "activated_at": datetime(1970, 1, 1, tzinfo=UTC),
            }
        return _project(receipt, self.models.activation_receipt)

    def lookup_expansions(self, request: Any) -> Any:
        data = payload_of(request)
        term = str(data.get("term", ""))
        version = str(data.get("lexicon_version", ""))
        pinned = CompatibilityTuple(lexicon_version=version)
        expansions = lookup_approved_expansions([term], self.lexicon_store, pinned)
        mappings = [
            {
                "mapping_id": stable_id("mapping", expansion.model_dump(mode="json")),
                "target_type": "CONTROLLED_VALUE",
                "target_id": expansion.target_id,
                "expansion_action": "CANONICAL_SYNONYM",
                "scope_match": "GLOBAL",
                "interpretation_label": expansion.canonical_term,
                "evidence_band": "APPROVED_HIGH",
            }
            for expansion in expansions[: int(data.get("max_mappings", 3))]
        ]
        result = {
            "normalized_term": normalize_text(term),
            "mappings": mappings,
            "ambiguous": len(mappings) > 1,
            "ambiguity_target_ids": sorted({str(item["target_id"]) for item in mappings}),
            "compatibility_ok": bool(
                self.lexicon_store.active and self.lexicon_store.active.version == version
            ),
            "warnings": [],
        }
        return _project(result, self.models.lookup_result)

    def write_candidate(self, candidate: Any, regression: Any, shadow: Any) -> Any:
        shared = self._shared_candidate(candidate)
        self.lexicon_store.write_candidate(shared)
        manifest = {
            "candidate_version": shared.version,
            "compatibility": {
                "catalog_version": shared.catalog_version,
                "taxonomy_version": "taxonomy-demo-v1",
                "category_schema_version": "category-schema-demo-v1",
                "lexicon_version": shared.version,
                "normalizer_version": "deterministic-normalizer-v1",
                "mapping_schema_version": "mapping-v1",
                "rank_policy_version": "ranking-demo-v1",
            },
            "candidate_checksum": canonical_hash(shared, length=64),
            "mapping_count": max(1, len(shared.mappings)),
            "mappings_checksum": canonical_hash(shared.mappings, length=64),
            "regression_report_checksum": canonical_hash(payload_of(regression), length=64),
            "shadow_report_checksum": canonical_hash(payload_of(shadow), length=64),
            "manifest_checksum": canonical_hash(
                {
                    "candidate": shared.version,
                    "regression": payload_of(regression),
                    "shadow": payload_of(shadow),
                },
                length=64,
            ),
            "created_at": datetime(1970, 1, 1, tzinfo=UTC),
        }
        return _project(manifest, self.models.artifact_manifest)


class CatalogOperationsPortAdapter:
    """Small port facade for the catalog publication workflow."""

    def __init__(self, store: CatalogVersionStore | None = None) -> None:
        self.store = store or CatalogVersionStore()

    def compare_with_current(self, candidate: CatalogRecord, current: CatalogRecord | None) -> Any:
        return compare_with_current(candidate, current)

    def build_catalog_diff(self, candidate: CatalogRecord, current: CatalogRecord | None) -> Any:
        return build_catalog_diff(candidate, current)

    def record_review_decision(self, subject_id: str, decision: ReviewDecision) -> Any:
        return record_review_decision(self.store, subject_id, decision)

    def publish_catalog_version(
        self, version: str, records: Iterable[CatalogRecord], decision: ReviewDecision
    ) -> Any:
        return publish_catalog_version(self.store, version, records, decision)

    def rollback_catalog_version(self, version: str) -> Any:
        return rollback_catalog_version(self.store, version)

    def build_and_smoke_test_index(
        self, version: str, records: Iterable[CatalogRecord], index_version: str
    ) -> Any:
        return build_and_smoke_test_index(version, records, index_version)


class QualityPortAdapter:
    """Facade matching the Quality Sentinel workflow's deterministic stages."""

    def ingest_quality_signal(
        self,
        signal_type: str,
        entity_id: str,
        message: str,
        source: str = "system",
        occurred_at: datetime | None = None,
    ) -> QualitySignal:
        return ingest_quality_signal(signal_type, entity_id, message, source, occurred_at)

    def qualify_signal_group(self, signals: Sequence[QualitySignal], minimum_count: int = 1) -> Any:
        return qualify_signal_group(signals, minimum_count)

    def get_catalog_snapshot(self, records: Iterable[CatalogRecord], version: str) -> Any:
        return get_catalog_snapshot(records, version)

    def assemble_case_evidence(
        self,
        case_id: str,
        signals: Sequence[QualitySignal],
        snapshot: Any = None,
        compatibility: CompatibilityTuple | None = None,
    ) -> Any:
        return assemble_case_evidence(case_id, signals, snapshot, compatibility)

    def classify_quality_issue(
        self, packet: Any, allowed_classes: Sequence[str] | None = None
    ) -> QualityAssessment:
        return classify_quality_issue(packet, allowed_classes)

    def validate_quality_assessment(
        self, assessment: QualityAssessment, packet: Any
    ) -> ValidationResult:
        return validate_quality_assessment(assessment, packet)

    def route_quality_case(
        self, case_id: str, assessment: QualityAssessment, urgent: bool = False
    ) -> Any:
        return route_quality_case(case_id, assessment, urgent)

    def record_human_case_decision(self, store: Any, case_id: str, decision: ReviewDecision) -> Any:
        return record_human_case_decision(store, case_id, decision)

    def close_or_reopen_case(self, store: Any, case_id: str, reopen: bool, reason: str) -> Any:
        return close_or_reopen_case(store, case_id, reopen, reason)
