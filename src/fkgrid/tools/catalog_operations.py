"""Deterministic Catalog Operations tools with review and atomic publication."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .contracts import (
    AttributeExtraction,
    CatalogDiff,
    CatalogRecord,
    CatalogVersionReceipt,
    ChangeRisk,
    CompatibilityTuple,
    EvidenceRef,
    IndexSmokeReport,
    IngestBatch,
    NormalizedProduct,
    ProductBinding,
    ReviewDecision,
    ReviewRoute,
    TaxonomyClassification,
    ValidationResult,
)
from .determinism import canonical_hash, normalize_text, stable_id, unique_sorted


def ingest_batch(source: str, payload: Sequence[Mapping[str, Any]]) -> IngestBatch:
    records = [dict(record) for record in payload]
    payload_hash = canonical_hash({"source": source, "records": records}, length=64)
    return IngestBatch(
        batch_id=stable_id("batch", {"source": source, "payload_hash": payload_hash}),
        source=source,
        records=records,
        payload_hash=payload_hash,
    )


def resolve_product_identity(
    raw_record: Mapping[str, Any], source: str = "catalog"
) -> ProductBinding:
    product_key = raw_record.get("product_id") or stable_id(
        "product",
        {
            "source": source,
            "title": raw_record.get("title", ""),
            "brand": raw_record.get("brand", ""),
            "category_id": raw_record.get("category_id", ""),
        },
    )
    sku_key = raw_record.get("sku_id") or stable_id(
        "sku", {"product_id": product_key, "variant": raw_record.get("variant", "default")}
    )
    offer_key = raw_record.get("offer_id") or stable_id(
        "offer", {"sku_id": sku_key, "seller": raw_record.get("seller_id", "prototype")}
    )
    return ProductBinding(
        product_id=str(product_key),
        sku_id=str(sku_key),
        offer_id=str(offer_key),
        variant_id=str(raw_record["variant_id"])
        if raw_record.get("variant_id") is not None
        else None,
    )


def _price(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = value.replace(",", "").replace("₹", "").replace("rs.", "").strip()
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def normalize_product(
    raw_record: Mapping[str, Any],
    source: str = "catalog",
    compatibility: CompatibilityTuple | None = None,
) -> NormalizedProduct:
    pinned = compatibility or CompatibilityTuple()
    binding = resolve_product_identity(raw_record, source)
    raw_attributes = raw_record.get("attributes", {})
    attributes = {
        str(key): str(value) for key, value in dict(raw_attributes).items() if value is not None
    }
    availability = str(raw_record.get("availability", "UNKNOWN")).upper()
    if availability not in {"AVAILABLE", "UNAVAILABLE", "UNKNOWN"}:
        availability = "UNKNOWN"
    evidence = raw_record.get("evidence_refs", [])
    evidence_refs = [
        item if isinstance(item, EvidenceRef) else EvidenceRef(**item) for item in evidence
    ]
    if not evidence_refs:
        evidence_refs = [
            EvidenceRef(
                evidence_id=stable_id("ev", {"binding": binding, "source": source}),
                source=source,
                field="raw_record",
                version=pinned.catalog_version,
            )
        ]
    record = CatalogRecord(
        binding=binding,
        title=str(raw_record.get("title", "")).strip(),
        category_id=str(raw_record.get("category_id", "")).strip(),
        brand=str(raw_record["brand"]).strip() if raw_record.get("brand") else None,
        description=str(raw_record.get("description", "")).strip(),
        attributes=attributes,
        price=_price(raw_record.get("price")),
        availability=availability,
        eligible=bool(raw_record.get("eligible", True)),
        aliases=[
            str(alias).strip() for alias in raw_record.get("aliases", []) if str(alias).strip()
        ],
        evidence_refs=evidence_refs,
    )
    warnings: list[str] = []
    if record.price is None:
        warnings.append("PRICE_UNKNOWN")
    if not record.brand:
        warnings.append("BRAND_UNKNOWN")
    return NormalizedProduct(
        record=record,
        source_record_id=str(raw_record["source_record_id"])
        if raw_record.get("source_record_id")
        else None,
        warnings=warnings,
    )


def validate_product(
    candidate: NormalizedProduct | CatalogRecord, category_schema: Mapping[str, Any] | None = None
) -> ValidationResult:
    record = candidate.record if isinstance(candidate, NormalizedProduct) else candidate
    errors: list[str] = []
    warnings: list[str] = []
    if not record.title:
        errors.append("TITLE_REQUIRED")
    if not record.category_id:
        errors.append("CATEGORY_REQUIRED")
    if not all((record.binding.product_id, record.binding.sku_id, record.binding.offer_id)):
        errors.append("CANONICAL_ID_TUPLE_REQUIRED")
    if record.price is not None and record.price < 0:
        errors.append("NEGATIVE_PRICE")
    if record.price is None:
        warnings.append("PRICE_UNKNOWN")
    if category_schema:
        required = set(category_schema.get("required_attributes", []))
        missing = sorted(required - set(record.attributes))
        if missing:
            errors.extend(f"ATTRIBUTE_REQUIRED:{key}" for key in missing)
    return ValidationResult(valid=not errors, errors=errors, warnings=warnings)


def get_category_schema(
    category_id: str, schemas: Mapping[str, Mapping[str, Any]] | None = None
) -> dict[str, Any]:
    if schemas and category_id in schemas:
        return dict(schemas[category_id])
    return {
        "category_id": category_id,
        "required_attributes": [],
        "supported_attributes": [],
        "allowed_values": {},
    }


def extract_supported_attributes(
    candidate: NormalizedProduct | CatalogRecord | Mapping[str, Any], schema: Mapping[str, Any]
) -> AttributeExtraction:
    if isinstance(candidate, NormalizedProduct):
        source = candidate.record.attributes
    elif isinstance(candidate, CatalogRecord):
        source = candidate.attributes
    else:
        source = dict(candidate.get("attributes", {}))
    supported = set(schema.get("supported_attributes", schema.get("allowed_values", {}).keys()))
    if not supported:
        return AttributeExtraction(
            attributes={str(key): str(value) for key, value in source.items()},
            unsupported_fields=[],
        )
    attributes = {str(key): str(value) for key, value in source.items() if key in supported}
    unsupported = sorted(str(key) for key in source if key not in supported)
    return AttributeExtraction(attributes=attributes, unsupported_fields=unsupported)


def get_allowed_taxonomy_children(
    parent_id: str, taxonomy: Mapping[str, Sequence[str]]
) -> list[str]:
    return unique_sorted(str(value) for value in taxonomy.get(parent_id, []))


def classify_taxonomy(
    candidate: NormalizedProduct | CatalogRecord | Mapping[str, Any],
    allowed_children: Sequence[str],
) -> TaxonomyClassification:
    if isinstance(candidate, (NormalizedProduct, CatalogRecord)):
        category = (
            candidate.record.category_id
            if isinstance(candidate, NormalizedProduct)
            else candidate.category_id
        )
        title = (
            candidate.record.title if isinstance(candidate, NormalizedProduct) else candidate.title
        )
    else:
        category = str(candidate.get("category_id", ""))
        title = str(candidate.get("title", ""))
    allowed = list(allowed_children)
    if category in allowed:
        return TaxonomyClassification(
            status="CLASSIFIED",
            category_id=category,
            confidence=1.0,
            reason="EXACT_CANDIDATE_CATEGORY",
        )
    normalized_title = normalize_text(title)
    matches = [child for child in allowed if normalize_text(child) in normalized_title]
    if len(matches) == 1:
        return TaxonomyClassification(
            status="CLASSIFIED",
            category_id=matches[0],
            confidence=0.75,
            reason="UNIQUE_TITLE_MATCH",
        )
    return TaxonomyClassification(
        status="ABSTAIN", confidence=0.0, reason="NO_UNIQUE_ALLOWED_CHILD"
    )


def compare_with_current(candidate: CatalogRecord, current: CatalogRecord | None) -> CatalogDiff:
    if current is None:
        changes = {"record": {"old": None, "new": candidate.model_dump(mode="json")}}
    else:
        old = current.model_dump(mode="json")
        new = candidate.model_dump(mode="json")
        changes = {
            key: {"old": old.get(key), "new": new.get(key)}
            for key in sorted(set(old) | set(new))
            if old.get(key) != new.get(key)
        }
    return CatalogDiff(product_id=candidate.binding.product_id, changes=changes)


def build_catalog_diff(candidate: CatalogRecord, current: CatalogRecord | None) -> CatalogDiff:
    return compare_with_current(candidate, current)


def score_change_risk(diff: CatalogDiff) -> ChangeRisk:
    if not diff.changes:
        return ChangeRisk(level="LOW", score=0.0, reasons=["NO_CHANGE"])
    keys = set(diff.changes)
    reasons: list[str] = []
    score = 0.1
    if "binding" in keys:
        return ChangeRisk(level="BLOCKED", score=1.0, reasons=["CANONICAL_ID_CHANGE"])
    if "category_id" in keys:
        score += 0.45
        reasons.append("TAXONOMY_CHANGE")
    if "attributes" in keys:
        score += 0.2
        reasons.append("ATTRIBUTE_CHANGE")
    if "price" in keys or "availability" in keys:
        score += 0.15
        reasons.append("COMMERCE_FIELD_CHANGE")
    score = min(1.0, score)
    level = "HIGH" if score >= 0.7 else "MEDIUM" if score >= 0.35 else "LOW"
    return ChangeRisk(level=level, score=score, reasons=reasons or ["CONTENT_CHANGE"])


def route_review_case(subject_id: str, risk: ChangeRisk) -> ReviewRoute:
    if risk.level == "BLOCKED":
        return ReviewRoute(
            subject_id=subject_id,
            queue="BLOCKED_CHANGE",
            priority="URGENT",
            reason="BLOCKED_CHANGE_REQUIRES_SPECIALIST",
        )
    if risk.level == "HIGH":
        return ReviewRoute(
            subject_id=subject_id, queue="SPECIALIST", priority="HIGH", reason="HIGH_RISK_CHANGE"
        )
    if risk.level == "MEDIUM":
        return ReviewRoute(
            subject_id=subject_id, queue="STANDARD", priority="MEDIUM", reason="MEDIUM_RISK_CHANGE"
        )
    return ReviewRoute(
        subject_id=subject_id, queue="STANDARD", priority="LOW", reason="LOW_RISK_CHANGE"
    )


class CatalogVersionStore:
    """Versioned records with human-gated atomic publication and rollback."""

    def __init__(
        self, active_version: str | None = None, active_records: Iterable[CatalogRecord] = ()
    ) -> None:
        self._versions: dict[str, list[CatalogRecord]] = {}
        self._checksums: dict[str, str] = {}
        self._decisions: dict[str, ReviewDecision] = {}
        self.active_version = active_version
        if active_version is not None:
            self._versions[active_version] = sorted(
                list(active_records), key=lambda item: item.binding.offer_id
            )
            self._checksums[active_version] = canonical_hash(
                self._versions[active_version], length=64
            )

    def record_review_decision(self, subject_id: str, decision: ReviewDecision) -> ReviewDecision:
        if decision.subject_id != subject_id:
            raise ValueError("SUBJECT_MISMATCH")
        self._decisions[subject_id] = decision
        return decision

    def publish_catalog_version(
        self, version: str, records: Iterable[CatalogRecord], decision: ReviewDecision
    ) -> CatalogVersionReceipt:
        if decision.subject_id != version or decision.decision != "APPROVED":
            return CatalogVersionReceipt(
                version=version,
                status="NEEDS_REVIEW",
                checksum="",
                active_version=self.active_version,
            )
        materialized = sorted(list(records), key=lambda item: item.binding.offer_id)
        validations = [validate_product(record) for record in materialized]
        if any(not result.valid for result in validations):
            return CatalogVersionReceipt(
                version=version, status="REJECTED", checksum="", active_version=self.active_version
            )
        checksum = canonical_hash(materialized, length=64)
        existing = self._checksums.get(version)
        if existing and existing != checksum:
            return CatalogVersionReceipt(
                version=version,
                status="REJECTED",
                checksum=checksum,
                active_version=self.active_version,
            )
        self._versions[version] = materialized
        self._checksums[version] = checksum
        self._decisions[version] = decision
        self.active_version = version
        return CatalogVersionReceipt(
            version=version, status="PUBLISHED", checksum=checksum, active_version=version
        )

    def rollback_catalog_version(self, version: str) -> CatalogVersionReceipt:
        if version not in self._versions:
            return CatalogVersionReceipt(
                version=version, status="REJECTED", checksum="", active_version=self.active_version
            )
        self.active_version = version
        return CatalogVersionReceipt(
            version=version,
            status="ROLLED_BACK",
            checksum=self._checksums[version],
            active_version=version,
        )

    def records(self, version: str | None = None) -> list[CatalogRecord]:
        selected = version or self.active_version
        return list(self._versions.get(selected or "", []))


def record_review_decision(
    store: CatalogVersionStore, subject_id: str, decision: ReviewDecision
) -> ReviewDecision:
    return store.record_review_decision(subject_id, decision)


def publish_catalog_version(
    store: CatalogVersionStore,
    version: str,
    records: Iterable[CatalogRecord],
    decision: ReviewDecision,
) -> CatalogVersionReceipt:
    return store.publish_catalog_version(version, records, decision)


def rollback_catalog_version(store: CatalogVersionStore, version: str) -> CatalogVersionReceipt:
    return store.rollback_catalog_version(version)


def build_and_smoke_test_index(
    version: str, records: Iterable[CatalogRecord], index_version: str | None = None
) -> IndexSmokeReport:
    materialized = sorted(list(records), key=lambda item: item.binding.offer_id)
    index = index_version or stable_id(
        "index", {"catalog_version": version, "records": materialized}
    )
    checksum = canonical_hash(
        {"catalog_version": version, "index_version": index, "records": materialized}, length=64
    )
    reasons = (
        [] if all(validate_product(record).valid for record in materialized) else ["INVALID_RECORD"]
    )
    return IndexSmokeReport(
        status="PASS" if not reasons else "FAIL",
        catalog_version=version,
        index_version=index,
        checksum=checksum,
        record_count=len(materialized),
        reasons=reasons,
    )
