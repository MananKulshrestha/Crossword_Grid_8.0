"""Strict, provider-neutral contracts for the shopper-facing agentic pipeline.

These models deliberately stop at the orchestration boundary. They describe what
the database, retrieval, cart, research, graph, and frontend owners must provide;
they do not implement those systems.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Generic, Literal, TypeVar, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    """Base class for every trust-boundary model."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True)


class Action(str, Enum):
    SEARCH = "SEARCH"
    REFINE = "REFINE"
    PRODUCT_DETAILS = "PRODUCT_DETAILS"
    COMPARE = "COMPARE"
    CHECK_AVAILABILITY = "CHECK_AVAILABILITY"
    ADD_TO_CART = "ADD_TO_CART"
    UPDATE_CART = "UPDATE_CART"
    REMOVE_FROM_CART = "REMOVE_FROM_CART"
    SHOW_CART = "SHOW_CART"
    RESEARCH_EXTERNAL = "RESEARCH_EXTERNAL"
    RESET_SEARCH = "RESET_SEARCH"
    MANAGE_MEMORY = "MANAGE_MEMORY"
    HELP = "HELP"


class TerminalState(str, Enum):
    ANSWERED_WITH_GROUNDED_RESULTS = "ANSWERED_WITH_GROUNDED_RESULTS"
    ANSWERED_WITH_PRODUCT_DETAILS = "ANSWERED_WITH_PRODUCT_DETAILS"
    ANSWERED_WITH_COMPARISON = "ANSWERED_WITH_COMPARISON"
    ANSWERED_WITH_AVAILABILITY = "ANSWERED_WITH_AVAILABILITY"
    CART_UPDATED = "CART_UPDATED"
    CART_SHOWN = "CART_SHOWN"
    ANSWERED_WITH_EXTERNAL_RESEARCH = "ANSWERED_WITH_EXTERNAL_RESEARCH"
    RESEARCH_UNAVAILABLE = "RESEARCH_UNAVAILABLE"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    NO_ELIGIBLE_MATCH = "NO_ELIGIBLE_MATCH"
    NO_SAFE_RECOVERY = "NO_SAFE_RECOVERY"
    ACTION_FAILED_WITH_REASON = "ACTION_FAILED_WITH_REASON"
    INTERPRETATION_UNAVAILABLE = "INTERPRETATION_UNAVAILABLE"
    STATE_CONFLICT = "STATE_CONFLICT"


class ModelCallType(str, Enum):
    RESOLVE_INTENT_AND_DELTA = "resolve_intent_and_delta"
    GENERATE_CLARIFYING_QUESTION = "generate_clarifying_question"
    PLAN_CONSTRAINED_REPAIR = "plan_constrained_repair"
    SYNTHESIZE_RESEARCH_ANSWER = "synthesize_research_answer"
    GENERATE_FOLLOW_UP_SUGGESTIONS = "generate_follow_up_suggestions"
    PROPOSE_CANONICAL_MAPPING = "propose_canonical_mapping"
    EXTRACT_SUPPORTED_ATTRIBUTES = "extract_supported_attributes"
    CLASSIFY_TAXONOMY = "classify_taxonomy"
    CLASSIFY_QUALITY_ISSUE = "classify_quality_issue"


class ModelStatus(str, Enum):
    OK = "OK"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"


class ToolStatus(str, Enum):
    OK = "OK"
    NO_CHANGE = "NO_CHANGE"
    NOT_FOUND = "NOT_FOUND"
    INVALID_INPUT = "INVALID_INPUT"
    UNAUTHORIZED = "UNAUTHORIZED"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    STALE = "STALE"
    CONFLICT = "CONFLICT"
    REVALIDATION_REQUIRED = "REVALIDATION_REQUIRED"
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class TruthStatus(str, Enum):
    VERIFIED = "VERIFIED"
    UNKNOWN = "UNKNOWN"
    NOT_MODELED = "NOT_MODELED"
    DERIVED = "DERIVED"


class FactScope(str, Enum):
    CATALOG = "CATALOG"
    PROTOTYPE = "PROTOTYPE"
    EXTERNAL = "EXTERNAL"


class FallbackState(str, Enum):
    NONE = "NONE"
    CURRENT_SESSION_ONLY = "CURRENT_SESSION_ONLY"
    DETERMINISTIC_TEMPLATE = "DETERMINISTIC_TEMPLATE"
    DETERMINISTIC_EXACT_GRAMMAR = "DETERMINISTIC_EXACT_GRAMMAR"
    BASELINE_RESULT = "BASELINE_RESULT"
    PARTIAL_EXTERNAL_EVIDENCE = "PARTIAL_EXTERNAL_EVIDENCE"
    OMITTED = "OMITTED"


class ConstraintOperator(str, Enum):
    EQ = "EQ"
    IN = "IN"
    ALL_OF = "ALL_OF"
    GTE = "GTE"
    GT = "GT"
    LTE = "LTE"
    LT = "LT"
    RANGE = "RANGE"


class SoftOperator(str, Enum):
    EQ = "EQ"
    IN = "IN"
    PREFER_LOWER = "PREFER_LOWER"
    PREFER_HIGHER = "PREFER_HIGHER"


class Money(StrictModel):
    amount_paise: int = Field(ge=0, le=1_000_000_000)
    currency: Literal["INR"] = "INR"


class CompatibilityTuple(StrictModel):
    contract_schema_version: str = Field(min_length=1, max_length=64)
    catalog_version: str = Field(min_length=1, max_length=128)
    index_version: str = Field(min_length=1, max_length=128)
    taxonomy_version: str = Field(min_length=1, max_length=128)
    category_schema_version: str = Field(min_length=1, max_length=128)
    lexicon_version: str = Field(min_length=1, max_length=128)
    rank_policy_version: str = Field(min_length=1, max_length=128)
    gate_policy_version: str = Field(min_length=1, max_length=128)
    intent_prompt_version: str = Field(min_length=1, max_length=128)
    intent_model_alias: str = Field(min_length=1, max_length=256)
    response_template_version: str = Field(min_length=1, max_length=128)
    commerce_policy_version: str = Field(min_length=1, max_length=128)
    research_policy_version: str = Field(min_length=1, max_length=128)
    suggestion_policy_version: str = Field(min_length=1, max_length=128)
    memory_schema_version: str = Field(min_length=1, max_length=128)
    query_enhancement_policy_version: str = Field(min_length=1, max_length=128)
    clarification_prompt_version: str | None = None
    clarification_model_alias: str | None = None
    recovery_prompt_version: str | None = None
    recovery_model_alias: str | None = None
    research_provider_version: str | None = None
    research_prompt_version: str | None = None
    research_model_alias: str | None = None
    suggestion_prompt_version: str | None = None
    suggestion_model_alias: str | None = None


class ProductBinding(StrictModel):
    product_id: str = Field(min_length=1, max_length=128)
    sku_id: str = Field(min_length=1, max_length=128)
    offer_id: str = Field(min_length=1, max_length=128)
    catalog_version: str = Field(min_length=1, max_length=128)


class ResolvedReference(StrictModel):
    result_entry_id: str
    display_position: int = Field(ge=1, le=10)
    binding: ProductBinding


class ReferenceResolution(StrictModel):
    status: Literal["RESOLVED", "AMBIGUOUS", "STALE", "NOT_FOUND"]
    references: list[ResolvedReference] = Field(default_factory=list, max_length=4)
    reason_code: str | None = None
    options: list[ClarificationOption] = Field(default_factory=list, max_length=4)


class CommerceEligibility(StrictModel):
    eligible: bool
    binding: ProductBinding
    policy_status: str
    price: Money | None = None
    availability_status: str | None = None
    reasons: list[str] = Field(default_factory=list, max_length=8)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=8)


class ConfidenceDecision(StrictModel):
    decision: Literal["ACCEPT", "RECOVER", "CLARIFY", "NO_SAFE_RECOVERY"]
    reasons: list[str] = Field(default_factory=list, max_length=8)
    hard_filter_hash: str
    signals: dict[str, float] = Field(default_factory=dict, max_length=12)


class RecoveryRun(StrictModel):
    result: SearchResult | None = None
    hard_filter_hash: str
    added_concepts: list[str] = Field(default_factory=list, max_length=8)
    planner_used: bool = False


class EnhancementOutput(StrictModel):
    envelope: EnhancedQueryEnvelope
    projection: IntentContextProjectionV1
    fallback: FallbackState = FallbackState.NONE
    included_source_ids: list[str] = Field(default_factory=list, max_length=100)
    excluded_source_ids: list[str] = Field(default_factory=list, max_length=100)
    token_count: int = Field(ge=0, le=1500)
    latency_ms: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list, max_length=8)


class EvidenceRef(StrictModel):
    evidence_id: str = Field(min_length=1, max_length=128)
    entity_type: str = Field(min_length=1, max_length=64)
    entity_id: str = Field(min_length=1, max_length=128)
    field_path: str = Field(min_length=1, max_length=256)
    source_type: str = Field(min_length=1, max_length=64)
    source_id: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=128)


class Fact(StrictModel):
    fact_id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=128)
    typed_value: Any
    status: TruthStatus
    scope: FactScope
    provenance_type: str = Field(min_length=1, max_length=64)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=8)
    as_of: datetime | None = None
    formatting_rule: str | None = Field(default=None, max_length=128)


class Constraint(StrictModel):
    field_id: str = Field(min_length=1, max_length=128)
    operator: ConstraintOperator
    values: list[Any] = Field(min_length=1, max_length=8)
    provenance_turn_id: str = Field(min_length=1, max_length=128)
    explicit: bool


class Preference(StrictModel):
    field_id: str = Field(min_length=1, max_length=128)
    operator: SoftOperator
    values: list[Any] = Field(default_factory=list, max_length=8)
    weight: int = Field(default=1, ge=1, le=10)
    provenance_turn_id: str = Field(min_length=1, max_length=128)
    explicit: bool
    comparative_anchor: str | None = None


class PendingClarification(StrictModel):
    clarification_id: str = Field(min_length=1, max_length=128)
    reason_code: str = Field(min_length=1, max_length=128)
    choice_ids: list[str] = Field(min_length=1, max_length=4)
    state_hash: str = Field(min_length=1, max_length=128)
    expires_at: datetime


class QueryState(StrictModel):
    state_version: int = Field(ge=0)
    hard_constraints: list[Constraint] = Field(default_factory=list, max_length=20)
    soft_preferences: list[Preference] = Field(default_factory=list, max_length=20)
    query_terms: list[str] = Field(default_factory=list, max_length=10)
    selected_result_entry_ids: list[str] = Field(default_factory=list, max_length=4)
    pending_clarification: PendingClarification | None = None
    latest_result_set_id: str | None = None
    active_suggestion_set_id: str | None = None
    catalog_version: str
    index_version: str
    lexicon_version: str
    compact_goal_summary: str = Field(default="", max_length=500)


class CartSummary(StrictModel):
    cart_id: str
    cart_version: int = Field(ge=0)
    item_count: int = Field(ge=0)
    total_quantity: int = Field(ge=0)
    item_ids: list[str] = Field(default_factory=list, max_length=50)


class ActiveResultBinding(StrictModel):
    result_entry_id: str
    display_position: int = Field(ge=1, le=10)
    binding: ProductBinding
    context_ref: str | None = None


class RecentResultContext(StrictModel):
    """Small, provider-safe rendering of one previously displayed result."""

    result_entry_id: str
    display_position: int = Field(ge=1, le=10)
    binding: ProductBinding
    title: str = Field(min_length=1, max_length=300)
    matched_criteria: list[str] = Field(default_factory=list, max_length=8)
    unknown_criteria: list[str] = Field(default_factory=list, max_length=8)


class RecentTurnContext(StrictModel):
    """One complete, bounded shopper/assistant turn pair.

    This is deliberately a compact context record rather than a transcript. The
    current message and current snapshot remain authoritative; these records
    only help the enhancement and intent stages resolve natural follow-ups.
    """

    turn_id: str
    user_query: str = Field(min_length=1, max_length=2000)
    assistant_summary: str = Field(max_length=1000)
    action: Action
    terminal_state: TerminalState
    state_version: int = Field(ge=0)
    hard_constraints: list[Constraint] = Field(default_factory=list, max_length=8)
    soft_preferences: list[Preference] = Field(default_factory=list, max_length=8)
    query_terms: list[str] = Field(default_factory=list, max_length=10)
    result_set_id: str | None = Field(default=None, max_length=128)
    results: list[RecentResultContext] = Field(default_factory=list, max_length=5)
    referenced_result_entry_ids: list[str] = Field(default_factory=list, max_length=10)
    warnings: list[str] = Field(default_factory=list, max_length=12)
    clarification_reason_code: str | None = Field(default=None, max_length=128)


class MemoryCandidate(StrictModel):
    memory_item_id: str
    kind: Literal[
        "EXPLICIT_PREFERENCE",
        "EXPLICIT_AVOIDANCE",
        "SESSION_SUMMARY",
        "INTERACTION_SIGNAL",
    ]
    field_id: str | None = None
    typed_value: Any = None
    taxonomy_scope_ids: list[str] = Field(default_factory=list, max_length=8)
    confidence: Literal["VERIFIED", "EXPLICIT", "INFERRED"]
    provenance_refs: list[str] = Field(default_factory=list, max_length=8)
    age_seconds: int = Field(ge=0)


class ProjectedMemoryCandidate(StrictModel):
    """Provider-safe memory item without stable owner/source identifiers."""

    context_ref: str = Field(min_length=8, max_length=32)
    kind: Literal[
        "EXPLICIT_PREFERENCE",
        "EXPLICIT_AVOIDANCE",
        "SESSION_SUMMARY",
        "INTERACTION_SIGNAL",
    ]
    field_id: str | None = None
    typed_value: Any = None
    taxonomy_scope_ids: list[str] = Field(default_factory=list, max_length=8)
    confidence: Literal["VERIFIED", "EXPLICIT", "INFERRED"]
    age_bucket: Literal["0_7_DAYS", "8_30_DAYS", "31_90_DAYS", "91_180_DAYS", "OLDER"]


class PurchaseContext(StrictModel):
    source_purchase_id: str
    public_product_name: str = Field(min_length=1, max_length=200)
    taxonomy_node_id: str | None = None
    selected_attributes: dict[str, Any] = Field(default_factory=dict, max_length=16)
    purchased_at: datetime | None = None
    provenance_type: Literal["VERIFIED_DEMO", "VERIFIED_PROVIDER"]


class ProjectedPurchaseContext(StrictModel):
    """Provider-safe purchase context without profile/purchase/source IDs."""

    context_ref: str = Field(min_length=8, max_length=32)
    public_product_name: str = Field(min_length=1, max_length=200)
    taxonomy_node_id: str | None = None
    selected_attributes: dict[str, Any] = Field(default_factory=dict, max_length=16)
    age_bucket: Literal["0_7_DAYS", "8_30_DAYS", "31_90_DAYS", "91_180_DAYS", "OLDER"]
    provenance_type: Literal["VERIFIED_DEMO", "VERIFIED_PROVIDER"]


class EnhancementConflict(StrictModel):
    conflict_id: str
    field_scope: str
    memory_item_ids: list[str] = Field(min_length=1, max_length=8)
    reason: str


class ExclusionSummary(StrictModel):
    reason_code: str
    count: int = Field(ge=1)


class EnhancedQueryEnvelope(StrictModel):
    enhancement_schema_version: Literal["1"] = "1"
    enhancement_id: str
    session_id: str
    originating_client_turn_id: str
    state_version: int = Field(ge=0)
    memory_profile_id: str | None = None
    memory_version: int | None = Field(default=None, ge=0)
    locale: Literal["en-IN"] = "en-IN"
    current_message_verbatim: str = Field(min_length=1, max_length=2000)
    normalized_current_message: str = Field(min_length=1, max_length=2000)
    action_context: Literal["FREE_TEXT_CHAT"] = "FREE_TEXT_CHAT"
    current_state: QueryState
    recent_turn_context: list[RecentTurnContext] = Field(default_factory=list, max_length=4)
    persistent_memory_candidates: list[MemoryCandidate] = Field(default_factory=list, max_length=50)
    verified_purchase_context: list[PurchaseContext] = Field(default_factory=list, max_length=10)
    active_result_bindings: list[ActiveResultBinding] = Field(default_factory=list, max_length=10)
    cart_summary: CartSummary
    conflicts: list[EnhancementConflict] = Field(default_factory=list, max_length=20)
    exclusions_summary: list[ExclusionSummary] = Field(default_factory=list, max_length=20)
    context_truncated: bool = False
    token_count: int = Field(ge=0, le=1500)
    token_count_approximate: bool = False
    enhancement_policy_version: str
    context_hash: str = Field(min_length=1, max_length=128)


class IntentContextProjectionV1(StrictModel):
    schema_version: Literal["IntentContextProjectionV1"] = "IntentContextProjectionV1"
    locale: Literal["en-IN"] = "en-IN"
    current_message_verbatim: str = Field(min_length=1, max_length=2000)
    normalized_current_message: str = Field(min_length=1, max_length=2000)
    current_state: QueryState
    recent_turn_context: list[RecentTurnContext] = Field(default_factory=list, max_length=4)
    persistent_memory_candidates: list[ProjectedMemoryCandidate] = Field(default_factory=list, max_length=50)
    verified_purchase_context: list[ProjectedPurchaseContext] = Field(default_factory=list, max_length=10)
    active_result_bindings: list[ActiveResultBinding] = Field(default_factory=list, max_length=10)
    cart_summary: CartSummary
    projection_hash: str = Field(min_length=1, max_length=128)


class ReferenceDraft(StrictModel):
    kind: Literal["ORDINAL", "DEMONSTRATIVE", "CONTEXT_REF", "OWNED_ID", "COMPARISON_SET"]
    value: str = Field(min_length=1, max_length=128)


_ORDINAL_ALIASES = {
    "first": "1",
    "1st": "1",
    "one": "1",
    "second": "2",
    "2nd": "2",
    "two": "2",
    "third": "3",
    "3rd": "3",
    "three": "3",
    "fourth": "4",
    "4th": "4",
    "four": "4",
    "fifth": "5",
    "5th": "5",
    "five": "5",
    "sixth": "6",
    "6th": "6",
    "six": "6",
    "seventh": "7",
    "7th": "7",
    "seven": "7",
    "eighth": "8",
    "8th": "8",
    "eight": "8",
    "ninth": "9",
    "9th": "9",
    "nine": "9",
    "tenth": "10",
    "10th": "10",
    "ten": "10",
}


def _canonical_ordinal(value: str) -> str | None:
    """Return a bounded display position for a lexical ordinal alias.

    Providers and typed clients may naturally use words such as ``first`` or
    ``the first one``.  The resolver contract uses the displayed numeric
    position, so this converts only the finite ordinal vocabulary and leaves
    arbitrary values untouched for safe rejection/clarification.
    """

    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    normalized = " ".join(normalized.split())
    if normalized.startswith("the "):
        normalized = normalized[4:]
    for suffix in (" one", " option", " result", " item"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)].rstrip()
            break
    if normalized in _ORDINAL_ALIASES:
        return _ORDINAL_ALIASES[normalized]
    if normalized.isdecimal():
        position = int(normalized)
        if 1 <= position <= 10:
            return str(position)
    return None


def _canonicalize_references(references: list[ReferenceDraft]) -> list[ReferenceDraft]:
    """Canonicalize provider/client reference spellings before routing.

    ``COMPARISON_SET`` was part of the original provider-facing vocabulary and
    is still accepted for compatibility.  Expand the safe ordinal form into
    the individual references consumed by the deterministic resolver.
    """

    normalized: list[ReferenceDraft] = []
    for reference in references:
        if reference.kind == "ORDINAL":
            ordinal = _canonical_ordinal(reference.value)
            normalized.append(
                reference.model_copy(update={"value": ordinal})
                if ordinal is not None
                else reference
            )
            continue
        if reference.kind == "COMPARISON_SET":
            parts = [
                part.strip()
                for part in re.split(r"\s*(?:,|\band\b)\s*", reference.value, flags=re.IGNORECASE)
                if part.strip()
            ]
            ordinals = [_canonical_ordinal(part) for part in parts]
            if len(parts) >= 2 and all(ordinal is not None for ordinal in ordinals):
                expanded = [
                    ReferenceDraft(kind="ORDINAL", value=ordinal)
                    for ordinal in ordinals
                    if ordinal is not None
                ]
                if len(normalized) + len(expanded) <= 5:
                    normalized.extend(expanded)
                    continue
        normalized.append(reference)
    return normalized


class SetHardOperation(StrictModel):
    op: Literal["SET_HARD"] = "SET_HARD"
    field_id: str
    operator: ConstraintOperator
    typed_values: list[Any] = Field(min_length=1, max_length=8)
    strength: Literal["HARD"] = "HARD"
    evidence_span: tuple[int, int] | None = None
    context_ref: str | None = None


class RemoveHardOperation(StrictModel):
    op: Literal["REMOVE_HARD"] = "REMOVE_HARD"
    field_id: str
    evidence_span: tuple[int, int] | None = None


class SetSoftOperation(StrictModel):
    op: Literal["SET_SOFT"] = "SET_SOFT"
    field_id: str
    operator: SoftOperator
    typed_values: list[Any] = Field(default_factory=list, max_length=8)
    weight: int = Field(default=1, ge=1, le=10)
    evidence_span: tuple[int, int] | None = None


class RemoveSoftOperation(StrictModel):
    op: Literal["REMOVE_SOFT"] = "REMOVE_SOFT"
    field_id: str


class AddScopeOperation(StrictModel):
    op: Literal["ADD_SCOPE"] = "ADD_SCOPE"
    taxonomy_node_id: str


class RemoveScopeOperation(StrictModel):
    op: Literal["REMOVE_SCOPE"] = "REMOVE_SCOPE"
    taxonomy_node_id: str


class SetComparativeOperation(StrictModel):
    op: Literal["SET_COMPARATIVE"] = "SET_COMPARATIVE"
    kind: Literal["CHEAPER", "LARGER", "BETTER"]
    anchor_reference: ReferenceDraft | None = None


class ClearSearchStateOperation(StrictModel):
    op: Literal["CLEAR_SEARCH_STATE"] = "CLEAR_SEARCH_STATE"
    confirmed: Literal[True] = True


DeltaOperation = Annotated[
    Union[
        SetHardOperation,
        RemoveHardOperation,
        SetSoftOperation,
        RemoveSoftOperation,
        AddScopeOperation,
        RemoveScopeOperation,
        SetComparativeOperation,
        ClearSearchStateOperation,
    ],
    Field(discriminator="op"),
]


class IntentDeltaV1(StrictModel):
    schema_version: Literal["IntentDeltaV1"] = "IntentDeltaV1"
    primary_action: Action
    delta_operations: list[DeltaOperation] = Field(default_factory=list, max_length=20)
    references: list[ReferenceDraft] = Field(default_factory=list, max_length=5)
    action_parameters: dict[str, Any] = Field(default_factory=dict, max_length=20)
    unknown_terms: list[str] = Field(default_factory=list, max_length=10)
    candidate_interpretations: list[str] = Field(default_factory=list, max_length=4)
    clarification_candidate: str | None = None

    @model_validator(mode="after")
    def canonicalize_reference_values(self) -> IntentDeltaV1:
        normalized = _canonicalize_references(self.references)
        if normalized != self.references:
            object.__setattr__(self, "references", normalized)
        return self


class RecoveryRewritePlan(StrictModel):
    action: Literal["REWRITE_WITH_ALLOWED_CONCEPTS"] = "REWRITE_WITH_ALLOWED_CONCEPTS"
    added_concept_ids: list[str] = Field(min_length=1, max_length=4)
    interpretation_label: str = Field(min_length=1, max_length=160)
    preserved_hard_filter_hash: str


class RecoveryClarificationPlan(StrictModel):
    action: Literal["ASK_CLARIFICATION"] = "ASK_CLARIFICATION"
    option_ids: list[str] = Field(min_length=1, max_length=4)
    target_field: str
    reason: str


class RecoveryNoSafePlan(StrictModel):
    action: Literal["NO_SAFE_RECOVERY"] = "NO_SAFE_RECOVERY"
    reason_code: str


RecoveryPlan = Annotated[
    Union[RecoveryRewritePlan, RecoveryClarificationPlan, RecoveryNoSafePlan],
    Field(discriminator="action"),
]


class UiAction(StrictModel):
    action: Action
    payload: dict[str, Any] = Field(default_factory=dict, max_length=30)
    signed_action_token: str | None = None


class TurnRequest(StrictModel):
    session_id: str = Field(min_length=1, max_length=128)
    client_turn_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=128)
    expected_state_version: int = Field(ge=0)
    expected_cart_version: int = Field(ge=0)
    locale: Literal["en-IN"] = "en-IN"
    message: str | None = Field(default=None, max_length=2000)
    ui_action: UiAction | None = None
    ignore_history: bool = False

    @model_validator(mode="after")
    def exactly_one_input(self) -> "TurnRequest":
        if (self.message is None) == (self.ui_action is None):
            raise ValueError("exactly one of message or ui_action is required")
        if self.message is not None and not self.message.strip():
            raise ValueError("message must not be empty")
        if self.message is not None:
            normalized = unicodedata.normalize("NFKC", self.message).casefold()
            if len(normalized.split()) > 100:
                raise ValueError("message exceeds the 100-token limit")
        return self


class ModelRequest(StrictModel):
    call_id: str
    logical_call_type: ModelCallType
    model_alias: str
    prompt_id: str
    prompt_version: str
    input_schema_version: str
    output_schema_version: str
    input_payload: dict[str, Any]
    output_schema: dict[str, Any]
    deadline_ms: int = Field(gt=0)
    temperature: float = Field(ge=0, le=1)
    max_output_tokens: int = Field(gt=0, le=4000)
    response_format: Literal["STRICT_JSON_SCHEMA"] = "STRICT_JSON_SCHEMA"
    tools: list[Any] = Field(default_factory=list, max_length=0)
    compatibility_tuple: CompatibilityTuple


class ValidationIssue(StrictModel):
    code: str = Field(min_length=1, max_length=128)
    path: str = Field(min_length=1, max_length=256)
    safe_message: str = Field(min_length=1, max_length=500)


class ModelResponse(StrictModel):
    call_id: str
    status: ModelStatus
    output_payload: dict[str, Any] | None = None
    raw_output_hash: str | None = None
    input_hash: str
    output_hash: str | None = None
    provider_name: str
    model_alias: str
    prompt_id: str
    prompt_version: str
    latency_ms: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_minor_units: int | None = Field(default=None, ge=0)
    validation_issues: list[ValidationIssue] = Field(default_factory=list, max_length=20)
    retryable: bool = False


class ToolError(StrictModel):
    code: str = Field(min_length=1, max_length=128)
    safe_message: str = Field(min_length=1, max_length=500)
    details: dict[str, Any] = Field(default_factory=dict, max_length=8)


class ToolRequest(StrictModel):
    tool_run_id: str
    trace_id: str
    turn_id: str
    session_id: str | None = None
    tool_name: str
    tool_version: str
    compatibility_tuple: CompatibilityTuple
    expected_state_version: int | None = Field(default=None, ge=0)
    expected_cart_version: int | None = Field(default=None, ge=0)
    deadline_ms: int = Field(gt=0)
    auth_context_ref: str
    payload: dict[str, Any]


ToolData = TypeVar("ToolData")


class ToolResult(StrictModel, Generic[ToolData]):
    tool_run_id: str
    tool_name: str
    tool_version: str
    status: ToolStatus
    data: ToolData | None = None
    error: ToolError | None = None
    warnings: list[str] = Field(default_factory=list, max_length=8)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=20)
    latency_ms: int = Field(ge=0)
    retryable: bool = False


class ClarificationOption(StrictModel):
    choice_id: str
    label: str = Field(min_length=1, max_length=160)


class ClarificationDecision(StrictModel):
    required: bool
    reason_code: str
    target_field: str | None = None
    options: list[ClarificationOption] = Field(default_factory=list, max_length=4)
    preserved_state_hash: str


class ClarificationPacket(StrictModel):
    reason_code: str
    target_field: str
    preserved_state_summary: str = Field(max_length=500)
    options: list[ClarificationOption] = Field(min_length=1, max_length=4)
    locale: Literal["en-IN"] = "en-IN"


class ClarificationDraft(StrictModel):
    question: str = Field(min_length=1, max_length=500)
    choice_ids: list[str] = Field(min_length=1, max_length=4)
    question_count: Literal[1] = 1
    mentioned_values: list[str] = Field(default_factory=list, max_length=8)


class SearchRequest(StrictModel):
    session_id: str
    query_state: QueryState
    top_k: int = Field(ge=1, le=10)
    exclusions: list[str] = Field(default_factory=list, max_length=50)
    compatibility_tuple: CompatibilityTuple


class SearchEntry(StrictModel):
    result_entry_id: str
    display_position: int = Field(ge=1, le=10)
    binding: ProductBinding
    title: str = Field(min_length=1, max_length=300)
    facts: list[Fact] = Field(default_factory=list, max_length=20)
    score_components: dict[str, float] = Field(default_factory=dict, max_length=10)
    matched_criteria: list[str] = Field(default_factory=list, max_length=8)
    unknown_criteria: list[str] = Field(default_factory=list, max_length=8)


class SearchResult(StrictModel):
    status: ToolStatus
    result_set_id: str | None = Field(default=None, max_length=128)
    entries: list[SearchEntry] = Field(default_factory=list, max_length=10)
    eligible_count: int = Field(ge=0)
    confidence_signals: dict[str, float] = Field(default_factory=dict, max_length=12)
    hard_filter_hash: str
    warnings: list[str] = Field(default_factory=list, max_length=8)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=20)
    degraded_components: list[str] = Field(default_factory=list, max_length=8)


class GraphContextRequest(StrictModel):
    query_terms: list[str] = Field(default_factory=list, max_length=10)
    canonical_entity_ids: list[str] = Field(default_factory=list, max_length=8)
    allowed_relation_types: list[str] = Field(default_factory=list, max_length=8)
    max_depth: int = Field(ge=0, le=3)
    max_results: int = Field(ge=1, le=20)
    graph_version: str
    deadline_ms: int = Field(gt=0)


class GraphRelation(StrictModel):
    relation_id: str
    subject_id: str
    relation_type: str
    object_id: str
    evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=8)


class GraphContextResult(StrictModel):
    status: Literal["OK", "UNAVAILABLE", "STALE", "TRUNCATED"]
    relations: list[GraphRelation] = Field(default_factory=list, max_length=20)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=20)
    graph_version: str
    truncated: bool = False
    warnings: list[str] = Field(default_factory=list, max_length=8)


class ProductDetails(StrictModel):
    status: ToolStatus
    binding: ProductBinding
    title: str | None = None
    facts: list[Fact] = Field(default_factory=list, max_length=30)
    variants: list[ProductBinding] = Field(default_factory=list, max_length=20)
    warnings: list[str] = Field(default_factory=list, max_length=8)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=30)


class ComparisonCell(StrictModel):
    field_id: str
    value: Any = None
    status: TruthStatus
    evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=8)


class ComparisonRow(StrictModel):
    field_id: str
    label: str
    cells: list[ComparisonCell] = Field(min_length=2, max_length=4)


class Comparison(StrictModel):
    status: ToolStatus
    bindings: list[ProductBinding] = Field(min_length=2, max_length=4)
    rows: list[ComparisonRow] = Field(default_factory=list, max_length=40)
    warnings: list[str] = Field(default_factory=list, max_length=8)


class Availability(StrictModel):
    status: ToolStatus
    binding: ProductBinding
    availability_status: Literal["AVAILABLE", "UNAVAILABLE", "UNKNOWN", "NOT_MODELED", "RETIRED"]
    quantity: int | None = Field(default=None, ge=0)
    as_of: datetime | None = None
    truth_status: TruthStatus
    scope: FactScope = FactScope.PROTOTYPE
    provenance_type: str = "MOCK_CATALOG"
    evidence_refs: list[EvidenceRef] = Field(default_factory=list, max_length=8)
    warnings: list[str] = Field(default_factory=list, max_length=8)


class CartItem(StrictModel):
    cart_item_id: str
    binding: ProductBinding
    selected_attributes: dict[str, Any] = Field(default_factory=dict, max_length=16)
    quantity: int = Field(ge=1, le=99)
    unit_price: Money
    line_subtotal: Money
    availability_status: str
    price_as_of: datetime | None = None
    availability_as_of: datetime | None = None
    warnings: list[str] = Field(default_factory=list, max_length=8)


class CartSnapshot(StrictModel):
    cart_id: str
    session_id: str
    cart_version: int = Field(ge=0)
    state_version: int = Field(ge=0)
    items: list[CartItem] = Field(default_factory=list, max_length=50)
    item_count: int = Field(ge=0)
    total_quantity: int = Field(ge=0)
    subtotal: Money
    mock_status: Literal["PROTOTYPE_MOCK_CATALOG"] = "PROTOTYPE_MOCK_CATALOG"
    warnings: list[str] = Field(default_factory=list, max_length=8)


class AddItemOperation(StrictModel):
    type: Literal["ADD_ITEM"] = "ADD_ITEM"
    operation_id: str
    result_entry_id: str
    binding: ProductBinding
    selected_variant_hash: str
    quantity: int = Field(ge=1, le=99)
    expected_unit_price: Money | None = None


class SetQuantityOperation(StrictModel):
    type: Literal["SET_QUANTITY"] = "SET_QUANTITY"
    operation_id: str
    cart_item_id: str
    quantity: int = Field(ge=1, le=99)


class IncrementItemOperation(StrictModel):
    type: Literal["INCREMENT_ITEM"] = "INCREMENT_ITEM"
    operation_id: str
    cart_item_id: str
    amount: int = Field(ge=1, le=99)


class DecrementItemOperation(StrictModel):
    type: Literal["DECREMENT_ITEM"] = "DECREMENT_ITEM"
    operation_id: str
    cart_item_id: str
    amount: int = Field(ge=1, le=99)


class RemoveItemOperation(StrictModel):
    type: Literal["REMOVE_ITEM"] = "REMOVE_ITEM"
    operation_id: str
    cart_item_id: str


class ClearCartOperation(StrictModel):
    type: Literal["CLEAR_CART"] = "CLEAR_CART"
    operation_id: str
    confirmation_token: str | None = None


CartOperation = Annotated[
    Union[
        AddItemOperation,
        SetQuantityOperation,
        IncrementItemOperation,
        DecrementItemOperation,
        RemoveItemOperation,
        ClearCartOperation,
    ],
    Field(discriminator="type"),
]


class UpdateCartRequest(StrictModel):
    session_id: str
    expected_state_version: int = Field(ge=0)
    expected_cart_version: int = Field(ge=0)
    idempotency_key: str
    operations: list[CartOperation] = Field(min_length=1, max_length=10)
    atomic: Literal[True] = True


class RevalidationChange(StrictModel):
    field: str
    old_value: Any
    current_value: Any


class UpdateCartResult(StrictModel):
    status: Literal["UPDATED", "REVALIDATION_REQUIRED", "CONFLICT", "REJECTED"]
    applied_operation_ids: list[str] = Field(default_factory=list, max_length=10)
    cart: CartSnapshot
    revalidation_changes: list[RevalidationChange] = Field(default_factory=list, max_length=10)
    warnings: list[str] = Field(default_factory=list, max_length=8)


class ResearchDecision(StrictModel):
    decision: Literal["NOT_NEEDED", "OPTIONAL", "REQUIRED", "PROHIBITED"]
    reason_code: str
    question_type: Literal["CATALOG_FACT", "GENERAL_GUIDANCE", "CURRENT_EXTERNAL", "UNSUPPORTED"]
    canonical_query: str = Field(min_length=1, max_length=500)
    selected_entity_ids: list[str] = Field(default_factory=list, max_length=4)
    locale: Literal["en-IN"] = "en-IN"


class ResearchSource(StrictModel):
    source_id: str
    canonical_url: str
    domain: str
    title: str = Field(min_length=1, max_length=300)
    extract: str = Field(min_length=1, max_length=4000)
    extract_hash: str
    retrieved_at: datetime
    published_at: datetime | None = None
    trust_tier: Literal["OFFICIAL", "PRIMARY", "EDITORIAL", "OTHER"]


class OnlineSearchResult(StrictModel):
    status: Literal["SUCCESS", "PARTIAL", "NO_RESULTS", "BLOCKED", "TIMEOUT", "PROVIDER_UNAVAILABLE"]
    provider: str
    executed_query: str
    searched_at: datetime
    sources: list[ResearchSource] = Field(default_factory=list, max_length=5)
    warnings: list[str] = Field(default_factory=list, max_length=8)


class ResearchClaim(StrictModel):
    claim_id: str
    text: str = Field(min_length=1, max_length=500)
    support_source_ids: list[str] = Field(min_length=1, max_length=3)
    conflict_source_ids: list[str] = Field(default_factory=list, max_length=3)
    confidence: Literal["HIGH", "MEDIUM", "LOW"]


class ResearchSynthesisV1(StrictModel):
    schema_version: Literal["ResearchSynthesisV1"] = "ResearchSynthesisV1"
    answer_summary: str = Field(min_length=1, max_length=500)
    claims: list[ResearchClaim] = Field(default_factory=list, max_length=10)
    conflicts: list[str] = Field(default_factory=list, max_length=5)
    unanswered_points: list[str] = Field(default_factory=list, max_length=5)


class ValidatedResearch(StrictModel):
    status: Literal["VALID", "PARTIAL", "UNAVAILABLE"]
    summary: str = Field(default="", max_length=500)
    claims: list[ResearchClaim] = Field(default_factory=list, max_length=10)
    sources: list[ResearchSource] = Field(default_factory=list, max_length=5)
    conflicts: list[str] = Field(default_factory=list, max_length=5)
    warnings: list[str] = Field(default_factory=list, max_length=8)


class SuggestionCandidate(StrictModel):
    candidate_id: str
    action_type: Action
    safe_default_label: str = Field(min_length=1, max_length=60)
    payload: dict[str, Any] = Field(default_factory=dict, max_length=20)
    target_ids: list[str] = Field(default_factory=list, max_length=8)
    reason_code: str


class Suggestion(StrictModel):
    suggestion_id: str
    candidate: SuggestionCandidate
    label: str = Field(min_length=1, max_length=60)
    signed_action_token: str


class FollowUpLabel(StrictModel):
    candidate_id: str
    label: str = Field(min_length=1, max_length=60)


class FollowUpSelection(StrictModel):
    selected_candidate_ids: list[str] = Field(default_factory=list, max_length=3)
    labels: list[FollowUpLabel] = Field(default_factory=list, max_length=3)


class SuggestionSet(StrictModel):
    suggestion_set_id: str
    session_id: str
    state_version: int = Field(ge=0)
    cart_version: int = Field(ge=0)
    active_result_set_id: str | None = None
    suggestions: list[Suggestion] = Field(default_factory=list, max_length=3)
    generator_version: str


class SuggestionSelectionRequest(StrictModel):
    session_id: str
    suggestion_set_id: str
    suggestion_id: str
    signed_action_token: str
    expected_state_version: int = Field(ge=0)
    expected_cart_version: int = Field(ge=0)


class SuggestionSelectionResult(StrictModel):
    status: Literal["VALID", "SUGGESTION_STALE"]
    ui_action: UiAction | None = None
    reason: str | None = None


class StateDeltaSummary(StrictModel):
    added: list[str] = Field(default_factory=list, max_length=20)
    changed: list[str] = Field(default_factory=list, max_length=20)
    removed: list[str] = Field(default_factory=list, max_length=20)
    preserved: list[str] = Field(default_factory=list, max_length=20)


class ShopperResponse(StrictModel):
    response_id: str
    action: Action
    terminal_state: TerminalState
    summary: str = Field(max_length=1000)
    facts: list[Fact] = Field(default_factory=list, max_length=80)
    search_entries: list[SearchEntry] = Field(default_factory=list, max_length=10)
    result_set_id: str | None = Field(default=None, max_length=128)
    details: ProductDetails | None = None
    comparison: Comparison | None = None
    availability: Availability | None = None
    cart: CartSnapshot | None = None
    research: ValidatedResearch | None = None
    clarification: ClarificationDraft | None = None
    clarification_reason_code: str | None = None
    clarification_target_field: str | None = None
    state_delta: StateDeltaSummary = Field(default_factory=StateDeltaSummary)
    suggestions: SuggestionSet | None = None
    warnings: list[str] = Field(default_factory=list, max_length=12)
    compatibility_tuple: CompatibilityTuple
    trace_id: str


class TurnSnapshot(StrictModel):
    session_id: str
    state: QueryState
    state_version: int = Field(ge=0)
    cart_version: int = Field(ge=0)
    cart: CartSnapshot
    acknowledged_result_set_id: str | None = None
    acknowledged_entries: list[ActiveResultBinding] = Field(default_factory=list, max_length=10)
    recent_turns: list[RecentTurnContext] = Field(default_factory=list, max_length=4)
    memory_profile_id: str | None = None
    memory_version: int | None = Field(default=None, ge=0)
    compatibility_tuple: CompatibilityTuple
    pending_clarification: PendingClarification | None = None


class Reservation(StrictModel):
    status: Literal["NEW", "REPLAY", "IN_PROGRESS", "CONFLICT"]
    reservation_id: str
    stored_response: ShopperResponse | None = None
    status_ref: str | None = None


class CommitCommand(StrictModel):
    reservation_id: str
    session_id: str
    expected_state_version: int = Field(ge=0)
    expected_cart_version: int = Field(ge=0)
    response: ShopperResponse
    proposed_state: QueryState
    cart_changed: bool = False
    acknowledged_result_set_id: str | None = None
    acknowledged_entries: list[ActiveResultBinding] = Field(default_factory=list, max_length=10)
    recent_turns: list[RecentTurnContext] = Field(default_factory=list, max_length=4)


class CommitResult(StrictModel):
    committed: bool
    status: Literal["COMMITTED", "STATE_CONFLICT", "FAILED"]
    response: ShopperResponse | None = None
    state_version: int = Field(ge=0)
    cart_version: int = Field(ge=0)
    safe_reason: str | None = None


class TraceEvent(StrictModel):
    event_name: str
    trace_id: str
    turn_id: str
    stage: str
    logical_name: str
    status: str
    latency_ms: int = Field(ge=0)
    input_hash: str | None = None
    output_hash: str | None = None
    validation_codes: list[str] = Field(default_factory=list, max_length=20)
    fallback: FallbackState = FallbackState.NONE
    safe_metadata: dict[str, Any] = Field(default_factory=dict, max_length=20)


class PublicTrace(StrictModel):
    trace_id: str
    action: Action | None = None
    terminal_state: TerminalState
    events: list[TraceEvent] = Field(default_factory=list, max_length=100)
    compatibility_tuple: CompatibilityTuple


class TraceHistoryResponse(StrictModel):
    session_id: str
    trace_count: int = Field(ge=0, le=100)
    traces: list[PublicTrace] = Field(default_factory=list, max_length=100)


class TurnResult(StrictModel):
    status: Literal["COMPLETED", "IN_PROGRESS", "REJECTED"]
    http_status: Literal[200, 202, 404, 409, 422, 429, 500, 503, 504]
    response: ShopperResponse | None = None
    trace: PublicTrace
    status_ref: str | None = None


class MarkdownHandoff(StrictModel):
    response_id: str
    status: Literal["READY_FOR_MARKDOWN", "UNAVAILABLE"]
    schema_version: Literal["shopper-response-v1"] = "shopper-response-v1"
    handoff_ref: str | None = None


# A few integration contracts intentionally refer to models declared later in this
# module. Rebuild them once the complete contract vocabulary is available.
for _model in (ReferenceResolution, CommerceEligibility, RecoveryRun, EnhancementOutput, ToolResult):
    _model.model_rebuild()
