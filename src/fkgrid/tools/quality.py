"""Tier-1 Quality Sentinel tools: qualify, evidence, classify, route, and audit."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from .contracts import (
    CatalogRecord,
    CatalogSnapshot,
    CompatibilityTuple,
    EvidencePacket,
    EvidenceRef,
    QualityAssessment,
    QualityCaseStatus,
    QualityQualification,
    QualityRoute,
    QualitySignal,
    ReviewDecision,
    ValidationResult,
)
from .determinism import canonical_hash, normalize_text, stable_id

_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d -]{7,}\d)(?!\d)")


def _redact(value: str) -> str:
    return _PHONE.sub("[REDACTED_PHONE]", _EMAIL.sub("[REDACTED_EMAIL]", value[:2000]))


def ingest_quality_signal(
    signal_type: str,
    entity_id: str,
    message: str,
    source: str = "system",
    occurred_at: datetime | None = None,
) -> QualitySignal:
    timestamp = occurred_at or datetime(1970, 1, 1, tzinfo=UTC)
    safe_message = _redact(message)
    signal_id = stable_id(
        "qs",
        {
            "signal_type": signal_type,
            "entity_id": entity_id,
            "message": safe_message,
            "occurred_at": timestamp,
        },
    )
    return QualitySignal(
        signal_id=signal_id,
        signal_type=signal_type,
        entity_id=entity_id,
        message=safe_message,
        occurred_at=timestamp,
        source=source,
    )


def qualify_signal_group(
    signals: Sequence[QualitySignal], minimum_count: int = 1
) -> QualityQualification:
    if not signals:
        return QualityQualification(qualified=False, group_key="empty", reason_codes=["NO_SIGNALS"])
    group_key = f"{signals[0].signal_type}:{signals[0].entity_id}"
    same_group = [
        signal for signal in signals if f"{signal.signal_type}:{signal.entity_id}" == group_key
    ]
    if len(same_group) < minimum_count:
        return QualityQualification(
            qualified=False, group_key=group_key, reason_codes=["BELOW_THRESHOLD"]
        )
    return QualityQualification(
        qualified=True, group_key=group_key, reason_codes=["QUALIFIED_SIGNAL_GROUP"]
    )


def get_catalog_snapshot(records: Iterable[CatalogRecord], version: str) -> CatalogSnapshot:
    materialized = sorted(list(records), key=lambda record: record.binding.offer_id)
    return CatalogSnapshot(
        version=version, records=materialized, checksum=canonical_hash(materialized, length=64)
    )


def assemble_case_evidence(
    case_id: str,
    signals: Sequence[QualitySignal],
    snapshot: CatalogSnapshot | None = None,
    compatibility: CompatibilityTuple | None = None,
) -> EvidencePacket:
    pinned = compatibility or CompatibilityTuple(
        catalog_version=snapshot.version if snapshot else CompatibilityTuple().catalog_version
    )
    refs = [
        EvidenceRef(
            evidence_id=signal.signal_id,
            source=signal.source,
            field="quality_signal",
            version=pinned.policy_version,
        )
        for signal in signals
    ]
    if snapshot:
        refs.append(
            EvidenceRef(
                evidence_id=stable_id("snap", snapshot.checksum),
                source="catalog_snapshot",
                field="catalog",
                version=snapshot.version,
            )
        )
    return EvidencePacket(
        case_id=case_id,
        signals=list(signals),
        catalog_snapshot_version=snapshot.version if snapshot else None,
        evidence_refs=refs,
    )


def classify_quality_issue(
    packet: EvidencePacket, allowed_classes: Sequence[str] | None = None
) -> QualityAssessment:
    text = normalize_text(" ".join(signal.message for signal in packet.signals))
    rules: list[tuple[str, tuple[str, ...], float]] = [
        ("WRONG_ITEM", ("wrong item", "different item", "not what i ordered"), 0.9),
        ("DAMAGED", ("broken", "damaged", "cracked"), 0.9),
        ("PRICE_MISMATCH", ("price mismatch", "charged more", "wrong price"), 0.85),
        ("MISSING_ATTRIBUTE", ("missing", "not listed", "attribute"), 0.65),
        ("DUPLICATE", ("duplicate", "same listing"), 0.8),
    ]
    permitted = set(
        allowed_classes or [rule[0] for rule in rules] + ["UNSUPPORTED", "INSUFFICIENT_EVIDENCE"]
    )
    for issue_class, keywords, confidence in rules:
        if issue_class in permitted and any(keyword in text for keyword in keywords):
            return QualityAssessment(
                issue_class=issue_class,
                confidence=confidence,
                evidence_refs=[],
                reason_codes=["DETERMINISTIC_KEYWORD_RULE"],
            )
    return QualityAssessment(
        issue_class="INSUFFICIENT_EVIDENCE", confidence=0.0, reason_codes=["NO_ALLOWLISTED_PATTERN"]
    )


def validate_quality_assessment(
    assessment: QualityAssessment, packet: EvidencePacket
) -> ValidationResult:
    errors: list[str] = []
    if assessment.confidence > 0 and not packet.signals:
        errors.append("EVIDENCE_REQUIRED")
    packet_ids = {ref.evidence_id for ref in packet.evidence_refs} | {
        signal.signal_id for signal in packet.signals
    }
    if any(ref.evidence_id not in packet_ids for ref in assessment.evidence_refs):
        errors.append("UNSUPPORTED_EVIDENCE_REFERENCE")
    if assessment.issue_class == "INSUFFICIENT_EVIDENCE" and assessment.confidence != 0:
        errors.append("INSUFFICIENT_EVIDENCE_MUST_BE_ZERO_CONFIDENCE")
    return ValidationResult(valid=not errors, errors=errors)


def route_quality_case(
    case_id: str, assessment: QualityAssessment, urgent: bool = False
) -> QualityRoute:
    if assessment.issue_class in {"WRONG_ITEM", "PRICE_MISMATCH", "MISSING_ATTRIBUTE", "DUPLICATE"}:
        destination = "CATALOG_REVIEW"
    elif assessment.issue_class == "INSUFFICIENT_EVIDENCE":
        destination = "NEEDS_REVIEW"
    else:
        destination = "SPECIALIST_REVIEW"
    priority = (
        "URGENT"
        if urgent
        else "HIGH"
        if assessment.confidence >= 0.85
        else "MEDIUM"
        if assessment.confidence >= 0.5
        else "LOW"
    )
    return QualityRoute(
        case_id=case_id,
        destination=destination,
        priority=priority,
        reason_codes=[assessment.issue_class],
    )


class QualityCaseStore:
    def __init__(self) -> None:
        self.decisions: dict[str, ReviewDecision] = {}
        self.statuses: dict[str, QualityCaseStatus] = {}

    def record_human_case_decision(self, case_id: str, decision: ReviewDecision) -> ReviewDecision:
        if decision.subject_id != case_id:
            raise ValueError("SUBJECT_MISMATCH")
        if decision.reviewer_id.startswith("system-"):
            raise ValueError("HUMAN_REVIEWER_REQUIRED")
        self.decisions[case_id] = decision
        self.statuses[case_id] = QualityCaseStatus(
            case_id=case_id, status="OPEN", reason=decision.reason
        )
        return decision

    def close_or_reopen_case(self, case_id: str, reopen: bool, reason: str) -> QualityCaseStatus:
        status = "REOPENED" if reopen else "CLOSED"
        result = QualityCaseStatus(case_id=case_id, status=status, reason=reason)
        self.statuses[case_id] = result
        return result


def record_human_case_decision(
    store: QualityCaseStore, case_id: str, decision: ReviewDecision
) -> ReviewDecision:
    return store.record_human_case_decision(case_id, decision)


def close_or_reopen_case(
    store: QualityCaseStore, case_id: str, reopen: bool, reason: str
) -> QualityCaseStatus:
    return store.close_or_reopen_case(case_id, reopen, reason)
