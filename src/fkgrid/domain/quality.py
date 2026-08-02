"""Strict, provider-neutral contracts for the Tier 1 Quality Sentinel.

The sentinel is an operations-review workflow.  It can qualify, assess, and
route a case, but it deliberately has no catalog-write, ranking, refund, seller
sanction, or enforcement capability.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    """Base class for trust-boundary models."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True)


class SignalType(StrEnum):
    SAFETY_URGENT = "SAFETY_URGENT"
    OBJECTIVE_INCORRECT = "OBJECTIVE_INCORRECT"
    FULFILLMENT_MISMATCH = "FULFILLMENT_MISMATCH"
    SUBJECTIVE_QUALITY = "SUBJECTIVE_QUALITY"
    ABUSE_SPAM = "ABUSE_SPAM"
    UNKNOWN = "UNKNOWN"


class SourceType(StrEnum):
    EXPLICIT_REPORT = "EXPLICIT_REPORT"
    POOR_REVIEW = "POOR_REVIEW"
    SYSTEM_SIGNAL = "SYSTEM_SIGNAL"
    FULFILLMENT_EVENT = "FULFILLMENT_EVENT"


class Severity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    URGENT = "URGENT"


class SignalStatus(StrEnum):
    RECEIVED = "RECEIVED"
    REDACTED = "REDACTED"
    GROUPED = "GROUPED"
    STORED = "STORED"
    INTAKE_CORRECTION = "INTAKE_CORRECTION"


class QualificationStatus(StrEnum):
    QUALIFIED = "QUALIFIED"
    NOT_QUALIFIED = "NOT_QUALIFIED"
    INTAKE_CORRECTION = "INTAKE_CORRECTION"


class CaseStatus(StrEnum):
    OPEN = "OPEN"
    TRIAGED = "TRIAGED"
    AWAITING_EVIDENCE = "AWAITING_EVIDENCE"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    ROUTED = "ROUTED"
    DECIDED = "DECIDED"
    CLOSED = "CLOSED"
    REOPENED = "REOPENED"


class IssueClass(StrEnum):
    PRODUCT_DEFECT = "PRODUCT_DEFECT"
    LISTING_CONTENT_MISMATCH = "LISTING_CONTENT_MISMATCH"
    COUNTERFEIT_OR_AUTHENTICITY = "COUNTERFEIT_OR_AUTHENTICITY"
    SAFETY = "SAFETY"
    FULFILLMENT_OR_PACKAGING = "FULFILLMENT_OR_PACKAGING"
    SELLER_OR_SERVICE = "SELLER_OR_SERVICE"
    SUBJECTIVE_PREFERENCE = "SUBJECTIVE_PREFERENCE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class CaseDecision(StrEnum):
    NO_ACTION = "NO_ACTION"
    CORRECT_CATALOG = "CORRECT_CATALOG"
    ESCALATE_SAFETY = "ESCALATE_SAFETY"
    ESCALATE_FULFILLMENT = "ESCALATE_FULFILLMENT"
    REQUEST_MORE_EVIDENCE = "REQUEST_MORE_EVIDENCE"


class RoutePriority(StrEnum):
    URGENT = "URGENT"
    HIGH = "HIGH"
    NORMAL = "NORMAL"
    LOW = "LOW"


class EvidenceKind(StrEnum):
    STRUCTURED_EVENT = "STRUCTURED_EVENT"
    PROSE_EXCERPT = "PROSE_EXCERPT"
    CATALOG_SNAPSHOT = "CATALOG_SNAPSHOT"


class ToolStatus(StrEnum):
    OK = "OK"
    NO_CHANGE = "NO_CHANGE"
    INVALID_INPUT = "INVALID_INPUT"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    UNAVAILABLE = "UNAVAILABLE"
    SAFE_TRIAGE = "SAFE_TRIAGE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class QualityToolName(StrEnum):
    INGEST_QUALITY_SIGNAL = "ingest_quality_signal"
    QUALIFY_SIGNAL_GROUP = "qualify_signal_group"
    GET_CATALOG_SNAPSHOT = "get_catalog_snapshot"
    ASSEMBLE_CASE_EVIDENCE = "assemble_case_evidence"
    CLASSIFY_QUALITY_ISSUE = "classify_quality_issue"
    ROUTE_QUALITY_CASE = "route_quality_case"
    RECORD_HUMAN_CASE_DECISION = "record_human_case_decision"
    CLOSE_OR_REOPEN_CASE = "close_or_reopen_case"


class QualityEntityBinding(StrictModel):
    """Exact canonical identity captured with a report event."""

    product_id: str = Field(min_length=1, max_length=128)
    sku_id: str | None = Field(default=None, max_length=128)
    listing_id: str | None = Field(default=None, max_length=128)
    catalog_version: str = Field(min_length=1, max_length=128)

    @field_validator("product_id", "sku_id", "listing_id", "catalog_version")
    @classmethod
    def non_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("canonical identifiers must not be blank")
        return value

    def grouping_scope(self) -> tuple[Literal["PRODUCT", "SKU", "LISTING"], str]:
        """Return the most specific identity available, never a fuzzy identity."""

        if self.listing_id is not None:
            return "LISTING", self.listing_id
        if self.sku_id is not None:
            return "SKU", self.sku_id
        return "PRODUCT", self.product_id


class SourceReference(StrictModel):
    source_id: str = Field(min_length=1, max_length=256)
    source_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_system: str = Field(min_length=1, max_length=128)


class AssetReference(StrictModel):
    """An inert, content-addressed reference; the workflow never fetches it."""

    reference: str = Field(min_length=1, max_length=512)
    content_checksum: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    media_type: str | None = Field(default=None, max_length=128)

    @field_validator("reference")
    @classmethod
    def safe_reference(cls, value: str) -> str:
        if any(ord(char) < 32 for char in value):
            raise ValueError("asset references cannot contain control characters")
        if re.search(r"\.(?:exe|dll|bat|cmd|ps1|sh|zip|rar|7z)(?:$|[?#])", value, re.I):
            raise ValueError("executable and archive assets are not accepted")
        return value


class RedactionReport(StrictModel):
    redaction_applied: bool
    redaction_reasons: list[str] = Field(default_factory=list, max_length=8)
    original_character_count: int = Field(ge=0, le=2000)
    retained_character_count: int = Field(ge=0, le=2000)
    raw_text_retained: Literal[False] = False


class QualitySignalInput(StrictModel):
    """Inbound schema.  ``text`` is redacted before a stored signal is built."""

    signal_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=128)
    source_type: SourceType
    signal_type: SignalType
    severity: Severity
    binding: QualityEntityBinding
    occurred_at: datetime
    received_at: datetime
    rating: int | None = Field(default=None, ge=1, le=5)
    objective_issue_flag: bool | None = None
    text: str = Field(default="", max_length=2000)
    asset_refs: list[AssetReference] = Field(default_factory=list, max_length=5)
    source_reference: SourceReference
    retention_class: str = Field(min_length=1, max_length=64)
    reporter_group_id: str | None = Field(default=None, max_length=128)
    correlation_group_id: str | None = Field(default=None, max_length=128)
    source_class: str = Field(min_length=1, max_length=64)
    verified_direct_system_signal: bool = False

    @field_validator("occurred_at", "received_at")
    @classmethod
    def utc_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_timeline(self) -> QualitySignalInput:
        if self.received_at < self.occurred_at:
            raise ValueError("received_at cannot precede occurred_at")
        if self.source_type is SourceType.SYSTEM_SIGNAL and not self.verified_direct_system_signal:
            raise ValueError("system signals require verified_direct_system_signal")
        return self


class QualitySignal(StrictModel):
    """Stored normalized signal.  It has no raw text field by design."""

    signal_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=128)
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_type: SourceType
    signal_type: SignalType
    severity: Severity
    binding: QualityEntityBinding
    occurred_at: datetime
    received_at: datetime
    rating: int | None = Field(default=None, ge=1, le=5)
    objective_issue_flag: bool | None = None
    redacted_text: str = Field(default="", max_length=2000)
    redaction_report: RedactionReport
    asset_refs: list[AssetReference] = Field(default_factory=list, max_length=5)
    source_reference: SourceReference
    retention_class: str = Field(min_length=1, max_length=64)
    reporter_group_id: str | None = Field(default=None, max_length=128)
    correlation_group_id: str | None = Field(default=None, max_length=128)
    source_class: str = Field(min_length=1, max_length=64)
    verified_direct_system_signal: bool = False
    status: SignalStatus = SignalStatus.RECEIVED


class GroupKey(StrictModel):
    scope_kind: Literal["PRODUCT", "SKU", "LISTING"]
    scope_id: str = Field(min_length=1, max_length=128)
    product_id: str = Field(min_length=1, max_length=128)
    listing_id: str | None = Field(default=None, max_length=128)
    sku_id: str | None = Field(default=None, max_length=128)
    signal_type: SignalType
    window_start: datetime
    window_end: datetime

    @field_validator("window_start", "window_end")
    @classmethod
    def group_time_is_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("group window must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def valid_window(self) -> GroupKey:
        if self.window_end <= self.window_start:
            raise ValueError("group window must have positive duration")
        return self

    @property
    def stable_key(self) -> str:
        return ":".join(
            [
                self.scope_kind,
                self.scope_id,
                self.product_id,
                self.sku_id or "-",
                self.listing_id or "-",
                self.signal_type.value,
                self.window_start.isoformat(),
                self.window_end.isoformat(),
            ]
        )


class RouteRule(StrictModel):
    issue_class: IssueClass
    queue_name: str = Field(min_length=1, max_length=128)
    priority: RoutePriority
    sla_minutes: int = Field(ge=1, le=10080)


class QualityPolicy(StrictModel):
    """Versioned prototype policy.  Values are policy input, not model output."""

    policy_version: str = Field(min_length=1, max_length=128)
    objective_window_days: int = Field(default=7, ge=1, le=365)
    fulfillment_window_days: int = Field(default=7, ge=1, le=365)
    subjective_window_days: int = Field(default=14, ge=1, le=365)
    objective_min_independent_groups: int = Field(default=3, ge=1, le=100)
    fulfillment_min_independent_groups: int = Field(default=3, ge=1, le=100)
    subjective_min_independent_groups: int = Field(default=5, ge=1, le=100)
    subjective_min_source_classes: int = Field(default=2, ge=1, le=20)
    poor_review_max_rating: int = Field(default=2, ge=1, le=5)
    poor_review_requires_objective_flag: bool = True
    correlated_group_cap_fraction: float = Field(default=0.4, gt=0, le=1)
    minimum_classifier_confidence: float = Field(default=0.70, ge=0, le=1)
    max_structured_evidence: int = Field(default=20, ge=1, le=100)
    max_prose_evidence: int = Field(default=8, ge=1, le=50)
    urgent_signal_types: set[SignalType] = Field(default_factory=lambda: {SignalType.SAFETY_URGENT})
    immediate_listing_mismatch: bool = True
    routes: list[RouteRule] = Field(min_length=1, max_length=32)

    def window_days(self, signal_type: SignalType) -> int:
        if signal_type is SignalType.OBJECTIVE_INCORRECT:
            return self.objective_window_days
        if signal_type is SignalType.FULFILLMENT_MISMATCH:
            return self.fulfillment_window_days
        if signal_type is SignalType.SUBJECTIVE_QUALITY:
            return self.subjective_window_days
        return self.objective_window_days

    def route_for(self, issue_class: IssueClass) -> RouteRule:
        for rule in self.routes:
            if rule.issue_class is issue_class:
                return rule
        raise ValueError(f"no deterministic route configured for {issue_class.value}")


class QualificationResult(StrictModel):
    status: QualificationStatus
    group_key: GroupKey
    policy_version: str
    trigger: str = Field(min_length=1, max_length=128)
    qualified_count: int = Field(ge=0)
    independent_group_count: int = Field(ge=0)
    source_class_count: int = Field(ge=0)
    correlated_group_count: int = Field(ge=0)
    evidence_complete: bool
    urgent: bool
    reason: str = Field(min_length=1, max_length=300)


class CatalogFact(StrictModel):
    field_path: str = Field(min_length=1, max_length=256)
    value: Any
    truth_status: Literal["VERIFIED", "UNKNOWN", "NOT_MODELED"]
    evidence_id: str = Field(min_length=1, max_length=128)


class CatalogSnapshot(StrictModel):
    snapshot_id: str = Field(min_length=1, max_length=128)
    binding: QualityEntityBinding
    captured_at: datetime
    facts: list[CatalogFact] = Field(default_factory=list, max_length=100)
    source_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("captured_at")
    @classmethod
    def snapshot_time_is_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("snapshot timestamp must be timezone-aware")
        return value.astimezone(UTC)


class EvidenceItem(StrictModel):
    evidence_id: str = Field(min_length=1, max_length=128)
    kind: EvidenceKind
    signal_id: str | None = Field(default=None, max_length=128)
    binding: QualityEntityBinding
    source_type: SourceType
    source_class: str = Field(min_length=1, max_length=64)
    severity: Severity
    occurred_at: datetime
    normalized_issue_type: SignalType
    redacted_excerpt: str = Field(default="", max_length=500)
    catalog_fact_ids: list[str] = Field(default_factory=list, max_length=20)
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    occurrence_count: int = Field(default=1, ge=1)
    source_count: int = Field(default=1, ge=1)
    untrusted_content: Literal[True] = True


class EvidencePacket(StrictModel):
    case_id: str = Field(min_length=1, max_length=128)
    policy_version: str = Field(min_length=1, max_length=128)
    group_key: GroupKey
    items: list[EvidenceItem] = Field(default_factory=list, max_length=28)
    snapshots: list[CatalogSnapshot] = Field(default_factory=list, max_length=20)
    missing_information: list[str] = Field(default_factory=list, max_length=20)
    allowed_issue_classes: list[IssueClass] = Field(min_length=1, max_length=8)
    untrusted_content_notice: Literal[True] = True


class QualityAssessmentProposal(StrictModel):
    issue_class: IssueClass
    confidence: float = Field(ge=0, le=1)
    supporting_evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    contradicting_evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    missing_information: list[str] = Field(default_factory=list, max_length=20)
    bounded_summary: str = Field(min_length=1, max_length=500)
    model_alias: str = Field(min_length=1, max_length=128)
    prompt_version: str = Field(min_length=1, max_length=128)


class QualityAssessment(StrictModel):
    issue_class: IssueClass
    confidence: float = Field(ge=0, le=1)
    supporting_evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    contradicting_evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    missing_information: list[str] = Field(default_factory=list, max_length=20)
    bounded_summary: str = Field(min_length=1, max_length=500)
    model_alias: str = Field(min_length=1, max_length=128)
    prompt_version: str = Field(min_length=1, max_length=128)
    validation_status: Literal["VALID", "FALLBACK_INSUFFICIENT_EVIDENCE"]
    validation_reasons: list[str] = Field(default_factory=list, max_length=8)


class QualityRoute(StrictModel):
    case_id: str = Field(min_length=1, max_length=128)
    issue_class: IssueClass
    queue_name: str = Field(min_length=1, max_length=128)
    priority: RoutePriority
    sla_minutes: int = Field(ge=1, le=10080)
    policy_version: str = Field(min_length=1, max_length=128)
    immutable_case_reference: str = Field(min_length=1, max_length=256)


class QualityCase(StrictModel):
    case_id: str = Field(min_length=1, max_length=128)
    group_key: GroupKey
    product_id: str = Field(min_length=1, max_length=128)
    catalog_version: str = Field(min_length=1, max_length=128)
    policy_version: str = Field(min_length=1, max_length=128)
    status: CaseStatus
    trigger: str = Field(min_length=1, max_length=128)
    signal_ids: list[str] = Field(default_factory=list, max_length=1000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=28)
    assessment: QualityAssessment | None = None
    route: QualityRoute | None = None
    opened_at: datetime
    updated_at: datetime
    reopened_from_case_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def case_time_order(self) -> QualityCase:
        if self.updated_at < self.opened_at:
            raise ValueError("case updated_at cannot precede opened_at")
        return self


class HumanDecision(StrictModel):
    decision_id: str = Field(min_length=1, max_length=128)
    case_id: str = Field(min_length=1, max_length=128)
    decision: CaseDecision
    reviewer_role: Literal["QUALITY_REVIEWER"]
    reviewer_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=1000)
    decided_at: datetime
    follow_up_recommendation: str | None = Field(default=None, max_length=500)

    @field_validator("decided_at")
    @classmethod
    def decision_time_is_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("decision timestamp must be timezone-aware")
        return value.astimezone(UTC)


class CaseEvent(StrictModel):
    event_id: str = Field(min_length=1, max_length=128)
    case_id: str = Field(min_length=1, max_length=128)
    from_status: CaseStatus | None = None
    to_status: CaseStatus
    actor_type: Literal["QUALITY_SENTINEL", "QUALITY_REVIEWER", "SYSTEM"]
    actor_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=500)
    created_at: datetime
    previous_event_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    event_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class QueueReceipt(StrictModel):
    queue_name: str = Field(min_length=1, max_length=128)
    case_id: str = Field(min_length=1, max_length=128)
    accepted: bool
    delivery_key: str = Field(min_length=1, max_length=256)


class ToolRun(StrictModel):
    tool_name: QualityToolName
    tool_version: str = Field(min_length=1, max_length=64)
    status: ToolStatus
    latency_ms: int = Field(ge=0, le=120000)
    sanitized_input_summary: dict[str, Any] = Field(default_factory=dict, max_length=20)
    warnings: list[str] = Field(default_factory=list, max_length=8)


class QualityTrace(StrictModel):
    trace_id: str = Field(min_length=1, max_length=128)
    policy_version: str
    tool_runs: list[ToolRun] = Field(default_factory=list, max_length=16)
    terminal_state: str = Field(min_length=1, max_length=64)


class QualityWorkflowResult(StrictModel):
    outcome: Literal[
        "QUALIFIED_CASE",
        "NOT_QUALIFIED",
        "INTAKE_CORRECTION",
        "REPLAY",
        "CONFLICT",
        "SAFE_TRIAGE",
    ]
    signal: QualitySignal
    qualification: QualificationResult | None = None
    case: QualityCase | None = None
    trace: QualityTrace
    warnings: list[str] = Field(default_factory=list, max_length=12)


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize a model/value for stable hashes and idempotency keys."""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return sha256(canonical_json_bytes(value)).hexdigest()


_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)")
_ORDER_TOKEN = re.compile(
    r"\b(?:order|txn|transaction|payment|upi|card|invoice)[\s:#-]*[A-Z0-9-]{4,}\b", re.I
)


def redact_untrusted_text(text: str) -> tuple[str, RedactionReport]:
    """Remove obvious PII/control data before persistence/model use.

    This is deliberately conservative: the output remains evidence, never a
    command.  Restricted raw input must be retained by a policy-owned service,
    not by this workflow.
    """

    normalized = unicodedata.normalize("NFKC", text)
    normalized = "".join(" " if unicodedata.category(char) == "Cc" else char for char in normalized)
    reasons: list[str] = []
    redacted = _EMAIL.sub("[REDACTED_EMAIL]", normalized)
    if redacted != normalized:
        reasons.append("EMAIL")
    before_phone = redacted
    redacted = _PHONE.sub("[REDACTED_PHONE]", redacted)
    if redacted != before_phone:
        reasons.append("PHONE")
    before_order = redacted
    redacted = _ORDER_TOKEN.sub("[REDACTED_ORDER_TOKEN]", redacted)
    if redacted != before_order:
        reasons.append("ORDER_OR_PAYMENT_TOKEN")
    redacted = " ".join(redacted.split())[:2000]
    report = RedactionReport(
        redaction_applied=bool(reasons),
        redaction_reasons=reasons,
        original_character_count=min(len(normalized), 2000),
        retained_character_count=len(redacted),
    )
    return redacted, report


def group_key_for_signal(signal: QualitySignal, policy: QualityPolicy) -> GroupKey:
    scope_kind, scope_id = signal.binding.grouping_scope()
    window_days = policy.window_days(signal.signal_type)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    elapsed_days = (signal.occurred_at - epoch).days
    bucket_start_days = elapsed_days - (elapsed_days % window_days)
    start = epoch + timedelta(days=bucket_start_days)
    end = start + timedelta(days=window_days)
    return GroupKey(
        scope_kind=scope_kind,
        scope_id=scope_id,
        product_id=signal.binding.product_id,
        sku_id=signal.binding.sku_id,
        listing_id=signal.binding.listing_id,
        signal_type=signal.signal_type,
        window_start=start,
        window_end=end,
    )


def case_event_hash(event: CaseEvent) -> str:
    payload = event.model_dump(mode="json", exclude={"event_hash"})
    return canonical_sha256(payload)


CASE_TRANSITIONS: dict[CaseStatus, set[CaseStatus]] = {
    CaseStatus.OPEN: {
        CaseStatus.AWAITING_EVIDENCE,
        CaseStatus.READY_FOR_REVIEW,
        CaseStatus.TRIAGED,
    },
    CaseStatus.AWAITING_EVIDENCE: {CaseStatus.READY_FOR_REVIEW, CaseStatus.TRIAGED},
    CaseStatus.READY_FOR_REVIEW: {CaseStatus.ROUTED, CaseStatus.TRIAGED},
    CaseStatus.ROUTED: {CaseStatus.DECIDED, CaseStatus.TRIAGED},
    CaseStatus.TRIAGED: {CaseStatus.READY_FOR_REVIEW, CaseStatus.CLOSED},
    CaseStatus.DECIDED: {CaseStatus.CLOSED, CaseStatus.REOPENED},
    CaseStatus.CLOSED: {CaseStatus.REOPENED},
    CaseStatus.REOPENED: {
        CaseStatus.AWAITING_EVIDENCE,
        CaseStatus.READY_FOR_REVIEW,
        CaseStatus.TRIAGED,
    },
}


def transition_allowed(from_status: CaseStatus, to_status: CaseStatus) -> bool:
    return to_status in CASE_TRANSITIONS.get(from_status, set())
