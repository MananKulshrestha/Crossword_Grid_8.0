"""Dependency composition for the API; business policy stays in the workflow."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime

from ..query_recovery.adapters.deepinfra import (
    DEEPINFRA_GEMMA_4_26B_A4B_IT,
    DeepInfraGemmaConfig,
    build_deepinfra_gemma4_recovery_planner,
)
from ..query_recovery.adapters.in_memory import (
    InMemoryApprovedExpansions,
    InMemoryRecoveryConstraints,
    InMemoryRecoveryEvents,
    SequenceIds,
)
from ..query_recovery.domain import (
    ApprovedExpansion,
    ConceptType,
    EvidenceBand,
    ExpansionAction,
    MappingType,
    RecoveryConstraint,
    RetrievalRun,
)
from ..query_recovery.ports import ClosedCircuitPort, RecoveryPlannerPort
from ..query_recovery.tools import RecoveryTools
from ..query_recovery.validation import hard_filter_hash, normalize_term, query_state_hash
from ..query_recovery.workflow import QueryRecoveryWorkflow


class ApiClock:
    """Wall-clock adapter for API event timestamps and monotonic deadlines."""

    def monotonic_ms(self) -> int:
        return time.monotonic_ns() // 1_000_000

    def now_utc(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True)
class DemoSemanticFamily:
    """A small approved semantic family used only by the Swagger demo seam."""

    key: str
    aliases: tuple[str, ...]
    product_ids: tuple[str, ...]
    interpretation: str
    popular: bool = True


_DEMO_SEMANTIC_FAMILIES = (
    DemoSemanticFamily(
        key="formal-direct",
        aliases=("formal", "wear", "formal wear"),
        product_ids=("demo-formal-direct-1",),
        interpretation="formal wear direct match",
        popular=False,
    ),
    DemoSemanticFamily(
        key="shirts",
        aliases=("shirt", "shirts"),
        product_ids=("demo-formal-shirt-1",),
        interpretation="formal shirts",
    ),
    DemoSemanticFamily(
        key="blazers",
        aliases=("blazer", "blazers"),
        product_ids=("demo-formal-blazer-1",),
        interpretation="formal blazers",
    ),
    DemoSemanticFamily(
        key="trousers",
        aliases=("trouser", "trousers", "pants"),
        product_ids=("demo-formal-trouser-1",),
        interpretation="formal trousers",
    ),
    DemoSemanticFamily(
        key="ties",
        aliases=("tie", "ties"),
        product_ids=("demo-formal-tie-1",),
        interpretation="formal ties",
        popular=False,
    ),
    DemoSemanticFamily(
        key="sports-shoes",
        aliases=("trainers", "sports shoes", "athletic shoes", "running shoes"),
        product_ids=("demo-sports-shoe-1",),
        interpretation="trainers / sports shoes / athletic shoes",
    ),
    DemoSemanticFamily(
        key="footwear",
        aliases=("shoes", "footwear", "sneakers"),
        product_ids=("demo-footwear-1",),
        interpretation="shoes / footwear",
    ),
)

_FORMAL_WEAR_DESCENDANT_KEYS = ("shirts", "blazers", "trousers", "ties")


class DemoPlanner:
    """Deterministic fallback when no provider credential is configured."""

    def plan(self, *, context, timeout_ms: int):
        del timeout_ms
        unresolved = {normalize_term(term) for term in context.unresolved_terms}
        typo_rewrites = {
            "shooes": "demo-footwear",
            "shoos": "demo-footwear",
            "sportshoes": "demo-sports-shoes",
            "sport shoe": "demo-sports-shoes",
        }
        for misspelling, concept_id in typo_rewrites.items():
            if misspelling not in unresolved:
                continue
            concept = next(
                (item for item in context.allowed_concepts if item.concept_id == concept_id),
                None,
            )
            if concept is not None:
                from ..query_recovery.domain import RecoveryRewritePlan

                return (
                    RecoveryRewritePlan(
                        added_concept_ids=[concept.concept_id],
                        interpretation_label=concept.label,
                        preserved_hard_filter_hash=context.hard_filter_hash,
                    ),
                    [],
                    0,
                    0,
                )
        from ..query_recovery.domain import RecoveryNoSafePlan

        return (
            RecoveryNoSafePlan(
                reason_code="DEMO_NO_SAFE_INTERPRETATION",
                preserved_hard_filter_hash=context.hard_filter_hash,
            ),
            [],
            0,
            0,
        )


class DemoRetrieval:
    """Small deterministic retrieval seam for Swagger-only local testing."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def search(self, *, query_state, query_terms, compatibility, run_kind, remaining_ms):
        del remaining_ms
        self.calls.append(run_kind)
        normalized_terms = [normalize_term(term) for term in query_terms]
        matched: list[DemoSemanticFamily] = []
        seen_keys: set[str] = set()
        for term in normalized_terms:
            for candidate in _DEMO_SEMANTIC_FAMILIES:
                if candidate.key in seen_keys or term not in candidate.aliases:
                    continue
                seen_keys.add(candidate.key)
                matched.append(candidate)
        if "formal wear" in normalized_terms:
            for key in _FORMAL_WEAR_DESCENDANT_KEYS:
                candidate = next(item for item in _DEMO_SEMANTIC_FAMILIES if item.key == key)
                if candidate.key not in seen_keys:
                    seen_keys.add(candidate.key)
                    matched.append(candidate)
        product_ids = [product_id for family in matched for product_id in family.product_ids]
        popular_count = sum(1 for family in matched if family.popular)
        interpretation = ", ".join(family.interpretation for family in matched) or None
        run_key = "+".join(family.key for family in matched) or "no-match"
        return RetrievalRun(
            run_id=f"demo-{run_kind.lower()}-{run_key}",
            query_state_hash=query_state_hash(query_state),
            hard_filter_hash=hard_filter_hash(query_state),
            compatibility=compatibility,
            eligible_count=len(product_ids),
            popular_result_count=popular_count if matched else None,
            top_popularity_score=0.86 if popular_count else None,
            top_score=0.95 if matched and matched[0].key == "formal-direct" else (0.86 if matched else None),
            top_score_margin=0.22 if matched else None,
            required_criteria_coverage=1.0 if matched else None,
            result_product_ids=product_ids[:5],
            interpretation_family=interpretation,
        )


@dataclass(frozen=True)
class RecoveryApiDependencies:
    """Everything the delivery layer needs; production callers inject real ports."""

    workflow: QueryRecoveryWorkflow
    mode: str
    planner_model: str
    data_mode: str
    ready: bool = True
    readiness_reasons: tuple[str, ...] = ()


def _demo_constraints() -> list[RecoveryConstraint]:
    versions = {
        "catalog_version": "catalog-swagger-demo",
        "taxonomy_version": "taxonomy-swagger-demo",
        "category_schema_version": "schema-swagger-demo",
        "lexicon_version": "lexicon-swagger-demo",
    }
    return [
        RecoveryConstraint(
            concept_id="demo-formal-wear",
            concept_type=ConceptType.TAXONOMY,
            label="Formal wear",
            canonical_term="formal wear",
            **versions,
        ),
        RecoveryConstraint(
            concept_id="demo-shirts",
            concept_type=ConceptType.TAXONOMY,
            label="Shirts",
            canonical_term="shirts",
            **versions,
        ),
        RecoveryConstraint(
            concept_id="demo-trousers",
            concept_type=ConceptType.TAXONOMY,
            label="Trousers",
            canonical_term="trousers",
            **versions,
        ),
        RecoveryConstraint(
            concept_id="demo-ties",
            concept_type=ConceptType.TAXONOMY,
            label="Ties",
            canonical_term="ties",
            **versions,
        ),
        RecoveryConstraint(
            concept_id="demo-blazers",
            concept_type=ConceptType.TAXONOMY,
            label="Blazers",
            canonical_term="blazers",
            **versions,
        ),
        RecoveryConstraint(
            concept_id="demo-footwear",
            concept_type=ConceptType.TAXONOMY,
            label="Footwear",
            canonical_term="footwear",
            **versions,
        ),
        RecoveryConstraint(
            concept_id="demo-sports-shoes",
            concept_type=ConceptType.TAXONOMY,
            label="Sports shoes",
            canonical_term="sports shoes",
            **versions,
        ),
        RecoveryConstraint(
            concept_id="demo-athletic-shoes",
            concept_type=ConceptType.TAXONOMY,
            label="Athletic shoes",
            canonical_term="athletic shoes",
            **versions,
        ),
    ]


def _demo_expansions() -> list[ApprovedExpansion]:
    versions = {
        "catalog_version": "catalog-swagger-demo",
        "taxonomy_version": "taxonomy-swagger-demo",
        "category_schema_version": "schema-swagger-demo",
        "lexicon_version": "lexicon-swagger-demo",
    }
    common = {
        "concept_type": ConceptType.TAXONOMY,
        "mapping_type": MappingType.ALIAS,
        "expansion_action": ExpansionAction.CANONICAL_SYNONYM,
        "evidence_band": EvidenceBand.APPROVED_HIGH,
        **versions,
    }
    return [
        ApprovedExpansion(
            mapping_id="demo-formal-wear-to-taxonomy",
            normalized_form="formal wear",
            original_form="formal wear",
            canonical_target_id="demo-formal-wear",
            canonical_label="formal wear",
            priority=200,
            **{
                **common,
                "mapping_type": MappingType.COMPOUND,
                "expansion_action": ExpansionAction.CANONICAL_SYNONYM,
            },
        ),
        ApprovedExpansion(
            mapping_id="demo-shoes-to-footwear",
            normalized_form="shoes",
            original_form="shoes",
            canonical_target_id="demo-footwear",
            canonical_label="footwear",
            priority=100,
            **common,
        ),
        ApprovedExpansion(
            mapping_id="demo-trainers-to-sports-shoes",
            normalized_form="trainers",
            original_form="trainers",
            canonical_target_id="demo-sports-shoes",
            canonical_label="sports shoes",
            priority=100,
            **common,
        ),
        ApprovedExpansion(
            mapping_id="demo-sneakers-to-sports-shoes",
            normalized_form="sneakers",
            original_form="sneakers",
            canonical_target_id="demo-sports-shoes",
            canonical_label="sports shoes",
            priority=100,
            **common,
        ),
        ApprovedExpansion(
            mapping_id="demo-athletic-to-athletic-shoes",
            normalized_form="athletic",
            original_form="athletic",
            canonical_target_id="demo-athletic-shoes",
            canonical_label="athletic shoes",
            priority=100,
            **common,
        ),
    ]


def build_demo_dependencies(
    *,
    planner_port: RecoveryPlannerPort | None = None,
    planner_model: str = "demo-planner",
    mode: str = "demo",
) -> RecoveryApiDependencies:
    """Compose the same workflow with deterministic Swagger-local adapters."""

    clock = ApiClock()
    events = InMemoryRecoveryEvents()
    tools = RecoveryTools(
        expansions=InMemoryApprovedExpansions(_demo_expansions()),
        constraints=InMemoryRecoveryConstraints(_demo_constraints()),
        retrieval=DemoRetrieval(),
        planner=planner_port or DemoPlanner(),
        events=events,
        clock=clock,
    )
    workflow = QueryRecoveryWorkflow(
        tools=tools,
        clock=clock,
        ids=SequenceIds(),
        circuit=ClosedCircuitPort(),
    )
    return RecoveryApiDependencies(
        workflow=workflow,
        mode=mode,
        planner_model=planner_model,
        data_mode="demo-in-memory",
    )


def build_default_dependencies() -> RecoveryApiDependencies:
    """Use DeepInfra Gemma when configured, otherwise keep Swagger usable offline.

    Only the planner is provider-backed here. Catalog/retrieval/lexicon data
    remains demo in-memory data until the owning DB adapters are injected.
    """

    try:
        config = DeepInfraGemmaConfig.from_env()
    except ValueError:
        return build_demo_dependencies()

    clock = ApiClock()
    events = InMemoryRecoveryEvents()
    tools = RecoveryTools(
        expansions=InMemoryApprovedExpansions(_demo_expansions()),
        constraints=InMemoryRecoveryConstraints(_demo_constraints()),
        retrieval=DemoRetrieval(),
        planner=build_deepinfra_gemma4_recovery_planner(config=config),
        events=events,
        clock=clock,
    )
    workflow = QueryRecoveryWorkflow(
        tools=tools,
        clock=clock,
        ids=SequenceIds(),
        circuit=ClosedCircuitPort(),
    )
    return RecoveryApiDependencies(
        workflow=workflow,
        mode="deepinfra-gemma4",
        planner_model=DEEPINFRA_GEMMA_4_26B_A4B_IT,
        data_mode="demo-in-memory",
    )


__all__ = [
    "RecoveryApiDependencies",
    "build_default_dependencies",
    "build_demo_dependencies",
]
