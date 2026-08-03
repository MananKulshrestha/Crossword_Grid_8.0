"""Explicit Tier 2 evidence-driven lexicon workflow.

The workflow is deliberately ordinary typed Python.  It observes bounded
evidence, calls only tool-less proposer/critic ports, validates every target,
and stops at regression, shadow, human review, or activation boundaries.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from fkgrid.catalog_language.normalization import cluster_surface_forms, normalize_surface_form
from fkgrid.catalog_language.serialization import sha256_hex
from fkgrid.catalog_language.validation import score_mapping_evidence, validate_mapping
from fkgrid.domain.catalog_language import (
    ActivationRequest,
    CriticDraft,
    CritiqueAssessment,
    EvidenceGroup,
    EvidenceWindow,
    LexiconCandidateVersion,
    LexiconMapping,
    LexiconPreviewResult,
    LexiconWorkflowRequest,
    LexiconWorkflowResult,
    MappingDecision,
    MappingDraft,
    ModelCallRequest,
    ModelStatus,
    RegressionCase,
    ShadowCase,
    SurfaceFormCluster,
    TargetCandidate,
    ValidationReport,
    WorkflowStatus,
    WorkflowTraceEvent,
)
from fkgrid.ports.catalog_language import (
    ActivationPort,
    ActiveLexiconPort,
    CanonicalVocabularyPort,
    CatalogLanguageModelPort,
    ClockPort,
    EvidenceAggregationPort,
    IdGeneratorPort,
    RegressionPort,
    ReviewPort,
    ShadowEvaluationPort,
    TargetRetrievalPort,
    TraceSinkPort,
)

CATALOG_LANGUAGE_CAPABILITIES = frozenset(
    {
        "aggregate_query_gap_events",
        "cluster_surface_forms",
        "load_canonical_vocabulary",
        "mine_candidate_terms",
        "normalize_surface_form",
        "retrieve_candidate_targets",
        "propose_canonical_mapping",
        "critique_mapping",
        "score_mapping_evidence",
        "validate_mapping",
        "run_lexicon_regression",
        "shadow_evaluate_lexicon",
        "review_lexicon_diff",
        "activate_lexicon_version",
    }
)

CATALOG_LANGUAGE_FORBIDDEN_CAPABILITIES = frozenset(
    {
        "shopper_cart",
        "update_cart",
        "publish_catalog",
        "suppress_product",
        "contact_seller",
        "online_search",
        "browse_url",
        "write_database_directly",
    }
)


class CatalogLanguageTier2Workflow:
    """Run one bounded Tier 2 proposal/review/activation attempt."""

    def __init__(
        self,
        *,
        evidence: EvidenceAggregationPort,
        vocabulary: CanonicalVocabularyPort,
        targets: TargetRetrievalPort,
        active_lexicon: ActiveLexiconPort,
        model: CatalogLanguageModelPort,
        regression: RegressionPort,
        shadow: ShadowEvaluationPort,
        review: ReviewPort,
        activation: ActivationPort,
        clock: ClockPort,
        ids: IdGeneratorPort,
        trace_sink: TraceSinkPort | None = None,
        regression_cases: Iterable[RegressionCase] = (),
        shadow_cases: Iterable[ShadowCase] = (),
    ) -> None:
        self.evidence = evidence
        self.vocabulary = vocabulary
        self.targets = targets
        self.active_lexicon = active_lexicon
        self.model = model
        self.regression = regression
        self.shadow = shadow
        self.review = review
        self.activation = activation
        self.clock = clock
        self.ids = ids
        self.trace_sink = trace_sink
        self.regression_cases = list(regression_cases)
        self.shadow_cases = list(shadow_cases)

    def _trace(
        self,
        events: list[WorkflowTraceEvent],
        event_type: str,
        step: str,
        status: str,
        codes: list[str] | None = None,
        mapping_ids: list[str] | None = None,
    ) -> None:
        event = WorkflowTraceEvent(
            event_type=event_type,
            step=step,
            status=status,
            detail_codes=codes or [],
            mapping_ids=mapping_ids or [],
            latency_ms=0,
            at=self.clock.now(),
        )
        events.append(event)
        if self.trace_sink is not None:
            self.trace_sink.emit(event)

    @staticmethod
    def _qualifies(group: EvidenceGroup, window: EvidenceWindow) -> tuple[bool, list[str]]:
        codes: list[str] = []
        if not group.privacy_safe:
            codes.append("PRIVACY_UNSAFE")
        if group.support_count < window.min_support_count:
            codes.append("INSUFFICIENT_SUPPORT")
        if group.distinct_source_groups < window.min_distinct_source_groups:
            codes.append("INSUFFICIENT_SOURCE_DIVERSITY")
        if len(set(group.source_classes)) < window.min_source_classes:
            codes.append("INSUFFICIENT_SOURCE_CLASSES")
        if group.source_concentration > window.max_source_concentration:
            codes.append("SOURCE_CONCENTRATION_TOO_HIGH")
        if (
            group.first_observed_at < window.window_start
            or group.last_observed_at >= window.window_end
        ):
            codes.append("OUTSIDE_EVIDENCE_WINDOW")
        return not codes, codes

    @staticmethod
    def _build_clusters(groups: list[EvidenceGroup]) -> list[SurfaceFormCluster]:
        buckets: dict[tuple[str, str | None, str | None], list[EvidenceGroup]] = defaultdict(list)
        for group in groups:
            buckets[(group.locale, group.taxonomy_node_id, group.attribute_id)].append(group)

        clusters: list[SurfaceFormCluster] = []
        for (locale, taxonomy_node_id, attribute_id), scoped_groups in sorted(buckets.items()):
            form_counts: dict[str, int] = defaultdict(int)
            for group in scoped_groups:
                for form in group.observed_surface_forms:
                    form_counts[form] += group.support_count
            for index, (normalized, forms) in enumerate(
                cluster_surface_forms(list(form_counts.items()), locale), start=1
            ):
                matched = [
                    group
                    for group in scoped_groups
                    if normalize_surface_form(group.normalized_term, locale) == normalized
                    or any(
                        normalize_surface_form(form, locale) == normalized or form in forms
                        for form in group.observed_surface_forms
                    )
                ]
                if not matched:
                    continue
                cluster_normalized = min(
                    normalize_surface_form(group.normalized_term, locale) for group in matched
                )
                clusters.append(
                    SurfaceFormCluster(
                        cluster_id=(
                            f"cluster-{sha256_hex([locale, cluster_normalized, index])[:16]}"
                        ),
                        normalized_form=cluster_normalized,
                        surface_forms=list(forms),
                        support_count=sum(group.support_count for group in matched),
                        source_group_count=sum(group.distinct_source_groups for group in matched),
                        source_classes=sorted(
                            {source for group in matched for source in group.source_classes},
                            key=lambda source: source.value,
                        ),
                        locale=locale,
                        taxonomy_node_id=taxonomy_node_id,
                        attribute_id=attribute_id,
                        evidence_group_ids=sorted({group.group_id for group in matched}),
                    )
                )
        return sorted(
            clusters,
            key=lambda cluster: (cluster.locale, cluster.normalized_form, cluster.cluster_id),
        )

    @staticmethod
    def _combined_evidence(
        groups: list[EvidenceGroup], cluster: SurfaceFormCluster
    ) -> EvidenceGroup:
        matched = [group for group in groups if group.group_id in cluster.evidence_group_ids]
        first = min(group.first_observed_at for group in matched)
        last = max(group.last_observed_at for group in matched)
        return EvidenceGroup(
            group_id=cluster.cluster_id,
            normalized_term=cluster.normalized_form,
            observed_surface_forms=cluster.surface_forms,
            locale=cluster.locale,
            taxonomy_node_id=cluster.taxonomy_node_id,
            attribute_id=cluster.attribute_id,
            support_count=sum(group.support_count for group in matched),
            distinct_source_groups=sum(group.distinct_source_groups for group in matched),
            source_classes=cluster.source_classes,
            source_concentration=max(group.source_concentration for group in matched),
            recovery_success_count=sum(group.recovery_success_count for group in matched),
            contradiction_count=sum(group.contradiction_count for group in matched),
            first_observed_at=first,
            last_observed_at=last,
        )

    def _proposer_request(
        self,
        request: LexiconWorkflowRequest,
        cluster: SurfaceFormCluster,
        candidates: list[TargetCandidate],
        evidence: EvidenceGroup,
    ) -> ModelCallRequest:
        return ModelCallRequest(
            call_id=self.ids.new_id("model"),
            logical_call="propose_canonical_mapping",
            prompt_id="catalog_language_proposer_v2",
            prompt_version="2",
            input_schema_version="CatalogLanguageProposerInputV1",
            output_schema_version="MappingDraftV1",
            input_payload={
                "normalized_form": cluster.normalized_form,
                "surface_forms": cluster.surface_forms,
                "locale": cluster.locale,
                "scope": {
                    "locale": cluster.locale,
                    "taxonomy_node_id": cluster.taxonomy_node_id,
                    "attribute_id": cluster.attribute_id,
                },
                "allowed_targets": [
                    candidate.target.model_dump(mode="json") for candidate in candidates
                ],
                "evidence_ids": cluster.evidence_group_ids,
                "evidence_summary": {
                    "support_count": evidence.support_count,
                    "source_group_count": evidence.distinct_source_groups,
                    "source_classes": [source.value for source in evidence.source_classes],
                    "recovery_success_count": evidence.recovery_success_count,
                    "contradiction_count": evidence.contradiction_count,
                },
            },
            allowed_target_ids=[candidate.target.target_id for candidate in candidates],
            deadline_ms=request.proposer_deadline_ms,
            temperature=0.0,
            compatibility=request.compatibility,
        )

    def _critic_request(
        self,
        request: LexiconWorkflowRequest,
        cluster: SurfaceFormCluster,
        draft: MappingDraft,
        evidence: EvidenceGroup,
    ) -> ModelCallRequest:
        assert draft.target_id is not None
        return ModelCallRequest(
            call_id=self.ids.new_id("model"),
            logical_call="critique_mapping",
            prompt_id="catalog_language_critic_v2",
            prompt_version="2",
            input_schema_version="CatalogLanguageCriticInputV1",
            output_schema_version="CriticDraftV1",
            input_payload={
                "mapping": draft.model_dump(mode="json"),
                "normalized_form": cluster.normalized_form,
                "evidence_ids": cluster.evidence_group_ids,
                "evidence_summary": {
                    "support_count": evidence.support_count,
                    "source_group_count": evidence.distinct_source_groups,
                    "source_concentration": evidence.source_concentration,
                    "contradiction_count": evidence.contradiction_count,
                },
            },
            allowed_target_ids=[draft.target_id],
            deadline_ms=request.critic_deadline_ms,
            temperature=0.0,
            compatibility=request.compatibility,
        )

    @staticmethod
    def _critique(response_payload: CriticDraft) -> CritiqueAssessment:
        return CritiqueAssessment(
            decision=response_payload.decision,
            concern_codes=response_payload.concern_codes,
            recommended_scope=response_payload.recommended_scope,
            evidence_ids=response_payload.evidence_ids,
            rationale_code=response_payload.rationale_code,
        )

    @staticmethod
    def _validate_proposer(
        response_payload: MappingDraft,
        cluster: SurfaceFormCluster,
        candidates: list[TargetCandidate],
    ) -> list[str]:
        if response_payload.decision == "ABSTAIN":
            return ["MODEL_ABSTAINED"]
        codes: list[str] = []
        allowed = {
            (candidate.target.target_type, candidate.target.target_id): candidate
            for candidate in candidates
        }
        key = (response_payload.target_type, response_payload.target_id)
        if key not in allowed:
            codes.append("MODEL_TARGET_NOT_ALLOWED")
        if response_payload.source_form is None or response_payload.normalized_form is None:
            codes.append("MODEL_FORM_MISSING")
        elif (
            normalize_surface_form(response_payload.source_form, cluster.locale)
            != response_payload.normalized_form
        ):
            codes.append("MODEL_NORMALIZED_FORM_MISMATCH")
        if response_payload.source_form not in cluster.surface_forms:
            codes.append("MODEL_SOURCE_FORM_NOT_SUPPLIED")
        if response_payload.scope is None or response_payload.scope.locale != cluster.locale:
            codes.append("MODEL_SCOPE_NOT_ALLOWED")
        if any(
            evidence_id not in cluster.evidence_group_ids
            for evidence_id in response_payload.evidence_ids
        ):
            codes.append("MODEL_EVIDENCE_ID_NOT_SUPPLIED")
        return sorted(set(codes))

    def preview(self, request: LexiconWorkflowRequest) -> LexiconPreviewResult:
        """Run one proposer-only preview without critic or publication side effects.

        This is deliberately separate from ``run``. It retains evidence, vocabulary,
        target, and mapping validation, but never calls the critic, regression, shadow,
        review, or activation ports. The result is therefore a preview, never an
        approved or active lexicon change.
        """

        events: list[WorkflowTraceEvent] = []
        self._trace(events, "PREVIEW_STARTED", "RECEIVED", "STARTED")
        groups = self.evidence.aggregate_query_gap_events(
            request.evidence_window, request.compatibility
        )
        self._trace(events, "TOOL_COMPLETED", "aggregate_query_gap_events", "OK")
        qualified: list[EvidenceGroup] = []
        rejected_evidence_codes: set[str] = set()
        for group in groups:
            valid, codes = self._qualifies(group, request.evidence_window)
            if valid:
                qualified.append(group)
            else:
                rejected_evidence_codes.update(codes)
        if not qualified:
            self._trace(
                events,
                "GATE_DECISION",
                "threshold_gate",
                "BLOCKED",
                sorted(rejected_evidence_codes) or ["NO_EVIDENCE"],
            )
            return LexiconPreviewResult(
                run_id=request.run_id,
                status="INSUFFICIENT_EVIDENCE",
                trace=events,
                warnings=sorted(rejected_evidence_codes) or ["NO_EVIDENCE"],
            )
        self._trace(events, "GATE_DECISION", "threshold_gate", "QUALIFIED")
        vocabulary = self.vocabulary.load_canonical_vocabulary(
            request.compatibility.catalog_version,
            request.compatibility.taxonomy_version,
            request.compatibility.category_schema_version,
        )
        self._trace(events, "TOOL_COMPLETED", "load_canonical_vocabulary", "OK")
        if (
            vocabulary.catalog_version != request.compatibility.catalog_version
            or vocabulary.taxonomy_version != request.compatibility.taxonomy_version
            or vocabulary.category_schema_version != request.compatibility.category_schema_version
            or vocabulary.normalizer_version != request.compatibility.normalizer_version
        ):
            self._trace(
                events,
                "INTEGRITY_FAILURE",
                "load_canonical_vocabulary",
                "BLOCKED",
                ["VERSION_MISMATCH"],
            )
            return LexiconPreviewResult(
                run_id=request.run_id,
                status="FAILED_SAFE",
                trace=events,
                warnings=["VOCABULARY_VERSION_MISMATCH"],
            )

        active_mappings = self.active_lexicon.load_active_mappings(
            request.active_lexicon_version, request.compatibility
        )
        clusters = self._build_clusters(qualified)
        self._trace(events, "TOOL_COMPLETED", "cluster_surface_forms", "OK")
        if not clusters:
            self._trace(events, "PREVIEW_TERMINAL", "proposal_generation", "NO_PROPOSALS")
            return LexiconPreviewResult(
                run_id=request.run_id,
                status="NO_PROPOSALS",
                trace=events,
                warnings=["NO_SURFACE_FORM_CLUSTERS"],
            )

        cluster = clusters[0]
        evidence = self._combined_evidence(qualified, cluster)
        candidates = self.targets.retrieve_candidate_targets(cluster, vocabulary, limit=10)
        self._trace(
            events,
            "TOOL_COMPLETED",
            "retrieve_candidate_targets",
            "OK" if candidates else "NO_TARGETS",
        )
        if not candidates:
            self._trace(events, "PREVIEW_TERMINAL", "proposal_generation", "NO_PROPOSALS")
            return LexiconPreviewResult(
                run_id=request.run_id,
                status="NO_PROPOSALS",
                trace=events,
                warnings=["NO_ALLOWED_TARGETS"],
            )

        proposer_response = self.model.propose_canonical_mapping(
            self._proposer_request(request, cluster, candidates, evidence)
        )
        self._trace(
            events,
            "MODEL_COMPLETED",
            "propose_canonical_mapping",
            proposer_response.status.value,
        )
        if proposer_response.status != ModelStatus.OK or not isinstance(
            proposer_response.payload, MappingDraft
        ):
            self._trace(events, "PREVIEW_TERMINAL", "propose_canonical_mapping", "SAFE_STOP")
            return LexiconPreviewResult(
                run_id=request.run_id,
                status="NO_PROPOSALS",
                proposer=proposer_response,
                trace=events,
                warnings=[f"PROPOSER_{proposer_response.status.value}"],
            )

        draft = proposer_response.payload
        proposer_codes = self._validate_proposer(draft, cluster, candidates)
        if proposer_codes:
            self._trace(
                events,
                "TOOL_COMPLETED",
                "validate_proposer",
                "REJECTED",
                proposer_codes,
            )
            return LexiconPreviewResult(
                run_id=request.run_id,
                status="FAILED_SAFE",
                proposer=proposer_response,
                trace=events,
                warnings=proposer_codes,
            )

        assert draft.evidence_band is not None
        validation = validate_mapping(
            draft,
            vocabulary,
            request.compatibility,
            active_mappings,
            self.ids.new_id("preview-mapping"),
            cluster.evidence_group_ids,
            draft.evidence_band,
            self.clock.now(),
        )
        self._trace(
            events,
            "TOOL_COMPLETED",
            "validate_mapping",
            "VALID" if validation.valid else "REJECTED",
            validation.codes,
        )
        if not validation.valid or validation.mapping is None:
            return LexiconPreviewResult(
                run_id=request.run_id,
                status="FAILED_SAFE",
                proposer=proposer_response,
                trace=events,
                warnings=validation.codes or ["PREVIEW_MAPPING_INVALID"],
            )
        self._trace(events, "PREVIEW_TERMINAL", "preview_proposal", "PREVIEW_ONLY")
        return LexiconPreviewResult(
            run_id=request.run_id,
            status="PREVIEW_ONLY",
            mapping=validation.mapping,
            proposer=proposer_response,
            trace=events,
            warnings=["PREVIEW_ONLY_NO_ACTIVATION"],
        )

    def run(self, request: LexiconWorkflowRequest) -> LexiconWorkflowResult:
        events: list[WorkflowTraceEvent] = []
        self._trace(events, "WORKFLOW_STARTED", "RECEIVED", "STARTED")
        groups = self.evidence.aggregate_query_gap_events(
            request.evidence_window, request.compatibility
        )
        self._trace(events, "TOOL_COMPLETED", "aggregate_query_gap_events", "OK")
        qualified: list[EvidenceGroup] = []
        rejected_evidence_codes: set[str] = set()
        for group in groups:
            valid, codes = self._qualifies(group, request.evidence_window)
            if valid:
                qualified.append(group)
            else:
                rejected_evidence_codes.update(codes)
        if not qualified:
            self._trace(
                events,
                "GATE_DECISION",
                "threshold_gate",
                "BLOCKED",
                sorted(rejected_evidence_codes) or ["NO_EVIDENCE"],
            )
            return LexiconWorkflowResult(
                run_id=request.run_id,
                status=WorkflowStatus.INSUFFICIENT_EVIDENCE,
                trace=events,
                warnings=sorted(rejected_evidence_codes) or ["NO_EVIDENCE"],
            )
        self._trace(events, "GATE_DECISION", "threshold_gate", "QUALIFIED")
        vocabulary = self.vocabulary.load_canonical_vocabulary(
            request.compatibility.catalog_version,
            request.compatibility.taxonomy_version,
            request.compatibility.category_schema_version,
        )
        self._trace(events, "TOOL_COMPLETED", "load_canonical_vocabulary", "OK")
        if (
            vocabulary.catalog_version != request.compatibility.catalog_version
            or vocabulary.taxonomy_version != request.compatibility.taxonomy_version
            or vocabulary.category_schema_version != request.compatibility.category_schema_version
            or vocabulary.normalizer_version != request.compatibility.normalizer_version
        ):
            self._trace(
                events,
                "INTEGRITY_FAILURE",
                "load_canonical_vocabulary",
                "BLOCKED",
                ["VERSION_MISMATCH"],
            )
            return LexiconWorkflowResult(
                run_id=request.run_id,
                status=WorkflowStatus.FAILED_SAFE,
                trace=events,
                warnings=["VOCABULARY_VERSION_MISMATCH"],
            )
        active_mappings = self.active_lexicon.load_active_mappings(
            request.active_lexicon_version, request.compatibility
        )
        clusters = self._build_clusters(qualified)
        self._trace(events, "TOOL_COMPLETED", "cluster_surface_forms", "OK")
        decisions: list[MappingDecision] = []
        mappings: list[LexiconMapping] = []
        proposal_ids: list[str] = []
        for cluster in clusters[: request.max_proposals]:
            proposal_id = self.ids.new_id("proposal")
            proposal_ids.append(proposal_id)
            evidence = self._combined_evidence(qualified, cluster)
            candidates = self.targets.retrieve_candidate_targets(cluster, vocabulary, limit=10)
            self._trace(
                events,
                "TOOL_COMPLETED",
                "retrieve_candidate_targets",
                "OK" if candidates else "NO_TARGETS",
            )
            if not candidates:
                decisions.append(
                    MappingDecision(
                        proposal_id=proposal_id,
                        source_form=cluster.surface_forms[0],
                        status="REJECTED",
                        validation_codes=["NO_ALLOWED_TARGETS"],
                    )
                )
                continue
            proposer_request = self._proposer_request(request, cluster, candidates, evidence)
            proposer_response = self.model.propose_canonical_mapping(proposer_request)
            self._trace(
                events,
                "MODEL_COMPLETED",
                "propose_canonical_mapping",
                proposer_response.status.value,
            )
            if proposer_response.status != ModelStatus.OK or not isinstance(
                proposer_response.payload, MappingDraft
            ):
                proposer_codes = [f"PROPOSER_{proposer_response.status.value}"]
                proposer_codes.extend(proposer_response.validation_codes)
                decisions.append(
                    MappingDecision(
                        proposal_id=proposal_id,
                        source_form=cluster.surface_forms[0],
                        status="ABSTAINED",
                        validation_codes=proposer_codes[:32],
                    )
                )
                continue
            proposer_codes = self._validate_proposer(proposer_response.payload, cluster, candidates)
            if proposer_codes:
                decisions.append(
                    MappingDecision(
                        proposal_id=proposal_id,
                        source_form=cluster.surface_forms[0],
                        status="REJECTED",
                        validation_codes=proposer_codes,
                    )
                )
                continue
            critic_response = self.model.critique_mapping(
                self._critic_request(request, cluster, proposer_response.payload, evidence)
            )
            self._trace(events, "MODEL_COMPLETED", "critique_mapping", critic_response.status.value)
            if critic_response.status != ModelStatus.OK or not isinstance(
                critic_response.payload, CriticDraft
            ):
                critic_codes = [f"CRITIC_{critic_response.status.value}"]
                critic_codes.extend(critic_response.validation_codes)
                decisions.append(
                    MappingDecision(
                        proposal_id=proposal_id,
                        source_form=cluster.surface_forms[0],
                        status="REJECTED",
                        validation_codes=critic_codes[:32],
                    )
                )
                continue
            if any(
                evidence_id not in cluster.evidence_group_ids
                for evidence_id in critic_response.payload.evidence_ids
            ):
                decisions.append(
                    MappingDecision(
                        proposal_id=proposal_id,
                        source_form=cluster.surface_forms[0],
                        status="REJECTED",
                        validation_codes=["CRITIC_EVIDENCE_ID_NOT_SUPPLIED"],
                    )
                )
                continue
            critic = self._critique(critic_response.payload)
            evidence_score = score_mapping_evidence(
                support_count=evidence.support_count,
                distinct_source_groups=evidence.distinct_source_groups,
                source_class_count=len(evidence.source_classes),
                recovery_success_count=evidence.recovery_success_count,
                source_concentration=evidence.source_concentration,
                contradiction_count=evidence.contradiction_count,
                critic=critic,
            )
            if critic.decision == "ABSTAIN":
                decisions.append(
                    MappingDecision(
                        proposal_id=proposal_id,
                        source_form=cluster.surface_forms[0],
                        status="REJECTED",
                        validation_codes=["CRITIC_ABSTAINED"],
                        evidence_score=evidence_score,
                        critic=critic,
                    )
                )
                continue
            evidence_ids = sorted(set(cluster.evidence_group_ids).union(critic.evidence_ids))
            validation: ValidationReport = validate_mapping(
                proposer_response.payload,
                vocabulary,
                request.compatibility,
                [*active_mappings, *mappings],
                self.ids.new_id("mapping"),
                evidence_ids,
                evidence_score.evidence_band,
                self.clock.now(),
            )
            self._trace(
                events,
                "TOOL_COMPLETED",
                "validate_mapping",
                "VALID" if validation.valid else "REJECTED",
                validation.codes,
            )
            if not validation.valid or validation.mapping is None:
                decisions.append(
                    MappingDecision(
                        proposal_id=proposal_id,
                        source_form=cluster.surface_forms[0],
                        status="REJECTED",
                        validation_codes=validation.codes,
                        evidence_score=evidence_score,
                        critic=critic,
                    )
                )
                continue
            mappings.append(validation.mapping)
            decisions.append(
                MappingDecision(
                    proposal_id=proposal_id,
                    source_form=cluster.surface_forms[0],
                    status="REVIEW_PENDING",
                    mapping_id=validation.mapping.mapping_id,
                    validation_codes=[],
                    evidence_score=evidence_score,
                    critic=critic,
                )
            )
        if not mappings:
            self._trace(events, "WORKFLOW_TERMINAL", "proposal_generation", "NO_PROPOSALS")
            return LexiconWorkflowResult(
                run_id=request.run_id,
                status=WorkflowStatus.NO_PROPOSALS,
                decisions=decisions,
                trace=events,
                warnings=["NO_VALID_MAPPINGS"],
            )
        candidate_version = self.ids.new_id("lexicon-candidate")
        candidate_compatibility = request.compatibility.model_copy(
            update={"lexicon_version": candidate_version}
        )
        inherited_mappings = [
            mapping.model_copy(update={"compatibility": candidate_compatibility})
            for mapping in active_mappings
        ]
        candidate_mappings = [
            mapping.model_copy(update={"compatibility": candidate_compatibility})
            for mapping in [*inherited_mappings, *mappings]
        ]
        candidate_checksum = sha256_hex(
            {
                "compatibility": candidate_compatibility,
                "mappings": candidate_mappings,
                "proposal_ids": proposal_ids,
                "proposed_mapping_ids": [mapping.mapping_id for mapping in mappings],
            }
        )
        candidate = LexiconCandidateVersion(
            candidate_version=candidate_version,
            parent_lexicon_version=request.active_lexicon_version,
            compatibility=candidate_compatibility,
            mappings=candidate_mappings,
            proposal_ids=proposal_ids,
            proposed_mapping_ids=[mapping.mapping_id for mapping in mappings],
            candidate_checksum=candidate_checksum,
            created_at=self.clock.now(),
        )
        regression_report = self.regression.run_lexicon_regression(
            candidate, active_mappings, self.regression_cases, request.regression_policy_version
        )
        self._trace(
            events,
            "TOOL_COMPLETED",
            "run_lexicon_regression",
            "PASSED" if regression_report.passed else "BLOCKED",
            regression_report.failure_codes,
            [mapping.mapping_id for mapping in mappings],
        )
        if not regression_report.passed:
            return LexiconWorkflowResult(
                run_id=request.run_id,
                status=WorkflowStatus.REGRESSION_BLOCKED,
                candidate=candidate,
                decisions=decisions,
                regression=regression_report,
                trace=events,
                warnings=["REGRESSION_POLICY_BLOCKED_ACTIVATION"],
            )
        shadow_report = self.shadow.shadow_evaluate_lexicon(
            candidate, active_mappings, self.shadow_cases, request.shadow_policy_version
        )
        self._trace(
            events,
            "TOOL_COMPLETED",
            "shadow_evaluate_lexicon",
            "PASSED" if shadow_report.passed else "BLOCKED",
            shadow_report.failure_codes,
            [mapping.mapping_id for mapping in mappings],
        )
        if not shadow_report.passed:
            return LexiconWorkflowResult(
                run_id=request.run_id,
                status=WorkflowStatus.SHADOW_BLOCKED,
                candidate=candidate,
                decisions=decisions,
                regression=regression_report,
                shadow=shadow_report,
                trace=events,
                warnings=["SHADOW_POLICY_BLOCKED_ACTIVATION"],
            )
        review = self.review.review_lexicon_diff(candidate)
        self._trace(
            events,
            "HUMAN_DECISION",
            "review_lexicon_diff",
            "APPROVED" if review.approved else "REJECTED",
        )
        all_mapping_ids = set(candidate.proposed_mapping_ids)
        approved_ids = set(review.approved_mapping_ids)
        if (
            not approved_ids.issubset(all_mapping_ids)
            or set(review.rejected_mapping_ids) - all_mapping_ids
        ):
            self._trace(
                events,
                "INTEGRITY_FAILURE",
                "review_lexicon_diff",
                "BLOCKED",
                ["REVIEW_ID_NOT_IN_CANDIDATE"],
            )
            return LexiconWorkflowResult(
                run_id=request.run_id,
                status=WorkflowStatus.FAILED_SAFE,
                candidate=candidate,
                decisions=decisions,
                regression=regression_report,
                shadow=shadow_report,
                review=review,
                trace=events,
                warnings=["REVIEW_REFERENCED_UNKNOWN_MAPPING"],
            )
        if not review.approved or not approved_ids:
            return LexiconWorkflowResult(
                run_id=request.run_id,
                status=WorkflowStatus.REVIEW_PENDING,
                candidate=candidate,
                decisions=decisions,
                regression=regression_report,
                shadow=shadow_report,
                review=review,
                trace=events,
                warnings=["HUMAN_REVIEW_DID_NOT_APPROVE"],
            )
        inherited_ids = {mapping.mapping_id for mapping in candidate.mappings} - all_mapping_ids
        activated_ids = sorted(inherited_ids | approved_ids)
        try:
            activation_receipt = self.activation.activate_lexicon_version(
                request=ActivationRequest(
                    candidate_version=candidate.candidate_version,
                    expected_active_version=request.active_lexicon_version,
                    approved_mapping_ids=activated_ids,
                    actor_id=review.reviewer_id,
                )
            )
        except (RuntimeError, ValueError):
            self._trace(
                events,
                "ACTIVATION_CONFLICT",
                "activate_lexicon_version",
                "BLOCKED",
                ["STALE_ACTIVE_VERSION"],
            )
            return LexiconWorkflowResult(
                run_id=request.run_id,
                status=WorkflowStatus.ACTIVATION_CONFLICT,
                candidate=candidate,
                decisions=decisions,
                regression=regression_report,
                shadow=shadow_report,
                review=review,
                trace=events,
                warnings=["ACTIVE_LEXICON_CHANGED_BEFORE_CAS"],
            )
        self._trace(
            events,
            "WORKFLOW_TERMINAL",
            "activate_lexicon_version",
            "ACTIVATED",
            mapping_ids=activation_receipt.activated_mapping_ids,
        )
        return LexiconWorkflowResult(
            run_id=request.run_id,
            status=WorkflowStatus.COMPLETED,
            candidate=candidate,
            decisions=decisions,
            regression=regression_report,
            shadow=shadow_report,
            review=review,
            activation=activation_receipt,
            trace=events,
        )
