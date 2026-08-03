"""Deterministic fakes used by contract tests and offline integration."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from ..domain import (
    ApprovedExpansion,
    CompatibilityTuple,
    RecoveryConstraint,
    RecoveryContext,
    RecoveryEvent,
    RecoveryPlannerOutput,
    RetrievalRun,
    QueryState,
)
from ..ports import ClosedCircuitPort
from ..validation import normalize_term


class FixedClock:
    def __init__(self, *, start_ms: int = 0) -> None:
        self.current_ms = start_ms
        self.current_utc = datetime(2026, 8, 2, tzinfo=timezone.utc)

    def monotonic_ms(self) -> int:
        return self.current_ms

    def now_utc(self) -> datetime:
        return self.current_utc

    def advance(self, milliseconds: int) -> None:
        self.current_ms += milliseconds


class SequenceIds:
    def __init__(self) -> None:
        self.counter = 0

    def new_id(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}-{self.counter:04d}"


class InMemoryApprovedExpansions:
    def __init__(self, values: Iterable[ApprovedExpansion] = ()) -> None:
        self.values = list(values)
        self.calls = 0

    def lookup(
        self,
        *,
        normalized_terms: list[str],
        query_state: QueryState,
        compatibility: CompatibilityTuple,
        limit: int,
    ) -> list[ApprovedExpansion]:
        self.calls += 1
        allowed = set(normalized_terms)
        values = [
            item
            for item in self.values
            if normalize_term(item.normalized_form) in allowed
            and item.locale == query_state.locale
            and item.lexicon_version == compatibility.lexicon_version
            and item.catalog_version == compatibility.catalog_version
            and item.taxonomy_version == compatibility.taxonomy_version
            and item.category_schema_version == compatibility.category_schema_version
            and item.taxonomy_scope_id in {None, query_state.taxonomy_scope_id}
        ]
        return values[:limit]


class InMemoryRecoveryConstraints:
    def __init__(self, values: Iterable[RecoveryConstraint] = ()) -> None:
        self.values = list(values)
        self.calls = 0

    def get_constraints(
        self,
        *,
        query_state: QueryState,
        unknown_terms: list[str],
        compatibility: CompatibilityTuple,
        limit: int,
    ) -> list[RecoveryConstraint]:
        self.calls += 1
        return [
            item
            for item in self.values
            if item.active
            and item.catalog_version == compatibility.catalog_version
            and item.taxonomy_version == compatibility.taxonomy_version
            and item.category_schema_version == compatibility.category_schema_version
            and item.lexicon_version == compatibility.lexicon_version
            and item.locale == query_state.locale
            and item.taxonomy_scope_id in {None, query_state.taxonomy_scope_id}
        ][:limit]


class ScriptedRetrieval:
    def __init__(self, runs: Iterable[RetrievalRun]) -> None:
        self.runs = list(runs)
        self.calls: list[str] = []

    def search(
        self,
        *,
        query_state: QueryState,
        query_terms: list[str],
        compatibility: CompatibilityTuple,
        run_kind: str,
        remaining_ms: int,
    ) -> RetrievalRun:
        self.calls.append(run_kind)
        if not self.runs:
            raise RuntimeError("no scripted retrieval run")
        return self.runs.pop(0)


class FakePlanner:
    def __init__(
        self,
        output: RecoveryPlannerOutput | None,
        *,
        codes: Iterable[str] = (),
        tokens: int = 0,
        latency_ms: int = 0,
    ) -> None:
        self.output = output
        self.codes = list(codes)
        self.tokens = tokens
        self.latency_ms = latency_ms
        self.calls = 0
        self.timeouts: list[int] = []
        self.last_context: RecoveryContext | None = None

    def plan(
        self,
        *,
        context: RecoveryContext,
        timeout_ms: int,
    ) -> tuple[RecoveryPlannerOutput | None, list[str], int, int]:
        self.calls += 1
        self.timeouts.append(timeout_ms)
        self.last_context = context
        return self.output, list(self.codes), self.tokens, self.latency_ms


class InMemoryRecoveryEvents:
    def __init__(self) -> None:
        self.events: list[RecoveryEvent] = []

    def record(self, event: RecoveryEvent) -> None:
        self.events.append(event)


__all__ = [
    "ClosedCircuitPort",
    "FakePlanner",
    "FixedClock",
    "InMemoryApprovedExpansions",
    "InMemoryRecoveryConstraints",
    "InMemoryRecoveryEvents",
    "ScriptedRetrieval",
    "SequenceIds",
]
