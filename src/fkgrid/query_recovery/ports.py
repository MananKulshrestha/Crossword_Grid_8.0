"""Provider/database/retrieval seams owned by other application components."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .domain import (
    ApprovedExpansion,
    CompatibilityTuple,
    RecoveryConstraint,
    RecoveryContext,
    RecoveryEvent,
    RecoveryPlannerOutput,
    RecoveryPlan,
    RetrievalRun,
    QueryState,
)


class ClockPort(Protocol):
    def monotonic_ms(self) -> int:
        """Monotonic milliseconds; never use wall time for a deadline."""

    def now_utc(self) -> datetime:
        """UTC time for persisted event timestamps."""


class IdPort(Protocol):
    def new_id(self, prefix: str) -> str:
        """Create an opaque runtime ID."""


class ApprovedExpansionPort(Protocol):
    """Read-only view of the Catalog Language Agent's active lexicon."""

    def lookup(
        self,
        *,
        normalized_terms: list[str],
        query_state: QueryState,
        compatibility: CompatibilityTuple,
        limit: int,
    ) -> list[ApprovedExpansion]:
        """Return compatible, approved mappings only."""


class RecoveryConstraintPort(Protocol):
    """Read-only active taxonomy/schema concepts for constrained planning."""

    def get_constraints(
        self,
        *,
        query_state: QueryState,
        unknown_terms: list[str],
        compatibility: CompatibilityTuple,
        limit: int,
    ) -> list[RecoveryConstraint]:
        """Return a bounded allowlist; the planner cannot invent concepts."""


class CatalogRetrievalPort(Protocol):
    """The normal deterministic retrieval owner, reused unchanged by recovery."""

    def search(
        self,
        *,
        query_state: QueryState,
        query_terms: list[str],
        compatibility: CompatibilityTuple,
        run_kind: str,
        remaining_ms: int,
    ) -> RetrievalRun:
        """Execute one bounded retrieval run and return a sanitized summary."""


class RecoveryPlannerPort(Protocol):
    """Provider-neutral structured gateway; no tool list is accepted here."""

    def plan(
        self,
        *,
        context: RecoveryContext,
        timeout_ms: int,
    ) -> tuple[RecoveryPlannerOutput | None, list[str], int, int]:
        """Return output, validation/transport codes, token count, latency."""


class RecoveryEventPort(Protocol):
    """Durable event/outbox boundary; it must be non-authoritative to the response."""

    def record(self, event: RecoveryEvent) -> None:
        """Persist a sanitized recovery event or enqueue it for retry."""


class RecoveryCircuitPort(Protocol):
    def is_open(self) -> bool:
        """Whether the Tier 2 provider circuit is open."""


class RecoveryPlanCachePort(Protocol):
    """Optional immutable-plan cache; it never stores session responses."""

    def get(self, key: str) -> RecoveryPlan | None:
        """Return an unexpired plan keyed by canonical state/tuple/policy."""

    def put(self, key: str, plan: RecoveryPlan) -> None:
        """Store an accepted plan for bounded TTL/size."""

    def clear(self) -> None:
        """Invalidate on activation or recovery-policy change."""


class ClosedCircuitPort:
    """Default circuit for local tests and offline operation."""

    def is_open(self) -> bool:
        return False
