"""Conservative, reviewed runtime normalisation for catalog language terms."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from fkgrid.domain.catalog_language import SurfaceFormCluster

_WHITESPACE = re.compile(r"\s+")
_PUNCTUATION = re.compile(r"[^\w+&.]", re.UNICODE)
_TOKEN = re.compile(r"[\w+&.]+", re.UNICODE)
_HYPHENS = "‐‑‒–—−﹘﹣－"


@dataclass(frozen=True, slots=True)
class NormalizerConfig:
    version: str = "normalizer-v1"
    reviewed_morphology: tuple[tuple[str, str], ...] = ()


def normalize_surface_form(
    text: str,
    locale: str = "en-IN",
    config: NormalizerConfig | None = None,
) -> str:
    """Apply NFKC/casefold and conservative whitespace/punctuation rules.

    This function deliberately does not transliterate, phonetic-match, or
    aggressively stem terms.  Locale is recorded by callers; it does not alter
    text unless a separately reviewed morphology table is supplied.
    """

    del locale  # Locale is a scope key, not permission for implicit transliteration.
    active_config = config or NormalizerConfig()
    normalized = unicodedata.normalize("NFKC", text).casefold().strip()
    for hyphen in _HYPHENS:
        normalized = normalized.replace(hyphen, "-")
    normalized = normalized.replace("-", " ")
    normalized = _PUNCTUATION.sub(" ", normalized)
    normalized = _WHITESPACE.sub(" ", normalized).strip()
    for source, target in active_config.reviewed_morphology:
        if normalized == source:
            normalized = target
            break
    return normalized


def tokenize(text: str, config: NormalizerConfig | None = None) -> tuple[str, ...]:
    normalized = normalize_surface_form(text, config=config)
    return tuple(_TOKEN.findall(normalized))


def generate_deterministic_variants(
    surface_form: str,
    config: NormalizerConfig | None = None,
) -> tuple[str, ...]:
    """Return only low-risk spelling/formatting variants for offline proposals."""

    normalized = normalize_surface_form(surface_form, config=config)
    variants: set[str] = {normalized}
    if " " in normalized:
        variants.add(normalized.replace(" ", "-"))
    if "-" in surface_form:
        variants.add(normalized.replace(" ", "-"))
    words = normalized.split()
    if words and words[-1].endswith("s") and len(words[-1]) > 3:
        variants.add(" ".join([*words[:-1], words[-1][:-1]]))
    elif words:
        variants.add(" ".join([*words[:-1], f"{words[-1]}s"]))
    return tuple(sorted(item for item in variants if item))


def _levenshtein_at_most_one(left: str, right: str) -> bool:
    if left == right:
        return True
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right, strict=True)) <= 1
    shorter, longer = sorted((left, right), key=len)
    index_shorter = index_longer = differences = 0
    while index_shorter < len(shorter) and index_longer < len(longer):
        if shorter[index_shorter] != longer[index_longer]:
            differences += 1
            index_longer += 1
            if differences > 1:
                return False
        else:
            index_shorter += 1
            index_longer += 1
    return True


def cluster_surface_forms(
    surface_forms: list[tuple[str, int]],
    locale: str = "en-IN",
    config: NormalizerConfig | None = None,
) -> list[tuple[str, tuple[str, ...]]]:
    """Cluster exact-normalized or one-edit spelling variants only.

    The output is intentionally small and deterministic.  Semantic similarity
    is left to the bounded proposer/critic workflow and never inferred here.
    """

    normalized = sorted(
        (
            (normalize_surface_form(form, locale, config), form, count)
            for form, count in surface_forms
        ),
        key=lambda value: (value[0], value[1]),
    )
    groups: list[list[tuple[str, str, int]]] = []
    for item in normalized:
        if not groups or not _levenshtein_at_most_one(groups[-1][0][0], item[0]):
            groups.append([item])
        else:
            groups[-1].append(item)
    return [
        (
            group[0][0],
            tuple(sorted({surface for _, surface, _ in group})),
        )
        for group in groups
    ]


def protected_ranges(text: str) -> tuple[tuple[int, int], ...]:
    """Find quoted spans and simple negation spans that runtime must not expand."""

    ranges: list[tuple[int, int]] = []
    quote_start: int | None = None
    for index, char in enumerate(text):
        if char in {'"', "'", "“", "”"}:
            if quote_start is None:
                quote_start = index
            else:
                ranges.append((quote_start, index + 1))
                quote_start = None
    negation = re.compile(r"\b(?:not|no|without|except)\b[^,.!?;]{0,80}", re.IGNORECASE)
    ranges.extend((match.start(), match.end()) for match in negation.finditer(text))
    return tuple(sorted(ranges))


def overlaps_protected(start: int, end: int, ranges: tuple[tuple[int, int], ...]) -> bool:
    return any(
        start < protected_end and end > protected_start for protected_start, protected_end in ranges
    )


def cluster_from_groups(
    groups: list[tuple[str, tuple[str, ...]]],
    counts: dict[str, int],
    locale: str,
    taxonomy_node_id: str | None,
    attribute_id: str | None,
    source_classes: list,
    evidence_group_ids: list[str],
) -> list[SurfaceFormCluster]:
    """Convert normalization output into strict workflow clusters."""

    clusters: list[SurfaceFormCluster] = []
    for index, (normalized, forms) in enumerate(groups, start=1):
        clusters.append(
            SurfaceFormCluster(
                cluster_id=f"cluster-{index}-{normalized}",
                normalized_form=normalized,
                surface_forms=list(forms),
                support_count=sum(counts.get(form, 0) for form in forms),
                source_group_count=len(evidence_group_ids),
                source_classes=source_classes,
                locale=locale,
                taxonomy_node_id=taxonomy_node_id,
                attribute_id=attribute_id,
                evidence_group_ids=evidence_group_ids,
            )
        )
    return clusters
