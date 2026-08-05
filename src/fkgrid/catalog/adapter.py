"""RetrievalCatalogAdapter -- the minimal CatalogSearchPort implementation
that lets the deployed RA retrieval service (search_catalog, exposed over
HTTP on port 8002 by RA/api_server.py) answer real orchestrator search
turns, instead of the synthetic 600-record fixture
(tools/compat.py's CatalogSearchPortAdapter -> DeterministicCatalog).

Why this is two data sources, not one
--------------------------------------
RA's `/api/search` response gives back `sku_id`, `rerank_score`, `metadata`
(a `product_metadata` row), `branches`, `evidence` -- and nothing else. It
has no `product_id`, no `offer_id`, no `catalog_version` (confirmed against
RA/search_catalog.py and RA/sql_filter.py directly, and documented as an
open gap in RA/AGENTIC_INTEGRATION.md section 1/7). But this orchestrator's
`ProductBinding` (agentic/contracts.py) requires exactly those three fields,
and the real `DatabaseCartAdapter` (cart/adapter.py) validates every
ADD_ITEM against them via a real FK lookup into `offers`/`skus`/`products`.

So this adapter resolves `sku_id -> product_id/offer_id/catalog_version`
with one extra MySQL query per search() call, using the *exact* same
`FLIPKART_DB_*` env vars, `pymysql`, fresh-connection-per-call convention
already established by cart/adapter.py and RA/sql_filter.py (both modules'
own docstrings cross-reference this as a shared convention -- see
cart/adapter.py's module docstring, "Connects with a fresh connection per
call (matches RA/sql_filter.py's _connect()-per-call pattern)").

This also means every ADD_ITEM built from a search result this adapter
returns carries the *real* catalog_version read fresh from `offers` at
search time -- not a hardcoded string -- so it can never silently drift out
of sync with whatever DatabaseCartAdapter independently validates against.
That is the fix for the long-documented catalog_version mismatch
(api/runtime.py's ApiRuntime.__init__ docstring; RA/AGENTIC_INTEGRATION.md
section 7's "catalog_version/offer_id/distinct product_id" gap): the
mismatch existed because the *fixture* catalog hardcodes
catalog_version="catalog-fixture-v1" while the real cart validates against
whatever is actually ACTIVE in `catalog_versions`. This adapter never
hardcodes a catalog_version anywhere; it always reads the live value.

Price/availability authority
-----------------------------
RA's `metadata["discounted_price"]`/`metadata["stock_status"]` are read
from the SAME `product_metadata` view `offers`/`skus` back onto, but through
an extra view-projection hop, in rupees (not paise) and a different stock
vocabulary (`in_stock`/`low_stock`/`out_of_stock` vs. this contract's
`AVAILABLE`/`UNAVAILABLE`/`UNKNOWN`/`NOT_MODELED`/`RETIRED`). Rather than
trust RA's projection *and* translate its vocabulary, this adapter reads
price_paise/availability_status straight from `offers` in the same query
that resolves offer_id/product_id -- one source of truth for commerce-
critical numbers, the same table DatabaseCartAdapter itself reads from.
`metadata`'s other columns (brand/category/material/subcategory_path) are
purely descriptive, so those are taken from RA's response as-is.

Explicitly NOT solved here (see module-level NOTE comments at each site,
and RA/AGENTIC_INTEGRATION.md section 7 for the full discussion):
  - Confidence-score calibration. `rerank_score` is passed through raw and
    unnormalized (a BGE cross-encoder raw score, not guaranteed to be in
    [0,1]). `tools/recovery.py`'s ACCEPT/CLARIFY/RECOVER thresholds were
    calibrated against the fixture's own heuristic score; piping this in
    unnormalized will likely miscalibrate that gate until real score
    samples are collected and a normalization step is added.
  - `unknown_terms`. RA computes nothing equivalent to "which query terms
    matched nothing" -- always returned empty here, which silently
    disables the RECOVER path that depends on it.
  - Category identity. RA's SQL filter matches a plain `category` string;
    this orchestrator's category constraints may carry a taxonomy_node_id.
    Passed through best-effort (str(value)); not confirmed to be the same
    identifier space.
  - `deadline_ms`. RA's search_catalog() has no server-side timeout
    awareness and (per RA's own manan.md) synchronously rebuilds/queries a
    persistent process-wide LightRAG instance. This adapter applies
    deadline_ms only as a client-side HTTP socket timeout -- RA itself may
    keep working past it.

Not wired into ApiRuntime by default. See the bottom of this docstring's
sibling PR/commit message for the exact one-line change (a new
`catalog_mode`/`FKGRID_CATALOG_MODE` branch in api/runtime.py's
ApiRuntime.create_session, parallel to the existing `cart_mode` branch) that
turns this on -- deliberately left as an opt-in step, matching how
FKGRID_CART_MODE=database was introduced, not defaulted.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pymysql
import pymysql.cursors

from fkgrid.agentic.contracts import (
    Availability,
    CommerceEligibility,
    Comparison,
    ComparisonCell,
    ComparisonRow,
    ConstraintOperator,
    EvidenceRef,
    Fact,
    FactScope,
    Money,
    ProductBinding,
    ProductDetails,
    QueryState,
    SearchEntry,
    SearchRequest,
    SearchResult,
    ToolStatus,
    TruthStatus,
)
from fkgrid.agentic.ports import CatalogSearchPort
from fkgrid.agentic.validation import canonical_hash

DB_HOST = os.environ.get("FLIPKART_DB_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("FLIPKART_DB_PORT", "3307"))
DB_USER = os.environ.get("FLIPKART_DB_USER", "flipkart_user")
DB_PASSWORD = os.environ.get("FLIPKART_DB_PASSWORD", "flipkart_pass")
DB_NAME = os.environ.get("FLIPKART_DB_NAME", "flipkart")

# RA's own api_server.py default -- see RA/api.sh / RA/api_server.py:API_PORT.
RA_BASE_URL = os.environ.get("FKGRID_RETRIEVAL_API_URL", "http://127.0.0.1:8002").rstrip("/")

_STOCK_STATUS_TO_RA = {
    "AVAILABLE": "in_stock",
    "UNAVAILABLE": "out_of_stock",
}
_RA_AVAILABILITY_STATUS_TO_CONTRACT = {
    "IN_STOCK": "AVAILABLE",
    "LOW_STOCK": "AVAILABLE",
    "OUT_OF_STOCK": "UNAVAILABLE",
}

_PRICE_FIELD_IDS = {"price", "max_price", "budget", "price_paise"}
_CATEGORY_FIELD_IDS = {"category", "category_id", "taxonomy_node_id", "product_type"}
_STOCK_FIELD_IDS = {"stock_status", "availability", "stock"}


def _connect() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD,
        database=DB_NAME, cursorclass=pymysql.cursors.DictCursor,
    )


def _money_to_rupees(value: Any) -> float | None:
    if isinstance(value, Money):
        return value.amount_paise / 100.0
    if isinstance(value, dict) and isinstance(value.get("amount_paise"), (int, float)):
        return value["amount_paise"] / 100.0
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _hard_constraints_to_ra(query_state: QueryState) -> tuple[dict[str, Any], list[str]]:
    """Best-effort translation of the typed hard_constraints into RA's flat
    dict shape (max_price/category/size/stock_status -- sql_filter.py's own
    _CONSTRAINT_CLAUSES keys, an unknown key raises there, so nothing else
    is safe to forward). Anything that cannot be mapped is NOT silently
    dropped: it comes back as a warning so the caller can see a hard
    constraint went unenforced by SQL, rather than assuming eligibility was
    checked when it wasn't."""
    hard_constraints: dict[str, Any] = {}
    warnings: list[str] = []
    for constraint in query_state.hard_constraints:
        field_id = constraint.field_id.casefold()
        if field_id in _PRICE_FIELD_IDS and constraint.operator in {
            ConstraintOperator.LTE, ConstraintOperator.LT, ConstraintOperator.RANGE,
        }:
            index = 1 if constraint.operator is ConstraintOperator.RANGE and len(constraint.values) > 1 else 0
            rupees = _money_to_rupees(constraint.values[index])
            if rupees is not None:
                hard_constraints["max_price"] = rupees
                continue
        elif field_id in _CATEGORY_FIELD_IDS and constraint.operator in {
            ConstraintOperator.EQ, ConstraintOperator.IN,
        } and constraint.values:
            hard_constraints["category"] = str(constraint.values[0])
            continue
        elif field_id == "size" and constraint.operator in {
            ConstraintOperator.EQ, ConstraintOperator.IN,
        } and constraint.values:
            hard_constraints["size"] = str(constraint.values[0])
            continue
        elif field_id in _STOCK_FIELD_IDS and constraint.values:
            mapped = _STOCK_STATUS_TO_RA.get(str(constraint.values[0]).upper())
            if mapped is not None:
                hard_constraints["stock_status"] = mapped
                continue
        warnings.append(f"HARD_CONSTRAINT_NOT_SQL_ENFORCED:{constraint.field_id}")
    return hard_constraints, warnings


def _soft_query_text(query_state: QueryState) -> str:
    if query_state.query_terms:
        return " ".join(query_state.query_terms)
    return query_state.compact_goal_summary.strip()


def _call_ra_search(query_state: dict, top_n: int, deadline_ms: int) -> list[dict]:
    """POSTs to RA/api_server.py's /api/search. Raises on transport/HTTP
    failure -- callers turn that into a safe SearchResult, never propagate
    RA's internal error text to the shopper untranslated."""
    payload = json.dumps(
        {
            "soft_query_text": query_state["soft_query_text"],
            "full_query_text": query_state["full_query_text"],
            "hard_constraints": query_state["hard_constraints"],
            "top_n": top_n,
        }
    ).encode("utf-8")
    request = Request(
        f"{RA_BASE_URL}/api/search",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # deadline_ms is this adapter's own client-side socket timeout only --
    # see the module docstring's "Explicitly NOT solved here" note. Floored
    # at 1s so a near-zero remaining budget doesn't produce a useless
    # sub-second timeout.
    timeout_s = max(1.0, deadline_ms / 1000.0)
    with urlopen(request, timeout=timeout_s) as response:
        body = json.loads(response.read().decode("utf-8"))
    return body.get("results", [])


def _fetch_bindings(cursor, sku_ids: Sequence[str]) -> dict[str, dict]:
    """One batched lookup resolving RA's bare sku_id into the real
    product_id/offer_id/catalog_version FK triple, plus the live
    price_paise/availability_status this adapter treats as authoritative
    over RA's own metadata projection (see module docstring). Only ACTIVE
    offers are eligible to become a ProductBinding -- an inactive/retired
    offer is exactly as unavailable to add-to-cart as one RA never
    returned."""
    if not sku_ids:
        return {}
    cursor.execute(
        "SELECT s.sku_id AS sku_id, s.product_id AS product_id, o.offer_id AS offer_id, "
        "o.catalog_version AS catalog_version, o.price_paise AS price_paise, "
        "o.availability_status AS availability_status, o.quantity AS quantity "
        "FROM skus s JOIN offers o "
        "ON o.catalog_version = s.catalog_version AND o.sku_id = s.sku_id "
        "WHERE o.status = 'ACTIVE' AND s.sku_id IN %s",
        (tuple(sku_ids),),
    )
    return {row["sku_id"]: row for row in cursor.fetchall()}


def _fact(
    *, fact_id: str, label: str, value: Any, status: TruthStatus,
    entity_id: str, field_path: str, catalog_version: str,
) -> Fact:
    return Fact(
        fact_id=fact_id,
        label=label,
        typed_value=value,
        status=status,
        scope=FactScope.CATALOG,
        provenance_type="RETRIEVAL_API",
        evidence_refs=[
            EvidenceRef(
                evidence_id=f"ra-evidence-{fact_id}",
                entity_type="SKU",
                entity_id=entity_id,
                field_path=field_path,
                source_type="RA_SEARCH_CATALOG",
                source_id="ra_search_catalog",
                version=catalog_version,
            )
        ],
        as_of=datetime.now(timezone.utc),
    )


def _facts_from_row(sku_id: str, metadata: dict, offer_row: dict) -> list[Fact]:
    catalog_version = offer_row["catalog_version"]
    facts: list[Fact] = []
    if metadata.get("product_name"):
        facts.append(_fact(
            fact_id=f"ra-fact-{sku_id}-title", label="title", value=metadata["product_name"],
            status=TruthStatus.VERIFIED, entity_id=sku_id, field_path="title",
            catalog_version=catalog_version,
        ))
    for label, key in (("category", "category"), ("brand", "brand"), ("material", "material")):
        if metadata.get(key):
            facts.append(_fact(
                fact_id=f"ra-fact-{sku_id}-{label}", label=label, value=metadata[key],
                status=TruthStatus.VERIFIED, entity_id=sku_id, field_path=label,
                catalog_version=catalog_version,
            ))
    price_paise = offer_row.get("price_paise")
    facts.append(_fact(
        fact_id=f"ra-fact-{sku_id}-price",
        label="price",
        value=Money(amount_paise=price_paise) if price_paise is not None else None,
        status=TruthStatus.VERIFIED if price_paise is not None else TruthStatus.UNKNOWN,
        entity_id=sku_id, field_path="price", catalog_version=catalog_version,
    ))
    availability = _RA_AVAILABILITY_STATUS_TO_CONTRACT.get(
        offer_row.get("availability_status"), "UNKNOWN"
    )
    facts.append(_fact(
        fact_id=f"ra-fact-{sku_id}-availability", label="availability", value=availability,
        status=TruthStatus.VERIFIED, entity_id=sku_id, field_path="availability",
        catalog_version=catalog_version,
    ))
    return facts


class RetrievalCatalogAdapter(CatalogSearchPort):
    """See module docstring. search() calls RA over HTTP; every other
    CatalogSearchPort method reads MySQL directly, because RA's HTTP API
    exposes search_catalog() only -- there is no RA endpoint for a single
    binding's details/comparison/availability/eligibility."""

    def __init__(self, *, base_url: str | None = None) -> None:
        self.base_url = (base_url or RA_BASE_URL).rstrip("/")
        self.search_calls = 0
        self.details_calls = 0
        self.compare_calls = 0

    def search(self, request: SearchRequest, deadline_ms: int) -> SearchResult:
        self.search_calls += 1
        soft_query_text = _soft_query_text(request.query_state)
        if not soft_query_text:
            return SearchResult(
                status=ToolStatus.NOT_FOUND,
                result_set_id=None,
                entries=[],
                eligible_count=0,
                confidence_signals={},
                hard_filter_hash=canonical_hash(request.query_state.hard_constraints),
                warnings=["EMPTY_QUERY_TEXT"],
            )
        hard_constraints, constraint_warnings = _hard_constraints_to_ra(request.query_state)
        # NOTE: full_query_text has no better source today -- see module
        # docstring / AGENTIC_INTEGRATION.md section 6 ("no single existing
        # agentic field is 'the full original query text' verbatim").
        # Reusing soft_query_text matches RA's own default-fallback rule
        # (search_catalog.py: "defaults to soft_query_text if not given").
        ra_query_state = {
            "soft_query_text": soft_query_text,
            "full_query_text": soft_query_text,
            "hard_constraints": hard_constraints,
        }
        try:
            candidates = _call_ra_search(ra_query_state, request.top_k, deadline_ms)
        except (URLError, HTTPError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            return SearchResult(
                status=ToolStatus.UNAVAILABLE,
                result_set_id=None,
                entries=[],
                eligible_count=0,
                confidence_signals={},
                hard_filter_hash=canonical_hash(request.query_state.hard_constraints),
                warnings=[f"RETRIEVAL_API_UNAVAILABLE:{type(exc).__name__}"],
            )
        excluded = set(request.exclusions)
        sku_ids = [c["sku_id"] for c in candidates if c.get("sku_id") not in excluded]

        conn = _connect()
        try:
            with conn.cursor() as cursor:
                bindings = _fetch_bindings(cursor, sku_ids)
        finally:
            conn.close()

        entries: list[SearchEntry] = []
        dropped = 0
        for position, candidate in enumerate(candidates, start=1):
            sku_id = candidate.get("sku_id")
            if sku_id is None or sku_id in excluded:
                continue
            offer_row = bindings.get(sku_id)
            if offer_row is None:
                # RA found this SKU (via BM25/semantic/SQL-filter's own
                # product_metadata snapshot) but it has no ACTIVE offer row
                # right now -- e.g. product_metadata/offers have drifted, or
                # the offer was retired since RA's last index build. Not
                # eligible for a ProductBinding; dropped, not fabricated.
                dropped += 1
                continue
            metadata = candidate.get("metadata") or {}
            entries.append(SearchEntry(
                result_entry_id=f"ra-entry-{sku_id}",
                display_position=min(position, 10),
                binding=ProductBinding(
                    product_id=offer_row["product_id"], sku_id=sku_id,
                    offer_id=offer_row["offer_id"], catalog_version=offer_row["catalog_version"],
                ),
                title=metadata.get("product_name") or sku_id,
                facts=_facts_from_row(sku_id, metadata, offer_row),
                score_components={"rerank_score": float(candidate.get("rerank_score") or 0.0)},
                # Approximate, not field-level: RA's branches are
                # bm25/semantic provenance, not matched hard-constraint
                # fields. See module docstring's "Explicitly NOT solved
                # here" note.
                matched_criteria=sorted(candidate.get("branches") or []),
                unknown_criteria=[],
            ))
            if len(entries) >= request.top_k:
                break

        warnings = list(constraint_warnings)
        if dropped:
            warnings.append(f"SKU_OFFER_NOT_FOUND_COUNT:{dropped}")
        if not entries:
            warnings.append("NO_ELIGIBLE_MATCH")

        return SearchResult(
            status=ToolStatus.OK if entries else ToolStatus.NOT_FOUND,
            result_set_id=canonical_hash([e.binding.sku_id for e in entries]) if entries else None,
            entries=entries,
            eligible_count=len(candidates),
            # top_score is raw/uncalibrated -- see module docstring.
            confidence_signals={
                "top_score": entries[0].score_components.get("rerank_score", 0.0) if entries else 0.0,
            },
            hard_filter_hash=canonical_hash(request.query_state.hard_constraints),
            warnings=warnings,
        )

    def get_details(self, binding: ProductBinding, compatibility: Any, deadline_ms: int) -> ProductDetails:
        self.details_calls += 1
        conn = _connect()
        try:
            with conn.cursor() as cursor:
                offer_rows = _fetch_bindings(cursor, [binding.sku_id])
                offer_row = offer_rows.get(binding.sku_id)
                if offer_row is None or offer_row["offer_id"] != binding.offer_id:
                    return ProductDetails(status=ToolStatus.NOT_FOUND, binding=binding)
                cursor.execute("SELECT * FROM product_metadata WHERE sku_id = %s", (binding.sku_id,))
                metadata = cursor.fetchone() or {}
                cursor.execute(
                    "SELECT s2.sku_id AS sku_id FROM skus s1 JOIN skus s2 "
                    "ON s2.catalog_version = s1.catalog_version AND s2.product_id = s1.product_id "
                    "WHERE s1.catalog_version = %s AND s1.sku_id = %s AND s2.sku_id != %s LIMIT 10",
                    (binding.catalog_version, binding.sku_id, binding.sku_id),
                )
                sibling_skus = [row["sku_id"] for row in cursor.fetchall()]
                sibling_bindings = _fetch_bindings(cursor, sibling_skus)
        finally:
            conn.close()
        variants = [
            ProductBinding(
                product_id=row["product_id"], sku_id=sku_id,
                offer_id=row["offer_id"], catalog_version=row["catalog_version"],
            )
            for sku_id, row in sibling_bindings.items()
        ]
        return ProductDetails(
            status=ToolStatus.OK,
            binding=binding,
            title=metadata.get("product_name"),
            facts=_facts_from_row(binding.sku_id, metadata, offer_row),
            variants=variants,
        )

    def compare(
        self, bindings: Sequence[ProductBinding], compatibility: Any, deadline_ms: int,
    ) -> Comparison:
        self.compare_calls += 1
        details = [self.get_details(binding, compatibility, deadline_ms) for binding in bindings]
        fields = ("title", "category", "brand", "price", "availability")
        rows: list[ComparisonRow] = []
        for field_id in fields:
            cells: list[ComparisonCell] = []
            for detail in details:
                fact = next((f for f in detail.facts if f.label == field_id), None)
                value = detail.title if field_id == "title" and detail.title else (fact.typed_value if fact else None)
                cells.append(ComparisonCell(
                    field_id=field_id, value=value,
                    status=fact.status if fact else TruthStatus.UNKNOWN,
                    evidence_refs=fact.evidence_refs if fact else [],
                ))
            rows.append(ComparisonRow(field_id=field_id, label=field_id.title(), cells=cells))
        return Comparison(status=ToolStatus.OK, bindings=list(bindings), rows=rows)

    def check_availability(self, binding: ProductBinding, compatibility: Any, deadline_ms: int) -> Availability:
        conn = _connect()
        try:
            with conn.cursor() as cursor:
                offer_row = _fetch_bindings(cursor, [binding.sku_id]).get(binding.sku_id)
        finally:
            conn.close()
        if offer_row is None or offer_row["offer_id"] != binding.offer_id:
            return Availability(
                status=ToolStatus.NOT_FOUND, binding=binding, availability_status="UNKNOWN",
                truth_status=TruthStatus.UNKNOWN, warnings=["BINDING_NOT_FOUND"],
            )
        availability = _RA_AVAILABILITY_STATUS_TO_CONTRACT.get(offer_row["availability_status"], "UNKNOWN")
        return Availability(
            status=ToolStatus.OK, binding=binding, availability_status=availability,
            quantity=offer_row.get("quantity"), truth_status=TruthStatus.VERIFIED,
            scope=FactScope.CATALOG, provenance_type="RETRIEVAL_API",
        )

    def check_eligibility(self, binding: ProductBinding, purpose: str, deadline_ms: int) -> CommerceEligibility:
        conn = _connect()
        try:
            with conn.cursor() as cursor:
                offer_row = _fetch_bindings(cursor, [binding.sku_id]).get(binding.sku_id)
        finally:
            conn.close()
        if offer_row is None or offer_row["offer_id"] != binding.offer_id:
            return CommerceEligibility(
                eligible=False, binding=binding, policy_status="NOT_FOUND",
                reasons=["BINDING_NOT_FOUND"],
            )
        available = offer_row["availability_status"] in {"IN_STOCK", "LOW_STOCK"}
        cart_blocked = purpose == "CART_UPDATE" and not available
        return CommerceEligibility(
            eligible=not cart_blocked,
            binding=binding,
            policy_status="ALLOWED" if not cart_blocked else "UNAVAILABLE",
            price=Money(amount_paise=offer_row["price_paise"]) if offer_row.get("price_paise") is not None else None,
            availability_status=_RA_AVAILABILITY_STATUS_TO_CONTRACT.get(offer_row["availability_status"], "UNKNOWN"),
            reasons=["OUT_OF_STOCK"] if cart_blocked else [],
        )
