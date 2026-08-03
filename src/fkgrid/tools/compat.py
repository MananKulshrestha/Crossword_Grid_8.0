"""Adapters matching the method shapes used by the existing agent worktrees.

The adapters coerce local worktree models into shared contracts before execution
and optionally validate the projected output with the consuming worktree's own
Pydantic class. This is the explicit compatibility boundary; no worktree source
imports another worktree's implementation.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from .catalog import DeterministicCatalog
from .contracts import (
    ApprovedExpansion,
    CompatibilityTuple,
    OnlineSearchResult,
    ResearchAnswer,
    SearchRequest,
)
from .determinism import canonical_hash, normalize_text, stable_id
from .interop import (
    availability_to_chat,
    binding_from_worktree,
    commerce_eligibility_to_chat,
    comparison_to_chat,
    compatibility_from_worktree,
    hard_filter_hash_from_worktree,
    online_search_to_chat,
    payload_of,
    product_details_to_chat,
    query_state_from_worktree,
    reference_resolution_to_chat,
    research_answer_to_chat,
    research_decision_from_worktree,
    research_decision_to_chat,
    search_request_from_worktree,
    search_result_from_worktree,
    search_result_to_chat,
    suggestion_selection_to_chat,
    suggestion_set_to_chat,
    validate_with_worktree_model,
)
from .recovery import get_recovery_constraints
from .recovery import lookup_approved_expansions as lookup_recovery_expansions
from .research import detect_research_need, online_search, validate_research_claims
from .suggestions import SuggestionStore


@dataclass(frozen=True)
class ChatModelTypes:
    """Optional local model classes supplied by the chat worktree owner."""

    search_result: type[BaseModel] | None = None
    product_details: type[BaseModel] | None = None
    comparison: type[BaseModel] | None = None
    availability: type[BaseModel] | None = None
    commerce_eligibility: type[BaseModel] | None = None
    reference_resolution: type[BaseModel] | None = None
    research_decision: type[BaseModel] | None = None
    online_search_result: type[BaseModel] | None = None
    validated_research: type[BaseModel] | None = None
    suggestion_set: type[BaseModel] | None = None
    suggestion_selection: type[BaseModel] | None = None
    confidence_decision: type[BaseModel] | None = None


@dataclass(frozen=True)
class RecoveryModelTypes:
    """Optional local query-recovery models for strict output validation."""

    approved_expansion: type[BaseModel] | None = None
    recovery_constraint: type[BaseModel] | None = None
    retrieval_run: type[BaseModel] | None = None


def _as_local(payload: dict[str, Any], model_type: type[BaseModel] | None) -> Any:
    return validate_with_worktree_model(payload, model_type) if model_type else payload


def _references(values: Sequence[object]) -> list[str]:
    result: list[str] = []
    for value in values:
        data = payload_of(value)
        result.append(str(data.get("value", data.get("reference", value))))
    return result


_COLOR_NEIGHBORS: dict[str, tuple[str, ...]] = {
    "red": ("maroon", "pink", "orange", "purple", "brown"),
    "blue": ("navy", "midblue", "indigo", "grey", "black"),
    "green": ("olive", "sage", "teal", "blue", "grey"),
    "black": ("charcoal", "grey", "navy", "indigo"),
    "white": ("silver", "grey", "sage", "blue"),
    "grey": ("charcoal", "silver", "black", "blue"),
}


def _soft_color_preferences(request: Any) -> list[str]:
    data = payload_of(request)
    query_state = payload_of(data.get("query_state", {}))
    values: list[str] = []
    for item in query_state.get("soft_preferences", []):
        preference = payload_of(item)
        if str(preference.get("field_id", "")).casefold() == "color":
            values.extend(str(value) for value in preference.get("values", []))
    return [normalize_text(value) for value in values if str(value).strip()]


def _rank_approximate_colors(result: Any, requested_colors: Sequence[str]) -> tuple[Any, bool]:
    if not requested_colors or not result.entries:
        return result, False
    normalized_requested = {normalize_text(value) for value in requested_colors}
    exact = any(
        normalize_text(entry.attributes.get("color", "")) in normalized_requested
        for entry in result.entries
    )
    if exact:
        return result, False

    def color_score(entry: Any) -> tuple[int, str]:
        actual = normalize_text(entry.attributes.get("color", ""))
        best = 0
        for requested in normalized_requested:
            if actual in _COLOR_NEIGHBORS.get(requested, ()):
                best = max(
                    best,
                    len(_COLOR_NEIGHBORS[requested]) - _COLOR_NEIGHBORS[requested].index(actual),
                )
        return best, actual

    ordered = sorted(
        result.entries,
        key=lambda entry: (
            -color_score(entry)[0],
            entry.price if entry.price is not None else float("inf"),
            entry.binding.product_id,
        ),
    )
    ordered = [
        entry.model_copy(update={"rank": index}) for index, entry in enumerate(ordered, start=1)
    ]
    result_set_id = stable_id(
        "rs",
        {
            "query_hash": result.query_hash,
            "entries": [entry.binding for entry in ordered],
        },
    )
    return result.model_copy(update={"entries": ordered, "result_set_id": result_set_id}), True


class CatalogSearchPortAdapter:
    """Matches the shopper worktree's CatalogSearchPort methods."""

    def __init__(
        self, catalog: DeterministicCatalog, chat_models: ChatModelTypes | None = None
    ) -> None:
        self.catalog = catalog
        self.chat_models = chat_models or ChatModelTypes()

    def search(self, request: Any, deadline_ms: int) -> Any:
        shared_request = (
            request if isinstance(request, SearchRequest) else search_request_from_worktree(request)
        )
        requested_colors = _soft_color_preferences(request)
        requested_limit = shared_request.top_k
        search_request = (
            shared_request.model_copy(update={"top_k": 50}) if requested_colors else shared_request
        )
        result = self.catalog.search(search_request, deadline_ms)
        result, approximate_color = _rank_approximate_colors(result, requested_colors)
        if len(result.entries) > requested_limit:
            result = result.model_copy(update={"entries": result.entries[:requested_limit]})
        result = result.model_copy(
            update={
                "result_set_id": stable_id(
                    "rs",
                    {
                        "query_hash": result.query_hash,
                        "entries": [entry.binding for entry in result.entries],
                    },
                )
            }
        )
        payload = search_result_to_chat(
            result, hard_filter_hash=hard_filter_hash_from_worktree(request)
        )
        if not result.entries:
            payload["warnings"] = list(
                dict.fromkeys([*payload.get("warnings", []), "NO_ELIGIBLE_MATCH"])
            )[:8]
        if approximate_color:
            payload["warnings"] = list(
                dict.fromkeys(
                    [
                        *payload.get("warnings", []),
                        "REQUESTED_COLOR_NOT_IN_DATASET",
                        "APPROXIMATE_COLOR_MATCH",
                    ]
                )
            )[:8]
        return _as_local(payload, self.chat_models.search_result)

    def get_details(self, binding: Any, compatibility: Any, deadline_ms: int) -> Any:
        pinned = (
            compatibility
            if isinstance(compatibility, CompatibilityTuple)
            else compatibility_from_worktree(compatibility)
        )
        result = self.catalog.get_product_details(
            binding_from_worktree(binding, pinned.catalog_version), pinned, deadline_ms
        )
        return _as_local(product_details_to_chat(result), self.chat_models.product_details)

    def compare(self, bindings: Sequence[Any], compatibility: Any, deadline_ms: int) -> Any:
        pinned = (
            compatibility
            if isinstance(compatibility, CompatibilityTuple)
            else compatibility_from_worktree(compatibility)
        )
        shared_bindings = [
            binding_from_worktree(binding, pinned.catalog_version) for binding in bindings
        ]
        result = self.catalog.compare_products(shared_bindings, pinned, deadline_ms)
        return _as_local(comparison_to_chat(result), self.chat_models.comparison)

    def check_availability(self, binding: Any, compatibility: Any, deadline_ms: int) -> Any:
        pinned = (
            compatibility
            if isinstance(compatibility, CompatibilityTuple)
            else compatibility_from_worktree(compatibility)
        )
        result = self.catalog.check_availability(
            binding_from_worktree(binding, pinned.catalog_version), pinned, deadline_ms
        )
        return _as_local(availability_to_chat(result), self.chat_models.availability)

    def check_eligibility(self, binding: Any, purpose: str, deadline_ms: int) -> Any:
        del deadline_ms
        shared_binding = binding_from_worktree(binding, self.catalog.compatibility.catalog_version)
        result = self.catalog.check_commerce_eligibility(shared_binding, purpose)
        return _as_local(
            commerce_eligibility_to_chat(result), self.chat_models.commerce_eligibility
        )


class ReferenceResolverPortAdapter:
    """Matches the chat worktree's session-owned reference resolver shape."""

    def __init__(
        self, catalog: DeterministicCatalog, chat_models: ChatModelTypes | None = None
    ) -> None:
        self.catalog = catalog
        self.chat_models = chat_models or ChatModelTypes()

    def resolve(
        self, session_id: str, references: Sequence[object], action: Any, snapshot: Any
    ) -> Any:
        del session_id, action
        snapshot_data = payload_of(snapshot)
        compatibility = compatibility_from_worktree(snapshot_data.get("compatibility_tuple"))
        entries = snapshot_data.get("acknowledged_entries", [])
        from .contracts import SearchEntry, SearchResult

        active_entries = []
        reference_aliases: dict[str, str] = {}
        for item in entries:
            data = payload_of(item)
            binding = binding_from_worktree(data["binding"], compatibility.catalog_version)
            for alias in (data.get("result_entry_id"), data.get("context_ref")):
                if alias:
                    reference_aliases[str(alias)] = binding.offer_id
            active_entries.append(
                SearchEntry(
                    rank=int(data.get("display_position", len(active_entries) + 1)),
                    binding=binding,
                    title=binding.product_id,
                    category_id="unknown",
                    score=0.0,
                )
            )
        active_result = SearchResult(
            status="OK" if active_entries else "NO_MATCH",
            result_set_id=str(snapshot_data.get("acknowledged_result_set_id", "active-result-set")),
            eligible_count=len(active_entries),
            entries=active_entries,
            compatibility=compatibility,
            query_hash="active-snapshot",
        )
        normalized_references = [
            reference_aliases.get(reference, reference) for reference in _references(references)
        ]
        result = self.catalog.resolve_reference(
            normalized_references, active_result, compatibility=compatibility
        )
        return _as_local(
            reference_resolution_to_chat(result, active_result),
            self.chat_models.reference_resolution,
        )


class RecoveryPortAdapter:
    def __init__(
        self,
        catalog: DeterministicCatalog,
        expansions: Iterable[ApprovedExpansion] = (),
        chat_models: ChatModelTypes | None = None,
    ) -> None:
        from .recovery import DeterministicRecoveryAdapter

        self._adapter = DeterministicRecoveryAdapter(catalog, expansions)
        self.chat_models = chat_models or ChatModelTypes()

    def assess(self, result: Any, query_state: Any) -> Any:
        from .interop import query_state_from_worktree

        shared_result = (
            result
            if result.__class__.__module__.startswith("fkgrid.tools")
            else search_result_from_worktree(result)
        )
        shared_state = query_state_from_worktree(query_state)
        decision = self._adapter.assess(shared_result, shared_state)
        local_payload = {
            "decision": {
                "NO_MATCH": "NO_SAFE_RECOVERY",
            }.get(decision.status, decision.status),
            "reasons": list(decision.reasons),
            "hard_filter_hash": hard_filter_hash_from_worktree(query_state),
            "signals": {
                "confidence": float(decision.confidence),
                **shared_result.confidence_signals,
            },
        }
        return _as_local(local_payload, self.chat_models.confidence_decision)

    def recover(self, result: Any, query_state: Any, compatibility: Any, deadline_ms: int) -> Any:
        from .interop import compatibility_from_worktree, query_state_from_worktree

        pinned = compatibility_from_worktree(compatibility)
        shared_result = (
            result
            if result.__class__.__module__.startswith("fkgrid.tools")
            else search_result_from_worktree(result)
        )
        recovered = self._adapter.recover(
            shared_result, query_state_from_worktree(query_state, pinned), pinned, deadline_ms
        )
        return _as_local(
            search_result_to_chat(
                recovered, hard_filter_hash=hard_filter_hash_from_worktree(query_state)
            ),
            self.chat_models.search_result,
        )


class QueryRecoveryPortAdapter:
    """Matches the query-recovery worktree's smaller ports."""

    def __init__(
        self,
        catalog: DeterministicCatalog,
        expansions: Iterable[ApprovedExpansion] = (),
        recovery_models: RecoveryModelTypes | None = None,
    ) -> None:
        self.catalog = catalog
        self.expansions = list(expansions)
        self.recovery_models = recovery_models or RecoveryModelTypes()

    def lookup(
        self, *, normalized_terms: list[str], query_state: Any, compatibility: Any, limit: int
    ) -> list[ApprovedExpansion]:
        pinned = compatibility_from_worktree(compatibility)
        state = query_state_from_worktree(query_state, pinned)
        expansions = lookup_recovery_expansions(
            normalized_terms, self.expansions, state, pinned, limit
        )
        return [
            _as_local(
                {
                    "mapping_id": stable_id("mapping", expansion.model_dump(mode="json")),
                    "normalized_form": expansion.surface_form,
                    "original_form": expansion.surface_form,
                    "canonical_target_id": expansion.target_id,
                    "canonical_label": expansion.canonical_term,
                    "concept_type": "VALUE",
                    "mapping_type": "ALIAS",
                    "expansion_action": "CANONICAL_SYNONYM",
                    "locale": expansion.locale,
                    "taxonomy_scope_id": None,
                    "attribute_id": None,
                    "lexicon_version": expansion.lexicon_version,
                    "catalog_version": pinned.catalog_version,
                    "taxonomy_version": pinned.taxonomy_version,
                    "category_schema_version": pinned.category_schema_version,
                    "evidence_band": "APPROVED_HIGH",
                    "priority": 0,
                    "status": "APPROVED",
                },
                self.recovery_models.approved_expansion,
            )
            for expansion in expansions
        ]

    def get_constraints(
        self, *, query_state: Any, unknown_terms: list[str], compatibility: Any, limit: int
    ) -> list[Any]:
        pinned = compatibility_from_worktree(compatibility)
        state = query_state_from_worktree(query_state, pinned)
        constraints = get_recovery_constraints(state, unknown_terms, self.expansions, pinned, limit)
        return [
            _as_local(
                {
                    "concept_id": constraint.constraint_id,
                    "concept_type": "VALUE",
                    "label": constraint.field,
                    "canonical_term": constraint.allowed_values[0]
                    if constraint.allowed_values
                    else constraint.field,
                    "taxonomy_scope_id": None,
                    "attribute_id": constraint.field,
                    "value_id": constraint.allowed_values[0] if constraint.allowed_values else None,
                    "locale": state.locale,
                    "catalog_version": pinned.catalog_version,
                    "taxonomy_version": pinned.taxonomy_version,
                    "category_schema_version": pinned.category_schema_version,
                    "lexicon_version": pinned.lexicon_version,
                    "active": True,
                },
                self.recovery_models.recovery_constraint,
            )
            for constraint in constraints
        ]

    def search(
        self,
        *,
        query_state: Any,
        query_terms: list[str],
        compatibility: Any,
        run_kind: str,
        remaining_ms: int,
    ) -> Any:
        pinned = compatibility_from_worktree(compatibility)
        state = query_state_from_worktree(query_state, pinned).model_copy(
            update={"normalized_terms": list(query_terms)}
        )
        result = self.catalog.search(
            SearchRequest(query_state=state, compatibility=pinned), remaining_ms
        )
        from .contracts import RetrievalRun

        shared_run = RetrievalRun(
            run_id=stable_id("retrieval", {"run_kind": run_kind, "result": result}),
            kind="BASELINE",
            result=result,
            query_state=state,
        )
        if self.recovery_models.retrieval_run is None:
            return shared_run
        return _as_local(
            {
                "run_id": shared_run.run_id,
                "query_state_hash": canonical_hash(state, length=64),
                "hard_filter_hash": canonical_hash(state.hard_filters, length=64),
                "compatibility": {
                    "contract_schema_version": pinned.contract_schema_version,
                    "catalog_version": pinned.catalog_version,
                    "index_version": pinned.index_version,
                    "taxonomy_version": pinned.taxonomy_version,
                    "category_schema_version": pinned.category_schema_version,
                    "lexicon_version": pinned.lexicon_version,
                    "rank_policy_version": pinned.rank_policy_version,
                    "gate_policy_version": pinned.gate_policy_version,
                    "recovery_policy_version": pinned.recovery_policy_version,
                    "recovery_prompt_version": pinned.recovery_prompt_version,
                    "recovery_model_alias": pinned.recovery_model_alias,
                },
                "eligible_count": result.eligible_count,
                "top_score": result.confidence_signals.get("top_score"),
                "top_score_margin": None,
                "required_criteria_coverage": None,
                "category_scope_consistent": True,
                "hard_filter_violations": 0,
                "protected_exclusion_violations": 0,
                "result_product_ids": [entry.binding.product_id for entry in result.entries[:5]],
                "interpretation_family": run_kind,
                "degraded": result.status != "OK",
                "warnings": [*result.unknown_terms, *result.degraded_components],
            },
            self.recovery_models.retrieval_run,
        )


class ResearchPortAdapter:
    """Matches the chat worktree's detect/search/validate research port."""

    def __init__(
        self,
        fixtures: Iterable[Any] = (),
        enabled: bool = False,
        chat_models: ChatModelTypes | None = None,
    ) -> None:
        self.fixtures = list(fixtures)
        self.enabled = enabled
        self.chat_models = chat_models or ChatModelTypes()

    def detect_need(
        self, intent: Any, current_message: str, selected_entity_ids: Sequence[str]
    ) -> Any:
        intent_data = payload_of(intent)
        explicit = bool(
            intent_data.get(
                "explicit_research", intent_data.get("primary_action") == "RESEARCH_EXTERNAL"
            )
        )
        result = detect_research_need(current_message, explicit_consent=explicit)
        payload = research_decision_to_chat(result)
        payload["selected_entity_ids"] = list(selected_entity_ids)[:4]
        return _as_local(payload, self.chat_models.research_decision)

    def search(self, decision: Any, deadline_ms: int) -> Any:
        del deadline_ms
        shared_decision = research_decision_from_worktree(decision)
        result = online_search(shared_decision, self.fixtures, enabled=self.enabled)
        return _as_local(online_search_to_chat(result), self.chat_models.online_search_result)

    def validate(self, decision: Any, search_result: Any, synthesis: Any) -> Any:
        del decision
        from .contracts import ResearchSource

        search_data = payload_of(search_result)
        sources = [
            source
            if isinstance(source, ResearchSource)
            else ResearchSource(
                source_id=str(source["source_id"]),
                url=str(source.get("canonical_url", source.get("url", "https://example.invalid"))),
                domain=str(source.get("domain", "example.invalid")),
                title=str(source.get("title", "source")),
                extract=str(source.get("extract", "")),
                retrieved_at=(
                    datetime.fromisoformat(str(source["retrieved_at"]).replace("Z", "+00:00"))
                    if source.get("retrieved_at")
                    else None
                ),
                citation_key=str(source.get("source_id", "source")),
            )
            for source in search_data.get("sources", [])
        ]
        synthesis_data = synthesis if hasattr(synthesis, "model_dump") else synthesis
        if isinstance(synthesis_data, dict) and "answer" not in synthesis_data:
            citations: list[str] = []
            for item in synthesis_data.get("claims", []):
                if not isinstance(item, dict):
                    continue
                citations.extend(str(source_id) for source_id in item.get("support_source_ids", []))
                if item.get("source_id"):
                    citations.append(str(item["source_id"]))
            answer = ResearchAnswer(
                status="OK",
                answer=str(synthesis_data.get("summary", synthesis_data.get("answer_summary", ""))),
                citations=list(dict.fromkeys(citations)),
            )
        else:
            answer = (
                synthesis_data
                if isinstance(synthesis_data, ResearchAnswer)
                else ResearchAnswer.model_validate(synthesis_data)
            )
        validated = validate_research_claims(answer, sources)
        chat_sources = online_search_to_chat(
            OnlineSearchResult(status="OK", provider="fixture", executed_query="", sources=sources)
        )["sources"]
        return _as_local(
            research_answer_to_chat(validated, chat_sources), self.chat_models.validated_research
        )


class SuggestionPortAdapter:
    """Exact chat-port seam; cart candidates are rejected at candidate creation."""

    def __init__(
        self, secret: bytes, clock_ms: int = 0, chat_models: ChatModelTypes | None = None
    ) -> None:
        self.store = SuggestionStore(secret)
        self.clock_ms = clock_ms
        self.chat_models = chat_models or ChatModelTypes()

    def build_and_store(self, response: Any, snapshot: Any, trace_id: str, deadline_ms: int) -> Any:
        del deadline_ms
        response_data = payload_of(response)
        snapshot_data = payload_of(snapshot)
        compatibility = compatibility_from_worktree(snapshot_data.get("compatibility_tuple"))
        action = str(response_data.get("action", "SEARCH"))
        bindings = []
        for entry in response_data.get("search_entries", []):
            bindings.append(
                binding_from_worktree(payload_of(entry)["binding"], compatibility.catalog_version)
            )
        result = self.store.build_and_store(
            str(snapshot_data["session_id"]),
            trace_id,
            action,
            bindings,
            compatibility,
            self.clock_ms,
            state_version=int(snapshot_data.get("state_version", 0)),
            cart_version=int(snapshot_data.get("cart_version", 0)),
            active_result_set_id=snapshot_data.get("acknowledged_result_set_id"),
            blocked=bool(payload_of(response).get("clarification")),
        )
        if result is None:
            return None
        return _as_local(suggestion_set_to_chat(result), self.chat_models.suggestion_set)

    def select(self, request: Any) -> Any:
        data = payload_of(request)
        result = self.store.select(
            str(data["session_id"]),
            str(data["suggestion_set_id"]),
            str(data["suggestion_id"]),
            self.clock_ms,
            expected_state_version=data.get("expected_state_version"),
            expected_cart_version=data.get("expected_cart_version"),
            signed_action_token=str(data.get("signed_action_token", "")),
        )
        return _as_local(
            suggestion_selection_to_chat(result), self.chat_models.suggestion_selection
        )
