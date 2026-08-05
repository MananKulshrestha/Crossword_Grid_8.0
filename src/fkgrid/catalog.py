"""Live catalog access: normal reranker mode and read-only fast mode.

Normal mode calls the existing reranker endpoint verbatim and joins its
returned sku_ids against MySQL. Fast mode never calls the endpoint: it applies
hard constraints in a parameterized SQL read, computes BM25 over the returned
title/description rows, and uses a stable deterministic tie-break. Neither
path writes to the database.
"""

from __future__ import annotations

import functools
import hashlib
import json
import math
import re
import urllib.error
import urllib.request
from collections import Counter

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
        raise RerankerError(f"RERANKER_HTTP_{exc.code}") from exc
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
        bm25_score=None,
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
        search_mode="normal",
    )


_FAST_TOKEN_RE = re.compile(r"[a-z0-9]+")
_FAST_BM25_K1 = 1.2
_FAST_BM25_B = 0.75
_FAST_STOCK_STATUS = {
    "in_stock": "IN_STOCK",
    "low_stock": "LOW_STOCK",
    "out_of_stock": "OUT_OF_STOCK",
}


def _tokenize(text: str | None) -> list[str]:
    return _FAST_TOKEN_RE.findall((text or "").lower())


def _document_text(row: dict) -> str:
    # Keep the fast lexical corpus deliberately bounded to catalog text. Hard
    # constraints are applied in SQL and must not become soft text terms.
    return f"{row.get('title') or ''} {row.get('description') or ''}"


def _bm25_rank(rows: list[dict], query_text: str, top_n: int) -> list[tuple[dict, float]]:
    """Score eligible SQL rows with BM25 and return a stable ranked slice.

    The live schema has no FULLTEXT index and the user explicitly asked not to
    change the database. Computing corpus statistics over the eligible rows
    therefore keeps the operation read-only while preserving BM25 semantics.
    """

    if not rows or top_n <= 0:
        return []

    tokenized_docs = [_tokenize(_document_text(row)) for row in rows]
    document_lengths = [len(tokens) for tokens in tokenized_docs]
    average_document_length = sum(document_lengths) / len(document_lengths) or 1.0
    document_frequency: Counter[str] = Counter()
    for tokens in tokenized_docs:
        document_frequency.update(set(tokens))

    query_terms = list(dict.fromkeys(_tokenize(query_text)))
    document_count = len(rows)
    scored: list[tuple[dict, float]] = []
    for row, tokens, document_length in zip(rows, tokenized_docs, document_lengths):
        term_frequency = Counter(tokens)
        score = 0.0
        for term in query_terms:
            frequency = term_frequency.get(term, 0)
            if frequency == 0:
                continue
            frequency_in_documents = document_frequency[term]
            inverse_document_frequency = math.log(
                1.0 + (document_count - frequency_in_documents + 0.5) / (frequency_in_documents + 0.5)
            )
            normalization = 1.0 - _FAST_BM25_B + _FAST_BM25_B * (
                document_length / average_document_length
            )
            score += inverse_document_frequency * (
                frequency * (_FAST_BM25_K1 + 1.0)
            ) / (frequency + _FAST_BM25_K1 * normalization)
        scored.append((row, score))

    def sort_key(item: tuple[dict, float]) -> tuple[float, float, int, str, str]:
        row, score = item
        rating = row.get("rating")
        price = row.get("price_paise")
        rating_key = -float(rating) if rating is not None else float("inf")
        price_key = int(price) if price is not None else 2**63 - 1
        return (
            -score,
            rating_key,
            price_key,
            str(row.get("sku_id") or ""),
            str(row.get("offer_id") or ""),
        )

    return sorted(scored, key=sort_key)[:top_n]


_FAST_ROW_QUERY = """
SELECT
    s.sku_id, s.product_id, o.offer_id,
    p.title, p.description, p.brand_name, p.rating,
    pm.category,
    o.price_paise, o.availability_status, o.quantity,
    s.variant_label
FROM skus s
JOIN offers o ON o.sku_id = s.sku_id AND o.catalog_version = s.catalog_version
JOIN products p ON p.product_id = s.product_id AND p.catalog_version = s.catalog_version
LEFT JOIN product_metadata pm ON pm.sku_id = s.sku_id
WHERE {where_clauses}
"""


def _fast_filter_sql(hard_constraints: dict) -> tuple[str, list[object]]:
    clauses = ["1 = 1"]
    params: list[object] = []
    allowed = {"max_price", "min_price", "brand", "category", "stock_status"}
    unknown = set(hard_constraints) - allowed
    if unknown:
        raise CatalogError(f"Unknown hard constraints: {sorted(unknown)}")

    if "max_price" in hard_constraints:
        clauses.append("o.price_paise <= %s")
        params.append(hard_constraints["max_price"])
    if "min_price" in hard_constraints:
        clauses.append("o.price_paise >= %s")
        params.append(hard_constraints["min_price"])
    if "brand" in hard_constraints:
        clauses.append("p.brand_name = %s")
        params.append(hard_constraints["brand"])
    if "category" in hard_constraints:
        clauses.append("pm.category = %s")
        params.append(hard_constraints["category"])
    if "stock_status" in hard_constraints:
        status = hard_constraints["stock_status"]
        if not isinstance(status, str):
            raise CatalogError("stock_status must be a string")
        clauses.append("o.availability_status = %s")
        params.append(_FAST_STOCK_STATUS.get(status.lower(), status.upper()))
    return " AND ".join(clauses), params


def fast_search(request: RerankerRequest) -> SearchResult:
    """Search only through SQL hard filters, BM25, and deterministic ranking."""

    where_clauses, params = _fast_filter_sql(request.hard_constraints)
    query = _FAST_ROW_QUERY.format(where_clauses=where_clauses)
    with db.connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()

    ranked = _bm25_rank(rows, request.soft_query_text, request.top_n)
    entries: list[SearchEntry] = []
    for row, score in ranked:
        entry = _entry_from_row(row, None).model_copy(update={"bm25_score": score})
        entries.append(entry)

    sku_ids = [entry.sku_id for entry in entries]
    digest = hashlib.sha256("\n".join(sku_ids).encode("utf-8")).hexdigest()[:16]
    return SearchResult(
        entries=entries,
        result_set_id=f"fast-{digest}" if entries else None,
        search_mode="fast",
        verified_sku_ids=[row["sku_id"] for row in rows if row.get("sku_id")],
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
