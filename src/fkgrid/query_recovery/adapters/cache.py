"""Bounded immutable recovery-plan cache."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from ..domain import RecoveryPlan
from ..ports import ClockPort, RecoveryPlanCachePort
from ..validation import recovery_cache_key


@dataclass(frozen=True)
class _CacheEntry:
    expires_at_ms: int
    plan: RecoveryPlan


class InMemoryRecoveryPlanCache(RecoveryPlanCachePort):
    def __init__(
        self,
        *,
        clock: ClockPort,
        max_entries: int = 256,
        ttl_ms: int = 300_000,
    ) -> None:
        self.clock = clock
        self.max_entries = max(1, min(max_entries, 256))
        self.ttl_ms = max(1, min(ttl_ms, 300_000))
        self._entries: OrderedDict[str, _CacheEntry] = OrderedDict()

    def get(self, key: str) -> RecoveryPlan | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at_ms <= self.clock.monotonic_ms():
            self._entries.pop(key, None)
            return None
        self._entries.move_to_end(key)
        return entry.plan.model_copy(deep=True)

    def put(self, key: str, plan: RecoveryPlan) -> None:
        self._entries[key] = _CacheEntry(
            expires_at_ms=self.clock.monotonic_ms() + self.ttl_ms,
            plan=plan.model_copy(deep=True),
        )
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()


__all__ = ["InMemoryRecoveryPlanCache", "recovery_cache_key"]
