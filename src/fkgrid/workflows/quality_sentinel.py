"""Explicit Tier 1 Quality Sentinel state machine and tool registry."""

from __future__ import annotations

from typing import Literal

from fkgrid.application.quality_service import (
    apply_case_transition,
    assemble_evidence_packet,
    human_decision_for_case,
    make_case_event,
    normalize_signal,
    qualify_signal_group,
    route_assessment,
    validate_assessment,
)
from fkgrid.domain.quality import (
    CaseStatus,
    CatalogSnapshot,
    EvidencePacket,
    GroupKey,
    IssueClass,
    QualificationResult,
    QualificationStatus,
    QualityAssessment,
    QualityAssessmentProposal,
    QualityCase,
    QualityPolicy,
    QualityRoute,
    QualitySignal,
    QualitySignalInput,
    QualityToolName,
    QualityTrace,
    QualityWorkflowResult,
    RiskRating,
    SignalStatus,
    ToolRun,
    ToolStatus,
    canonical_sha256,
    group_key_for_signal,
)
from fkgrid.ports.quality import (
    CatalogSnapshotReader,
    Clock,
    IdGenerator,
    QualityAuditSink,
    QualityClassifier,
    QualityPersistence,
    QualityPolicyProvider,
    ReviewQueue,
)


class QualityToolRegistry:
    """Static capability inventory; model text cannot add tools at runtime."""

    allowed_tools = frozenset(QualityToolName)
    forbidden_capabilities = frozenset(
        {
            "publish_catalog",
            "activate_catalog",
            "suppress_listing",
            "change_ranking",
            "refund_order",
            "penalize_seller",
            "contact_seller",
        }
    )

    def __init__(self) -> None:
        if self.allowed_tools & self.forbidden_capabilities:  # pragma: no cover - invariant guard
            raise RuntimeError("forbidden Quality Sentinel capability registered")

    def contains(self, tool_name: str) -> bool:
        return tool_name in {tool.value for tool in self.allowed_tools}


class QualitySentinelWorkflow:
    """Run one signal through bounded deterministic tools and one classifier call."""

    tool_version = "quality-tools-v1"
    classifier_prompt_version = "quality_v2"

    def __init__(
        self,
        *,
        persistence: QualityPersistence,
        policy_provider: QualityPolicyProvider,
        catalog: CatalogSnapshotReader,
        classifier: QualityClassifier,
        review_queue: ReviewQueue,
        clock: Clock,
        ids: IdGenerator,
        audit: QualityAuditSink,
    ) -> None:
        self.persistence = persistence
        self.policy_provider = policy_provider
        self.catalog = catalog
        self.classifier = classifier
        self.review_queue = review_queue
        self.clock = clock
        self.ids = ids
        self.audit = audit
        self.registry = QualityToolRegistry()
        self._active_tool_runs: list[ToolRun] | None = None

    def run(self, signal_input: QualitySignalInput) -> QualityWorkflowResult:
        policy = self.policy_provider.active_policy()
        trace_id = self.ids.new_id("trace")
        self._active_tool_runs = []
        warnings: list[str] = []
        try:
            expected_request_hash = canonical_sha256(signal_input)
            existing = self.persistence.get_signal_by_idempotency(signal_input.idempotency_key)
            if existing is not None:
                terminal = "IDEMPOTENCY_REPLAY"
                if existing.request_hash != expected_request_hash:
                    terminal = "IDEMPOTENCY_CONFLICT"
                    warnings.append("IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST")
                    outcome: Literal["REPLAY", "CONFLICT"] = "CONFLICT"
                else:
                    outcome = "REPLAY"
                trace = self._trace(trace_id, policy, terminal)
                return QualityWorkflowResult(
                    outcome=outcome,
                    signal=existing,
                    trace=trace,
                    warnings=warnings,
                )

            signal = self.ingest_quality_signal(signal_input, expected_request_hash)
            group_key = self._group_key(signal, policy)
            signals = self._signals_for_group(group_key)
            qualification = self.qualify_signal_group(signals, group_key, policy)
            self.persistence.save_qualification(qualification)
            if qualification.status is QualificationStatus.INTAKE_CORRECTION:
                return QualityWorkflowResult(
                    outcome="INTAKE_CORRECTION",
                    signal=signal,
                    qualification=qualification,
                    trace=self._trace(trace_id, policy, "INTAKE_CORRECTION"),
                    warnings=["MISSING_OR_UNREVIEWED_STRUCTURED_CONTEXT"],
                )
            if qualification.status is QualificationStatus.NOT_QUALIFIED:
                return QualityWorkflowResult(
                    outcome="NOT_QUALIFIED",
                    signal=signal,
                    qualification=qualification,
                    trace=self._trace(trace_id, policy, "STORED_FOR_AGGREGATE_MONITORING"),
                    warnings=[],
                )

            case = self._open_or_reuse_case(qualification, signal, policy)
            snapshots = self._get_snapshots(signals)
            if len(snapshots) < len({item.binding.catalog_version for item in signals}):
                case = self._move_case(
                    case, CaseStatus.AWAITING_EVIDENCE, "EVENT_TIME_SNAPSHOT_UNAVAILABLE"
                )
                self.persistence.save_case(case)
                warnings.append("EVENT_TIME_SNAPSHOT_UNAVAILABLE")
                return QualityWorkflowResult(
                    outcome="SAFE_TRIAGE",
                    signal=signal,
                    qualification=qualification,
                    case=case,
                    trace=self._trace(trace_id, policy, "AWAITING_EVENT_TIME_EVIDENCE"),
                    warnings=warnings,
                )

            packet = self.assemble_case_evidence(
                case, qualification.group_key, signals, snapshots, policy
            )
            proposal = self.classify_quality_issue(packet)
            assessment = validate_assessment(proposal, packet, policy)
            case = self._attach_assessment(case, assessment, packet)
            try:
                route = self.route_quality_case(case, assessment, policy, qualification.urgent)
            except Exception:
                case = self._move_case(case, CaseStatus.TRIAGED, "ROUTING_UNAVAILABLE")
                case = case.model_copy(update={"assessment": assessment})
                self.persistence.save_case(case)
                warnings.append("ROUTING_UNAVAILABLE")
                return QualityWorkflowResult(
                    outcome="SAFE_TRIAGE",
                    signal=signal,
                    qualification=qualification,
                    case=case,
                    trace=self._trace(trace_id, policy, "ROUTING_UNAVAILABLE"),
                    warnings=warnings,
                )
            case = case.model_copy(update={"route": route})
            case = self._move_case(case, CaseStatus.ROUTED, "DETERMINISTIC_REVIEW_ROUTE")
            self.persistence.save_case(case)
            warnings.extend(assessment.validation_reasons)
            return QualityWorkflowResult(
                outcome="QUALIFIED_CASE",
                signal=signal,
                qualification=qualification,
                case=case,
                trace=self._trace(trace_id, policy, "ROUTED_FOR_HUMAN_REVIEW"),
                warnings=warnings,
            )
        finally:
            self._active_tool_runs = None

    def ingest_quality_signal(
        self, signal_input: QualitySignalInput, request_hash: str | None = None
    ) -> QualitySignal:
        signal = normalize_signal(signal_input).model_copy(
            update={"request_hash": request_hash or canonical_sha256(signal_input)}
        )
        saved = self.persistence.save_signal(signal).model_copy(
            update={"status": SignalStatus.STORED}
        )
        self._record_tool(
            QualityToolName.INGEST_QUALITY_SIGNAL,
            ToolStatus.OK,
            {"signal_id": saved.signal_id, "source_type": saved.source_type.value},
        )
        return saved

    def qualify_signal_group(
        self, signals: list[QualitySignal], group_key: GroupKey, policy: QualityPolicy
    ) -> QualificationResult:
        result = qualify_signal_group(signals, group_key, policy)
        self._record_tool(
            QualityToolName.QUALIFY_SIGNAL_GROUP,
            ToolStatus.OK,
            {
                "signal_type": group_key.signal_type.value,
                "qualified_count": result.qualified_count,
                "independent_group_count": result.independent_group_count,
                "trigger": result.trigger,
            },
        )
        return result

    def get_catalog_snapshot(self, signal: QualitySignal) -> CatalogSnapshot | None:
        snapshot = self.catalog.get_snapshot(signal)
        identity_matches = (
            snapshot is not None
            and snapshot.binding == signal.binding
            and snapshot.captured_at <= signal.occurred_at
        )
        if snapshot is not None and not identity_matches:
            snapshot = None
        self._record_tool(
            QualityToolName.GET_CATALOG_SNAPSHOT,
            ToolStatus.OK if snapshot is not None else ToolStatus.UNAVAILABLE,
            {
                "signal_id": signal.signal_id,
                "catalog_version": signal.binding.catalog_version,
                "found": snapshot is not None,
            },
            [] if snapshot is not None else ["EVENT_TIME_SNAPSHOT_UNAVAILABLE"],
        )
        return snapshot

    def assemble_case_evidence(
        self,
        case: QualityCase,
        group_key: GroupKey,
        signals: list[QualitySignal],
        snapshots: list[CatalogSnapshot],
        policy: QualityPolicy,
    ) -> EvidencePacket:
        packet = assemble_evidence_packet(case.case_id, group_key, signals, snapshots, policy)
        self._record_tool(
            QualityToolName.ASSEMBLE_CASE_EVIDENCE,
            ToolStatus.OK,
            {
                "case_id": case.case_id,
                "structured_items": sum(
                    item.kind.value != "PROSE_EXCERPT" for item in packet.items
                ),
                "prose_items": sum(item.kind.value == "PROSE_EXCERPT" for item in packet.items),
                "snapshot_count": len(packet.snapshots),
            },
            packet.missing_information,
        )
        return packet

    def classify_quality_issue(self, packet: EvidencePacket) -> QualityAssessmentProposal:
        try:
            proposal = self.classifier.classify(packet)
            status = ToolStatus.OK
            warnings: list[str] = []
        except Exception:
            proposal = QualityAssessmentProposal(
                issue_class=IssueClass.INSUFFICIENT_EVIDENCE,
                risk_rating=RiskRating.LOW,
                confidence=0.0,
                supporting_evidence_ids=[],
                contradicting_evidence_ids=[],
                missing_information=["CLASSIFIER_UNAVAILABLE"],
                bounded_summary="Classifier unavailable; route to human triage.",
                model_alias="unavailable",
                prompt_version=self.classifier_prompt_version,
            )
            status = ToolStatus.UNAVAILABLE
            warnings = ["CLASSIFIER_UNAVAILABLE"]
        self._record_tool(
            QualityToolName.CLASSIFY_QUALITY_ISSUE,
            status,
            {
                "case_id": packet.case_id,
                "allowed_class_count": len(packet.allowed_issue_classes),
                "model_alias": proposal.model_alias,
                "citation_count": len(proposal.supporting_evidence_ids)
                + len(proposal.contradicting_evidence_ids),
            },
            warnings,
        )
        return proposal

    def route_quality_case(
        self,
        case: QualityCase,
        assessment: QualityAssessment,
        policy: QualityPolicy,
        urgent: bool,
    ) -> QualityRoute:
        route = route_assessment(case, assessment, policy)
        receipt = self.review_queue.enqueue(route)
        if not receipt.accepted:
            raise RuntimeError("review queue rejected the case")
        self._record_tool(
            QualityToolName.ROUTE_QUALITY_CASE,
            ToolStatus.OK,
            {
                "case_id": case.case_id,
                "queue_name": route.queue_name,
                "priority": route.priority.value,
                "urgent": urgent,
            },
        )
        return route

    def record_human_case_decision(
        self,
        case_id: str,
        decision: str,
        reviewer_role: str,
        reviewer_id: str,
        reason: str,
        follow_up_recommendation: str | None = None,
    ) -> QualityCase:
        from fkgrid.domain.quality import CaseDecision

        case = self.persistence.get_case(case_id)
        if case is None:
            raise ValueError("quality case not found")
        if reviewer_role != "QUALITY_REVIEWER":
            raise PermissionError("only QUALITY_REVIEWER may decide a case")
        human_decision = human_decision_for_case(
            case,
            CaseDecision(decision),
            self.ids.new_id("decision"),
            reviewer_id,
            reason,
            self.clock.now(),
            follow_up_recommendation,
        )
        updated = self._move_case(case, CaseStatus.DECIDED, "HUMAN_REVIEW_DECISION")
        self.persistence.save_human_decision(human_decision)
        self.persistence.save_case(updated)
        self._append_event(case, updated, "QUALITY_REVIEWER", reviewer_id, reason)
        self._record_tool(
            QualityToolName.RECORD_HUMAN_CASE_DECISION,
            ToolStatus.OK,
            {"case_id": case_id, "decision": human_decision.decision.value},
        )
        return updated

    def close_or_reopen_case(
        self,
        case_id: str,
        action: Literal["CLOSE", "REOPEN"],
        actor_id: str,
        reason: str,
    ) -> QualityCase:
        case = self.persistence.get_case(case_id)
        if case is None:
            raise ValueError("quality case not found")
        target = CaseStatus.CLOSED if action == "CLOSE" else CaseStatus.REOPENED
        updated = self._move_case(case, target, reason)
        self.persistence.save_case(updated)
        self._append_event(
            case,
            updated,
            "QUALITY_REVIEWER" if action == "CLOSE" else "SYSTEM",
            actor_id,
            reason,
        )
        self._record_tool(
            QualityToolName.CLOSE_OR_REOPEN_CASE,
            ToolStatus.OK,
            {"case_id": case_id, "action": action},
        )
        return updated

    def _group_key(self, signal: QualitySignal, policy: QualityPolicy) -> GroupKey:
        return group_key_for_signal(signal, policy)

    def _signals_for_group(self, group_key: GroupKey) -> list[QualitySignal]:
        return self.persistence.list_group_signals(group_key)

    def _open_or_reuse_case(
        self, qualification: QualificationResult, signal: QualitySignal, policy: QualityPolicy
    ) -> QualityCase:
        existing = self.persistence.get_case_by_group(qualification.group_key)
        now = self.clock.now()
        if existing is not None:
            if signal.signal_id not in existing.signal_ids:
                existing = existing.model_copy(
                    update={"signal_ids": existing.signal_ids + [signal.signal_id]}
                )
            if existing.status is CaseStatus.CLOSED:
                reopened = existing.model_copy(
                    update={
                        "status": CaseStatus.REOPENED,
                        "updated_at": now,
                        "reopened_from_case_id": existing.case_id,
                    }
                )
                self.persistence.save_case(reopened)
                self._append_event(
                    existing,
                    reopened,
                    "SYSTEM",
                    "quality-sentinel",
                    "NEW_QUALIFIED_EVIDENCE",
                )
                return reopened
            self.persistence.save_case(existing)
            return existing
        case = QualityCase(
            case_id=self.ids.new_id("case"),
            group_key=qualification.group_key,
            product_id=signal.binding.product_id,
            catalog_version=signal.binding.catalog_version,
            policy_version=policy.policy_version,
            status=CaseStatus.OPEN,
            trigger=qualification.trigger,
            signal_ids=[signal.signal_id],
            evidence_ids=[],
            assessment=None,
            route=None,
            opened_at=now,
            updated_at=now,
        )
        self.persistence.save_case(case)
        self._append_event(None, case, "QUALITY_SENTINEL", "quality-sentinel", "CASE_OPENED")
        return case

    def _get_snapshots(self, signals: list[QualitySignal]) -> list[CatalogSnapshot]:
        snapshots: list[CatalogSnapshot] = []
        seen: set[str] = set()
        for signal in signals:
            snapshot = self.get_catalog_snapshot(signal)
            if snapshot is not None and snapshot.snapshot_id not in seen:
                snapshots.append(snapshot)
                seen.add(snapshot.snapshot_id)
        return snapshots

    def _attach_assessment(
        self, case: QualityCase, assessment: QualityAssessment, packet: EvidencePacket
    ) -> QualityCase:
        current = case
        if current.status in {CaseStatus.OPEN, CaseStatus.REOPENED, CaseStatus.AWAITING_EVIDENCE}:
            current = self._move_case(current, CaseStatus.READY_FOR_REVIEW, "EVIDENCE_ASSEMBLED")
        return current.model_copy(
            update={
                "assessment": assessment,
                "evidence_ids": [item.evidence_id for item in packet.items],
            }
        )

    def _move_case(self, case: QualityCase, target: CaseStatus, reason: str) -> QualityCase:
        if case.status is target:
            return case
        updated = apply_case_transition(case, target, self.clock.now(), reason)
        self.persistence.save_case(updated)
        self._append_event(case, updated, "QUALITY_SENTINEL", "quality-sentinel", reason)
        return updated

    def _append_event(
        self,
        previous: QualityCase | None,
        current: QualityCase,
        actor_type: Literal["QUALITY_SENTINEL", "QUALITY_REVIEWER", "SYSTEM"],
        actor_id: str,
        reason: str,
    ) -> None:
        events = self.persistence.list_case_events(current.case_id)
        previous_hash = events[-1].event_hash if events else None
        event = make_case_event(
            event_id=self.ids.new_id("case-event"),
            case_id=current.case_id,
            from_status=previous.status if previous is not None else None,
            to_status=current.status,
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
            created_at=self.clock.now(),
            previous_event_hash=previous_hash,
        )
        self.persistence.append_case_event(event)
        self.audit.record(event)

    def _record_tool(
        self,
        name: QualityToolName,
        status: ToolStatus,
        input_summary: dict[str, object],
        warnings: list[str] | None = None,
    ) -> None:
        if self._active_tool_runs is None:
            return
        self._active_tool_runs.append(
            ToolRun(
                tool_name=name,
                tool_version=self.tool_version,
                status=status,
                latency_ms=0,
                sanitized_input_summary=input_summary,
                warnings=warnings or [],
            )
        )

    def _trace(self, trace_id: str, policy: QualityPolicy, terminal_state: str) -> QualityTrace:
        return QualityTrace(
            trace_id=trace_id,
            policy_version=policy.policy_version,
            tool_runs=list(self._active_tool_runs or []),
            terminal_state=terminal_state,
        )
