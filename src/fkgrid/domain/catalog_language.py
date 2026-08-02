"""Strict domain contracts for the Tier 2 catalog-language workflow.

The models in this module are deliberately independent of SQLAlchemy, provider
SDKs, HTTP, and the eventual catalog database.  They are the contract between
the offline workflow, its ports, the artifact builder, and runtime lookup.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    """Base class for trust-boundary models."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True)


class MappingKind(StrEnum):
    SYNONYM = "SYNONYM"
    ABBREVIATION = "ABBREVIATION"
    MISSPELLING = "MISSPELLING"
    COLLOQUIAL = "COLLOQUIAL"
    UNIT_ALIAS = "UNIT_ALIAS"
    ATTRIBUTE_PARAPHRASE = "ATTRIBUTE_PARAPHRASE"
    COMPOUND = "COMPOUND"


class MappingStatus(StrEnum):
    DRAFT = "DRAFT"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    RETIRED = "RETIRED"


class EvidenceBand(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    APPROVED_HIGH = "APPROVED_HIGH"


class ExpansionAction(StrEnum):
    CANONICAL_SYNONYM = "CANONICAL_SYNONYM"
    SPELLING_NORMALIZATION = "SPELLING_NORMALIZATION"
    SOFT_RANK_BOOST = "SOFT_RANK_BOOST"
    CLARIFICATION_CANDIDATE = "CLARIFICATION_CANDIDATE"
    EXPLICIT_FILTER_AFTER_CONFIRMATION = "EXPLICIT_FILTER_AFTER_CONFIRMATION"


class TargetType(StrEnum):
    TAXONOMY_NODE = "TAXONOMY_NODE"
    ATTRIBUTE = "ATTRIBUTE"
    BRAND = "BRAND"
    CONTROLLED_VALUE = "CONTROLLED_VALUE"


class MappingDirection(StrEnum):
    QUERY_TO_CANONICAL = "QUERY_TO_CANONICAL"


class EvidenceSourceClass(StrEnum):
    ZERO_RESULT = "ZERO_RESULT"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    RECOVERY = "RECOVERY"
    FEEDBACK = "FEEDBACK"
    CATALOG_DIFF = "CATALOG_DIFF"
    CURATED = "CURATED"


class ReviewRoute(StrEnum):
    BATCH = "BATCH"
    INDIVIDUAL = "INDIVIDUAL"
    REJECT = "REJECT"


class WorkflowStatus(StrEnum):
    COMPLETED = "COMPLETED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NO_PROPOSALS = "NO_PROPOSALS"
    REGRESSION_BLOCKED = "REGRESSION_BLOCKED"
    SHADOW_BLOCKED = "SHADOW_BLOCKED"
    REVIEW_PENDING = "REVIEW_PENDING"
    ACTIVATION_CONFLICT = "ACTIVATION_CONFLICT"
    FAILED_SAFE = "FAILED_SAFE"


CATALOG_LANGUAGE_DEFAULT_MODEL_DEADLINE_MS = 10_000
CATALOG_LANGUAGE_MODEL_MAX_DEADLINE_MS = 30_000


class ModelStatus(StrEnum):
    OK = "OK"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"


class LexiconCompatibility(StrictModel):
    catalog_version: str = Field(min_length=1, max_length=128)
    taxonomy_version: str = Field(min_length=1, max_length=128)
    category_schema_version: str = Field(min_length=1, max_length=128)
    lexicon_version: str = Field(min_length=1, max_length=128)
    normalizer_version: str = Field(min_length=1, max_length=128)
    mapping_schema_version: str = Field(min_length=1, max_length=128)
    rank_policy_version: str = Field(min_length=1, max_length=128)


class LexiconScope(StrictModel):
    locale: str = Field(default="en-IN", min_length=2, max_length=32)
    taxonomy_node_id: str | None = Field(default=None, max_length=128)
    parent_taxonomy_node_id: str | None = Field(default=None, max_length=128)
    attribute_id: str | None = Field(default=None, max_length=128)


class VocabularyItem(StrictModel):
    target_type: TargetType
    target_id: str = Field(min_length=1, max_length=128)
    canonical_name: str = Field(min_length=1, max_length=256)
    normalized_name: str = Field(min_length=1, max_length=256)
    scope: LexiconScope
    active: bool = True


class CanonicalVocabularySnapshot(StrictModel):
    catalog_version: str = Field(min_length=1, max_length=128)
    taxonomy_version: str = Field(min_length=1, max_length=128)
    category_schema_version: str = Field(min_length=1, max_length=128)
    normalizer_version: str = Field(min_length=1, max_length=128)
    items: list[VocabularyItem] = Field(default_factory=list, max_length=100_000)
    checksum: str = Field(min_length=64, max_length=64)


class EvidenceGroup(StrictModel):
    """Privacy-safe aggregate; no session, user, transcript, or raw event ID."""

    group_id: str = Field(min_length=8, max_length=128)
    normalized_term: str = Field(min_length=1, max_length=256)
    observed_surface_forms: list[str] = Field(min_length=1, max_length=32)
    locale: str = Field(default="en-IN", min_length=2, max_length=32)
    taxonomy_node_id: str | None = Field(default=None, max_length=128)
    attribute_id: str | None = Field(default=None, max_length=128)
    support_count: int = Field(ge=1, le=1_000_000)
    distinct_source_groups: int = Field(ge=1, le=1_000_000)
    source_classes: list[EvidenceSourceClass] = Field(min_length=1, max_length=6)
    source_concentration: float = Field(ge=0.0, le=1.0)
    recovery_success_count: int = Field(ge=0)
    contradiction_count: int = Field(ge=0)
    first_observed_at: datetime
    last_observed_at: datetime
    privacy_safe: Literal[True] = True

    @model_validator(mode="after")
    def validate_counts(self) -> EvidenceGroup:
        if self.recovery_success_count > self.support_count:
            raise ValueError("recovery_success_count cannot exceed support_count")
        if self.first_observed_at > self.last_observed_at:
            raise ValueError("first_observed_at must not be after last_observed_at")
        return self


class EvidenceWindow(StrictModel):
    window_start: datetime
    window_end: datetime
    min_observation_days: int = Field(default=7, ge=1, le=365)
    min_support_count: int = Field(default=5, ge=1, le=1_000_000)
    min_distinct_source_groups: int = Field(default=5, ge=1, le=1_000_000)
    min_source_classes: int = Field(default=2, ge=1, le=6)
    max_source_concentration: float = Field(default=0.4, gt=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_window(self) -> EvidenceWindow:
        if self.window_start >= self.window_end:
            raise ValueError("window_start must be before window_end")
        if (
            self.window_end - self.window_start
        ).total_seconds() < self.min_observation_days * 86_400:
            raise ValueError("evidence window is shorter than the minimum observation period")
        return self


class SurfaceFormCluster(StrictModel):
    cluster_id: str = Field(min_length=1, max_length=128)
    normalized_form: str = Field(min_length=1, max_length=256)
    surface_forms: list[str] = Field(min_length=1, max_length=64)
    support_count: int = Field(ge=1)
    source_group_count: int = Field(ge=1)
    source_classes: list[EvidenceSourceClass] = Field(min_length=1, max_length=6)
    locale: str = Field(default="en-IN", min_length=2, max_length=32)
    taxonomy_node_id: str | None = None
    attribute_id: str | None = None
    evidence_group_ids: list[str] = Field(min_length=1, max_length=64)


class TargetCandidate(StrictModel):
    target: VocabularyItem
    lexical_overlap: float = Field(ge=0.0, le=1.0)
    scope_rank: int = Field(ge=0, le=3)
    evidence_refs: list[str] = Field(default_factory=list, max_length=16)


class MappingDraft(StrictModel):
    """Strict proposer output; target must be checked against supplied candidates."""

    decision: Literal["SELECT", "ABSTAIN"]
    source_form: str | None = Field(default=None, max_length=256)
    normalized_form: str | None = Field(default=None, max_length=256)
    mapping_kind: MappingKind | None = None
    target_type: TargetType | None = None
    target_id: str | None = Field(default=None, max_length=128)
    scope: LexiconScope | None = None
    direction: MappingDirection | None = None
    expansion_action: ExpansionAction | None = None
    evidence_band: EvidenceBand | None = None
    interpretation_label: str | None = Field(default=None, max_length=160)
    evidence_ids: list[str] = Field(default_factory=list, max_length=32)
    compound_semantics: Literal["NONE", "AND", "OR"] = "NONE"

    @model_validator(mode="after")
    def validate_selection(self) -> MappingDraft:
        fields = (
            self.source_form,
            self.normalized_form,
            self.mapping_kind,
            self.target_type,
            self.target_id,
            self.scope,
            self.direction,
            self.expansion_action,
            self.evidence_band,
        )
        if self.decision == "SELECT" and any(value is None for value in fields):
            raise ValueError("a selected mapping must include every mapping field")
        if self.decision == "ABSTAIN" and any(value is not None for value in fields):
            raise ValueError("an abstention cannot contain a partial mapping")
        return self


class CriticDraft(StrictModel):
    decision: Literal["ACCEPT", "CONCERN", "ABSTAIN"]
    concern_codes: list[str] = Field(default_factory=list, max_length=8)
    recommended_scope: LexiconScope | None = None
    evidence_ids: list[str] = Field(default_factory=list, max_length=32)
    rationale_code: str = Field(min_length=1, max_length=128)


class ModelCallRequest(StrictModel):
    call_id: str = Field(min_length=1, max_length=128)
    logical_call: Literal["propose_canonical_mapping", "critique_mapping"]
    prompt_id: str = Field(min_length=1, max_length=128)
    prompt_version: str = Field(min_length=1, max_length=128)
    input_schema_version: str = Field(min_length=1, max_length=128)
    output_schema_version: str = Field(min_length=1, max_length=128)
    input_payload: dict[str, Any] = Field(max_length=64)
    allowed_target_ids: list[str] = Field(default_factory=list, max_length=10)
    deadline_ms: int = Field(ge=1, le=CATALOG_LANGUAGE_MODEL_MAX_DEADLINE_MS)
    temperature: float = Field(ge=0.0, le=0.0)
    tools: list[Any] = Field(default_factory=list, max_length=0)
    compatibility: LexiconCompatibility


class ModelCallResponse(StrictModel):
    call_id: str
    status: ModelStatus
    payload: MappingDraft | CriticDraft | None = None
    input_hash: str = Field(min_length=64, max_length=64)
    output_hash: str | None = Field(default=None, min_length=64, max_length=64)
    model_alias: str
    latency_ms: int = Field(ge=0)
    validation_codes: list[str] = Field(default_factory=list, max_length=16)
    retryable: bool = False


class CritiqueAssessment(StrictModel):
    decision: Literal["ACCEPT", "CONCERN", "ABSTAIN"]
    concern_codes: list[str] = Field(default_factory=list, max_length=8)
    recommended_scope: LexiconScope | None = None
    evidence_ids: list[str] = Field(default_factory=list, max_length=32)
    rationale_code: str = Field(min_length=1, max_length=128)


class EvidenceScore(StrictModel):
    support_count: int = Field(ge=0)
    distinct_source_groups: int = Field(ge=0)
    source_class_count: int = Field(ge=0)
    recovery_success_rate: float = Field(ge=0.0, le=1.0)
    source_concentration: float = Field(ge=0.0, le=1.0)
    contradiction_count: int = Field(ge=0)
    critic_penalty: float = Field(ge=0.0, le=1.0)
    deterministic_score: float = Field(ge=0.0, le=1.0)
    evidence_band: EvidenceBand
    review_route: ReviewRoute
    rule_version: str = Field(min_length=1, max_length=128)


class LexiconMapping(StrictModel):
    mapping_id: str = Field(min_length=1, max_length=128)
    surface_form: str = Field(min_length=1, max_length=256)
    normalized_form: str = Field(min_length=1, max_length=256)
    locale: str = Field(default="en-IN", min_length=2, max_length=32)
    mapping_kind: MappingKind
    target_type: TargetType
    target_id: str = Field(min_length=1, max_length=128)
    scope: LexiconScope
    direction: MappingDirection
    expansion_action: ExpansionAction
    compound_semantics: Literal["NONE", "AND", "OR"] = "NONE"
    evidence_band: EvidenceBand
    origin: Literal["TIER_1_CURATED", "TIER_2_EVIDENCE"]
    evidence_ids: list[str] = Field(min_length=1, max_length=32)
    status: MappingStatus
    compatibility: LexiconCompatibility
    created_at: datetime
    reviewed_at: datetime | None = None


class ValidationReport(StrictModel):
    valid: bool
    codes: list[str] = Field(default_factory=list, max_length=32)
    mapping: LexiconMapping | None = None


class RegressionCase(StrictModel):
    case_id: str = Field(min_length=1, max_length=128)
    query: str = Field(min_length=1, max_length=256)
    locale: str = Field(default="en-IN", min_length=2, max_length=32)
    taxonomy_node_id: str | None = None
    attribute_id: str | None = None
    expected_target_ids: list[str] = Field(default_factory=list, max_length=8)
    forbidden_target_ids: list[str] = Field(default_factory=list, max_length=8)
    protected: bool = False
    expects_expansion: bool = True


class RegressionReport(StrictModel):
    report_id: str
    candidate_version: str
    cases_total: int = Field(ge=0)
    cases_passed: int = Field(ge=0)
    expansion_precision: float = Field(ge=0.0, le=1.0)
    expected_recovery_rate: float = Field(ge=0.0, le=1.0)
    protected_regressions: int = Field(ge=0)
    scope_leakage: int = Field(ge=0)
    invalid_targets: int = Field(ge=0)
    passed: bool
    policy_version: str
    failure_codes: list[str] = Field(default_factory=list, max_length=32)


class ShadowCase(StrictModel):
    case_id: str
    query: str
    locale: str = "en-IN"
    taxonomy_node_id: str | None = None
    attribute_id: str | None = None
    baseline_target_ids: list[str] = Field(default_factory=list, max_length=8)
    observed_target_ids: list[str] = Field(default_factory=list, max_length=8)
    expected_target_ids: list[str] = Field(default_factory=list, max_length=8)


class ShadowReport(StrictModel):
    report_id: str
    candidate_version: str
    cases_total: int = Field(ge=0)
    cases_improved: int = Field(ge=0)
    cases_regressed: int = Field(ge=0)
    irrelevant_result_increase: float = Field(ge=0.0, le=1.0)
    passed: bool
    policy_version: str
    failure_codes: list[str] = Field(default_factory=list, max_length=32)


class LexiconCandidateVersion(StrictModel):
    candidate_version: str = Field(min_length=1, max_length=128)
    parent_lexicon_version: str = Field(min_length=1, max_length=128)
    compatibility: LexiconCompatibility
    mappings: list[LexiconMapping] = Field(min_length=1, max_length=5_000)
    proposal_ids: list[str] = Field(min_length=1, max_length=5_000)
    proposed_mapping_ids: list[str] = Field(min_length=1, max_length=5_000)
    candidate_checksum: str = Field(min_length=64, max_length=64)
    created_at: datetime


class LexiconArtifactManifest(StrictModel):
    candidate_version: str
    compatibility: LexiconCompatibility
    candidate_checksum: str = Field(min_length=64, max_length=64)
    mapping_count: int = Field(ge=1)
    mappings_checksum: str = Field(min_length=64, max_length=64)
    regression_report_checksum: str = Field(min_length=64, max_length=64)
    shadow_report_checksum: str = Field(min_length=64, max_length=64)
    manifest_checksum: str = Field(min_length=64, max_length=64)
    created_at: datetime


class ReviewDecision(StrictModel):
    candidate_version: str
    approved: bool
    approved_mapping_ids: list[str] = Field(default_factory=list, max_length=5_000)
    rejected_mapping_ids: list[str] = Field(default_factory=list, max_length=5_000)
    reviewer_id: str = Field(min_length=1, max_length=128)
    decision_reason_code: str = Field(min_length=1, max_length=128)
    decided_at: datetime


class ActivationRequest(StrictModel):
    candidate_version: str
    expected_active_version: str
    approved_mapping_ids: list[str] = Field(min_length=1, max_length=5_000)
    actor_id: str = Field(min_length=1, max_length=128)


class ActivationReceipt(StrictModel):
    activated: bool
    active_lexicon_version: str
    activated_mapping_ids: list[str] = Field(default_factory=list, max_length=5_000)
    event_id: str = Field(min_length=1, max_length=128)
    activated_at: datetime


class LexiconLookupRequest(StrictModel):
    term: str = Field(min_length=1, max_length=256)
    locale: str = Field(default="en-IN", min_length=2, max_length=32)
    taxonomy_node_id: str | None = None
    parent_taxonomy_node_id: str | None = None
    attribute_id: str | None = None
    lexicon_version: str = Field(min_length=1, max_length=128)
    max_mappings: int = Field(default=3, ge=1, le=3)


class ExpansionResult(StrictModel):
    mapping_id: str
    target_type: TargetType
    target_id: str
    expansion_action: ExpansionAction
    scope_match: Literal["EXACT", "ANCESTOR", "GLOBAL"]
    interpretation_label: str
    evidence_band: EvidenceBand


class LexiconLookupResult(StrictModel):
    normalized_term: str
    mappings: list[ExpansionResult] = Field(default_factory=list, max_length=3)
    ambiguous: bool = False
    ambiguity_target_ids: list[str] = Field(default_factory=list, max_length=8)
    compatibility_ok: bool = True
    warnings: list[str] = Field(default_factory=list, max_length=8)


class LexiconWorkflowRequest(StrictModel):
    run_id: str = Field(min_length=1, max_length=128)
    evidence_window: EvidenceWindow
    compatibility: LexiconCompatibility
    active_lexicon_version: str = Field(min_length=1, max_length=128)
    max_proposals: int = Field(default=100, ge=1, le=500)
    proposer_deadline_ms: int = Field(
        default=CATALOG_LANGUAGE_DEFAULT_MODEL_DEADLINE_MS,
        ge=1,
        le=CATALOG_LANGUAGE_MODEL_MAX_DEADLINE_MS,
    )
    critic_deadline_ms: int = Field(
        default=CATALOG_LANGUAGE_DEFAULT_MODEL_DEADLINE_MS,
        ge=1,
        le=CATALOG_LANGUAGE_MODEL_MAX_DEADLINE_MS,
    )
    regression_policy_version: str = Field(min_length=1, max_length=128)
    shadow_policy_version: str = Field(min_length=1, max_length=128)


class MappingDecision(StrictModel):
    proposal_id: str
    source_form: str
    status: Literal["ABSTAINED", "REJECTED", "VALID", "REVIEW_PENDING"]
    mapping_id: str | None = None
    validation_codes: list[str] = Field(default_factory=list, max_length=32)
    evidence_score: EvidenceScore | None = None
    critic: CritiqueAssessment | None = None


class WorkflowTraceEvent(StrictModel):
    event_type: str = Field(min_length=1, max_length=128)
    step: str = Field(min_length=1, max_length=128)
    status: str = Field(min_length=1, max_length=64)
    detail_codes: list[str] = Field(default_factory=list, max_length=16)
    mapping_ids: list[str] = Field(default_factory=list, max_length=32)
    latency_ms: int = Field(default=0, ge=0)
    at: datetime


class LexiconWorkflowResult(StrictModel):
    run_id: str
    status: WorkflowStatus
    candidate: LexiconCandidateVersion | None = None
    decisions: list[MappingDecision] = Field(default_factory=list, max_length=500)
    regression: RegressionReport | None = None
    shadow: ShadowReport | None = None
    review: ReviewDecision | None = None
    activation: ActivationReceipt | None = None
    trace: list[WorkflowTraceEvent] = Field(default_factory=list, max_length=2_000)
    warnings: list[str] = Field(default_factory=list, max_length=32)


class CatalogLanguageRun(StrictModel):
    """Durable handoff envelope; persistence owns the actual row/transaction."""

    run_id: str
    status: WorkflowStatus
    started_at: datetime
    finished_at: datetime | None = None
    compatibility: LexiconCompatibility
    candidate_version: str | None = None
    trace_hash: str | None = Field(default=None, min_length=64, max_length=64)


def utc_now() -> datetime:
    """Small injectable-default helper for callers that need a timestamp."""

    return datetime.now(UTC)
