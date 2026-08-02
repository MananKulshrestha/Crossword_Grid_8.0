"""Dependency composition for the API; business policy stays in the workflow."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from ..query_recovery.adapters.gemini import (
    GEMMA_4_26B_A4B_IT,
    GeminiGemmaConfig,
    build_gemma4_recovery_planner,
)
from ..query_recovery.adapters.in_memory import (
    InMemoryApprovedExpansions,
    InMemoryRecoveryConstraints,
    InMemoryRecoveryEvents,
    ScriptedRetrieval,
    SequenceIds,
)
from ..query_recovery.domain import (
    CompatibilityTuple,
    ConceptType,
    RecoveryConstraint,
    RetrievalRun,
)
from ..query_recovery.ports import ClosedCircuitPort, RecoveryPlannerPort
from ..query_recovery.tools import RecoveryTools
from ..query_recovery.validation import hard_filter_hash, query_state_hash
from ..query_recovery.workflow import QueryRecoveryWorkflow


class ApiClock:
    """Wall-clock adapter for API event timestamps and monotonic deadlines."""

    def monotonic_ms(self) -> int:
        return time.monotonic_ns() // 1_000_000

    def now_utc(self) -> datetime:
        return datetime.now(timezone.utc)


class DemoPlanner:
    """Deterministic fallback when no provider credential is configured."""

    def plan(self, *, context, timeout_ms: int):
        del timeout_ms
        if len(context.allowed_concepts) >= 2:
            from ..query_recovery.domain import RecoveryClarificationPlan

            return (
                RecoveryClarificationPlan(
                    option_ids=[concept.concept_id for concept in context.allowed_concepts[:2]],
                    target_field="category",
                    reason="The demo query has two possible category interpretations.",
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
        del query_terms, remaining_ms
        self.calls.append(run_kind)
        return RetrievalRun(
            run_id=f"demo-{run_kind.lower()}",
            query_state_hash=query_state_hash(query_state),
            hard_filter_hash=hard_filter_hash(query_state),
            compatibility=compatibility,
            eligible_count=1,
            top_score=0.8,
            top_score_margin=0.2,
            required_criteria_coverage=1.0,
            result_product_ids=["demo-product-1"],
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
        expansions=InMemoryApprovedExpansions(),
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
    """Use Gemma when configured, otherwise keep Swagger usable offline.

    Only the planner is provider-backed here. Catalog/retrieval/lexicon data
    remains demo in-memory data until the owning DB adapters are injected.
    """

    try:
        config = GeminiGemmaConfig.from_env()
    except ValueError:
        return build_demo_dependencies()

    clock = ApiClock()
    events = InMemoryRecoveryEvents()
    tools = RecoveryTools(
        expansions=InMemoryApprovedExpansions(),
        constraints=InMemoryRecoveryConstraints(_demo_constraints()),
        retrieval=DemoRetrieval(),
        planner=build_gemma4_recovery_planner(config=config),
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
        mode="gemma4",
        planner_model=GEMMA_4_26B_A4B_IT,
        data_mode="demo-in-memory",
    )


__all__ = [
    "RecoveryApiDependencies",
    "build_default_dependencies",
    "build_demo_dependencies",
]
