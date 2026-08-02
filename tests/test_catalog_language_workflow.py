from __future__ import annotations

from datetime import UTC, datetime

from fkgrid.adapters.catalog_language.fakes import (
    ApprovingReview,
    CompareAndSwapActivation,
    FakeActiveLexicon,
    FakeClock,
    FakeEvidenceAggregation,
    FakeVocabulary,
    InMemoryTargetRetriever,
    InMemoryTrace,
    PassingRegression,
    PassingShadow,
    SequentialIds,
)
from fkgrid.adapters.model.fake import FakeCatalogLanguageModel, QueuedModelResponse
from fkgrid.domain.catalog_language import (
    CanonicalVocabularySnapshot,
    EvidenceBand,
    EvidenceGroup,
    EvidenceSourceClass,
    EvidenceWindow,
    ExpansionAction,
    LexiconCompatibility,
    LexiconMapping,
    LexiconScope,
    LexiconWorkflowRequest,
    MappingDirection,
    MappingKind,
    MappingStatus,
    ModelStatus,
    RegressionReport,
    TargetType,
    VocabularyItem,
    WorkflowStatus,
)
from fkgrid.workflows.catalog_language import CatalogLanguageTier2Workflow


def compatibility(lexicon_version: str = "lex-1") -> LexiconCompatibility:
    return LexiconCompatibility(
        catalog_version="cat-1",
        taxonomy_version="tax-1",
        category_schema_version="schema-1",
        lexicon_version=lexicon_version,
        normalizer_version="normalizer-v1",
        mapping_schema_version="mapping-v1",
        rank_policy_version="rank-v1",
    )


def evidence_group() -> EvidenceGroup:
    return EvidenceGroup(
        group_id="group-trainers",
        normalized_term="trainers",
        observed_surface_forms=["trainers", "trainer"],
        locale="en-IN",
        taxonomy_node_id="footwear",
        support_count=8,
        distinct_source_groups=8,
        source_classes=[EvidenceSourceClass.ZERO_RESULT, EvidenceSourceClass.RECOVERY],
        source_concentration=0.25,
        recovery_success_count=4,
        contradiction_count=0,
        first_observed_at=datetime(2026, 7, 2, tzinfo=UTC),
        last_observed_at=datetime(2026, 7, 30, tzinfo=UTC),
    )


def vocabulary() -> CanonicalVocabularySnapshot:
    return CanonicalVocabularySnapshot(
        catalog_version="cat-1",
        taxonomy_version="tax-1",
        category_schema_version="schema-1",
        normalizer_version="normalizer-v1",
        checksum="0" * 64,
        items=[
            VocabularyItem(
                target_type=TargetType.TAXONOMY_NODE,
                target_id="athletic-shoes",
                canonical_name="Athletic trainers",
                normalized_name="athletic trainers",
                scope={"locale": "en-IN", "taxonomy_node_id": "footwear"},
            )
        ],
    )


def request() -> LexiconWorkflowRequest:
    return LexiconWorkflowRequest(
        run_id="run-1",
        evidence_window=EvidenceWindow(
            window_start=datetime(2026, 7, 1, tzinfo=UTC),
            window_end=datetime(2026, 8, 1, tzinfo=UTC),
        ),
        compatibility=compatibility(),
        active_lexicon_version="lex-1",
        regression_policy_version="regression-v1",
        shadow_policy_version="shadow-v1",
    )


def workflow(
    *,
    model: FakeCatalogLanguageModel | None = None,
    regression: PassingRegression | None = None,
    review: ApprovingReview | None = None,
    active_mappings: list[LexiconMapping] | None = None,
) -> tuple[CatalogLanguageTier2Workflow, FakeCatalogLanguageModel, CompareAndSwapActivation]:
    selected_model = model or FakeCatalogLanguageModel()
    activation = CompareAndSwapActivation("lex-1")
    workflow = CatalogLanguageTier2Workflow(
        evidence=FakeEvidenceAggregation([evidence_group()]),
        vocabulary=FakeVocabulary(vocabulary()),
        targets=InMemoryTargetRetriever(),
        active_lexicon=FakeActiveLexicon(active_mappings or []),
        model=selected_model,
        regression=regression or PassingRegression(),
        shadow=PassingShadow(),
        review=review or ApprovingReview(),
        activation=activation,
        clock=FakeClock(),
        ids=SequentialIds(),
        trace_sink=InMemoryTrace(),
    )
    return workflow, selected_model, activation


def existing_mapping() -> LexiconMapping:
    return LexiconMapping(
        mapping_id="existing-mapping",
        surface_form="sports shoes",
        normalized_form="sports shoes",
        locale="en-IN",
        mapping_kind=MappingKind.SYNONYM,
        target_type=TargetType.TAXONOMY_NODE,
        target_id="athletic-shoes",
        scope=LexiconScope(locale="en-IN", taxonomy_node_id="footwear"),
        direction=MappingDirection.QUERY_TO_CANONICAL,
        expansion_action=ExpansionAction.CANONICAL_SYNONYM,
        evidence_band=EvidenceBand.APPROVED_HIGH,
        origin="TIER_1_CURATED",
        evidence_ids=["curated-1"],
        status=MappingStatus.APPROVED,
        compatibility=compatibility(),
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
    )


def test_tier2_workflow_runs_proposer_critic_regression_review_and_cas_activation() -> None:
    app, model, activation = workflow()

    result = app.run(request())

    assert result.status == WorkflowStatus.COMPLETED
    assert result.candidate is not None
    assert result.activation is not None
    assert result.activation.active_lexicon_version == result.candidate.candidate_version
    assert [call.logical_call for call in model.calls] == [
        "propose_canonical_mapping",
        "critique_mapping",
    ]
    assert activation.calls[0].expected_active_version == "lex-1"
    assert [event.step for event in result.trace][:4] == [
        "RECEIVED",
        "aggregate_query_gap_events",
        "threshold_gate",
        "load_canonical_vocabulary",
    ]


def test_insufficient_privacy_safe_evidence_stops_before_any_model_call() -> None:
    app, model, _ = workflow()
    app.evidence.groups[0] = evidence_group().model_copy(
        update={"distinct_source_groups": 2, "source_classes": [EvidenceSourceClass.ZERO_RESULT]}
    )

    result = app.run(request())

    assert result.status == WorkflowStatus.INSUFFICIENT_EVIDENCE
    assert model.calls == []
    assert "INSUFFICIENT_SOURCE_DIVERSITY" in result.warnings


def test_proposer_cannot_select_a_target_outside_supplied_allowed_set() -> None:
    model = FakeCatalogLanguageModel()
    model.queue(
        "propose_canonical_mapping",
        QueuedModelResponse(
            status=ModelStatus.OK,
            payload={
                "decision": "SELECT",
                "source_form": "trainers",
                "normalized_form": "trainers",
                "mapping_kind": "SYNONYM",
                "target_type": "TAXONOMY_NODE",
                "target_id": "invented-target",
                "scope": {"locale": "en-IN", "taxonomy_node_id": "footwear"},
                "direction": "QUERY_TO_CANONICAL",
                "expansion_action": "CANONICAL_SYNONYM",
                "evidence_band": "MEDIUM",
                "interpretation_label": "invented",
                "evidence_ids": ["group-trainers"],
            },
        ),
    )
    app, _, _ = workflow(model=model)

    result = app.run(request())

    assert result.status == WorkflowStatus.NO_PROPOSALS
    assert result.decisions[0].validation_codes == ["MODEL_TARGET_NOT_ALLOWED"]
    assert len(model.calls) == 1


def test_critic_cannot_add_unsupplied_evidence_ids() -> None:
    model = FakeCatalogLanguageModel()
    model.queue(
        "critique_mapping",
        QueuedModelResponse(
            status=ModelStatus.OK,
            payload={
                "decision": "ACCEPT",
                "concern_codes": [],
                "recommended_scope": None,
                "evidence_ids": ["foreign-evidence"],
                "rationale_code": "bad-evidence",
            },
        ),
    )
    app, _, _ = workflow(model=model)

    result = app.run(request())

    assert result.status == WorkflowStatus.NO_PROPOSALS
    assert result.decisions[0].validation_codes == ["CRITIC_EVIDENCE_ID_NOT_SUPPLIED"]


def test_regression_failure_blocks_review_and_activation() -> None:
    regression = PassingRegression(
        RegressionReport(
            report_id="regression-failed",
            candidate_version="candidate",
            cases_total=1,
            cases_passed=0,
            expansion_precision=0.0,
            expected_recovery_rate=0.0,
            protected_regressions=1,
            scope_leakage=0,
            invalid_targets=0,
            passed=False,
            policy_version="regression-v1",
            failure_codes=["PROTECTED_QUERY_REGRESSION"],
        )
    )
    app, _, activation = workflow(regression=regression)

    result = app.run(request())

    assert result.status == WorkflowStatus.REGRESSION_BLOCKED
    assert result.review is None
    assert activation.calls == []


def test_review_rejection_keeps_candidate_in_review_and_does_not_activate() -> None:
    review = ApprovingReview(approve=False)
    app, _, activation = workflow(review=review)

    result = app.run(request())

    assert result.status == WorkflowStatus.REVIEW_PENDING
    assert result.review is not None and result.review.approved is False
    assert activation.calls == []


def test_candidate_is_a_complete_snapshot_and_activation_keeps_inherited_mappings() -> None:
    app, _, activation = workflow(active_mappings=[existing_mapping()])

    result = app.run(request())

    assert result.status == WorkflowStatus.COMPLETED
    assert result.candidate is not None and result.activation is not None
    assert {mapping.mapping_id for mapping in result.candidate.mappings} == {
        "existing-mapping",
        *result.candidate.proposed_mapping_ids,
    }
    assert set(activation.calls[0].approved_mapping_ids) == {
        "existing-mapping",
        *result.candidate.proposed_mapping_ids,
    }
