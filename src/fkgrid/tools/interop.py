"""Explicit translations between shared tools and existing worktree contracts.

The specialist worktrees intentionally own their local Pydantic models. This
module makes the mapping explicit instead of pretending structurally different
models are interchangeable. A workflow can pass its local model class to
``validate_with_worktree_model`` after this projection.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import Enum
from types import UnionType
from typing import Any, get_args, get_origin

from pydantic import BaseModel

from .contracts import (
    Availability,
    CommerceEligibility,
    Comparison,
    CompatibilityTuple,
    EvidenceRef,
    OnlineSearchResult,
    ProductBinding,
    ProductDetails,
    QueryState,
    ReferenceResolution,
    ResearchAnswer,
    ResearchDecision,
    SearchRequest,
    SearchResult,
    SuggestionSelection,
    SuggestionSet,
)
from .determinism import canonical_hash, canonical_json, stable_id


def payload_of(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError("expected a mapping or Pydantic model")


def hard_filter_hash_from_worktree(value: object) -> str:
    """Create the chat contract's hash without importing the chat package."""

    data = payload_of(value)
    if "query_state" in data:
        data = payload_of(data["query_state"])
    if "hard_constraints" not in data:
        return canonical_hash(data.get("hard_filters", {}), length=64)
    clauses = sorted(
        [payload_of(item) for item in data.get("hard_constraints", [])],
        key=canonical_json,
    )
    return canonical_hash({"schema": "hard-filter-v1", "clauses": clauses}, length=64)


def _coerce_annotation(value: Any, annotation: Any) -> Any:
    """Restore strict enum/model values before a local Pydantic validation."""

    if annotation is Any or annotation is None:
        return value
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (list, tuple, set, frozenset):
        item_type = args[0] if args else Any
        converted = [_coerce_annotation(item, item_type) for item in value]
        return origin(converted) if origin is not tuple else tuple(converted)
    if origin is dict:
        key_type, value_type = args if len(args) == 2 else (Any, Any)
        return {
            _coerce_annotation(key, key_type): _coerce_annotation(item, value_type)
            for key, item in value.items()
        }
    if origin in (UnionType, getattr(__import__("typing"), "Union", object())):
        for candidate in args:
            if candidate is type(None):
                continue
            try:
                return _coerce_annotation(value, candidate)
            except (TypeError, ValueError):
                continue
        return value
    try:
        if isinstance(annotation, type) and issubclass(annotation, Enum):
            return value if isinstance(value, annotation) else annotation(value)
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            if isinstance(value, BaseModel):
                value = value.model_dump(mode="python")
            return _coerce_model_payload(value, annotation)
    except TypeError:
        pass
    return value


def _coerce_model_payload(
    payload: Mapping[str, Any], model_type: type[BaseModel]
) -> dict[str, Any]:
    result = dict(payload)
    for name, field in model_type.model_fields.items():
        if name in result:
            result[name] = _coerce_annotation(result[name], field.annotation)
    return result


def validate_with_worktree_model[T: BaseModel](
    payload: Mapping[str, Any], model_type: type[T]
) -> T:
    """Validate a projected payload with the consuming worktree's model."""

    return model_type.model_validate(_coerce_model_payload(payload, model_type))


def compatibility_from_worktree(value: object | None) -> CompatibilityTuple:
    data = payload_of(value) if value is not None else {}
    return CompatibilityTuple(
        contract_schema_version=str(data.get("contract_schema_version", "fkgrid-contract-v1")),
        catalog_version=str(data.get("catalog_version", "catalog-demo-v1")),
        index_version=str(data.get("index_version", "index-demo-v1")),
        taxonomy_version=str(data.get("taxonomy_version", "taxonomy-demo-v1")),
        category_schema_version=str(data.get("category_schema_version", "category-schema-demo-v1")),
        lexicon_version=str(data.get("lexicon_version", "lexicon-demo-v1")),
        ranking_version=str(
            data.get("ranking_version", data.get("rank_policy_version", "ranking-demo-v1"))
        ),
        rank_policy_version=str(
            data.get("rank_policy_version", data.get("ranking_version", "ranking-demo-v1"))
        ),
        gate_policy_version=str(data.get("gate_policy_version", "gate-demo-v1")),
        prompt_version=str(
            data.get("prompt_version", data.get("intent_prompt_version", "prompt-demo-v1"))
        ),
        intent_prompt_version=str(
            data.get("intent_prompt_version", data.get("prompt_version", "intent-demo-v1"))
        ),
        policy_version=str(
            data.get("policy_version", data.get("commerce_policy_version", "policy-demo-v1"))
        ),
        commerce_policy_version=str(
            data.get("commerce_policy_version", data.get("policy_version", "commerce-demo-v1"))
        ),
        recovery_policy_version=str(data.get("recovery_policy_version", "recovery-demo-v1")),
        research_policy_version=str(data.get("research_policy_version", "research-demo-v1")),
        suggestion_policy_version=str(data.get("suggestion_policy_version", "suggestion-demo-v1")),
        memory_schema_version=str(data.get("memory_schema_version", "memory-demo-v1")),
        query_enhancement_policy_version=str(
            data.get("query_enhancement_policy_version", "enhancement-demo-v1")
        ),
        model_version=str(
            data.get("model_version", data.get("intent_model_alias", "model-deterministic-v1"))
        ),
        intent_model_alias=str(data.get("intent_model_alias", "deterministic-intent")),
        response_template_version=str(data.get("response_template_version", "response-demo-v1")),
        clarification_prompt_version=data.get(
            "clarification_prompt_version", "clarification-demo-v1"
        ),
        recovery_prompt_version=data.get("recovery_prompt_version", "recovery-demo-v1"),
        recovery_model_alias=data.get("recovery_model_alias", "deterministic-recovery"),
        research_prompt_version=data.get("research_prompt_version", "research-demo-v1"),
        research_model_alias=data.get("research_model_alias", "deterministic-research"),
        suggestion_prompt_version=data.get("suggestion_prompt_version", "suggestion-demo-v1"),
        suggestion_model_alias=data.get("suggestion_model_alias", "deterministic-suggestions"),
        research_provider_version=data.get("research_provider_version", "fixture-provider-v1"),
    )


def binding_from_worktree(
    value: object, catalog_version: str = "catalog-demo-v1"
) -> ProductBinding:
    data = payload_of(value)
    return ProductBinding(
        product_id=str(data["product_id"]),
        sku_id=str(data["sku_id"]),
        offer_id=str(data["offer_id"]),
        variant_id=str(data["variant_id"]) if data.get("variant_id") is not None else None,
        catalog_version=str(data.get("catalog_version", catalog_version)),
    )


def query_state_from_worktree(
    value: object, compatibility: CompatibilityTuple | None = None
) -> QueryState:
    data = payload_of(value)
    query_terms = [str(item) for item in data.get("query_terms", data.get("normalized_terms", []))]
    hard_filters: dict[str, Any] = dict(data.get("hard_filters", {}))
    for constraint in data.get("hard_constraints", []):
        item = payload_of(constraint)
        field_id = str(item.get("field_id", ""))
        operator_value = item.get("operator", "EQ")
        operator = operator_value.value if isinstance(operator_value, Enum) else str(operator_value)
        values = item.get("values", [])
        if not field_id or not isinstance(values, list):
            hard_filters.setdefault("unsupported_constraints", []).append(
                {"field_id": field_id, "operator": operator}
            )
            continue
        normalized_values = [_plain_query_value(value) for value in values]
        if field_id in {"category", "taxonomy_node_id", "taxonomy_scope_id"}:
            if operator == "ALL_OF" and len(normalized_values) > 1:
                hard_filters.setdefault("unsupported_constraints", []).append(
                    {"field_id": field_id, "operator": operator}
                )
            else:
                hard_filters["category_id"] = (
                    normalized_values[0] if len(normalized_values) == 1 else list(normalized_values)
                )
        elif field_id in {"price", "min_price", "max_price"}:
            price_values = [float(value) for value in normalized_values]
            if operator == "RANGE" and len(price_values) >= 2:
                lower, upper = sorted(price_values[:2])
                hard_filters["min_price"] = lower
                hard_filters["max_price"] = upper
            elif operator == "GT" or field_id == "min_price" and operator == "GT":
                hard_filters["min_price_exclusive"] = price_values[0]
            elif operator == "GTE" or field_id == "min_price":
                hard_filters["min_price"] = price_values[0]
            elif operator == "LT" or field_id == "max_price" and operator == "LT":
                hard_filters["max_price_exclusive"] = price_values[0]
            elif operator == "LTE" or field_id == "max_price":
                hard_filters["max_price"] = price_values[0]
            elif operator == "IN":
                hard_filters["price_values"] = price_values
            elif price_values:
                hard_filters["min_price"] = price_values[0]
                hard_filters["max_price"] = price_values[0]
        elif field_id in {"brand", "availability"}:
            if operator not in {"EQ", "IN"}:
                hard_filters.setdefault("unsupported_constraints", []).append(
                    {"field_id": field_id, "operator": operator}
                )
            else:
                hard_filters[field_id] = (
                    normalized_values[0] if len(normalized_values) == 1 else list(normalized_values)
                )
        else:
            if operator == "ALL_OF":
                attribute_all_of = dict(hard_filters.get("attribute_all_of", {}))
                attribute_all_of[field_id] = list(normalized_values)
                hard_filters["attribute_all_of"] = attribute_all_of
            elif operator == "RANGE" and len(normalized_values) >= 2:
                attribute_ranges = dict(hard_filters.get("attribute_ranges", {}))
                attribute_ranges[field_id] = sorted(float(value) for value in normalized_values[:2])
                hard_filters["attribute_ranges"] = attribute_ranges
            elif operator not in {"EQ", "IN"}:
                hard_filters.setdefault("unsupported_constraints", []).append(
                    {"field_id": field_id, "operator": operator}
                )
            else:
                attributes = dict(hard_filters.get("attributes", {}))
                attributes[field_id] = (
                    normalized_values[0] if len(normalized_values) == 1 else list(normalized_values)
                )
                hard_filters["attributes"] = attributes
    soft_terms = [str(item) for item in data.get("soft_terms", [])]
    for preference in data.get("soft_preferences", []):
        item = payload_of(preference)
        soft_terms.extend(str(value) for value in item.get("values", []))
    category_id = data.get("category_id", data.get("taxonomy_scope_id"))
    if category_id is None:
        category_filter = hard_filters.get("category_id")
        if isinstance(category_filter, str):
            category_id = category_filter
    return QueryState(
        text=str(data.get("text", " ".join(query_terms))),
        normalized_terms=query_terms,
        hard_filters=hard_filters,
        soft_terms=soft_terms,
        excluded_terms=[str(item) for item in data.get("excluded_terms", [])],
        category_id=category_id,
        locale=str(data.get("locale", "en-IN")),
        result_set_id=data.get("result_set_id", data.get("latest_result_set_id")),
    )


def _plain_query_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        if "amount_paise" in value:
            return float(value["amount_paise"]) / 100.0
        return {str(key): _plain_query_value(item) for key, item in value.items()}
    return value.value if isinstance(value, Enum) else value


def search_request_from_worktree(value: object) -> SearchRequest:
    data = payload_of(value)
    compatibility = compatibility_from_worktree(
        data.get("compatibility_tuple", data.get("compatibility"))
    )
    state = query_state_from_worktree(data.get("query_state", {}), compatibility)
    return SearchRequest(
        query_state=state,
        top_k=int(data.get("top_k", 5)),
        exclusions=[str(item) for item in data.get("exclusions", [])],
        compatibility=compatibility,
    )


def search_result_from_worktree(value: object) -> SearchResult:
    data = payload_of(value)
    compatibility = compatibility_from_worktree(data.get("compatibility_tuple"))
    entries = []
    from .contracts import SearchEntry

    for index, item in enumerate(data.get("entries", []), start=1):
        entry = payload_of(item)
        binding = binding_from_worktree(entry["binding"], compatibility.catalog_version)
        entries.append(
            SearchEntry(
                rank=int(entry.get("display_position", index)),
                binding=binding,
                title=str(entry.get("title", binding.product_id)),
                category_id="unknown",
                score=float(entry.get("score_components", {}).get("total", 0.0)),
                score_components={
                    str(key): float(value)
                    for key, value in entry.get("score_components", {}).items()
                    if isinstance(value, (int, float))
                },
                evidence_refs=[],
            )
        )
    status = str(data.get("status", "UNAVAILABLE"))
    status_map = {
        "NOT_FOUND": "NO_MATCH",
        "NO_ELIGIBLE_MATCH": "NO_MATCH",
        "UNAVAILABLE": "UNAVAILABLE",
        "OK": "OK",
    }
    return SearchResult(
        status=status_map.get(status, "UNAVAILABLE"),
        result_set_id=str(data.get("result_set_id", "worktree-result-set")),
        eligible_count=int(data.get("eligible_count", len(entries))),
        entries=entries,
        compatibility=compatibility,
        confidence_signals={
            str(key): float(value)
            for key, value in data.get("confidence_signals", {}).items()
            if isinstance(value, (int, float))
        },
        unknown_terms=[str(item) for item in data.get("unknown_terms", [])],
        degraded_components=[str(item) for item in data.get("degraded_components", [])],
        query_hash=str(data.get("hard_filter_hash", "worktree-query")),
    )


def evidence_ref_to_chat(value: EvidenceRef, entity_id: str = "catalog") -> dict[str, Any]:
    return {
        "evidence_id": value.evidence_id,
        "entity_type": "catalog",
        "entity_id": entity_id,
        "field_path": value.field,
        "source_type": value.source,
        "source_id": value.evidence_id,
        "version": value.version,
    }


def binding_to_chat(value: ProductBinding) -> dict[str, Any]:
    return {
        "product_id": value.product_id,
        "sku_id": value.sku_id,
        "offer_id": value.offer_id,
        "catalog_version": value.catalog_version,
    }


def _tool_status(status: str) -> str:
    return {
        "OK": "OK",
        "NO_MATCH": "NOT_FOUND",
        "DEGRADED": "UNAVAILABLE",
        "UNAVAILABLE": "UNAVAILABLE",
        "STALE": "STALE",
    }.get(status, "INTERNAL_ERROR")


def search_result_to_chat(
    result: SearchResult, hard_filter_hash: str | None = None
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for entry in result.entries:
        entry_id = stable_id(
            "entry", {"result_set_id": result.result_set_id, "binding": entry.binding}
        )
        facts: list[dict[str, Any]] = [
            {
                "fact_id": stable_id("fact", {"entry": entry_id, "field": "title"}),
                "label": "Title",
                "typed_value": entry.title,
                "status": "VERIFIED",
                "scope": "CATALOG",
                "provenance_type": "MOCK_CATALOG",
                "evidence_refs": [
                    evidence_ref_to_chat(ref, entry.binding.product_id)
                    for ref in entry.evidence_refs[:8]
                ],
            }
        ]
        if entry.price is not None:
            facts.append(
                {
                    "fact_id": stable_id("fact", {"entry": entry_id, "field": "price"}),
                    "label": "Prototype price",
                    "typed_value": {
                        "amount_paise": int(round(entry.price * 100)),
                        "currency": "INR",
                    },
                    "status": "VERIFIED",
                    "scope": "PROTOTYPE",
                    "provenance_type": "MOCK_CATALOG",
                    "evidence_refs": [
                        evidence_ref_to_chat(ref, entry.binding.offer_id)
                        for ref in entry.evidence_refs[:8]
                    ],
                }
            )
        if entry.category_id:
            facts.append(
                {
                    "fact_id": stable_id("fact", {"entry": entry_id, "field": "category"}),
                    "label": "category",
                    "typed_value": entry.category_id,
                    "status": "VERIFIED",
                    "scope": "CATALOG",
                    "provenance_type": "MOCK_CATALOG",
                    "evidence_refs": [
                        evidence_ref_to_chat(ref, entry.binding.product_id)
                        for ref in entry.evidence_refs[:8]
                    ],
                }
            )
        for field, value in sorted(entry.attributes.items()):
            facts.append(
                {
                    "fact_id": stable_id("fact", {"entry": entry_id, "field": field}),
                    "label": field,
                    "typed_value": value,
                    "status": "VERIFIED",
                    "scope": "CATALOG",
                    "provenance_type": "MOCK_CATALOG",
                    "evidence_refs": [
                        evidence_ref_to_chat(ref, entry.binding.product_id)
                        for ref in entry.evidence_refs[:8]
                    ],
                }
            )
        if entry.availability:
            facts.append(
                {
                    "fact_id": stable_id("fact", {"entry": entry_id, "field": "availability"}),
                    "label": "availability",
                    "typed_value": entry.availability,
                    "status": "VERIFIED" if entry.availability != "UNKNOWN" else "UNKNOWN",
                    "scope": "PROTOTYPE",
                    "provenance_type": "MOCK_CATALOG",
                    "evidence_refs": [
                        evidence_ref_to_chat(ref, entry.binding.offer_id)
                        for ref in entry.evidence_refs[:8]
                    ],
                }
            )
        entries.append(
            {
                "result_entry_id": entry_id,
                "display_position": entry.rank,
                "binding": binding_to_chat(entry.binding),
                "title": entry.title,
                "facts": facts,
                "score_components": entry.score_components,
                "matched_criteria": sorted(
                    key for key, value in entry.score_components.items() if value > 0
                ),
                "unknown_criteria": list(result.unknown_terms),
            }
        )
    evidence = [
        evidence_ref_to_chat(ref, ref.field)
        for entry in result.entries
        for ref in entry.evidence_refs[:4]
    ][:20]
    return {
        "status": _tool_status(result.status),
        "result_set_id": result.result_set_id,
        "entries": entries,
        "eligible_count": result.eligible_count,
        "confidence_signals": result.confidence_signals,
        "hard_filter_hash": hard_filter_hash or result.query_hash,
        "warnings": [f"UNKNOWN_TERM:{term}" for term in result.unknown_terms],
        "evidence_refs": evidence,
        "degraded_components": result.degraded_components,
    }


def commerce_eligibility_to_chat(result: CommerceEligibility) -> dict[str, Any]:
    price = None
    if result.price is not None:
        price = {"amount_paise": int(round(result.price * 100)), "currency": "INR"}
    return {
        "eligible": result.eligible is True,
        "binding": binding_to_chat(result.binding),
        "policy_status": result.policy_status,
        "price": price,
        "availability_status": result.availability_status,
        "reasons": list(result.reason_codes),
        "evidence_refs": [
            evidence_ref_to_chat(ref, result.binding.offer_id) for ref in result.evidence_refs[:8]
        ],
    }


def product_details_to_chat(result: ProductDetails) -> dict[str, Any]:
    def fact_evidence(field_id: str) -> list[dict[str, Any]]:
        source_fields = {field_id, "category" if field_id == "category_id" else field_id}
        field_refs = [ref for ref in result.evidence_refs if ref.field in source_fields]
        if not field_refs:
            field_refs = result.evidence_refs[:8]
        return [evidence_ref_to_chat(ref, result.binding.offer_id) for ref in field_refs[:8]]

    facts = []
    if result.title is not None:
        facts.append(
            {
                "fact_id": stable_id("fact", {"binding": result.binding, "field": "title"}),
                "label": "Title",
                "typed_value": result.title,
                "status": "VERIFIED",
                "scope": "CATALOG",
                "provenance_type": "MOCK_CATALOG",
                "evidence_refs": fact_evidence("title"),
            }
        )
    for key, value in sorted(result.attributes.items()):
        facts.append(
            {
                "fact_id": stable_id("fact", {"binding": result.binding, "field": key}),
                "label": key,
                "typed_value": value,
                "status": "VERIFIED",
                "scope": "CATALOG",
                "provenance_type": "MOCK_CATALOG",
                "evidence_refs": fact_evidence(key),
            }
        )
    if result.category_id is not None:
        facts.append(
            {
                "fact_id": stable_id("fact", {"binding": result.binding, "field": "category"}),
                "label": "category",
                "typed_value": result.category_id,
                "status": "VERIFIED",
                "scope": "CATALOG",
                "provenance_type": "MOCK_CATALOG",
                "evidence_refs": fact_evidence("category_id"),
            }
        )
    if result.brand is not None:
        facts.append(
            {
                "fact_id": stable_id("fact", {"binding": result.binding, "field": "brand"}),
                "label": "brand",
                "typed_value": result.brand,
                "status": "VERIFIED",
                "scope": "CATALOG",
                "provenance_type": "MOCK_CATALOG",
                "evidence_refs": fact_evidence("brand"),
            }
        )
    if result.price is not None:
        facts.append(
            {
                "fact_id": stable_id("fact", {"binding": result.binding, "field": "price"}),
                "label": "Prototype price",
                "typed_value": {"amount_paise": int(round(result.price * 100)), "currency": "INR"},
                "status": "VERIFIED",
                "scope": "PROTOTYPE",
                "provenance_type": "MOCK_CATALOG",
                "evidence_refs": fact_evidence("price"),
            }
        )
    if result.availability:
        facts.append(
            {
                "fact_id": stable_id("fact", {"binding": result.binding, "field": "availability"}),
                "label": "availability",
                "typed_value": result.availability,
                "status": "VERIFIED" if result.availability != "UNKNOWN" else "UNKNOWN",
                "scope": "PROTOTYPE",
                "provenance_type": "MOCK_CATALOG",
                "evidence_refs": fact_evidence("availability"),
            }
        )
    return {
        "status": _tool_status(result.status),
        "binding": binding_to_chat(result.binding),
        "title": result.title,
        "facts": facts,
        "variants": [],
        "warnings": [],
        "evidence_refs": [
            evidence_ref_to_chat(ref, result.binding.product_id)
            for ref in result.evidence_refs[:30]
        ],
    }


def comparison_to_chat(result: Comparison) -> dict[str, Any]:
    rows = []
    for field_id, values in result.rows.items():
        cells = []
        for _, value in enumerate(values):
            binding_index = len(cells)
            binding = (
                result.bindings[binding_index]
                if binding_index < len(result.bindings)
                else result.bindings[0]
            )
            field_refs = [ref for ref in result.evidence_refs if ref.field == field_id]
            if not field_refs:
                field_refs = result.evidence_refs[:8]
            cells.append(
                {
                    "field_id": field_id,
                    "value": value,
                    "status": "UNKNOWN" if value is None else "VERIFIED",
                    "evidence_refs": [
                        evidence_ref_to_chat(ref, binding.offer_id) for ref in field_refs[:8]
                    ],
                }
            )
        rows.append({"field_id": field_id, "label": field_id, "cells": cells})
    return {
        "status": _tool_status(result.status),
        "bindings": [binding_to_chat(binding) for binding in result.bindings],
        "rows": rows,
        "warnings": [],
    }


def availability_to_chat(result: Availability) -> dict[str, Any]:
    status = (
        "OK"
        if result.status in {"AVAILABLE", "UNAVAILABLE", "UNKNOWN"}
        else _tool_status(result.status)
    )
    return {
        "status": status,
        "binding": binding_to_chat(result.binding),
        "availability_status": result.availability,
        "quantity": None,
        "as_of": None,
        "truth_status": "UNKNOWN" if result.availability == "UNKNOWN" else "VERIFIED",
        "scope": "PROTOTYPE",
        "provenance_type": "MOCK_CATALOG",
        "evidence_refs": [
            evidence_ref_to_chat(ref, result.binding.offer_id) for ref in result.evidence_refs[:8]
        ],
        "warnings": [result.reason],
    }


def reference_resolution_to_chat(
    result: ReferenceResolution, active_result: SearchResult | None = None
) -> dict[str, Any]:
    entries_by_binding = {
        (entry.binding.product_id, entry.binding.sku_id, entry.binding.offer_id): entry
        for entry in (active_result.entries if active_result else [])
    }
    references = []
    for binding in result.resolved:
        entry = entries_by_binding.get((binding.product_id, binding.sku_id, binding.offer_id))
        references.append(
            {
                "result_entry_id": stable_id(
                    "entry", {"result_set_id": result.result_set_id, "binding": binding}
                ),
                "display_position": entry.rank if entry else 1,
                "binding": binding_to_chat(binding),
            }
        )
    options = []
    for reference, bindings in result.ambiguous.items():
        for binding in bindings[:4]:
            options.append(
                {"choice_id": binding.offer_id, "label": f"{reference}: {binding.product_id}"}
            )
    return {
        "status": result.status if result.status != "INVALID" else "NOT_FOUND",
        "references": references,
        "reason_code": "AMBIGUOUS_REFERENCE" if result.ambiguous else None,
        "options": options[:4],
    }


def research_decision_from_worktree(value: object) -> ResearchDecision:
    data = payload_of(value)
    decision = str(data.get("decision", "NOT_NEEDED"))
    return ResearchDecision(
        needed=decision in {"OPTIONAL", "REQUIRED"},
        query=str(data.get("canonical_query", data.get("query", ""))),
        reason=str(data.get("reason_code", "WORKTREE_DECISION")),
        explicit=decision in {"OPTIONAL", "REQUIRED"},
    )


def research_decision_to_chat(result: ResearchDecision) -> dict[str, Any]:
    return {
        "decision": "REQUIRED" if result.needed else "NOT_NEEDED",
        "reason_code": result.reason,
        "question_type": "CURRENT_EXTERNAL" if result.needed else "GENERAL_GUIDANCE",
        "canonical_query": result.query or "no external research requested",
        "selected_entity_ids": [],
        "locale": "en-IN",
    }


def online_search_to_chat(result: OnlineSearchResult) -> dict[str, Any]:
    status = {
        "OK": "SUCCESS",
        "DECLINED": "BLOCKED",
        "UNAVAILABLE": "PROVIDER_UNAVAILABLE",
    }.get(result.status, "PROVIDER_UNAVAILABLE")
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    sources = []
    for source in result.sources[:5]:
        extract_hash = canonical_hash(source.extract, length=64)
        sources.append(
            {
                "source_id": source.source_id,
                "canonical_url": source.url,
                "domain": source.domain,
                "title": source.title,
                "extract": source.extract,
                "extract_hash": extract_hash,
                "retrieved_at": source.retrieved_at or epoch,
                "published_at": None,
                "trust_tier": "OTHER",
            }
        )
    return {
        "status": status,
        "provider": result.provider,
        "executed_query": result.executed_query,
        "searched_at": epoch,
        "sources": sources,
        "warnings": result.warnings,
    }


def research_answer_to_chat(
    result: ResearchAnswer, sources: Sequence[Mapping[str, Any]] = ()
) -> dict[str, Any]:
    source_index = {str(item.get("source_id")): item for item in sources}
    claims = []
    if result.status == "OK":
        claims.append(
            {
                "claim_id": stable_id("claim", result.answer),
                "text": result.answer[:500],
                "support_source_ids": [
                    source_index.get(key, {}).get("source_id", key) for key in result.citations[:3]
                ],
                "conflict_source_ids": [],
                "confidence": "MEDIUM",
            }
        )
    return {
        "status": "VALID" if result.status == "OK" else "UNAVAILABLE",
        "summary": result.answer[:500],
        "claims": claims,
        "sources": list(sources)[:5],
        "conflicts": [],
        "warnings": list(result.unsupported_claims),
    }


def suggestion_set_to_chat(result: SuggestionSet) -> dict[str, Any]:
    action_map = {
        "REFINE_RESULTS": "REFINE",
        "VIEW_DETAILS": "PRODUCT_DETAILS",
        "COMPARE_RESULTS": "COMPARE",
        "CHECK_AVAILABILITY": "CHECK_AVAILABILITY",
        "RESEARCH_EXTERNAL": "RESEARCH_EXTERNAL",
        "NEW_SEARCH": "RESET_SEARCH",
    }
    suggestions = []
    for item in result.suggestions:
        candidate = {
            "candidate_id": item.action.action_id,
            "action_type": action_map[item.action.action_type],
            "safe_default_label": item.label[:60],
            "payload": item.action.payload,
            "target_ids": [],
            "reason_code": "DETERMINISTIC_CANDIDATE",
        }
        suggestions.append(
            {
                "suggestion_id": item.suggestion_id,
                "candidate": candidate,
                "label": item.label[:60],
                "signed_action_token": result.signature,
            }
        )
    return {
        "suggestion_set_id": result.suggestion_set_id,
        "session_id": result.session_id,
        "state_version": result.state_version,
        "cart_version": result.cart_version,
        "active_result_set_id": result.active_result_set_id,
        "suggestions": suggestions,
        "generator_version": result.generator_version,
    }


def suggestion_selection_to_chat(result: SuggestionSelection) -> dict[str, Any]:
    if result.status != "SELECTED" or result.action is None:
        return {"status": "SUGGESTION_STALE", "ui_action": None, "reason": result.status}
    action_map = {
        "REFINE_RESULTS": "REFINE",
        "VIEW_DETAILS": "PRODUCT_DETAILS",
        "COMPARE_RESULTS": "COMPARE",
        "CHECK_AVAILABILITY": "CHECK_AVAILABILITY",
        "RESEARCH_EXTERNAL": "RESEARCH_EXTERNAL",
        "NEW_SEARCH": "RESET_SEARCH",
    }
    return {
        "status": "VALID",
        "ui_action": {
            "action": action_map[result.action.action_type],
            "payload": result.action.payload,
            "signed_action_token": None,
        },
        "reason": None,
    }
