"""Shared, strict contracts for the non-cart FK GRiD tools.

The existing agent worktrees each define local versions of many of these models.
This package deliberately keeps the contracts provider- and ORM-neutral so those
worktrees can translate at their adapter boundary without importing one another.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Boundary model: reject unknown fields and implicit scalar coercion."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class CompatibilityTuple(StrictModel):
    contract_schema_version: str = "fkgrid-contract-v1"
    catalog_version: str = "catalog-demo-v1"
    index_version: str = "index-demo-v1"
    taxonomy_version: str = "taxonomy-demo-v1"
    category_schema_version: str = "category-schema-demo-v1"
    lexicon_version: str = "lexicon-demo-v1"
    ranking_version: str = "ranking-demo-v1"
    rank_policy_version: str = "ranking-demo-v1"
    gate_policy_version: str = "gate-demo-v1"
    prompt_version: str = "prompt-demo-v1"
    intent_prompt_version: str = "intent-demo-v1"
    policy_version: str = "policy-demo-v1"
    commerce_policy_version: str = "commerce-demo-v1"
    recovery_policy_version: str = "recovery-demo-v1"
    research_policy_version: str = "research-demo-v1"
    suggestion_policy_version: str = "suggestion-demo-v1"
    memory_schema_version: str = "memory-demo-v1"
    query_enhancement_policy_version: str = "enhancement-demo-v1"
    model_version: str = "model-deterministic-v1"
    intent_model_alias: str = "deterministic-intent"
    response_template_version: str = "response-demo-v1"
    clarification_prompt_version: str | None = "clarification-demo-v1"
    recovery_prompt_version: str | None = "recovery-demo-v1"
    recovery_model_alias: str | None = "deterministic-recovery"
    research_prompt_version: str | None = "research-demo-v1"
    research_model_alias: str | None = "deterministic-research"
    suggestion_prompt_version: str | None = "suggestion-demo-v1"
    suggestion_model_alias: str | None = "deterministic-suggestions"
    research_provider_version: str | None = "fixture-provider-v1"


class EvidenceRef(StrictModel):
    evidence_id: str
    source: str
    field: str
    version: str
    as_of: datetime | None = None


class ProductBinding(StrictModel):
    product_id: str
    sku_id: str
    offer_id: str
    variant_id: str | None = None
    catalog_version: str = "catalog-demo-v1"


class CatalogRecord(StrictModel):
    binding: ProductBinding
    title: str
    category_id: str
    brand: str | None = None
    description: str = ""
    attributes: dict[str, str] = Field(default_factory=dict)
    price: float | None = None
    availability: Literal["AVAILABLE", "UNAVAILABLE", "UNKNOWN"] = "UNKNOWN"
    eligible: bool = True
    aliases: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class QueryState(StrictModel):
    text: str = ""
    normalized_terms: list[str] = Field(default_factory=list)
    hard_filters: dict[str, Any] = Field(default_factory=dict)
    soft_terms: list[str] = Field(default_factory=list)
    excluded_terms: list[str] = Field(default_factory=list)
    category_id: str | None = None
    locale: str = "en-IN"
    result_set_id: str | None = None


class SearchRequest(StrictModel):
    query_state: QueryState
    top_k: int = Field(default=5, ge=1, le=50)
    exclusions: list[str] = Field(default_factory=list)
    compatibility: CompatibilityTuple = Field(default_factory=CompatibilityTuple)


class SearchEntry(StrictModel):
    rank: int
    binding: ProductBinding
    title: str
    category_id: str
    brand: str | None = None
    score: float
    score_components: dict[str, float] = Field(default_factory=dict)
    attributes: dict[str, str] = Field(default_factory=dict)
    price: float | None = None
    availability: Literal["AVAILABLE", "UNAVAILABLE", "UNKNOWN"] = "UNKNOWN"
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class SearchResult(StrictModel):
    status: Literal["OK", "NO_MATCH", "DEGRADED", "UNAVAILABLE"]
    result_set_id: str
    eligible_count: int = Field(ge=0)
    entries: list[SearchEntry] = Field(default_factory=list)
    compatibility: CompatibilityTuple
    confidence_signals: dict[str, float] = Field(default_factory=dict)
    unknown_terms: list[str] = Field(default_factory=list)
    degraded_components: list[str] = Field(default_factory=list)
    query_hash: str


class CommerceEligibility(StrictModel):
    status: Literal["ELIGIBLE", "INELIGIBLE", "UNKNOWN", "STALE"]
    eligible: bool | None
    binding: ProductBinding
    purpose: str
    policy_status: str = "PROTOTYPE_MOCK_CATALOG"
    price: float | None = None
    availability_status: str = "UNKNOWN"
    as_of: datetime | None = None
    provenance_type: str = "MOCK_CATALOG"
    reason_codes: list[str] = Field(default_factory=list)
    compatibility: CompatibilityTuple
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class ProductDetails(StrictModel):
    status: Literal["OK", "NOT_FOUND", "STALE", "UNAVAILABLE"]
    binding: ProductBinding
    title: str | None = None
    category_id: str | None = None
    brand: str | None = None
    description: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)
    price: float | None = None
    availability: str = "UNKNOWN"
    compatibility: CompatibilityTuple
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class Comparison(StrictModel):
    status: Literal["OK", "NOT_FOUND", "STALE", "UNAVAILABLE"]
    bindings: list[ProductBinding]
    rows: dict[str, list[Any]] = Field(default_factory=dict)
    compatibility: CompatibilityTuple
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class Availability(StrictModel):
    status: Literal["AVAILABLE", "UNAVAILABLE", "UNKNOWN", "STALE"]
    binding: ProductBinding
    availability: str
    reason: str
    compatibility: CompatibilityTuple
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class ReferenceResolution(StrictModel):
    status: Literal["RESOLVED", "AMBIGUOUS", "STALE", "NOT_FOUND", "INVALID"]
    resolved: list[ProductBinding] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    ambiguous: dict[str, list[ProductBinding]] = Field(default_factory=dict)
    result_set_id: str | None = None
    compatibility: CompatibilityTuple


class QueryEnhancement(StrictModel):
    original_text: str
    enhanced_text: str
    normalized_terms: list[str]
    soft_context_terms: list[str] = Field(default_factory=list)
    provenance: list[str] = Field(default_factory=list)
    used_memory: bool = False
    used_history: bool = False


class IntentDelta(StrictModel):
    action: str
    query_text: str = ""
    hard_filter_delta: dict[str, Any] = Field(default_factory=dict)
    soft_terms: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    explicit_research: bool = False
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class FollowUpNeed(StrictModel):
    needed: bool
    reason: str
    blocking: bool = False


class ClarifyingQuestion(StrictModel):
    status: Literal["ASK", "NO_QUESTION"]
    question: str | None = None
    field: str | None = None
    allowed_values: list[str] = Field(default_factory=list)


class ClarificationPacket(StrictModel):
    reason_code: str
    target_field: str
    preserved_state_summary: str = ""
    choice_ids: list[str] = Field(default_factory=list)
    options: list[str] = Field(default_factory=list)
    locale: str = "en-IN"


class FollowUpPhrasing(StrictModel):
    selected_candidate_ids: list[str] = Field(default_factory=list, max_length=3)
    labels: dict[str, str] = Field(default_factory=dict, max_length=3)


class ConstrainedRepairPlan(StrictModel):
    action: Literal[
        "REWRITE_WITH_ALLOWED_CONCEPTS",
        "ASK_CLARIFICATION",
        "NO_SAFE_RECOVERY",
    ]
    added_concept_ids: list[str] = Field(default_factory=list, max_length=4)
    interpretation_label: str | None = None
    preserved_hard_filter_hash: str | None = None
    option_ids: list[str] = Field(default_factory=list, max_length=4)
    target_field: str | None = None
    reason_code: str | None = None


class EnhancementContext(StrictModel):
    enhancement_id: str
    session_id: str
    originating_client_turn_id: str
    state_version: int = Field(ge=0)
    memory_version: int | None = Field(default=None, ge=0)
    locale: str = "en-IN"
    current_message_verbatim: str
    normalized_current_message: str
    query_state: QueryState
    included_source_ids: list[str] = Field(default_factory=list, max_length=100)
    excluded_source_ids: list[str] = Field(default_factory=list, max_length=100)
    context_truncated: bool = False
    token_count: int = Field(ge=0, le=1500)
    token_count_approximate: bool = False
    fallback_state: Literal["NONE", "CURRENT_SESSION_ONLY"] = "NONE"
    context_hash: str
    warnings: list[str] = Field(default_factory=list, max_length=8)


class ConfidenceDecision(StrictModel):
    status: Literal["ACCEPT", "RECOVER", "CLARIFY", "NO_MATCH"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    unknown_terms: list[str] = Field(default_factory=list)


class ApprovedExpansion(StrictModel):
    surface_form: str
    canonical_term: str
    target_id: str
    scope: str = "global"
    locale: str = "en-IN"
    lexicon_version: str
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class RecoveryConstraint(StrictModel):
    constraint_id: str
    field: str
    allowed_values: list[str] = Field(default_factory=list)
    source: str
    hard: bool = True
    compatibility: CompatibilityTuple


class RecoveryPlan(StrictModel):
    plan_id: str
    action: Literal["REWRITE", "CLARIFY", "ABSTAIN"]
    add_terms: list[str] = Field(default_factory=list)
    remove_terms: list[str] = Field(default_factory=list)
    clarification_field: str | None = None
    clarification_values: list[str] = Field(default_factory=list)
    hard_filter_hash: str
    compatibility: CompatibilityTuple
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class RecoveryEventRecord(StrictModel):
    event_id: str
    outcome: str
    reason_codes: list[str] = Field(default_factory=list)
    hard_filter_hash: str
    compatibility: CompatibilityTuple
    created_at: datetime
    planner_used: bool = False
    warnings: list[str] = Field(default_factory=list)


class RetrievalRun(StrictModel):
    run_id: str
    kind: Literal["BASELINE", "DIRECT_RECOVERY", "PLANNER_RECOVERY"]
    result: SearchResult
    query_state: QueryState


class RetrievalComparison(StrictModel):
    status: Literal["ACCEPT_RECOVERY", "KEEP_BASELINE", "CLARIFY", "NO_CHANGE"]
    baseline_run_id: str
    candidate_run_id: str
    baseline_count: int
    candidate_count: int
    new_bindings: list[ProductBinding] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class ResearchDecision(StrictModel):
    needed: bool
    query: str
    reason: str
    explicit: bool


class ResearchSource(StrictModel):
    source_id: str
    url: str
    domain: str
    title: str
    extract: str
    retrieved_at: datetime | None = None
    citation_key: str


class OnlineSearchResult(StrictModel):
    status: Literal["OK", "DECLINED", "UNAVAILABLE"]
    provider: str
    executed_query: str
    sources: list[ResearchSource] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    cache_status: Literal["MISS", "HIT", "NOT_USED"] = "NOT_USED"


class ResearchAnswer(StrictModel):
    status: Literal["OK", "INSUFFICIENT_EVIDENCE", "UNAVAILABLE"]
    answer: str
    citations: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)


class SuggestionAction(StrictModel):
    action_id: str
    action_type: Literal[
        "REFINE_RESULTS",
        "VIEW_DETAILS",
        "COMPARE_RESULTS",
        "CHECK_AVAILABILITY",
        "RESEARCH_EXTERNAL",
        "NEW_SEARCH",
    ]
    payload: dict[str, Any] = Field(default_factory=dict)


class SuggestionItem(StrictModel):
    suggestion_id: str
    label: str
    action: SuggestionAction


class SuggestionSet(StrictModel):
    suggestion_set_id: str
    session_id: str
    turn_id: str
    expires_at_ms: int
    suggestions: list[SuggestionItem] = Field(max_length=3)
    signature: str
    compatibility: CompatibilityTuple
    state_version: int = Field(default=0, ge=0)
    cart_version: int = Field(default=0, ge=0)
    active_result_set_id: str | None = None
    generator_version: str = "deterministic-suggestions-v1"
    revision: int = Field(default=1, ge=1)


class SuggestionSelection(StrictModel):
    status: Literal["SELECTED", "SUGGESTION_STALE", "NOT_FOUND", "INVALID"]
    action: SuggestionAction | None = None
    suggestion_set_id: str | None = None


class Vocabulary(StrictModel):
    catalog_version: str
    categories: list[str] = Field(default_factory=list)
    brands: list[str] = Field(default_factory=list)
    attributes: dict[str, list[str]] = Field(default_factory=dict)
    aliases: dict[str, str] = Field(default_factory=dict)


class CandidateTerm(StrictModel):
    surface_form: str
    normalized_form: str
    locale: str = "en-IN"
    count: int = Field(ge=1)
    source_ids: list[str] = Field(default_factory=list)


class MappingProposal(StrictModel):
    surface_form: str
    normalized_form: str
    target_id: str | None = None
    canonical_term: str | None = None
    status: Literal["PROPOSED", "ABSTAIN", "REJECTED"]
    direction: Literal["SURFACE_TO_CANONICAL", "NONE"] = "SURFACE_TO_CANONICAL"
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)


class LexiconCandidate(StrictModel):
    version: str
    catalog_version: str
    mappings: list[MappingProposal] = Field(default_factory=list)
    status: Literal["CANDIDATE", "APPROVED", "REJECTED", "ACTIVE"] = "CANDIDATE"
    regression_hash: str | None = None


class RegressionReport(StrictModel):
    status: Literal["PASS", "FAIL"]
    candidate_version: str
    cases: int = Field(ge=0)
    precision: float = Field(ge=0.0, le=1.0)
    leakage_count: int = Field(ge=0)
    changed_protected_cases: int = Field(ge=0)
    reasons: list[str] = Field(default_factory=list)


class ReviewDecision(StrictModel):
    subject_id: str
    decision: Literal["APPROVED", "REJECTED", "NEEDS_REVIEW"]
    reviewer_id: str
    reason: str
    recorded_at: datetime | None = None


class IngestBatch(StrictModel):
    batch_id: str
    source: str
    records: list[dict[str, Any]]
    payload_hash: str


class NormalizedProduct(StrictModel):
    record: CatalogRecord
    source_record_id: str | None = None
    warnings: list[str] = Field(default_factory=list)


class ValidationResult(StrictModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CatalogDiff(StrictModel):
    product_id: str
    changes: dict[str, dict[str, Any]] = Field(default_factory=dict)
    risk: Literal["LOW", "MEDIUM", "HIGH", "BLOCKED"] = "LOW"


class ChangeRisk(StrictModel):
    level: Literal["LOW", "MEDIUM", "HIGH", "BLOCKED"]
    score: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)


class AttributeExtraction(StrictModel):
    attributes: dict[str, str] = Field(default_factory=dict)
    unsupported_fields: list[str] = Field(default_factory=list)


class TaxonomyClassification(StrictModel):
    status: Literal["CLASSIFIED", "ABSTAIN", "INVALID"]
    category_id: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class IndexSmokeReport(StrictModel):
    status: Literal["PASS", "FAIL"]
    catalog_version: str
    index_version: str
    checksum: str
    record_count: int = Field(ge=0)
    reasons: list[str] = Field(default_factory=list)


class CatalogVersionReceipt(StrictModel):
    version: str
    status: Literal["PUBLISHED", "ROLLED_BACK", "REJECTED", "NEEDS_REVIEW"]
    checksum: str
    active_version: str | None = None


class ReviewRoute(StrictModel):
    subject_id: str
    queue: Literal["STANDARD", "SPECIALIST", "BLOCKED_CHANGE"]
    priority: Literal["LOW", "MEDIUM", "HIGH", "URGENT"]
    reason: str


class QualitySignal(StrictModel):
    signal_id: str
    signal_type: str
    entity_id: str
    message: str
    occurred_at: datetime
    source: str
    redacted: bool = True


class QualityQualification(StrictModel):
    qualified: bool
    group_key: str
    reason_codes: list[str] = Field(default_factory=list)


class EvidencePacket(StrictModel):
    case_id: str
    signals: list[QualitySignal] = Field(default_factory=list)
    catalog_snapshot_version: str | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class CatalogSnapshot(StrictModel):
    version: str
    records: list[CatalogRecord] = Field(default_factory=list)
    checksum: str


class QualityAssessment(StrictModel):
    issue_class: Literal[
        "WRONG_ITEM",
        "DAMAGED",
        "MISSING_ATTRIBUTE",
        "DUPLICATE",
        "PRICE_MISMATCH",
        "UNSUPPORTED",
        "INSUFFICIENT_EVIDENCE",
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)


class QualityRoute(StrictModel):
    case_id: str
    destination: Literal["CATALOG_REVIEW", "SPECIALIST_REVIEW", "NO_ACTION", "NEEDS_REVIEW"]
    priority: Literal["LOW", "MEDIUM", "HIGH", "URGENT"]
    reason_codes: list[str] = Field(default_factory=list)


class QualityCaseStatus(StrictModel):
    case_id: str
    status: Literal["OPEN", "CLOSED", "REOPENED"]
    reason: str


class ToolReceipt(StrictModel):
    tool_name: str
    tool_version: str
    status: Literal["OK", "REJECTED", "UNAVAILABLE", "NEEDS_REVIEW"]
    input_hash: str
    output_hash: str | None = None
    warnings: list[str] = Field(default_factory=list)


class ModelCallRequest(StrictModel):
    call_name: str
    schema_version: str = "v1"
    payload: dict[str, Any] = Field(default_factory=dict)
    deadline_ms: int = Field(default=1800, ge=1, le=6500)


class ModelCallResponse(StrictModel):
    status: Literal["OK", "ABSTAIN", "UNAVAILABLE", "INVALID"]
    call_name: str
    schema_version: str
    provider: str
    output: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class ToolRequest(StrictModel):
    tool_run_id: str = "tool-local"
    trace_id: str = "trace-local"
    turn_id: str = "turn-local"
    session_id: str | None = None
    tool_name: str
    tool_version: str = "v1"
    compatibility_tuple: CompatibilityTuple = Field(default_factory=CompatibilityTuple)
    expected_state_version: int | None = Field(default=None, ge=0)
    expected_cart_version: int | None = Field(default=None, ge=0)
    deadline_ms: int = Field(default=300, ge=1)
    auth_context_ref: str = "local-authorized"
    payload: dict[str, Any] = Field(default_factory=dict)


class ToolResult(StrictModel):
    tool_run_id: str = "tool-local"
    tool_name: str
    tool_version: str
    status: Literal["OK", "REJECTED", "UNAVAILABLE", "NEEDS_REVIEW"]
    output: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
