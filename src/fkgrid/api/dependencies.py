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
    product_id: str
    interpretation: str


_DEMO_SEMANTIC_FAMILIES = (
    DemoSemanticFamily(
        key="sports-shoes",
        aliases=("trainers", "sports shoes", "athletic shoes", "running shoes"),
        product_id="demo-sports-shoe-1",
        interpretation="trainers / sports shoes / athletic shoes",
    ),
    DemoSemanticFamily(
        key="footwear",
        aliases=("shoes", "footwear", "sneakers"),
        product_id="demo-footwear-1",
        interpretation="shoes / footwear",
    ),
)


class DemoPlanner:
    """Deterministic fallback when no provider credential is configured."""

    def plan(self, *, context, timeout_ms: int):
        del timeout_ms
        unresolved = {normalize_term(term) for term in context.unresolved_terms}
        if unresolved.intersection({"formal", "wear", "formal wear"}):
            from ..query_recovery.domain import RecoveryClarificationPlan

            option_ids = [
                concept.concept_id
                for concept in context.allowed_concepts
                if concept.concept_id in {"demo-shirts", "demo-blazers"}
            ]
            return (
                RecoveryClarificationPlan(
                    option_ids=option_ids,
                    target_field="category",
                    reason="Formal wear can refer to more than one approved category.",
                    preserved_hard_filter_hash=context.hard_filter_hash,
                ),
                [],
                0,
                0,
            )
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
        normalized_terms = {normalize_term(term) for term in query_terms}
        family = next(
            (
                candidate
                for candidate in _DEMO_SEMANTIC_FAMILIES
                if normalized_terms.intersection(candidate.aliases)
            ),
            None,
        )
        return RetrievalRun(
            run_id=f"demo-{run_kind.lower()}-{family.key if family else 'no-match'}",
            query_state_hash=query_state_hash(query_state),
            hard_filter_hash=hard_filter_hash(query_state),
            compatibility=compatibility,
            eligible_count=1 if family else 0,
            top_score=0.86 if family else None,
            top_score_margin=0.22 if family else None,
            required_criteria_coverage=1.0 if family else None,
            result_product_ids=[family.product_id] if family else [],
            interpretation_family=family.interpretation if family else None,
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
            concept_id="demo-shirts",
            concept_type=ConceptType.TAXONOMY,
            label="Shirts",
            canonical_term="shirts",
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
