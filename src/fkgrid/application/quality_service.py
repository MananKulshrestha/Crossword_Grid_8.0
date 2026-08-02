"""Deterministic Tier 1 Quality Sentinel application operations."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from fkgrid.domain.quality import (
    CaseDecision,
    CaseEvent,
    CaseStatus,
    CatalogSnapshot,
    EvidenceItem,
    EvidenceKind,
    EvidencePacket,
    GroupKey,
    HumanDecision,
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
    RiskRating,
    RoutePriority,
    RouteRule,
    Severity,
    SignalStatus,
    SignalType,
    SourceType,
    canonical_sha256,
    case_event_hash,
    redact_untrusted_text,
    transition_allowed,
)


def default_quality_policy() -> QualityPolicy:
    """Load the reviewed prototype defaults without pretending they are production policy."""

    return QualityPolicy(
        policy_version="quality-policy-v2-prototype",
        objective_min_independent_groups=2,
        fulfillment_min_independent_groups=2,
        subjective_min_independent_groups=2,
        subjective_min_source_classes=1,
        poor_review_requires_objective_flag=False,
        routes=[
            RouteRule(
                issue_class=IssueClass.PRODUCT_DEFECT,
                queue_name="product-quality-operations",
                priority=RoutePriority.HIGH,
                sla_minutes=60,
            ),
            RouteRule(
                issue_class=IssueClass.LISTING_CONTENT_MISMATCH,
                queue_name="catalog-content-operations",
                priority=RoutePriority.HIGH,
                sla_minutes=60,
            ),
            RouteRule(
                issue_class=IssueClass.COUNTERFEIT_OR_AUTHENTICITY,
                queue_name="safety-specialist-review",
                priority=RoutePriority.URGENT,
                sla_minutes=1,
            ),
            RouteRule(
                issue_class=IssueClass.SAFETY,
                queue_name="safety-specialist-review",
                priority=RoutePriority.URGENT,
                sla_minutes=1,
            ),
            RouteRule(
                issue_class=IssueClass.FULFILLMENT_OR_PACKAGING,
                queue_name="fulfillment-operations",
                priority=RoutePriority.NORMAL,
                sla_minutes=60,
            ),
            RouteRule(
                issue_class=IssueClass.SELLER_OR_SERVICE,
                queue_name="seller-service-operations",
                priority=RoutePriority.NORMAL,
                sla_minutes=1440,
            ),
            RouteRule(
                issue_class=IssueClass.SUBJECTIVE_PREFERENCE,
                queue_name="quality-feedback-aggregate",
                priority=RoutePriority.LOW,
                sla_minutes=1440,
            ),
            RouteRule(
                issue_class=IssueClass.INSUFFICIENT_EVIDENCE,
                queue_name="general-quality-triage",
                priority=RoutePriority.NORMAL,
                sla_minutes=60,
            ),
        ],
    )


def normalize_signal(signal_input: QualitySignalInput) -> QualitySignal:
    """Create the only persisted form of a signal; raw text never leaves this function."""

    redacted_text, report = redact_untrusted_text(signal_input.text)
    return QualitySignal(
        signal_id=signal_input.signal_id,
        idempotency_key=signal_input.idempotency_key,
        request_hash=canonical_sha256(signal_input),
        source_type=signal_input.source_type,
        signal_type=signal_input.signal_type,
        severity=signal_input.severity,
        binding=signal_input.binding,
        occurred_at=signal_input.occurred_at,
        received_at=signal_input.received_at,
        rating=signal_input.rating,
        objective_issue_flag=signal_input.objective_issue_flag,
        redacted_text=redacted_text,
        redaction_report=report,
        asset_refs=signal_input.asset_refs,
        source_reference=signal_input.source_reference,
        retention_class=signal_input.retention_class,
        reporter_group_id=signal_input.reporter_group_id,
        correlation_group_id=signal_input.correlation_group_id,
        source_class=signal_input.source_class,
        verified_direct_system_signal=signal_input.verified_direct_system_signal,
        status=SignalStatus.REDACTED,
    )


def qualify_signal_group(
    signals: list[QualitySignal], group_key: GroupKey, policy: QualityPolicy
) -> QualificationResult:
    """Apply only structured policy inputs; prose is intentionally ignored."""

    if not signals:
        return QualificationResult(
            status=QualificationStatus.INTAKE_CORRECTION,
            group_key=group_key,
            policy_version=policy.policy_version,
            trigger="MISSING_SIGNAL_GROUP",
            qualified_count=0,
            independent_group_count=0,
            source_class_count=0,
            correlated_group_count=0,
            evidence_complete=False,
            urgent=False,
            reason="No verified signal context is available for this group.",
        )

    if any(signal.signal_type is SignalType.UNKNOWN for signal in signals):
        return QualificationResult(
            status=QualificationStatus.INTAKE_CORRECTION,
            group_key=group_key,
            policy_version=policy.policy_version,
            trigger="UNKNOWN_REASON_CODE",
            qualified_count=len(signals),
            independent_group_count=0,
            source_class_count=len({signal.source_class for signal in signals}),
            correlated_group_count=0,
            evidence_complete=False,
            urgent=False,
            reason="A reviewed structured reason code is required before qualification.",
        )

    for signal in signals:
        if signal.source_type is SourceType.POOR_REVIEW and (
            signal.rating is None
            or signal.rating > policy.poor_review_max_rating
            or (
                policy.poor_review_requires_objective_flag
                and signal.objective_issue_flag is not True
            )
        ):
            return QualificationResult(
                status=QualificationStatus.NOT_QUALIFIED,
                group_key=group_key,
                policy_version=policy.policy_version,
                trigger="INELIGIBLE_POOR_REVIEW",
                qualified_count=len(signals),
                independent_group_count=0,
                source_class_count=len({item.source_class for item in signals}),
                correlated_group_count=0,
                evidence_complete=True,
                urgent=False,
                reason=("Poor reviews require the configured low-rating and objective-issue gate."),
            )

    independent_groups = {
        signal.reporter_group_id for signal in signals if signal.reporter_group_id is not None
    }
    direct_system_count = sum(1 for signal in signals if signal.verified_direct_system_signal)
    source_class_count = len({signal.source_class for signal in signals})
    evidence_complete = all(
        signal.binding.product_id
        and signal.binding.catalog_version
        and signal.source_class
        and signal.source_reference.source_checksum
        for signal in signals
    )

    correlation_members: dict[str, set[str]] = {}
    for signal in signals:
        if signal.correlation_group_id is not None and signal.reporter_group_id is not None:
            correlation_members.setdefault(signal.correlation_group_id, set()).add(
                signal.reporter_group_id
            )
    correlated_count = sum(len(groups) for groups in correlation_members.values())
    correlated_credit = min(
        correlated_count,
        max(1, int(len(independent_groups) * policy.correlated_group_cap_fraction))
        if correlated_count
        else 0,
    )
    credited_independent_count = len(independent_groups) - correlated_count + correlated_credit
    credited_independent_count = max(0, credited_independent_count)

    urgent = group_key.signal_type in policy.urgent_signal_types or (
        group_key.signal_type is SignalType.OBJECTIVE_INCORRECT
        and policy.immediate_listing_mismatch
        and any(signal.severity in {Severity.HIGH, Severity.URGENT} for signal in signals)
    )
    if urgent and evidence_complete:
        return QualificationResult(
            status=QualificationStatus.QUALIFIED,
            group_key=group_key,
            policy_version=policy.policy_version,
            trigger="URGENT_POLICY_BYPASS",
            qualified_count=len(signals),
            independent_group_count=max(credited_independent_count, direct_system_count),
            source_class_count=source_class_count,
            correlated_group_count=max(0, correlated_count - correlated_credit),
            evidence_complete=True,
            urgent=True,
            reason="An urgent structured reason code bypasses recurrence thresholds.",
        )

    if group_key.signal_type is SignalType.ABUSE_SPAM:
        return QualificationResult(
            status=QualificationStatus.NOT_QUALIFIED,
            group_key=group_key,
            policy_version=policy.policy_version,
            trigger="AGGREGATE_FEEDBACK_ONLY",
            qualified_count=len(signals),
            independent_group_count=credited_independent_count,
            source_class_count=source_class_count,
            correlated_group_count=max(0, correlated_count - correlated_credit),
            evidence_complete=evidence_complete,
            urgent=False,
            reason="Abuse/spam feedback remains aggregate evidence in Tier 1.",
        )

    if not evidence_complete:
        status = QualificationStatus.INTAKE_CORRECTION
        trigger = "INCOMPLETE_STRUCTURED_EVIDENCE"
        qualified = False
    elif group_key.signal_type is SignalType.OBJECTIVE_INCORRECT:
        qualified = credited_independent_count >= policy.objective_min_independent_groups
        status = QualificationStatus.QUALIFIED if qualified else QualificationStatus.NOT_QUALIFIED
        trigger = "OBJECTIVE_RECURRENCE_THRESHOLD"
    elif group_key.signal_type is SignalType.FULFILLMENT_MISMATCH:
        qualified = (
            direct_system_count >= 1
            or credited_independent_count >= policy.fulfillment_min_independent_groups
        )
        status = QualificationStatus.QUALIFIED if qualified else QualificationStatus.NOT_QUALIFIED
        trigger = (
            "FULFILLMENT_DIRECT_SIGNAL"
            if direct_system_count
            else "FULFILLMENT_RECURRENCE_THRESHOLD"
        )
    elif group_key.signal_type is SignalType.SUBJECTIVE_QUALITY:
        qualified = (
            credited_independent_count >= policy.subjective_min_independent_groups
            and source_class_count >= policy.subjective_min_source_classes
        )
        status = QualificationStatus.QUALIFIED if qualified else QualificationStatus.NOT_QUALIFIED
        trigger = "SUBJECTIVE_REVIEW_THRESHOLD"
    else:
        qualified = False
        status = QualificationStatus.NOT_QUALIFIED
        trigger = "UNSUPPORTED_QUALIFICATION_PATH"

    return QualificationResult(
        status=status,
        group_key=group_key,
        policy_version=policy.policy_version,
        trigger=trigger,
        qualified_count=len(signals),
        independent_group_count=max(credited_independent_count, direct_system_count),
        source_class_count=source_class_count,
        correlated_group_count=max(0, correlated_count - correlated_credit),
        evidence_complete=evidence_complete,
        urgent=False,
        reason=(
            "Versioned recurrence threshold met."
            if qualified
            else "The versioned recurrence/evidence threshold was not met."
        ),
    )


def _severity_rank(value: str) -> int:
    return {"URGENT": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(value, 0)


def _near_duplicate(left: str, right: str) -> bool:
    left_tokens = set(re.findall(r"[a-z0-9]+", left.casefold()))
    right_tokens = set(re.findall(r"[a-z0-9]+", right.casefold()))
    if not left_tokens or not right_tokens:
        return left.strip() == right.strip()
    intersection = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    return intersection / union >= 0.90


def assemble_evidence_packet(
    case_id: str,
    group_key: GroupKey,
    signals: list[QualitySignal],
    snapshots: list[CatalogSnapshot],
    policy: QualityPolicy,
) -> EvidencePacket:
    """Build a bounded packet while retaining counts and event-time facts."""

    ranked = sorted(
        signals,
        key=lambda signal: (
            -_severity_rank(signal.severity.value),
            -signal.occurred_at.timestamp(),
            signal.signal_id,
        ),
    )
    ordered: list[QualitySignal] = []
    for source_class in sorted({signal.source_class for signal in ranked}):
        first = next(signal for signal in ranked if signal.source_class == source_class)
        ordered.append(first)
    ordered.extend(signal for signal in ranked if signal not in ordered)
    structured_items: list[EvidenceItem] = []
    prose_items: list[EvidenceItem] = []
    prose_sources: list[set[str]] = []
    seen_text: list[str] = []
    for signal in ordered:
        evidence_hash = canonical_sha256(
            {
                "binding": signal.binding,
                "signal_type": signal.signal_type.value,
                "source_class": signal.source_class,
                "text": signal.redacted_text,
            }
        )
        structured_id = f"evidence_{signal.signal_id}"
        structured_items.append(
            EvidenceItem(
                evidence_id=structured_id,
                kind=EvidenceKind.STRUCTURED_EVENT,
                signal_id=signal.signal_id,
                binding=signal.binding,
                source_type=signal.source_type,
                source_class=signal.source_class,
                severity=signal.severity,
                occurred_at=signal.occurred_at,
                normalized_issue_type=signal.signal_type,
                redacted_excerpt="",
                evidence_hash=evidence_hash,
                untrusted_content=True,
            )
        )
        duplicate_index = next(
            (
                index
                for index, previous in enumerate(seen_text)
                if _near_duplicate(signal.redacted_text, previous)
            ),
            None,
        )
        if signal.redacted_text and duplicate_index is None:
            prose_items.append(
                EvidenceItem(
                    evidence_id=f"prose_{signal.signal_id}",
                    kind=EvidenceKind.PROSE_EXCERPT,
                    signal_id=signal.signal_id,
                    binding=signal.binding,
                    source_type=signal.source_type,
                    source_class=signal.source_class,
                    severity=signal.severity,
                    occurred_at=signal.occurred_at,
                    normalized_issue_type=signal.signal_type,
                    redacted_excerpt=signal.redacted_text[:500],
                    evidence_hash=evidence_hash,
                    untrusted_content=True,
                )
            )
            seen_text.append(signal.redacted_text)
            prose_sources.append({signal.source_class})
        elif duplicate_index is not None:
            source_classes = prose_sources[duplicate_index] | {signal.source_class}
            prose_sources[duplicate_index] = source_classes
            prose_items[duplicate_index] = prose_items[duplicate_index].model_copy(
                update={
                    "occurrence_count": prose_items[duplicate_index].occurrence_count + 1,
                    "source_count": len(source_classes),
                }
            )

    snapshot_items: list[EvidenceItem] = []
    for snapshot in sorted(snapshots, key=lambda item: item.snapshot_id):
        snapshot_items.append(
            EvidenceItem(
                evidence_id=f"snapshot_{snapshot.snapshot_id}",
                kind=EvidenceKind.CATALOG_SNAPSHOT,
                binding=snapshot.binding,
                source_type=SourceType.SYSTEM_SIGNAL,
                source_class="EVENT_TIME_CATALOG",
                severity=Severity.MEDIUM,
                occurred_at=snapshot.captured_at,
                normalized_issue_type=group_key.signal_type,
                catalog_fact_ids=[fact.evidence_id for fact in snapshot.facts],
                evidence_hash=canonical_sha256(snapshot),
                untrusted_content=True,
            )
        )

    missing: list[str] = []
    if len(snapshots) < len({signal.binding.catalog_version for signal in signals}):
        missing.append("EVENT_TIME_CATALOG_SNAPSHOT")
    if any(not signal.reporter_group_id for signal in signals) and not any(
        signal.verified_direct_system_signal for signal in signals
    ):
        missing.append("INDEPENDENCE_REPORTER_GROUP")
    structured_items = structured_items[: policy.max_structured_evidence]
    prose_items = prose_items[: policy.max_prose_evidence]
    return EvidencePacket(
        case_id=case_id,
        policy_version=policy.policy_version,
        group_key=group_key,
        items=(
            structured_items
            + snapshot_items[: max(0, policy.max_structured_evidence - len(structured_items))]
            + prose_items
        ),
        snapshots=snapshots[:20],
        missing_information=missing,
        allowed_issue_classes=list(IssueClass),
    )


def deterministic_risk_rating(packet: EvidencePacket, issue_class: IssueClass) -> RiskRating:
    """Derive the final risk from structured facts, never from model prose.

    The model still proposes a risk value for a constrained, grounded response,
    but this policy-owned result makes replayed identical evidence stable even
    when a provider varies its proposed label.
    """

    severities = {item.severity for item in packet.items}
    if (
        packet.group_key.signal_type is SignalType.SAFETY_URGENT
        or Severity.URGENT in severities
        or issue_class is IssueClass.SAFETY
    ):
        return RiskRating.CRITICAL
    if packet.group_key.signal_type is SignalType.SUBJECTIVE_QUALITY:
        return RiskRating.LOW
    if packet.group_key.signal_type is SignalType.FULFILLMENT_MISMATCH:
        return RiskRating.MEDIUM
    if (
        packet.group_key.signal_type is SignalType.OBJECTIVE_INCORRECT
        or Severity.HIGH in severities
        or issue_class
        in {
            IssueClass.PRODUCT_DEFECT,
            IssueClass.LISTING_CONTENT_MISMATCH,
            IssueClass.COUNTERFEIT_OR_AUTHENTICITY,
        }
    ):
        return RiskRating.HIGH
    if issue_class in {IssueClass.FULFILLMENT_OR_PACKAGING, IssueClass.SELLER_OR_SERVICE}:
        return RiskRating.MEDIUM
    return RiskRating.LOW


def validate_assessment(
    proposal: QualityAssessmentProposal,
    packet: EvidencePacket,
    policy: QualityPolicy,
) -> QualityAssessment:
    """Validate model output as an untrusted proposal and safely abstain on failure."""

    available_ids = {item.evidence_id for item in packet.items} | {
        fact.evidence_id for snapshot in packet.snapshots for fact in snapshot.facts
    }
    reasons: list[str] = []
    if proposal.issue_class not in packet.allowed_issue_classes:
        reasons.append("UNSUPPORTED_ISSUE_CLASS")
    if proposal.confidence < policy.minimum_classifier_confidence:
        reasons.append("LOW_CONFIDENCE")
    if not set(proposal.supporting_evidence_ids).issubset(available_ids):
        reasons.append("UNRESOLVED_SUPPORTING_CITATION")
    if not set(proposal.contradicting_evidence_ids).issubset(available_ids):
        reasons.append("UNRESOLVED_CONTRADICTING_CITATION")
    if set(proposal.supporting_evidence_ids) & set(proposal.contradicting_evidence_ids):
        reasons.append("CONFLICTING_CITATION_ROLES")
    if (
        proposal.issue_class is not IssueClass.INSUFFICIENT_EVIDENCE
        and not proposal.supporting_evidence_ids
    ):
        reasons.append("NO_SUPPORTING_CITATION")
    if packet.missing_information and proposal.issue_class is not IssueClass.INSUFFICIENT_EVIDENCE:
        reasons.append("MISSING_REQUIRED_CONTEXT")

    if reasons:
        fallback_issue_class = IssueClass.INSUFFICIENT_EVIDENCE
        return QualityAssessment(
            issue_class=fallback_issue_class,
            risk_rating=deterministic_risk_rating(packet, fallback_issue_class),
            confidence=0.0,
            supporting_evidence_ids=[],
            contradicting_evidence_ids=[
                evidence_id
                for evidence_id in proposal.contradicting_evidence_ids
                if evidence_id in available_ids
            ],
            missing_information=sorted(set(packet.missing_information + reasons)),
            bounded_summary=(
                "Evidence is insufficient for an automated quality conclusion; "
                "human triage is required."
            ),
            model_alias=proposal.model_alias,
            prompt_version=proposal.prompt_version,
            validation_status="FALLBACK_INSUFFICIENT_EVIDENCE",
            validation_reasons=reasons,
        )
    return QualityAssessment(
        issue_class=proposal.issue_class,
        risk_rating=deterministic_risk_rating(packet, proposal.issue_class),
        confidence=proposal.confidence,
        supporting_evidence_ids=proposal.supporting_evidence_ids,
        contradicting_evidence_ids=proposal.contradicting_evidence_ids,
        missing_information=proposal.missing_information,
        bounded_summary=proposal.bounded_summary,
        model_alias=proposal.model_alias,
        prompt_version=proposal.prompt_version,
        validation_status="VALID",
        validation_reasons=[],
    )


def route_assessment(
    case: QualityCase, assessment: QualityAssessment, policy: QualityPolicy
) -> QualityRoute:
    # An urgent structured signal owns its specialist destination.  The model
    # may describe the evidence, but it cannot downgrade or redirect urgency.
    if (
        case.trigger == "URGENT_POLICY_BYPASS"
        or case.group_key.signal_type is SignalType.SAFETY_URGENT
    ):
        rule = policy.route_for(IssueClass.SAFETY)
    elif case.group_key.signal_type is SignalType.FULFILLMENT_MISMATCH:
        rule = policy.route_for(IssueClass.FULFILLMENT_OR_PACKAGING)
    else:
        rule = policy.route_for(assessment.issue_class)
    priority = rule.priority
    sla_minutes = rule.sla_minutes
    if case.trigger == "URGENT_POLICY_BYPASS":
        priority = RoutePriority.URGENT
        sla_minutes = 1
    return QualityRoute(
        case_id=case.case_id,
        issue_class=assessment.issue_class,
        queue_name=rule.queue_name,
        priority=priority,
        sla_minutes=sla_minutes,
        policy_version=policy.policy_version,
        immutable_case_reference=f"quality-case:{case.case_id}",
    )


def make_case_event(
    event_id: str,
    case_id: str,
    from_status: CaseStatus | None,
    to_status: CaseStatus,
    actor_type: Literal["QUALITY_SENTINEL", "QUALITY_REVIEWER", "SYSTEM"],
    actor_id: str,
    reason: str,
    created_at: datetime,
    previous_event_hash: str | None,
) -> CaseEvent:
    draft = CaseEvent(
        event_id=event_id,
        case_id=case_id,
        from_status=from_status,
        to_status=to_status,
        actor_type=actor_type,
        actor_id=actor_id,
        reason=reason,
        created_at=created_at,
        previous_event_hash=previous_event_hash,
        event_hash="0" * 64,
    )
    return draft.model_copy(update={"event_hash": case_event_hash(draft)})


def apply_case_transition(
    case: QualityCase,
    to_status: CaseStatus,
    now: datetime,
    reason: str,
) -> QualityCase:
    if not transition_allowed(case.status, to_status):
        raise ValueError(
            f"invalid Quality Sentinel transition {case.status.value}->{to_status.value}"
        )
    return case.model_copy(update={"status": to_status, "updated_at": now})


def human_decision_for_case(
    case: QualityCase,
    decision: CaseDecision,
    decision_id: str,
    reviewer_id: str,
    reason: str,
    now: datetime,
    follow_up_recommendation: str | None = None,
) -> HumanDecision:
    if case.status not in {CaseStatus.ROUTED, CaseStatus.READY_FOR_REVIEW, CaseStatus.TRIAGED}:
        raise ValueError("human decisions require a review-ready or routed case")
    return HumanDecision(
        decision_id=decision_id,
        case_id=case.case_id,
        decision=decision,
        reviewer_role="QUALITY_REVIEWER",
        reviewer_id=reviewer_id,
        reason=reason,
        decided_at=now,
        follow_up_recommendation=follow_up_recommendation,
    )
