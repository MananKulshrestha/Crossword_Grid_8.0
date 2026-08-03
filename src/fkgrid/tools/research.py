"""Explicit, bounded external-research seams. No arbitrary network client is included."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from urllib.parse import urlparse

from .contracts import OnlineSearchResult, ResearchAnswer, ResearchDecision, ResearchSource
from .determinism import normalize_text, stable_id


def detect_research_need(current_message: str, explicit_consent: bool = False) -> ResearchDecision:
    normalized = normalize_text(current_message)
    explicit = explicit_consent or any(
        token in normalized
        for token in ("research", "latest", "news", "online", "according to the web")
    )
    if not explicit:
        return ResearchDecision(needed=False, query="", reason="NOT_EXPLICIT", explicit=False)
    query = current_message[:500].strip()
    return ResearchDecision(needed=True, query=query, reason="EXPLICIT_REQUEST", explicit=True)


def _source_from_fixture(
    value: ResearchSource | Mapping[str, object], index: int
) -> ResearchSource:
    if isinstance(value, ResearchSource):
        return value
    url = str(value.get("url", "https://example.invalid/source"))
    domain = str(value.get("domain", urlparse(url).netloc or "example.invalid")).lower()
    title = str(value.get("title", f"Source {index}"))[:300]
    extract = str(value.get("extract", value.get("snippet", "")))[:4000]
    source_id = str(
        value.get(
            "source_id", stable_id("source", {"url": url, "title": title, "extract": extract})
        )
    )
    citation_key = str(value.get("citation_key", f"src-{index}"))
    retrieved_at = value.get("retrieved_at")
    if not isinstance(retrieved_at, datetime):
        retrieved_at = datetime(1970, 1, 1, tzinfo=UTC)
    return ResearchSource(
        source_id=source_id,
        url=url,
        domain=domain,
        title=title,
        extract=extract,
        retrieved_at=retrieved_at,
        citation_key=citation_key,
    )


def online_search(
    decision: ResearchDecision,
    fixtures: Iterable[ResearchSource | Mapping[str, object]] = (),
    allow_domains: Sequence[str] = (),
    block_domains: Sequence[str] = (),
    max_results: int = 8,
    enabled: bool = False,
) -> OnlineSearchResult:
    if not decision.needed or not decision.explicit:
        return OnlineSearchResult(
            status="DECLINED",
            provider="disabled",
            executed_query="",
            warnings=["EXPLICIT_REQUEST_REQUIRED"],
        )
    if not enabled:
        return OnlineSearchResult(
            status="UNAVAILABLE",
            provider="fixture-only",
            executed_query=decision.query,
            warnings=["RESEARCH_PROVIDER_DISABLED"],
        )
    allowed = {domain.lower() for domain in allow_domains}
    blocked = {domain.lower() for domain in block_domains}
    sources: list[ResearchSource] = []
    for index, fixture in enumerate(fixtures, start=1):
        source = _source_from_fixture(fixture, index)
        if source.domain in blocked or (allowed and source.domain not in allowed):
            continue
        sources.append(source)
        if len(sources) >= min(8, max_results):
            break
    return OnlineSearchResult(
        status="OK" if sources else "UNAVAILABLE",
        provider="deterministic-fixture",
        executed_query=decision.query,
        sources=sources,
        warnings=[] if sources else ["NO_ALLOWLISTED_SOURCES"],
        cache_status="MISS",
    )


def fetch_research_source(
    source: ResearchSource | Mapping[str, object], allow_direct_fetch: bool = False
) -> ResearchSource:
    """Normalize a provider extract; direct HTTP fetching is intentionally absent.

    The flag is accepted to preserve the owning port's shape. A production
    adapter may implement allowlisted direct fetch behind a separate bulkhead.
    """

    del allow_direct_fetch
    return _source_from_fixture(source, 1)


def synthesize_research_answer(
    query: str, sources: Sequence[ResearchSource], max_sources: int = 3
) -> ResearchAnswer:
    del query
    selected = [
        source if isinstance(source, ResearchSource) else _source_from_fixture(source, index)
        for index, source in enumerate(sources[:max_sources], start=1)
    ]
    if not selected:
        return ResearchAnswer(
            status="INSUFFICIENT_EVIDENCE",
            answer="I could not find a cited source for that request.",
        )
    paragraphs = [
        f"{source.extract.strip()} [{source.citation_key}]"
        for source in selected
        if source.extract.strip()
    ]
    citations = [source.citation_key for source in selected if source.extract.strip()]
    if not paragraphs:
        return ResearchAnswer(
            status="INSUFFICIENT_EVIDENCE",
            answer="The available sources did not contain a usable extract.",
        )
    return ResearchAnswer(status="OK", answer=" ".join(paragraphs), citations=citations)


def validate_research_claims(
    answer: ResearchAnswer, sources: Sequence[ResearchSource]
) -> ResearchAnswer:
    allowed = {source.citation_key for source in sources}
    cited = set(answer.citations)
    missing = sorted(cited - allowed)
    if missing:
        return answer.model_copy(
            update={
                "status": "INSUFFICIENT_EVIDENCE",
                "unsupported_claims": [f"UNKNOWN_CITATION:{item}" for item in missing],
            }
        )
    if answer.status == "OK" and not cited:
        return answer.model_copy(
            update={"status": "INSUFFICIENT_EVIDENCE", "unsupported_claims": ["NO_CITATIONS"]}
        )
    return answer
