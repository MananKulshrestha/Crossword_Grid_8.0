"""Ports for the Quality Sentinel.

These protocols are the integration contract for the database, catalog
snapshot service, model gateway, queue, clock, and audit owners.  No adapter
type is imported here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from fkgrid.domain.quality import (
    CaseEvent,
    CatalogSnapshot,
    EvidencePacket,
    GroupKey,
    HumanDecision,
    QualificationResult,
    QualityAssessmentProposal,
    QualityCase,
    QualityPolicy,
    QualityRoute,
    QualitySignal,
    QueueReceipt,
)


class Clock(Protocol):
    def now(self) -> datetime: ...


class IdGenerator(Protocol):
    def new_id(self, prefix: str) -> str: ...


class QualityPolicyProvider(Protocol):
    def active_policy(self) -> QualityPolicy: ...


class QualityPersistence(Protocol):
    """Required durable operations; implement with SQLAlchemy/Alembic elsewhere."""

    def get_signal_by_idempotency(self, idempotency_key: str) -> QualitySignal | None: ...

    def save_signal(self, signal: QualitySignal) -> QualitySignal: ...

    def save_qualification(self, result: QualificationResult) -> QualificationResult: ...

    def list_group_signals(self, group_key: GroupKey) -> list[QualitySignal]: ...

    def get_case_by_group(self, group_key: GroupKey) -> QualityCase | None: ...

    def get_case(self, case_id: str) -> QualityCase | None: ...

    def save_case(self, case: QualityCase) -> QualityCase: ...

    def append_case_event(self, event: CaseEvent) -> CaseEvent: ...

    def list_case_events(self, case_id: str) -> list[CaseEvent]: ...

    def save_human_decision(self, decision: HumanDecision) -> HumanDecision: ...


class CatalogSnapshotReader(Protocol):
    """Reads immutable event-time facts, never current facts as a substitute."""

    def get_snapshot(self, signal: QualitySignal) -> CatalogSnapshot | None: ...


class QualityClassifier(Protocol):
    """One bounded structured classification call with no tools."""

    def classify(self, packet: EvidencePacket) -> QualityAssessmentProposal: ...


class ReviewQueue(Protocol):
    """Review-only handoff; there is intentionally no enforcement method."""

    def enqueue(self, route: QualityRoute) -> QueueReceipt: ...


class QualityAuditSink(Protocol):
    def record(self, event: CaseEvent) -> None: ...
