"""Deterministic validation and evidence scoring for lexicon proposals."""

from __future__ import annotations

from datetime import UTC, datetime

from fkgrid.catalog_language.normalization import normalize_surface_form
from fkgrid.domain.catalog_language import (
    CanonicalVocabularySnapshot,
    CritiqueAssessment,
    EvidenceBand,
    EvidenceScore,
    ExpansionAction,
    LexiconCompatibility,
    LexiconMapping,
    MappingDraft,
    MappingKind,
    MappingStatus,
    ReviewRoute,
    ValidationReport,
)

_SAFE_ACTIONS = {
    MappingKind.MISSPELLING: {
        ExpansionAction.SPELLING_NORMALIZATION,
        ExpansionAction.CANONICAL_SYNONYM,
    },
    MappingKind.ABBREVIATION: {
        ExpansionAction.CANONICAL_SYNONYM,
        ExpansionAction.CLARIFICATION_CANDIDATE,
    },
    MappingKind.SYNONYM: {ExpansionAction.CANONICAL_SYNONYM, ExpansionAction.SOFT_RANK_BOOST},
    MappingKind.COLLOQUIAL: {
        ExpansionAction.CANONICAL_SYNONYM,
        ExpansionAction.CLARIFICATION_CANDIDATE,
    },
    MappingKind.UNIT_ALIAS: {
        ExpansionAction.SPELLING_NORMALIZATION,
        ExpansionAction.CLARIFICATION_CANDIDATE,
    },
    MappingKind.ATTRIBUTE_PARAPHRASE: {
        ExpansionAction.SOFT_RANK_BOOST,
        ExpansionAction.EXPLICIT_FILTER_AFTER_CONFIRMATION,
        ExpansionAction.CLARIFICATION_CANDIDATE,
    },
    MappingKind.COMPOUND: {
        ExpansionAction.CLARIFICATION_CANDIDATE,
        ExpansionAction.SOFT_RANK_BOOST,
    },
}


def score_mapping_evidence(
    support_count: int,
    distinct_source_groups: int,
    source_class_count: int,
    recovery_success_count: int,
    source_concentration: float,
    contradiction_count: int,
    critic: CritiqueAssessment,
    rule_version: str = "evidence-score-v1",
) -> EvidenceScore:
    """Score review priority, never activation authority.

    The score is deliberately explainable and bounded.  It uses evidence only
    to route a proposal; a reviewer and regression/shadow gates remain required.
    """

    recovery_rate = recovery_success_count / support_count if support_count else 0.0
    critic_penalty = min(1.0, 0.25 * len(critic.concern_codes))
    support_signal = min(1.0, support_count / 25.0)
    diversity_signal = min(1.0, distinct_source_groups / 10.0)
    source_signal = min(1.0, source_class_count / 3.0)
    concentration_signal = max(0.0, 1.0 - source_concentration)
    contradiction_penalty = min(1.0, contradiction_count / 5.0)
    score = (
        0.30 * support_signal
        + 0.25 * diversity_signal
        + 0.15 * source_signal
        + 0.15 * recovery_rate
        + 0.15 * concentration_signal
        - 0.20 * contradiction_penalty
        - 0.25 * critic_penalty
    )
    score = max(0.0, min(1.0, score))
    if critic.decision != "ACCEPT" or contradiction_count > 0 or source_concentration > 0.4:
        band = EvidenceBand.LOW if score < 0.55 else EvidenceBand.MEDIUM
        route = ReviewRoute.INDIVIDUAL
    elif score >= 0.72 and source_class_count >= 2:
        band = EvidenceBand.APPROVED_HIGH
        route = ReviewRoute.BATCH
    else:
        band = EvidenceBand.MEDIUM
        route = ReviewRoute.INDIVIDUAL
    return EvidenceScore(
        support_count=support_count,
        distinct_source_groups=distinct_source_groups,
        source_class_count=source_class_count,
        recovery_success_rate=recovery_rate,
        source_concentration=source_concentration,
        contradiction_count=contradiction_count,
        critic_penalty=critic_penalty,
        deterministic_score=score,
        evidence_band=band,
        review_route=route,
        rule_version=rule_version,
    )


def validate_mapping(
    draft: MappingDraft,
    vocabulary: CanonicalVocabularySnapshot,
    compatibility: LexiconCompatibility,
    existing_mappings: list[LexiconMapping],
    mapping_id: str,
    evidence_ids: list[str],
    evidence_band: EvidenceBand,
    now: datetime | None = None,
) -> ValidationReport:
    """Validate a selected draft against the pinned canonical vocabulary."""

    if draft.decision != "SELECT":
        return ValidationReport(valid=False, codes=["MODEL_ABSTAINED"])
    assert draft.source_form is not None
    assert draft.normalized_form is not None
    assert draft.mapping_kind is not None
    assert draft.target_type is not None
    assert draft.target_id is not None
    assert draft.scope is not None
    assert draft.direction is not None
    assert draft.expansion_action is not None

    codes: list[str] = []
    target = next(
        (
            item
            for item in vocabulary.items
            if item.target_type == draft.target_type
            and item.target_id == draft.target_id
            and item.active
        ),
        None,
    )
    if target is None:
        codes.append("TARGET_NOT_IN_VOCABULARY")
    if normalize_surface_form(draft.source_form, draft.scope.locale) != draft.normalized_form:
        codes.append("NORMALIZED_FORM_MISMATCH")
    if draft.normalized_form == target.normalized_name if target else False:
        codes.append("SOURCE_EQUALS_TARGET")
    if draft.expansion_action not in _SAFE_ACTIONS.get(draft.mapping_kind, set()):
        codes.append("ACTION_INCOMPATIBLE_WITH_MAPPING_KIND")
    if draft.mapping_kind == MappingKind.COMPOUND and draft.compound_semantics == "NONE":
        codes.append("COMPOUND_SEMANTICS_REQUIRED")
    if draft.scope.locale != target.scope.locale if target else False:
        codes.append("LOCALE_SCOPE_MISMATCH")
    if (
        target
        and draft.scope.attribute_id
        and target.scope.attribute_id
        not in {
            None,
            draft.scope.attribute_id,
        }
    ):
        codes.append("ATTRIBUTE_SCOPE_MISMATCH")
    for mapping in existing_mappings:
        if (
            mapping.status in {MappingStatus.APPROVED, MappingStatus.IN_REVIEW}
            and mapping.normalized_form == draft.normalized_form
            and mapping.locale == draft.scope.locale
            and mapping.scope == draft.scope
            and (mapping.target_id != draft.target_id or mapping.target_type != draft.target_type)
        ):
            codes.append("INCOMPATIBLE_SCOPE_COLLISION")
    if not evidence_ids:
        codes.append("EVIDENCE_REQUIRED")
    if len(evidence_ids) > 32:
        codes.append("EVIDENCE_LIMIT_EXCEEDED")

    if codes:
        return ValidationReport(valid=False, codes=sorted(set(codes)))
    timestamp = now or datetime.now(UTC)
    mapping = LexiconMapping(
        mapping_id=mapping_id,
        surface_form=draft.source_form,
        normalized_form=draft.normalized_form,
        locale=draft.scope.locale,
        mapping_kind=draft.mapping_kind,
        target_type=draft.target_type,
        target_id=draft.target_id,
        scope=draft.scope,
        direction=draft.direction,
        expansion_action=draft.expansion_action,
        compound_semantics=draft.compound_semantics,
        evidence_band=evidence_band,
        origin="TIER_2_EVIDENCE",
        evidence_ids=sorted(set(evidence_ids)),
        status=MappingStatus.IN_REVIEW,
        compatibility=compatibility,
        created_at=timestamp,
    )
    return ValidationReport(valid=True, codes=[], mapping=mapping)
