"""Pure validation and merge rules for model and tool boundaries."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ValidationError

from .contracts import (
    Action,
    AddScopeOperation,
    ClearSearchStateOperation,
    Constraint,
    ConstraintOperator,
    DeltaOperation,
    IntentContextProjectionV1,
    IntentDeltaV1,
    Preference,
    QueryState,
    ReferenceDraft,
    RemoveHardOperation,
    RemoveScopeOperation,
    RemoveSoftOperation,
    SetComparativeOperation,
    SetHardOperation,
    SetSoftOperation,
    SoftOperator,
    StateDeltaSummary,
    ToolStatus,
    ClarificationDraft,
    ClarificationPacket,
    ResearchSynthesisV1,
    ValidatedResearch,
    OnlineSearchResult,
)


def canonical_json(value: Any) -> str:
    """Serialize trusted contract data deterministically and reject non-finite numbers."""

    def normalize(item: Any) -> Any:
        if isinstance(item, BaseModel):
            return normalize(item.model_dump(mode="python", exclude_none=False))
        if isinstance(item, Enum):
            return item.value
        if isinstance(item, (datetime, date)):
            return item.isoformat()
        if isinstance(item, dict):
            return {str(key): normalize(value) for key, value in item.items()}
        if isinstance(item, (list, tuple)):
            return [normalize(value) for value in item]
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("canonical JSON does not permit non-finite numbers")
        return item

    encoded = json.dumps(
        normalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return encoded


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def conservative_token_count(text: str) -> int:
    """Shared fallback estimator; it never truncates an accepted message."""

    return max(1, len(text.strip().split())) if text.strip() else 0


def hard_filter_hash(state: QueryState) -> str:
    clauses = sorted(
        [constraint.model_dump(mode="json") for constraint in state.hard_constraints],
        key=lambda item: canonical_json(item),
    )
    return canonical_hash({"schema": "hard-filter-v1", "clauses": clauses})


def validate_intent_semantics(
    intent: IntentDeltaV1,
    projection: IntentContextProjectionV1,
    allowed_owned_ids: set[str],
) -> list[str]:
    """Validate model output after Pydantic parsing.

    This function treats provider JSON as untrusted. It does not decide whether a
    catalog result is relevant; that remains a retrieval/tool responsibility.
    """

    issues: list[str] = []
    current_text = projection.current_message_verbatim
    for operation in intent.delta_operations:
        if isinstance(operation, SetHardOperation):
            if operation.evidence_span is None:
                issues.append("HARD_CONSTRAINT_WITHOUT_CURRENT_EVIDENCE")
            else:
                start, end = operation.evidence_span
                if start < 0 or end < start or end > len(current_text):
                    issues.append("EVIDENCE_SPAN_OUT_OF_RANGE")
            if operation.context_ref is not None:
                issues.append("HARD_CONSTRAINT_CONTEXT_REF_NOT_ALLOWED")
        elif isinstance(operation, ClearSearchStateOperation) and intent.primary_action not in {
            Action.RESET_SEARCH,
            Action.SEARCH,
            Action.REFINE,
        }:
            issues.append("RESET_IN_WRONG_ACTION")

    for reference in intent.references:
        if reference.kind in {"OWNED_ID", "CONTEXT_REF"} and reference.value not in allowed_owned_ids:
            issues.append("REFERENCE_ID_NOT_IN_OWNED_ALLOWLIST")
        if reference.kind == "ORDINAL":
            try:
                ordinal = int(reference.value)
            except ValueError:
                ordinal = 0
            if ordinal < 1 or ordinal > 10:
                issues.append("ORDINAL_OUT_OF_RANGE")

    forbidden_parameter_names = {
        "tool",
        "tool_name",
        "url",
        "provider",
        "authorization",
        "action_signature",
        "database_query",
    }
    if forbidden_parameter_names.intersection(intent.action_parameters):
        issues.append("FORBIDDEN_ACTION_PARAMETER")
    if len(intent.action_parameters) > 20:
        issues.append("ACTION_PARAMETERS_LIMIT")

    # A memory-only candidate cannot create a hard clause. We reject the only
    # provider-visible indication of that attempt, rather than guessing.
    if any(
        isinstance(operation, SetHardOperation) and operation.context_ref is not None
        for operation in intent.delta_operations
    ):
        issues.append("MEMORY_ONLY_HARD_CONSTRAINT")
    return sorted(set(issues))


def normalize_action(action: Action) -> Action:
    if action in {Action.ADD_TO_CART, Action.REMOVE_FROM_CART}:
        return Action.UPDATE_CART
    return action


def merge_query_state(
    previous: QueryState,
    intent: IntentDeltaV1,
    turn_id: str,
) -> tuple[QueryState, StateDeltaSummary]:
    """Apply the deterministic plan-06 merge matrix without retrieval side effects."""

    hard = list(previous.hard_constraints)
    soft = list(previous.soft_preferences)
    terms = list(previous.query_terms)
    selected = list(previous.selected_result_entry_ids)
    pending = previous.pending_clarification
    latest_result_set_id = previous.latest_result_set_id
    active_suggestion_set_id = previous.active_suggestion_set_id
    added: list[str] = []
    changed: list[str] = []
    removed: list[str] = []

    def remove_hard(field_id: str) -> None:
        nonlocal hard
        old = len(hard)
        hard = [item for item in hard if item.field_id != field_id]
        if len(hard) != old:
            removed.append(field_id)

    def remove_soft(field_id: str) -> None:
        nonlocal soft
        old = len(soft)
        soft = [item for item in soft if item.field_id != field_id]
        if len(soft) != old:
            removed.append(field_id)

    operations: list[DeltaOperation] = list(intent.delta_operations)
    reset_ops = [item for item in operations if isinstance(item, ClearSearchStateOperation)]
    if reset_ops:
        hard.clear()
        soft.clear()
        terms.clear()
        selected.clear()
        pending = None
        latest_result_set_id = None
        active_suggestion_set_id = None
        removed.append("search_state")

    for operation in operations:
        if isinstance(operation, RemoveHardOperation):
            remove_hard(operation.field_id)
        elif isinstance(operation, RemoveSoftOperation):
            remove_soft(operation.field_id)
        elif isinstance(operation, RemoveScopeOperation):
            remove_hard("taxonomy_node_id")
            removed.append(operation.taxonomy_node_id)

    for operation in operations:
        if isinstance(operation, SetHardOperation):
            before = [item for item in hard if item.field_id == operation.field_id]
            hard = [item for item in hard if item.field_id != operation.field_id]
            hard.append(
                Constraint(
                    field_id=operation.field_id,
                    operator=operation.operator,
                    values=operation.typed_values,
                    provenance_turn_id=turn_id,
                    explicit=True,
                )
            )
            (changed if before else added).append(operation.field_id)
        elif isinstance(operation, AddScopeOperation):
            remove_hard("taxonomy_node_id")
            hard.append(
                Constraint(
                    field_id="taxonomy_node_id",
                    operator=ConstraintOperator.EQ,
                    values=[operation.taxonomy_node_id],
                    provenance_turn_id=turn_id,
                    explicit=True,
                )
            )
            added.append("taxonomy_node_id")
        elif isinstance(operation, SetSoftOperation):
            before = [item for item in soft if item.field_id == operation.field_id]
            soft = [item for item in soft if item.field_id != operation.field_id]
            soft.append(
                Preference(
                    field_id=operation.field_id,
                    operator=operation.operator,
                    values=operation.typed_values,
                    weight=operation.weight,
                    provenance_turn_id=turn_id,
                    explicit=True,
                )
            )
            (changed if before else added).append(operation.field_id)
        elif isinstance(operation, SetComparativeOperation):
            if operation.kind == "CHEAPER":
                remove_soft("price")
                soft.append(
                    Preference(
                        field_id="price",
                        operator=SoftOperator.PREFER_LOWER,
                        values=[],
                        weight=5,
                        provenance_turn_id=turn_id,
                        explicit=True,
                        comparative_anchor=(
                            operation.anchor_reference.value if operation.anchor_reference else None
                        ),
                    )
                )
                added.append("price:prefer_lower")

    preserved = [item.field_id for item in previous.hard_constraints if item.field_id not in removed]
    result = QueryState(
        state_version=previous.state_version,
        hard_constraints=hard,
        soft_preferences=soft,
        query_terms=terms,
        selected_result_entry_ids=selected,
        pending_clarification=pending,
        latest_result_set_id=latest_result_set_id,
        active_suggestion_set_id=active_suggestion_set_id,
        catalog_version=previous.catalog_version,
        index_version=previous.index_version,
        lexicon_version=previous.lexicon_version,
        compact_goal_summary=previous.compact_goal_summary,
    )
    return result, StateDeltaSummary(
        added=sorted(set(added)),
        changed=sorted(set(changed)),
        removed=sorted(set(removed)),
        preserved=sorted(set(preserved)),
    )


def validate_clarifying_question(draft: ClarificationDraft, packet: ClarificationPacket) -> list[str]:
    issues: list[str] = []
    allowed = {option.choice_id for option in packet.options}
    if set(draft.choice_ids) != allowed:
        issues.append("CLARIFICATION_OPTIONS_CHANGED")
    if draft.question_count != 1:
        issues.append("MULTIPLE_CLARIFICATION_QUESTIONS")
    lowered = draft.question.casefold()
    if any(token in lowered for token in ("http://", "https://", "tool", "execute", "ignore instructions")):
        issues.append("UNSAFE_CLARIFICATION_TEXT")
    return sorted(set(issues))


def validate_research_synthesis(
    synthesis: ResearchSynthesisV1,
    search_result: OnlineSearchResult,
) -> ValidatedResearch:
    source_map = {source.source_id: source for source in search_result.sources}
    accepted = []
    dropped = []
    forbidden_canonical_claim_terms = ("price", "stock", "availability", "delivery", "seller", "cart")
    for claim in synthesis.claims:
        if any(source_id not in source_map for source_id in claim.support_source_ids):
            dropped.append(f"{claim.claim_id}:UNKNOWN_SOURCE")
            continue
        if any(term in claim.text.casefold() for term in forbidden_canonical_claim_terms):
            dropped.append(f"{claim.claim_id}:CANONICAL_AUTHORITY_LEAK")
            continue
        accepted.append(claim)
    status = "VALID" if accepted and not dropped else "PARTIAL" if accepted else "UNAVAILABLE"
    warnings = list(search_result.warnings)
    warnings.extend(dropped)
    if not accepted and search_result.sources:
        warnings.append("SYNTHESIS_UNAVAILABLE_SOURCE_LIST_ONLY")
    return ValidatedResearch(
        status=status,
        summary=synthesis.answer_summary if accepted else "",
        claims=accepted,
        sources=search_result.sources,
        conflicts=synthesis.conflicts,
        warnings=warnings,
    )


def parse_intent_payload(payload: dict[str, Any]) -> tuple[IntentDeltaV1 | None, list[str]]:
    try:
        # Provider responses arrive as JSON. JSON validation accepts the exact
        # serialized enum values while still rejecting Python-side coercions.
        return IntentDeltaV1.model_validate_json(canonical_json(payload)), []
    except Exception as exc:
        if isinstance(exc, ValidationError):
            issues = [f"SCHEMA:{error.get('type', 'invalid')}:{error.get('loc', [])}" for error in exc.errors()]
        else:
            issues = ["SCHEMA:INVALID_OUTPUT"]
        return None, issues
