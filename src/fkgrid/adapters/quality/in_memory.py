"""Deterministic in-memory ports for tests and local workflow demos.

This adapter is not a production database.  It exists to prove the domain and
workflow contracts while another owner supplies SQLAlchemy/Alembic storage.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from threading import RLock
from uuid import uuid4

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
    canonical_sha256,
)


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class UuidIdGenerator:
    def new_id(self, prefix: str) -> str:
        return f"{prefix}_{uuid4().hex}"


class StaticPolicyProvider:
    def __init__(self, policy: QualityPolicy) -> None:
        self.policy = policy

    def active_policy(self) -> QualityPolicy:
        return deepcopy(self.policy)


class InMemoryQualityPersistence:
    """Thread-safe fake with idempotent signal/case operations."""

    def __init__(self) -> None:
        self._lock = RLock()
        self.signals_by_id: dict[str, QualitySignal] = {}
        self.signal_ids_by_idempotency: dict[str, str] = {}
        self.qualifications_by_group: dict[str, QualificationResult] = {}
        self.cases_by_id: dict[str, QualityCase] = {}
        self.case_ids_by_group: dict[str, str] = {}
        self.events_by_case: dict[str, list[CaseEvent]] = {}
        self.decisions: list[object] = []

    def get_signal_by_idempotency(self, idempotency_key: str) -> QualitySignal | None:
        with self._lock:
            signal_id = self.signal_ids_by_idempotency.get(idempotency_key)
            return deepcopy(self.signals_by_id[signal_id]) if signal_id else None

    def save_signal(self, signal: QualitySignal) -> QualitySignal:
        with self._lock:
            prior_id = self.signal_ids_by_idempotency.get(signal.idempotency_key)
            if prior_id is not None:
                return deepcopy(self.signals_by_id[prior_id])
            self.signals_by_id[signal.signal_id] = deepcopy(signal)
            self.signal_ids_by_idempotency[signal.idempotency_key] = signal.signal_id
            return deepcopy(signal)

    def save_qualification(self, result: QualificationResult) -> QualificationResult:
        with self._lock:
            self.qualifications_by_group[result.group_key.stable_key] = deepcopy(result)
            return deepcopy(result)

    def list_group_signals(self, group_key: GroupKey) -> list[QualitySignal]:
        with self._lock:
            values = [
                signal
                for signal in self.signals_by_id.values()
                if signal.occurred_at >= group_key.window_start
                and signal.occurred_at < group_key.window_end
                and signal.signal_type is group_key.signal_type
                and signal.binding.product_id == group_key.product_id
                and signal.binding.sku_id == group_key.sku_id
                and signal.binding.listing_id == group_key.listing_id
            ]
            return deepcopy(sorted(values, key=lambda item: (item.occurred_at, item.signal_id)))

    def get_case_by_group(self, group_key: GroupKey) -> QualityCase | None:
        with self._lock:
            case_id = self.case_ids_by_group.get(group_key.stable_key)
            return deepcopy(self.cases_by_id[case_id]) if case_id else None

    def get_case(self, case_id: str) -> QualityCase | None:
        with self._lock:
            case = self.cases_by_id.get(case_id)
            return deepcopy(case) if case else None

    def save_case(self, case: QualityCase) -> QualityCase:
        with self._lock:
            self.cases_by_id[case.case_id] = deepcopy(case)
            self.case_ids_by_group[case.group_key.stable_key] = case.case_id
            return deepcopy(case)

    def append_case_event(self, event: CaseEvent) -> CaseEvent:
        with self._lock:
            self.events_by_case.setdefault(event.case_id, []).append(deepcopy(event))
            return deepcopy(event)

    def list_case_events(self, case_id: str) -> list[CaseEvent]:
        with self._lock:
            return deepcopy(self.events_by_case.get(case_id, []))

    def save_human_decision(self, decision: HumanDecision) -> HumanDecision:
        with self._lock:
            self.decisions.append(deepcopy(decision))
            return deepcopy(decision)

    def case_events(self, case_id: str) -> list[CaseEvent]:
        with self._lock:
            return deepcopy(self.events_by_case.get(case_id, []))


class InMemoryCatalogSnapshotReader:
    def __init__(self, snapshots: list[CatalogSnapshot] | None = None) -> None:
        self.snapshots = {snapshot.snapshot_id: deepcopy(snapshot) for snapshot in snapshots or []}
        self.calls: list[str] = []

    def get_snapshot(self, signal: QualitySignal) -> CatalogSnapshot | None:
        binding_key = canonical_sha256(signal.binding)
        self.calls.append(binding_key)
        for snapshot in self.snapshots.values():
            if snapshot.binding == signal.binding and snapshot.captured_at <= signal.occurred_at:
                return deepcopy(snapshot)
        return None


class InMemoryReviewQueue:
    def __init__(self) -> None:
        self.routes: list[QualityRoute] = []

    def enqueue(self, route: QualityRoute) -> QueueReceipt:
        self.routes.append(deepcopy(route))
        return QueueReceipt(
            queue_name=route.queue_name,
            case_id=route.case_id,
            accepted=True,
            delivery_key=f"{route.queue_name}:{route.case_id}",
        )


class DeterministicQualityClassifier:
    """Small fake; real model providers implement the same port elsewhere."""

    def __init__(self, issue_class: str = "PRODUCT_DEFECT", confidence: float = 0.90) -> None:
        self.issue_class = issue_class
        self.confidence = confidence
        self.calls: list[str] = []

    def classify(self, packet: EvidencePacket) -> QualityAssessmentProposal:
        self.calls.append(packet.case_id)
        evidence_ids = [item.evidence_id for item in packet.items[:3]]
        from fkgrid.domain.quality import IssueClass

        return QualityAssessmentProposal(
            issue_class=IssueClass(self.issue_class),
            confidence=self.confidence,
            supporting_evidence_ids=evidence_ids,
            contradicting_evidence_ids=[],
            missing_information=packet.missing_information,
            bounded_summary="Structured evidence supports review; human confirmation is required.",
            model_alias="fake-quality-classifier",
            prompt_version="quality_v1",
        )


class InMemoryAuditSink:
    def __init__(self) -> None:
        self.events: list[CaseEvent] = []

    def record(self, event: CaseEvent) -> None:
        self.events.append(deepcopy(event))
