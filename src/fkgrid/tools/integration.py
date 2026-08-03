"""Runtime wiring between the deterministic tools and the shopper workflow.

The chat worktree owns its public Pydantic models, while this package owns the
provider-neutral specialist implementations.  This module is the one explicit
composition boundary: fixture/catalog records are projected once, adapters
validate their outputs back into the chat contracts, and the static registry is
kept separate from the cart port owned by the shopper workflow.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from fkgrid.agentic.contracts import (
    Availability as ChatAvailability,
)
from fkgrid.agentic.contracts import (
    CommerceEligibility as ChatCommerceEligibility,
)
from fkgrid.agentic.contracts import (
    Comparison as ChatComparison,
)
from fkgrid.agentic.contracts import (
    CompatibilityTuple as ChatCompatibilityTuple,
)
from fkgrid.agentic.contracts import (
    ConfidenceDecision as ChatConfidenceDecision,
)
from fkgrid.agentic.contracts import (
    OnlineSearchResult as ChatOnlineSearchResult,
)
from fkgrid.agentic.contracts import (
    ProductDetails as ChatProductDetails,
)
from fkgrid.agentic.contracts import (
    ReferenceResolution as ChatReferenceResolution,
)
from fkgrid.agentic.contracts import (
    ResearchDecision as ChatResearchDecision,
)
from fkgrid.agentic.contracts import (
    SearchEntry as ChatSearchEntry,
)
from fkgrid.agentic.contracts import (
    SearchResult as ChatSearchResult,
)
from fkgrid.agentic.contracts import (
    SuggestionSelectionResult as ChatSuggestionSelectionResult,
)
from fkgrid.agentic.contracts import (
    SuggestionSet as ChatSuggestionSet,
)
from fkgrid.agentic.contracts import (
    ValidatedResearch as ChatValidatedResearch,
)

from .catalog import DeterministicCatalog
from .compat import (
    CatalogSearchPortAdapter,
    ChatModelTypes,
    QueryRecoveryPortAdapter,
    RecoveryPortAdapter,
    ReferenceResolverPortAdapter,
    ResearchPortAdapter,
    SuggestionPortAdapter,
)
from .contracts import CatalogRecord, EvidenceRef, ProductBinding
from .interop import compatibility_from_worktree
from .registry import ToolRegistry, build_default_registry
from .workflow_adapters import (
    CatalogLanguagePortAdapter,
    CatalogOperationsPortAdapter,
    QualityPortAdapter,
)

_CORE_FIELDS = {
    "title",
    "category",
    "category_id",
    "brand",
    "price",
    "prototype price",
    "availability",
}


def _value_of(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "amount_paise"):
        return float(value.amount_paise) / 100.0
    return value


def _text_value(value: Any) -> str:
    value = _value_of(value)
    if isinstance(value, (dict, list, tuple, set)):
        return str(value)
    return str(value)


def _record_from_chat_entry(entry: ChatSearchEntry) -> CatalogRecord:
    facts = {fact.label.casefold(): fact for fact in entry.facts}
    category_value = facts.get("category")
    category_id = _text_value(category_value.typed_value) if category_value else "unknown"
    brand_value = facts.get("brand")
    brand = _text_value(brand_value.typed_value) if brand_value else None
    price_value = facts.get("price") or facts.get("prototype price")
    price = _value_of(price_value.typed_value) if price_value else None
    if not isinstance(price, (int, float)):
        price = None
    availability_value = facts.get("availability")
    raw_availability = (
        _text_value(availability_value.typed_value) if availability_value else "UNKNOWN"
    )
    availability = raw_availability
    if raw_availability == "RETIRED":
        availability = "UNAVAILABLE"
    if availability not in {"AVAILABLE", "UNAVAILABLE", "UNKNOWN"}:
        availability = "UNKNOWN"

    attributes: dict[str, str] = {}
    evidence_refs: dict[str, EvidenceRef] = {}
    for fact in entry.facts:
        if fact.label.casefold() not in _CORE_FIELDS and fact.typed_value is not None:
            attributes[fact.label] = _text_value(fact.typed_value)
        for evidence in fact.evidence_refs:
            evidence_refs.setdefault(
                evidence.evidence_id,
                EvidenceRef(
                    evidence_id=evidence.evidence_id,
                    source=evidence.source_type,
                    field=evidence.field_path,
                    version=evidence.version,
                    as_of=getattr(evidence, "as_of", None),
                ),
            )

    binding = ProductBinding(
        product_id=entry.binding.product_id,
        sku_id=entry.binding.sku_id,
        offer_id=entry.binding.offer_id,
        catalog_version=entry.binding.catalog_version,
    )
    return CatalogRecord(
        binding=binding,
        title=entry.title,
        category_id=category_id,
        brand=brand,
        attributes=attributes,
        price=float(price) if price is not None else None,
        availability=availability,
        # Retired/unknown offers remain visible as catalogue records so the
        # shopper can see the grounded availability outcome; cart eligibility
        # is enforced separately by the purpose-aware catalog policy.
        eligible=True,
        evidence_refs=list(evidence_refs.values()),
    )


def records_from_chat_entries(entries: Iterable[ChatSearchEntry]) -> list[CatalogRecord]:
    """Project canonical chat fixture entries into tool catalog records."""

    return [_record_from_chat_entry(entry) for entry in entries]


@dataclass(frozen=True)
class RuntimeTooling:
    """All non-cart tool implementations bound for one API runtime."""

    records: tuple[CatalogRecord, ...]
    catalog: DeterministicCatalog
    registry: ToolRegistry
    shopper_catalog: CatalogSearchPortAdapter
    references: ReferenceResolverPortAdapter
    recovery: RecoveryPortAdapter
    query_recovery: QueryRecoveryPortAdapter
    research: ResearchPortAdapter
    suggestions: SuggestionPortAdapter
    catalog_language: CatalogLanguagePortAdapter
    catalog_operations: CatalogOperationsPortAdapter
    quality: QualityPortAdapter

    @property
    def tool_names(self) -> tuple[str, ...]:
        return self.registry.names


def _chat_models() -> ChatModelTypes:
    return ChatModelTypes(
        search_result=ChatSearchResult,
        product_details=ChatProductDetails,
        comparison=ChatComparison,
        availability=ChatAvailability,
        commerce_eligibility=ChatCommerceEligibility,
        reference_resolution=ChatReferenceResolution,
        confidence_decision=ChatConfidenceDecision,
        research_decision=ChatResearchDecision,
        online_search_result=ChatOnlineSearchResult,
        validated_research=ChatValidatedResearch,
        suggestion_set=ChatSuggestionSet,
        suggestion_selection=ChatSuggestionSelectionResult,
    )


def build_runtime_tooling(
    entries: Sequence[ChatSearchEntry],
    compatibility: ChatCompatibilityTuple,
    *,
    suggestion_secret: bytes | None = None,
    research_enabled: bool = False,
    research_fixtures: Iterable[Any] = (),
) -> RuntimeTooling:
    """Create the explicit tool/adaptor graph for one in-memory API runtime."""

    pinned = compatibility_from_worktree(compatibility)
    records = tuple(records_from_chat_entries(entries))
    catalog = DeterministicCatalog(records, pinned)
    models = _chat_models()
    shopper_catalog = CatalogSearchPortAdapter(catalog, models)
    references = ReferenceResolverPortAdapter(catalog, models)
    recovery = RecoveryPortAdapter(catalog, chat_models=models)
    query_recovery = QueryRecoveryPortAdapter(catalog)
    research = ResearchPortAdapter(
        fixtures=research_fixtures,
        enabled=research_enabled,
        chat_models=models,
    )
    suggestions = SuggestionPortAdapter(
        suggestion_secret or secrets.token_bytes(32), chat_models=models
    )
    registry = build_default_registry(catalog)
    return RuntimeTooling(
        records=records,
        catalog=catalog,
        registry=registry,
        shopper_catalog=shopper_catalog,
        references=references,
        recovery=recovery,
        query_recovery=query_recovery,
        research=research,
        suggestions=suggestions,
        catalog_language=CatalogLanguagePortAdapter(records),
        catalog_operations=CatalogOperationsPortAdapter(),
        quality=QualityPortAdapter(),
    )


__all__ = ["RuntimeTooling", "build_runtime_tooling", "records_from_chat_entries"]
