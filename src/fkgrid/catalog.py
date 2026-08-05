"""Live catalog access: reranker endpoint for ranking, MySQL for product truth.

No local reranking or scoring happens here - the reranker endpoint already
does retrieval + RAG + reranking server-side. This module only calls it
verbatim and joins the returned sku_ids against MySQL, preserving the
reranker's order. Any reranker or MySQL failure is raised, not swallowed.
"""

from __future__ import annotations

import functools
import json
import urllib.error
import urllib.request

from . import db
from .config import RerankerConfig, load_reranker_config
from .contracts import (
    Availability,
    Comparison,
    ComparisonCell,
    ComparisonRow,
    ProductDetails,
    RerankerRequest,
    SearchEntry,
    SearchResult,
)


@functools.lru_cache(maxsize=1)
def _config() -> RerankerConfig:
    return load_reranker_config()


class RerankerError(RuntimeError):
    """Raised on any reranker call failure. Hard pause - never fall back."""


class CatalogError(RuntimeError):
    """Raised on catalog/MySQL failure."""


def _call_reranker(request: RerankerRequest) -> list[dict]:
    config = _config()
    body = json.dumps(request.model_dump(mode="json")).encode("utf-8")
    http_request = urllib.request.Request(
        config.endpoint,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "fkgrid-chat/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(http_request, timeout=config.timeout_s) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RerankerError(f"RERANKER_HTTP_{exc.code}:{detail}") from exc
    except urllib.error.URLError as exc:
        raise RerankerError(f"RERANKER_UNAVAILABLE:{exc.reason}") from exc
    except TimeoutError as exc:
        raise RerankerError("RERANKER_TIMEOUT") from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RerankerError("RERANKER_RESPONSE_MALFORMED") from exc

    results = parsed.get("results") if isinstance(parsed, dict) else None
    if not isinstance(results, list):
        raise RerankerError("RERANKER_RESPONSE_MALFORMED")
    return results


_ROW_QUERY = """
SELECT
    s.sku_id, s.product_id, o.offer_id,
    p.title, p.brand_name, p.rating,
    pm.category,
    o.price_paise, o.availability_status, o.quantity
FROM skus s
JOIN offers o ON o.sku_id = s.sku_id AND o.catalog_version = s.catalog_version
JOIN products p ON p.product_id = s.product_id AND p.catalog_version = s.catalog_version
LEFT JOIN product_metadata pm ON pm.sku_id = s.sku_id
WHERE s.sku_id IN ({placeholders})
"""


def _fetch_rows(sku_ids: list[str]) -> dict[str, dict]:
    if not sku_ids:
        return {}
    placeholders = ",".join(["%s"] * len(sku_ids))
    query = _ROW_QUERY.format(placeholders=placeholders)
    with db.connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, sku_ids)
            rows = cursor.fetchall()
    return {row["sku_id"]: row for row in rows}


def _entry_from_row(row: dict, rerank_score: float | None) -> SearchEntry:
    return SearchEntry(
        sku_id=row["sku_id"],
        product_id=row["product_id"],
        offer_id=row["offer_id"],
        title=row["title"] or row["sku_id"],
        brand=row.get("brand_name"),
        category=row.get("category"),
        price_paise=row.get("price_paise"),
        rating=float(row["rating"]) if row.get("rating") is not None else None,
        availability_status=row.get("availability_status"),
        quantity=row.get("quantity"),
        rerank_score=rerank_score,
    )


def search(request: RerankerRequest) -> SearchResult:
    results = _call_reranker(request)
    sku_ids = [item.get("sku_id") for item in results if item.get("sku_id")]
    rows = _fetch_rows(sku_ids)

    # Check every sku_id the reranker returned against MySQL, not just the
    # first top_n - a hallucinated sku_id should be visible even if it
    # would have been truncated anyway.
    entries: list[SearchEntry] = []
    verified: list[str] = []
    hallucinated: list[str] = []
    for item in results:
        sku_id = item.get("sku_id")
        if not sku_id:
            continue
        row = rows.get(sku_id)
        if row is None:
            hallucinated.append(sku_id)
            continue
        verified.append(sku_id)
        if len(entries) < request.top_n:
            entries.append(_entry_from_row(row, item.get("rerank_score")))

    return SearchResult(
        entries=entries,
        result_set_id=f"reranker-{hash(tuple(sku_ids)) & 0xFFFFFFFF:08x}" if entries else None,
        verified_sku_ids=verified,
        hallucinated_sku_ids=hallucinated,
    )


def get_details(sku_id: str) -> ProductDetails:
    rows = _fetch_rows([sku_id])
    row = rows.get(sku_id)
    if row is None:
        return ProductDetails(found=False)
    return ProductDetails(found=True, entry=_entry_from_row(row, None))


def compare(sku_ids: list[str]) -> Comparison:
    rows = _fetch_rows(sku_ids)
    fields = ["title", "category", "brand", "price_paise", "rating", "availability_status"]
    rows_out: list[ComparisonRow] = []
    for field in fields:
        cells: list[ComparisonCell] = []
        for sku_id in sku_ids:
            row = rows.get(sku_id)
            if row is None:
                cells.append(ComparisonCell(sku_id=sku_id, value=None))
                continue
            entry = _entry_from_row(row, None)
            cells.append(ComparisonCell(sku_id=sku_id, value=getattr(entry, field, None)))
        rows_out.append(ComparisonRow(field=field, cells=cells))
    return Comparison(rows=rows_out)


def check_availability(sku_id: str) -> Availability:
    rows = _fetch_rows([sku_id])
    row = rows.get(sku_id)
    if row is None:
        return Availability(found=False)
    return Availability(
        found=True,
        availability_status=row.get("availability_status"),
        quantity=row.get("quantity"),
    )
