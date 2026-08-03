"""Strict domain contracts for the confidence-gated recovery boundary."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    """Trust-boundary base model; provider and adapter payloads use this too."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        validate_assignment=True,
        frozen=False,
    )


class RecoveryTriggerReason(str, Enum):
    NO_ELIGIBLE_RESULTS = "NO_ELIGIBLE_RESULTS"
    UNKNOWN_IMPORTANT_TERM = "UNKNOWN_IMPORTANT_TERM"
    AMBIGUOUS_CATEGORY_OR_ATTRIBUTE = "AMBIGUOUS_CATEGORY_OR_ATTRIBUTE"
    LOW_COVERAGE = "LOW_COVERAGE"
    CATEGORY_SCOPE_LEAKAGE = "CATEGORY_SCOPE_LEAKAGE"
    CONSTRAINT_CONFLICT = "CONSTRAINT_CONFLICT"
    INCOMPLETE_ACTION_PARAMETERS = "INCOMPLETE_ACTION_PARAMETERS"
    UNCALIBRATED_GATE = "UNCALIBRATED_GATE"


class GateDecision(str, Enum):
    CONFIDENT = "CONFIDENT"
    RECOVERY_ELIGIBLE = "RECOVERY_ELIGIBLE"
    NO_ELIGIBLE_MATCH = "NO_ELIGIBLE_MATCH"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"


class ComparatorDecision(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    AMBIGUOUS = "AMBIGUOUS"


class RecoveryOutcome(str, Enum):
    RECOVERY_SKIPPED_CONFIDENT = "RECOVERY_SKIPPED_CONFIDENT"
    RECOVERY_SKIPPED_UNSAFE_GATE = "RECOVERY_SKIPPED_UNSAFE_GATE"
    RECOVERED_TIER1 = "RECOVERED_TIER1"
    RECOVERED_TIER2 = "RECOVERED_TIER2"
    BASELINE_PRESERVED = "BASELINE_PRESERVED"
    RECOVERY_SUGGESTIONS = "RECOVERY_SUGGESTIONS"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    NO_SAFE_RECOVERY = "NO_SAFE_RECOVERY"
    RECOVERY_UNAVAILABLE = "RECOVERY_UNAVAILABLE"


class PlannerAction(str, Enum):
    REWRITE_WITH_ALLOWED_CONCEPTS = "REWRITE_WITH_ALLOWED_CONCEPTS"
    ASK_CLARIFICATION = "ASK_CLARIFICATION"
    NO_SAFE_RECOVERY = "NO_SAFE_RECOVERY"


class MappingType(str, Enum):
    SPELLING_VARIANT = "SPELLING_VARIANT"
    ALIAS = "ALIAS"
    ATTRIBUTE_PARAPHRASE = "ATTRIBUTE_PARAPHRASE"
    COMPOUND = "COMPOUND"


class ExpansionAction(str, Enum):
    CANONICAL_SYNONYM = "CANONICAL_SYNONYM"
    CANONICAL_ATTRIBUTE = "CANONICAL_ATTRIBUTE"
    CANONICAL_VALUE = "CANONICAL_VALUE"


class EvidenceBand(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    APPROVED_HIGH = "APPROVED_HIGH"


class ConceptType(str, Enum):
    TAXONOMY = "TAXONOMY"
    ATTRIBUTE = "ATTRIBUTE"
    VALUE = "VALUE"
    BRAND = "BRAND"


class HardConstraint(StrictModel):
    field_id: str = Field(min_length=1, max_length=128)
    operator: str = Field(min_length=1, max_length=32)
    values: list[Any] = Field(min_length=1, max_length=8)
    mode: Literal["MUST", "MUST_NOT"] = "MUST"
    provenance_turn_id: str = Field(min_length=1, max_length=128)
    explicit: bool = True

    def canonical_clause(self) -> dict[str, Any]:
        """Return only semantic filter fields used by the hard-filter hash."""

        return {
            "field_id": self.field_id,
            "mode": self.mode,
            "operator": self.operator,
            "values": self.values,
        }


class SoftPreference(StrictModel):
    field_id: str = Field(min_length=1, max_length=128)
    operator: str = Field(min_length=1, max_length=32)
    values: list[Any] = Field(default_factory=list, max_length=8)
    weight: int = Field(default=1, ge=1, le=10)


class QueryState(StrictModel):
    """The canonical state projection recovery is allowed to consume."""

    schema_version: str = Field(default="query-state-v1", min_length=1, max_length=64)
    hard_constraints: list[HardConstraint] = Field(default_factory=list, max_length=20)
    soft_preferences: list[SoftPreference] = Field(default_factory=list, max_length=20)
    query_terms: list[str] = Field(default_factory=list, max_length=20)
    taxonomy_scope_id: str | None = Field(default=None, max_length=128)
    locale: Literal["en-IN"] = "en-IN"
    catalog_version: str = Field(min_length=1, max_length=128)
    index_version: str = Field(min_length=1, max_length=128)
    taxonomy_version: str = Field(min_length=1, max_length=128)
    category_schema_version: str = Field(min_length=1, max_length=128)
    lexicon_version: str = Field(min_length=1, max_length=128)


class CompatibilityTuple(StrictModel):
    """Recovery-relevant portion of the turn compatibility tuple.

    The shopper orchestrator may wrap this in its larger tuple. Recovery refuses
    to mix the values represented here, and the extra-forbid boundary makes a
    missing integration mapping visible during contract tests.
    """

    contract_schema_version: str = Field(min_length=1, max_length=64)
    catalog_version: str = Field(min_length=1, max_length=128)
    index_version: str = Field(min_length=1, max_length=128)
    taxonomy_version: str = Field(min_length=1, max_length=128)
    category_schema_version: str = Field(min_length=1, max_length=128)
    lexicon_version: str = Field(min_length=1, max_length=128)
    rank_policy_version: str = Field(min_length=1, max_length=128)
    gate_policy_version: str = Field(min_length=1, max_length=128)
    recovery_policy_version: str = Field(min_length=1, max_length=128)
    recovery_prompt_version: str | None = Field(default=None, max_length=128)
    recovery_model_alias: str | None = Field(default=None, max_length=256)


class BaselineSignals(StrictModel):
    eligible_count: int = Field(ge=0)
    top_score: float | None = Field(default=None, ge=-1.0, le=1.0)
    top_score_margin: float | None = Field(default=None, ge=0.0, le=2.0)
    required_criteria_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    unknown_preference_weight: float = Field(default=0.0, ge=0.0)
    unknown_terms: list[str] = Field(default_factory=list, max_length=10)
    parser_ambiguous: bool = False
    constraint_conflict: bool = False
    category_scope_consistent: bool = True
    hard_filter_violations: int = Field(default=0, ge=0)
    protected_exclusion_violations: int = Field(default=0, ge=0)


class RetrievalRun(StrictModel):
    """Sanitized output of one deterministic retrieval execution."""

    run_id: str = Field(min_length=1, max_length=128)
    query_state_hash: str = Field(min_length=64, max_length=64)
    hard_filter_hash: str = Field(min_length=64, max_length=64)
    compatibility: CompatibilityTuple
    eligible_count: int = Field(ge=0)
    top_score: float | None = Field(default=None, ge=-1.0, le=1.0)
    top_score_margin: float | None = Field(default=None, ge=0.0, le=2.0)
    required_criteria_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    category_scope_consistent: bool = True
    hard_filter_violations: int = Field(default=0, ge=0)
    protected_exclusion_violations: int = Field(default=0, ge=0)
    result_product_ids: list[str] = Field(default_factory=list, max_length=5)
    query_branches: list[str] = Field(default_factory=list, max_length=3)
    interpretation_family: str | None = Field(default=None, max_length=128)
    degraded: bool = False
    warnings: list[str] = Field(default_factory=list, max_length=12)

    @property
    def is_scope_safe(self) -> bool:
        return (
            self.category_scope_consistent
            and self.hard_filter_violations == 0
            and self.protected_exclusion_violations == 0
        )


class RecoveryGate(StrictModel):
    decision: GateDecision
    reasons: list[RecoveryTriggerReason] = Field(default_factory=list, max_length=8)
    unknown_terms: list[str] = Field(default_factory=list, max_length=10)
    signals: BaselineSignals
    hard_filter_hash: str = Field(min_length=64, max_length=64)
    gate_policy_version: str = Field(min_length=1, max_length=128)
    calibrated: bool = False
    reason_details: dict[str, str] = Field(default_factory=dict, max_length=8)


class ApprovedExpansion(StrictModel):
    mapping_id: str = Field(min_length=1, max_length=128)
    normalized_form: str = Field(min_length=1, max_length=256)
    original_form: str = Field(min_length=1, max_length=256)
    canonical_target_id: str = Field(min_length=1, max_length=128)
    canonical_label: str = Field(min_length=1, max_length=256)
    concept_type: ConceptType
    mapping_type: MappingType
    expansion_action: ExpansionAction
    locale: Literal["en-IN"] = "en-IN"
    taxonomy_scope_id: str | None = Field(default=None, max_length=128)
    attribute_id: str | None = Field(default=None, max_length=128)
    lexicon_version: str = Field(min_length=1, max_length=128)
    catalog_version: str = Field(min_length=1, max_length=128)
    taxonomy_version: str = Field(min_length=1, max_length=128)
    category_schema_version: str = Field(min_length=1, max_length=128)
    evidence_band: EvidenceBand
    priority: int = Field(default=0, ge=0, le=1000)
    status: Literal["APPROVED"] = "APPROVED"


class RecoveryConstraint(StrictModel):
    """One concept the planner may choose; it is supplied by deterministic code."""

    concept_id: str = Field(min_length=1, max_length=128)
    concept_type: ConceptType
    label: str = Field(min_length=1, max_length=256)
    canonical_term: str = Field(min_length=1, max_length=256)
    taxonomy_scope_id: str | None = Field(default=None, max_length=128)
    attribute_id: str | None = Field(default=None, max_length=128)
    value_id: str | None = Field(default=None, max_length=128)
    locale: Literal["en-IN"] = "en-IN"
    catalog_version: str = Field(min_length=1, max_length=128)
    taxonomy_version: str = Field(min_length=1, max_length=128)
    category_schema_version: str = Field(min_length=1, max_length=128)
    lexicon_version: str = Field(min_length=1, max_length=128)
    active: bool = True


class RecoveryContext(StrictModel):
    """Bounded planner context; it intentionally has no memory/history fields."""

    schema_version: Literal["recovery-context-v1"] = "recovery-context-v1"
    unresolved_terms: list[str] = Field(min_length=1, max_length=10)
    query_state: QueryState
    hard_filter_hash: str = Field(min_length=64, max_length=64)
    gate_reasons: list[RecoveryTriggerReason] = Field(min_length=1, max_length=8)
    baseline_run_id: str = Field(min_length=1, max_length=128)
    baseline_summary: BaselineSignals
    allowed_concepts: list[RecoveryConstraint] = Field(default_factory=list, max_length=20)
    approved_suggestions: list[ApprovedExpansion] = Field(default_factory=list, max_length=3)
    compatibility: CompatibilityTuple
    locale: Literal["en-IN"] = "en-IN"
    planner_input_hash: str = Field(min_length=64, max_length=64)


class RecoveryRewritePlan(StrictModel):
    action: Literal["REWRITE_WITH_ALLOWED_CONCEPTS"] = "REWRITE_WITH_ALLOWED_CONCEPTS"
    added_concept_ids: list[str] = Field(min_length=1, max_length=3)
    interpretation_label: str = Field(min_length=1, max_length=160)
    preserved_hard_filter_hash: str = Field(min_length=64, max_length=64)


class RecoveryClarificationPlan(StrictModel):
    action: Literal["ASK_CLARIFICATION"] = "ASK_CLARIFICATION"
    option_ids: list[str] = Field(min_length=2, max_length=4)
    target_field: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=256)
    preserved_hard_filter_hash: str = Field(min_length=64, max_length=64)


class RecoveryNoSafePlan(StrictModel):
    action: Literal["NO_SAFE_RECOVERY"] = "NO_SAFE_RECOVERY"
    reason_code: str = Field(min_length=1, max_length=128)
    preserved_hard_filter_hash: str = Field(min_length=64, max_length=64)


RecoveryPlannerOutput = Annotated[
    Union[RecoveryRewritePlan, RecoveryClarificationPlan, RecoveryNoSafePlan],
    Field(discriminator="action"),
]


class RecoveryPlan(StrictModel):
    """Validated executable rewrite plan; never constructed from raw model JSON."""

    source: Literal["DIRECT", "GENERATIVE"]
    mapping_ids: list[str] = Field(default_factory=list, max_length=5)
    added_concept_ids: list[str] = Field(min_length=1, max_length=3)
    added_query_terms: list[str] = Field(min_length=1, max_length=3)
    interpretation_label: str = Field(min_length=1, max_length=160)
    preserved_hard_filter_hash: str = Field(min_length=64, max_length=64)
    max_reruns: Literal[1] = 1

    @model_validator(mode="after")
    def concepts_and_terms_are_bounded(self) -> RecoveryPlan:
        if len(self.added_concept_ids) != len(self.added_query_terms):
            raise ValueError("concept and query-term counts must match")
        if len(set(self.added_concept_ids)) != len(self.added_concept_ids):
            raise ValueError("duplicate recovery concepts are not allowed")
        return self


class ComparatorResult(StrictModel):
    decision: ComparatorDecision
    rule_id: str = Field(min_length=1, max_length=128)
    reasons: list[str] = Field(default_factory=list, max_length=12)
    eligible_count_delta: int
    top_score_delta: float | None = None
    coverage_delta: float | None = None
    diversity_delta: int = 0
    hard_filter_hash_equal: bool
    compatibility_equal: bool


class ClarificationOption(StrictModel):
    option_id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=160)
    concept_id: str = Field(min_length=1, max_length=128)
    concept_type: ConceptType


class ClarificationPacket(StrictModel):
    question: str = Field(min_length=1, max_length=500)
    reason_code: str = Field(min_length=1, max_length=128)
    target_field: str = Field(min_length=1, max_length=128)
    options: list[ClarificationOption] = Field(min_length=2, max_length=4)
    preserved_hard_filter_hash: str = Field(min_length=64, max_length=64)
    state_hash: str = Field(min_length=64, max_length=64)


class RecoverySuggestion(StrictModel):
    """Bounded, executable-looking labels for silent query recovery."""

    suggestion_id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=160)
    query_terms: list[str] = Field(min_length=1, max_length=3)
    concept_ids: list[str] = Field(min_length=1, max_length=3)
    source: Literal["APPROVED_LEXICON", "ALLOWED_CONCEPT"]


class RecoveryPolicy(StrictModel):
    policy_version: str = Field(min_length=1, max_length=128)
    tier2_enabled: bool = True
    max_direct_mappings: int = Field(default=3, ge=1, le=3)
    max_added_concepts: int = Field(default=3, ge=1, le=3)
    max_lookup_terms: int = Field(default=3, ge=1, le=10)
    max_retrieval_runs: Literal[3] = 3
    recovery_deadline_ms: int = Field(default=1800, ge=1, le=10000)
    tier2_min_remaining_ms: int = Field(default=450, ge=50, le=5000)
    min_improvement_margin: float = Field(default=0.10, ge=0.0, le=1.0)
    max_top_score_drop: float = Field(default=0.05, ge=0.0, le=1.0)
    close_margin: float = Field(default=0.02, ge=0.0, le=1.0)
    close_diversity_gain: int = Field(default=1, ge=0, le=5)


class RecoveryRequest(StrictModel):
    session_id: str = Field(min_length=1, max_length=128)
    turn_id: str = Field(min_length=1, max_length=128)
    trace_id: str = Field(min_length=1, max_length=128)
    query_state: QueryState
    baseline_run: RetrievalRun
    gate: RecoveryGate
    unresolved_terms: list[str] = Field(default_factory=list, max_length=10)
    compatibility: CompatibilityTuple
    policy: RecoveryPolicy


class ToolReceipt(StrictModel):
    tool_name: str = Field(min_length=1, max_length=128)
    status: Literal["OK", "SKIPPED", "REJECTED", "FAILED", "TIMEOUT"]
    latency_ms: int = Field(default=0, ge=0)
    validation_codes: list[str] = Field(default_factory=list, max_length=20)


class RecoveryEvent(StrictModel):
    event_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    turn_id: str = Field(min_length=1, max_length=128)
    trace_id: str = Field(min_length=1, max_length=128)
    outcome: RecoveryOutcome
    terminal_state: str = Field(min_length=1, max_length=128)
    trigger_reasons: list[RecoveryTriggerReason] = Field(default_factory=list, max_length=8)
    original_terms: list[str] = Field(default_factory=list, max_length=10)
    hard_filter_hash_before: str = Field(min_length=64, max_length=64)
    hard_filter_hash_after: str = Field(min_length=64, max_length=64)
    baseline_run_id: str = Field(min_length=1, max_length=128)
    direct_run_id: str | None = Field(default=None, max_length=128)
    generative_run_id: str | None = Field(default=None, max_length=128)
    mapping_ids: list[str] = Field(default_factory=list, max_length=5)
    planner_action: PlannerAction | None = None
    planner_called: bool = False
    planner_validation_codes: list[str] = Field(default_factory=list, max_length=20)
    comparator_decisions: list[ComparatorDecision] = Field(default_factory=list, max_length=3)
    tool_calls: list[ToolReceipt] = Field(default_factory=list, max_length=12)
    retrieval_run_count: int = Field(ge=1, le=3)
    hard_filter_mutation_count: int = Field(default=0, ge=0)
    added_latency_ms: int = Field(default=0, ge=0)
    budget_ms: int = Field(ge=1)
    budget_used_ms: int = Field(ge=0)
    model_prompt_version: str | None = Field(default=None, max_length=128)
    model_alias: str | None = Field(default=None, max_length=256)
    compatibility: CompatibilityTuple
    cache_hit: bool = False
    warnings: list[str] = Field(default_factory=list, max_length=12)
    created_at: datetime


class RecoveryResponse(StrictModel):
    outcome: RecoveryOutcome
    terminal_state: str = Field(min_length=1, max_length=128)
    baseline_run: RetrievalRun
    selected_run: RetrievalRun | None = None
    plan: RecoveryPlan | None = None
    comparator: ComparatorResult | None = None
    clarification: ClarificationPacket | None = None
    suggestions: list[RecoverySuggestion] = Field(default_factory=list, max_length=3)
    interpretation_label: str | None = Field(default=None, max_length=160)
    warnings: list[str] = Field(default_factory=list, max_length=16)
    event: RecoveryEvent
