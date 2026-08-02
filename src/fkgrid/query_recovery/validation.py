"""Canonical hashing and semantic validation for recovery plans."""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, TypeAdapter, ValidationError

from .domain import (
    CompatibilityTuple,
    RecoveryConstraint,
    RecoveryContext,
    RecoveryPlan,
    RecoveryPlannerOutput,
    RecoveryRequest,
    RecoveryRewritePlan,
    RecoveryClarificationPlan,
    RecoveryNoSafePlan,
)


def _canonicalize(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _canonicalize(value.model_dump(mode="python", exclude_none=False))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        normalized = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, dict):
        return {str(key): _canonicalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("canonical JSON does not permit NaN or Infinity")
    return value


def canonical_json(value: Any) -> str:
    """Serialize a contract using stable UTF-8 JSON suitable for hashes."""

    return json.dumps(
        _canonicalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def normalize_term(value: str) -> str:
    """Runtime lexicon normalization; protected span parsing belongs upstream."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized.split())


def hard_filter_hash(state: Any) -> str:
    """Hash semantic hard clauses, excluding provenance/timestamps."""

    clauses = [constraint.canonical_clause() for constraint in state.hard_constraints]
    clauses.sort(key=canonical_json)
    return canonical_hash({"schema_version": "hard-filter-v1", "clauses": clauses})


def query_state_hash(state: Any) -> str:
    return canonical_hash(state)


def recovery_cache_key(request: RecoveryRequest) -> str:
    """Build a session-independent key for an immutable accepted plan."""

    return canonical_hash(
        {
            "query_state_hash": query_state_hash(request.query_state),
            "compatibility": request.compatibility,
            "policy_version": request.policy.policy_version,
            "recovery_prompt_version": request.compatibility.recovery_prompt_version,
            "recovery_model_alias": request.compatibility.recovery_model_alias,
        }
    )


def compatibility_equal(left: CompatibilityTuple, right: CompatibilityTuple) -> bool:
    return canonical_json(left) == canonical_json(right)


def planner_output_from_json(payload: str) -> tuple[RecoveryPlannerOutput | None, list[str]]:
    """Parse provider JSON once; no recursive repair is permitted here."""

    try:
        return TypeAdapter(RecoveryPlannerOutput).validate_json(payload), []
    except ValidationError as exc:
        return None, [f"SCHEMA:{error.get('type', 'invalid')}:{error.get('loc', [])}" for error in exc.errors()]
    except Exception as exc:  # pragma: no cover - defensive provider boundary
        return None, [f"SCHEMA:{type(exc).__name__}"]


def validate_recovery_context(request: RecoveryRequest) -> list[str]:
    issues: list[str] = []
    expected_hash = hard_filter_hash(request.query_state)
    if request.gate.hard_filter_hash != expected_hash:
        issues.append("GATE_HARD_FILTER_HASH_MISMATCH")
    if request.baseline_run.hard_filter_hash != expected_hash:
        issues.append("BASELINE_HARD_FILTER_HASH_MISMATCH")
    if not compatibility_equal(request.baseline_run.compatibility, request.compatibility):
        issues.append("BASELINE_COMPATIBILITY_MISMATCH")
    if request.query_state.catalog_version != request.compatibility.catalog_version:
        issues.append("QUERY_CATALOG_VERSION_MISMATCH")
    if request.query_state.index_version != request.compatibility.index_version:
        issues.append("QUERY_INDEX_VERSION_MISMATCH")
    if request.query_state.taxonomy_version != request.compatibility.taxonomy_version:
        issues.append("QUERY_TAXONOMY_VERSION_MISMATCH")
    if request.query_state.category_schema_version != request.compatibility.category_schema_version:
        issues.append("QUERY_SCHEMA_VERSION_MISMATCH")
    if request.query_state.lexicon_version != request.compatibility.lexicon_version:
        issues.append("QUERY_LEXICON_VERSION_MISMATCH")
    return sorted(set(issues))


def validate_planner_plan(
    plan: RecoveryPlannerOutput,
    context: RecoveryContext,
    concepts_by_id: dict[str, RecoveryConstraint],
) -> list[str]:
    """Validate IDs, scope, version, and hard-filter preservation after parsing."""

    issues: list[str] = []
    expected_hash = context.hard_filter_hash
    if isinstance(plan, RecoveryRewritePlan):
        if plan.preserved_hard_filter_hash != expected_hash:
            issues.append("HARD_FILTER_HASH_CHANGED")
        if len(set(plan.added_concept_ids)) != len(plan.added_concept_ids):
            issues.append("DUPLICATE_CONCEPT_ID")
        for concept_id in plan.added_concept_ids:
            concept = concepts_by_id.get(concept_id)
            if concept is None:
                issues.append("CONCEPT_ID_NOT_ALLOWED")
                continue
            if not concept.active:
                issues.append("CONCEPT_NOT_ACTIVE")
            if concept.catalog_version != context.compatibility.catalog_version:
                issues.append("CONCEPT_CATALOG_VERSION_MISMATCH")
            if concept.taxonomy_version != context.compatibility.taxonomy_version:
                issues.append("CONCEPT_TAXONOMY_VERSION_MISMATCH")
            if concept.category_schema_version != context.compatibility.category_schema_version:
                issues.append("CONCEPT_SCHEMA_VERSION_MISMATCH")
            if concept.lexicon_version != context.compatibility.lexicon_version:
                issues.append("CONCEPT_LEXICON_VERSION_MISMATCH")
            if concept.taxonomy_scope_id not in {None, context.query_state.taxonomy_scope_id}:
                issues.append("CONCEPT_SCOPE_MISMATCH")
    elif isinstance(plan, RecoveryClarificationPlan):
        if plan.preserved_hard_filter_hash != expected_hash:
            issues.append("HARD_FILTER_HASH_CHANGED")
        allowed_ids = {concept.concept_id for concept in context.allowed_concepts}
        if any(option_id not in allowed_ids for option_id in plan.option_ids):
            issues.append("CLARIFICATION_OPTION_NOT_ALLOWED")
        if len(set(plan.option_ids)) != len(plan.option_ids):
            issues.append("DUPLICATE_CLARIFICATION_OPTION")
        for option_id in plan.option_ids:
            concept = concepts_by_id.get(option_id)
            if concept and concept.taxonomy_scope_id not in {None, context.query_state.taxonomy_scope_id}:
                issues.append("CLARIFICATION_SCOPE_MISMATCH")
    elif isinstance(plan, RecoveryNoSafePlan):
        if plan.preserved_hard_filter_hash != expected_hash:
            issues.append("HARD_FILTER_HASH_CHANGED")
    return sorted(set(issues))


def validate_internal_rewrite_plan(
    plan: RecoveryPlan,
    request: RecoveryRequest,
    concepts_by_id: dict[str, RecoveryConstraint],
) -> list[str]:
    issues: list[str] = []
    expected_hash = hard_filter_hash(request.query_state)
    if plan.preserved_hard_filter_hash != expected_hash:
        issues.append("HARD_FILTER_HASH_CHANGED")
    if len(plan.added_concept_ids) > request.policy.max_added_concepts:
        issues.append("RECOVERY_CONCEPT_LIMIT")
    for concept_id, query_term in zip(plan.added_concept_ids, plan.added_query_terms, strict=True):
        concept = concepts_by_id.get(concept_id)
        if concept is None:
            issues.append("CONCEPT_ID_NOT_ALLOWED")
        elif concept.taxonomy_scope_id not in {None, request.query_state.taxonomy_scope_id}:
            issues.append("CONCEPT_SCOPE_MISMATCH")
        elif normalize_term(query_term) != normalize_term(concept.canonical_term):
            issues.append("CONCEPT_TERM_MISMATCH")
    return sorted(set(issues))
