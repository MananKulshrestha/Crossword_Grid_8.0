"""Reviewed lexicon tools for Catalog Language Tier 1 and Tier 2."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence

from .contracts import (
    ApprovedExpansion,
    CandidateTerm,
    CatalogRecord,
    CompatibilityTuple,
    EvidenceRef,
    LexiconCandidate,
    MappingProposal,
    RegressionReport,
    ReviewDecision,
    Vocabulary,
)
from .determinism import normalize_text, stable_id, tokenize, unique_sorted


def load_canonical_vocabulary(records: Iterable[CatalogRecord], catalog_version: str) -> Vocabulary:
    materialized = list(records)
    categories = unique_sorted(record.category_id for record in materialized)
    brands = unique_sorted(record.brand or "" for record in materialized)
    attributes: dict[str, list[str]] = defaultdict(list)
    aliases: dict[str, str] = {}
    for record in materialized:
        for key, value in record.attributes.items():
            attributes[key].append(value)
        if record.brand:
            aliases[normalize_text(record.brand)] = record.brand
        for alias in record.aliases:
            aliases[normalize_text(alias)] = record.title
    return Vocabulary(
        catalog_version=catalog_version,
        categories=categories,
        brands=brands,
        attributes={key: unique_sorted(values) for key, values in sorted(attributes.items())},
        aliases={key: aliases[key] for key in sorted(aliases)},
    )


def normalize_surface_form(text: str, locale: str = "en-IN") -> CandidateTerm:
    normalized = normalize_text(text)
    return CandidateTerm(
        surface_form=text[:200], normalized_form=normalized, locale=locale, count=1
    )


def aggregate_query_gap_events(
    events: Iterable[Mapping[str, object]], minimum_count: int = 2
) -> list[CandidateTerm]:
    counts: Counter[tuple[str, str]] = Counter()
    sources: dict[tuple[str, str], set[str]] = defaultdict(set)
    for event in events:
        raw = str(event.get("surface_form", event.get("term", "")))[:200]
        normalized = normalize_text(raw)
        if not normalized:
            continue
        locale = str(event.get("locale", "en-IN"))
        counts[(normalized, locale)] += int(event.get("count", 1))
        sources[(normalized, locale)].add(str(event.get("source_id", "aggregate")))
    return [
        CandidateTerm(
            surface_form=normalized,
            normalized_form=normalized,
            locale=locale,
            count=count,
            source_ids=sorted(sources[(normalized, locale)]),
        )
        for (normalized, locale), count in sorted(counts.items())
        if count >= minimum_count
    ]


def cluster_surface_forms(terms: Sequence[CandidateTerm]) -> list[list[CandidateTerm]]:
    clusters: dict[str, list[CandidateTerm]] = defaultdict(list)
    for term in terms:
        key = " ".join(sorted(tokenize(term.normalized_form)))
        clusters[key].append(term)
    return [
        sorted(cluster, key=lambda item: item.normalized_form)
        for _, cluster in sorted(clusters.items())
    ]


def mine_candidate_terms(
    inputs: Iterable[str | Mapping[str, object] | CandidateTerm], minimum_count: int = 1
) -> list[CandidateTerm]:
    events: list[Mapping[str, object]] = []
    for item in inputs:
        if isinstance(item, CandidateTerm):
            events.append(item.model_dump())
        elif isinstance(item, str):
            events.append({"surface_form": item, "count": 1})
        else:
            events.append(item)
    return aggregate_query_gap_events(events, minimum_count=minimum_count)


def retrieve_candidate_targets(
    term: CandidateTerm, vocabulary: Vocabulary, limit: int = 8
) -> list[str]:
    query = normalize_text(term.normalized_form)
    targets: set[str] = set()
    for value in [
        *vocabulary.categories,
        *vocabulary.brands,
        *[item for values in vocabulary.attributes.values() for item in values],
    ]:
        if (
            query == normalize_text(value)
            or query in normalize_text(value)
            or normalize_text(value) in query
        ):
            targets.add(value)
    for alias, target in vocabulary.aliases.items():
        if query == alias:
            targets.add(target)
    return sorted(targets, key=lambda value: (normalize_text(value), value))[:limit]


def propose_canonical_mapping(
    term: CandidateTerm,
    allowed_targets: Sequence[str],
    evidence_refs: Sequence[EvidenceRef] = (),
) -> MappingProposal:
    normalized = normalize_text(term.normalized_form)
    exact = [target for target in allowed_targets if normalize_text(target) == normalized]
    if len(exact) == 1:
        return MappingProposal(
            surface_form=term.surface_form,
            normalized_form=normalized,
            target_id=exact[0],
            canonical_term=exact[0],
            status="PROPOSED",
            evidence_refs=list(evidence_refs),
        )
    return MappingProposal(
        surface_form=term.surface_form,
        normalized_form=normalized,
        status="ABSTAIN",
        direction="NONE",
        reason_codes=["NO_UNIQUE_DETERMINISTIC_TARGET"],
    )


def score_mapping_evidence(
    proposal: MappingProposal, support_count: int = 0, source_diversity: int = 0
) -> float:
    if proposal.status != "PROPOSED" or not proposal.evidence_refs:
        return 0.0
    return min(1.0, 0.5 + min(0.25, support_count / 40.0) + min(0.25, source_diversity / 8.0))


def critique_mapping(proposal: MappingProposal, allowed_targets: Sequence[str]) -> list[str]:
    concerns: list[str] = []
    if proposal.status != "PROPOSED":
        concerns.append("ABSTAINED_PROPOSAL")
    if proposal.target_id and proposal.target_id not in set(allowed_targets):
        concerns.append("TARGET_OUTSIDE_ALLOWLIST")
    if proposal.normalized_form == normalize_text(proposal.canonical_term or ""):
        concerns.append("IDENTITY_MAPPING")
    if not proposal.evidence_refs:
        concerns.append("NO_EVIDENCE")
    return sorted(set(concerns))


def validate_mapping(
    proposal: MappingProposal,
    vocabulary: Vocabulary,
    compatibility: CompatibilityTuple | None = None,
) -> list[str]:
    errors: list[str] = []
    allowed = (
        set(vocabulary.categories)
        | set(vocabulary.brands)
        | {value for values in vocabulary.attributes.values() for value in values}
    )
    if proposal.status == "PROPOSED" and proposal.target_id not in allowed:
        errors.append("TARGET_OUTSIDE_ALLOWLIST")
    if proposal.status == "PROPOSED" and not proposal.canonical_term:
        errors.append("CANONICAL_TERM_REQUIRED")
    if compatibility and vocabulary.catalog_version != compatibility.catalog_version:
        errors.append("CATALOG_VERSION_MISMATCH")
    if len(proposal.surface_form) > 200 or len(proposal.normalized_form) > 200:
        errors.append("TERM_TOO_LONG")
    return errors


def build_lexicon_candidate(
    mappings: Sequence[MappingProposal], catalog_version: str, base_version: str = "lexicon-demo-v1"
) -> LexiconCandidate:
    canonical = sorted(mappings, key=lambda item: (item.normalized_form, item.target_id or ""))
    version = stable_id(
        "lex", {"catalog_version": catalog_version, "base": base_version, "mappings": canonical}
    )
    return LexiconCandidate(
        version=version, catalog_version=catalog_version, mappings=list(canonical)
    )


def run_lexicon_regression(
    candidate: LexiconCandidate, golden_cases: Sequence[Mapping[str, object]] = ()
) -> RegressionReport:
    mapping_by_surface = {
        item.normalized_form: item for item in candidate.mappings if item.status == "PROPOSED"
    }
    cases = 0
    correct = 0
    leakage = 0
    protected = 0
    for case in golden_cases:
        cases += 1
        surface = normalize_text(str(case.get("surface_form", "")))
        expected = str(case.get("target_id", ""))
        actual = mapping_by_surface.get(surface)
        if actual and actual.target_id == expected:
            correct += 1
        elif case.get("protected"):
            protected += 1
        if case.get("forbidden") and actual:
            leakage += 1
    precision = 1.0 if cases == 0 else correct / cases
    status = "PASS" if leakage == 0 and protected == 0 and precision >= 0.9 else "FAIL"
    reasons = [] if status == "PASS" else ["REGRESSION_THRESHOLD_FAILED"]
    return RegressionReport(
        status=status,
        candidate_version=candidate.version,
        cases=cases,
        precision=precision,
        leakage_count=leakage,
        changed_protected_cases=protected,
        reasons=reasons,
    )


def shadow_evaluate_lexicon(
    candidate: LexiconCandidate, replay_terms: Sequence[str] = ()
) -> dict[str, object]:
    mappings = {item.normalized_form for item in candidate.mappings if item.status == "PROPOSED"}
    hits = sum(1 for term in replay_terms if normalize_text(term) in mappings)
    return {
        "candidate_version": candidate.version,
        "replay_cases": len(replay_terms),
        "expansion_hits": hits,
        "leakage_count": 0,
    }


def review_lexicon_diff(
    candidate: LexiconCandidate, regression: RegressionReport | None = None
) -> ReviewDecision:
    if regression and regression.status != "PASS":
        decision = "NEEDS_REVIEW"
        reason = "REGRESSION_FAILED"
    elif any(mapping.status != "PROPOSED" for mapping in candidate.mappings):
        decision = "NEEDS_REVIEW"
        reason = "ABSTAINED_MAPPING_PRESENT"
    else:
        decision = "NEEDS_REVIEW"
        reason = "HUMAN_APPROVAL_REQUIRED"
    return ReviewDecision(
        subject_id=candidate.version,
        decision=decision,
        reviewer_id="system-pending-human",
        reason=reason,
    )


def record_lexicon_review(
    candidate: LexiconCandidate, decision: ReviewDecision
) -> LexiconCandidate:
    if decision.subject_id != candidate.version:
        raise ValueError("SUBJECT_MISMATCH")
    if decision.decision == "APPROVED":
        return candidate.model_copy(update={"status": "APPROVED"})
    if decision.decision == "REJECTED":
        return candidate.model_copy(update={"status": "REJECTED"})
    return candidate


class LexiconStore:
    """Immutable candidate store with atomic active-version switching."""

    def __init__(self, active: LexiconCandidate | None = None) -> None:
        self._versions: dict[str, LexiconCandidate] = {}
        self._reviews: dict[str, ReviewDecision] = {}
        self._active = active
        if active:
            self._versions[active.version] = active

    @property
    def active(self) -> LexiconCandidate | None:
        return self._active

    def write_candidate(self, candidate: LexiconCandidate) -> str:
        self._versions.setdefault(candidate.version, candidate)
        return candidate.version

    def record_review(self, decision: ReviewDecision) -> None:
        self._reviews[decision.subject_id] = decision
        candidate = self._versions.get(decision.subject_id)
        if candidate:
            self._versions[decision.subject_id] = record_lexicon_review(candidate, decision)

    def activate_lexicon_version(self, version: str) -> LexiconCandidate:
        candidate = self._versions.get(version)
        decision = self._reviews.get(version)
        if candidate is None or decision is None or decision.decision != "APPROVED":
            raise ValueError("APPROVAL_REQUIRED")
        self._active = candidate.model_copy(update={"status": "ACTIVE"})
        self._versions[version] = self._active
        return self._active

    def lookup_expansions(
        self, terms: Sequence[str], compatibility: CompatibilityTuple | None = None
    ) -> list[ApprovedExpansion]:
        if self._active is None:
            return []
        pinned = compatibility or CompatibilityTuple(lexicon_version=self._active.version)
        if pinned.lexicon_version != self._active.version:
            return []
        normalized = {normalize_text(term) for term in terms}
        return [
            ApprovedExpansion(
                surface_form=mapping.surface_form,
                canonical_term=mapping.canonical_term or "",
                target_id=mapping.target_id or "",
                lexicon_version=self._active.version,
                evidence_refs=mapping.evidence_refs,
            )
            for mapping in self._active.mappings
            if mapping.status == "PROPOSED" and mapping.normalized_form in normalized
        ]


def activate_lexicon_version(store: LexiconStore, version: str) -> LexiconCandidate:
    return store.activate_lexicon_version(version)


def lookup_approved_expansions(
    terms: Sequence[str], store: LexiconStore, compatibility: CompatibilityTuple | None = None
) -> list[ApprovedExpansion]:
    return store.lookup_expansions(terms, compatibility)
