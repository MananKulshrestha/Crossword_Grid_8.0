"""Real, Retrieval-API + MySQL-backed CatalogSearchPort implementation.

Implements CatalogSearchPort (agentic/ports.py) directly, against
agentic.contracts types -- the same pattern DatabaseCartAdapter already
uses for CartPort (fkgrid/cart/adapter.py), not through
fkgrid.tools.compat.CatalogSearchPortAdapter's DeterministicCatalog chain.
No import from fkgrid.tools anywhere in this module; this package is a
standalone drop-in for the `catalog=` argument TurnOrchestrator already
accepts as a Protocol, the same injection point cart_mode uses today
(api/runtime.py's ApiRuntime.create_session):

    catalog = RetrievalCatalogAdapter()
    # instead of: catalog=self.tooling.shopper_catalog

This module does not wire itself in -- that one-line swap at the
composition root is deliberately left out of scope here.

--- What this adapter does ---

search() calls the deployed RA Retrieval API (POST {base}/api/search --
BM25 + LightRAG semantic branch fused and reordered by a cross-encoder,
see Vatsalya Version/RA/search_catalog.py on the graph-optimization /
vatsalya/reranking branches) for ranking, then resolves every returned
sku_id against the live MySQL offers/skus/products/product_metadata
tables to build a real ProductBinding (product_id/sku_id/offer_id/
catalog_version) and the display Facts the frontend renders. RA's own
response carries none of those four binding fields -- confirmed directly
against the live endpoint, not assumed -- so this resolution step is not
optional.

get_details/compare/check_availability/check_eligibility never call the
Retrieval API at all: they're always given an exact ProductBinding
already, so they go straight to the same DB join search() uses.

--- Confirmed (checked directly against the live RunPod DB, 2026-08-05) ---

- offers.offer_id == 'off_' + sku_id for all 19,998 rows (0 mismatches).
- Exactly one ACTIVE offer per sku_id (0 duplicates, 0 missing).
- Every product_metadata.sku_id has a matching skus row (0 orphans).
- catalog_version is the single constant "flipkart_v1" for every row in
  both skus and offers (0 exceptions) -- matches
  fkgrid.cart.adapter.DATABASE_CART_CATALOG_VERSION exactly, which this
  module imports rather than re-hardcodes, so the two subsystems cannot
  drift out of agreement on what "the real catalog" means.

Despite that clean 1:1 offer_id-from-sku_id string pattern, this adapter
resolves bindings via a real SQL join (_load_rows), not string
derivation -- an explicit choice: the join also fetches price/
availability/quantity/product_metadata fields this adapter needs anyway,
gets fresher data than baking in a mock-data-generator convention as a
permanent contract, and costs one indexed batch query per search.

--- Confirmed against the real frontend (not the fixture's convention) ---

Fact labels are deliberately lowercase and exactly match what
frontend/src/features/catalog/product-card.tsx and frontend/src/pages.tsx
read by name via a plain `facts.find(f => f.label === label)`:
"price" (typed_value: {"amount_paise": int, "currency": "INR"}),
"brand", "category", "rating", "availability". The Add-to-cart button in
product-card.tsx is literally `disabled={availability !== "AVAILABLE"}`
-- that exact uppercase string is load-bearing, not a display nicety, so
_availability_vocab is the one place that mapping happens and every
caller goes through it.

The fixture path's own convention (tools/interop.py's "Prototype price"
label, tools/compat.py's CatalogSearchPortAdapter) is a different,
parallel implementation -- not reused here, not modified here.

--- Latency: expected, not a defect, not this module's problem to fix ---

The deployed Retrieval API's real response time is ~20-30s per search
(BM25 + LightRAG semantic branch + cross-encoder rerank), confirmed by
direct, repeated measurement against the live RunPod deployment -- not a
cold-start artifact (three sequential calls: ~23s, ~19s, ~20s) and, per
explicit product direction, an accepted characteristic of this deployment
that this task does not optimize.

Consequence for this adapter's design: its HTTP timeout is a fixed,
generous ceiling (RETRIEVAL_API_TIMEOUT_S, default 45s) sized for that
real latency -- NOT derived from the deadline_ms the orchestrator passes
into search() (which defaults to OrchestratorConfig.local_tool_budget_ms
= 300ms). Truncating the call to deadline_ms would make a request that is
working correctly indistinguishable from a dead service. deadline_ms is
accepted (CatalogSearchPort requires it) but deliberately not used to
bound this call -- turn-level timeout/budget policy is an orchestrator
decision, out of scope here by explicit instruction.

--- What this adapter deliberately does NOT change ---

1. OrchestratorConfig.local_tool_budget_ms / any orchestrator timeout or
   budget logic -- untouched. Reconciling that budget with this adapter's
   real latency (e.g. raising it, or giving SEARCH its own longer budget)
   is a decision for whoever owns orchestrator.py, not made here.
2. RecoveryPort (self.recovery in TurnOrchestrator) is a separate port,
   still wired to the fixture DeterministicCatalog via
   tools.integration.build_runtime_tooling(). Confirmed (not assumed) to
   be harmless as-is: DeterministicRecoveryAdapter.recover() only
   re-queries its own (fixture) catalog when result.unknown_terms is
   non-empty, and the shape-A -> shape-B translation it runs this
   adapter's SearchResult through (tools/interop.py:
   search_result_from_worktree) never populates unknown_terms (shape A
   has no such field) -- so recover() is always a same-result pass-
   through against this adapter's output, never a silent revert to
   fixture data.
3. Hard-constraint category/size fidelity: RA's SQL filter matches a
   plain category string with no confirmed shared identity space against
   the orchestrator's taxonomy_node_id-shaped hard constraints (see
   AGENTIC_INTEGRATION.md section 7, still an open decision as of the
   graph-optimization/vatsalya/reranking branches). Passed through
   best-effort as a literal string; not silently claimed to be exact.

Environment variables:
  FKGRID_RETRIEVAL_API_URL     base URL of the deployed RA HTTP API
                               (default: http://127.0.0.1:8002)
  FKGRID_RETRIEVAL_TIMEOUT_S   HTTP timeout in seconds for the Retrieval
                               API call (default: 45 -- see latency note
                               above; not derived from deadline_ms)
  FLIPKART_DB_HOST/PORT/USER/PASSWORD/NAME   same variables
                               DatabaseCartAdapter already reads, same
                               defaults -- one DB configuration for both
                               subsystems.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Sequence
from typing import Any

import httpx
import pymysql
import pymysql.cursors

from fkgrid.agentic.contracts import (
    Availability,
    Comparison,
    ComparisonCell,
    ComparisonRow,
    CommerceEligibility,
    CompatibilityTuple,
    EvidenceRef,
    Fact,
    FactScope,
    Money,
    ProductBinding,
    ProductDetails,
    SearchEntry,
    SearchRequest,
    SearchResult,
    ToolStatus,
    TruthStatus,
)
from fkgrid.agentic.ports import CatalogSearchPort
from fkgrid.cart.adapter import DATABASE_CART_CATALOG_VERSION

RETRIEVAL_API_URL = os.environ.get("FKGRID_RETRIEVAL_API_URL", "http://127.0.0.1:8002").rstrip("/")

# Fixed, generous ceiling sized for the real ~20-30s observed latency --
# NOT derived from deadline_ms. See module docstring's latency note.
RETRIEVAL_API_TIMEOUT_S = float(os.environ.get("FKGRID_RETRIEVAL_TIMEOUT_S", "45"))

DB_HOST = os.environ.get("FLIPKART_DB_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("FLIPKART_DB_PORT", "3307"))
DB_USER = os.environ.get("FLIPKART_DB_USER", "flipkart_user")
DB_PASSWORD = os.environ.get("FLIPKART_DB_PASSWORD", "flipkart_pass")
DB_NAME = os.environ.get("FLIPKART_DB_NAME", "flipkart")

# Imported, not re-hardcoded -- see module docstring. This is the one
# catalog_version this adapter will ever produce bindings under.
EXPECTED_CATALOG_VERSION = DATABASE_CART_CATALOG_VERSION


def _connect() -> pymysql.connections.Connection:
    # Fresh connection per call -- matches DatabaseCartAdapter's own
    # _connect() exactly (same rationale: an adapter instance's lifetime
    # spans idle time between turns, not a request).
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        cursorclass=pymysql.cursors.DictCursor,
    )


def _json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return str(value)


def _stable_id(prefix: str, payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=_json_default, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
    return f"{prefix}_{digest}"


def _availability_vocab(raw: str | None) -> str:
    """offers.availability_status (IN_STOCK/LOW_STOCK/OUT_OF_STOCK) -> the
    AVAILABLE/UNAVAILABLE/UNKNOWN vocabulary the real frontend keys off of
    by exact string (product-card.tsx's `disabled={availability !==
    "AVAILABLE"}`). LOW_STOCK still counts as purchasable -> AVAILABLE."""
    return {
        "IN_STOCK": "AVAILABLE",
        "LOW_STOCK": "AVAILABLE",
        "OUT_OF_STOCK": "UNAVAILABLE",
    }.get(raw or "", "UNKNOWN")


# ---------------------------------------------------------------------
# RA Retrieval API client
# ---------------------------------------------------------------------


def _call_retrieval_api(payload: dict[str, Any]) -> list[dict[str, Any]] | None:
    """POST {RETRIEVAL_API_URL}/api/search. Returns None on any failure
    (network error, non-2xx, unparseable body) -- never raises. Uses the
    fixed RETRIEVAL_API_TIMEOUT_S ceiling, not the caller's deadline_ms --
    see module docstring's latency note for why."""
    try:
        response = httpx.post(
            f"{RETRIEVAL_API_URL}/api/search", json=payload, timeout=RETRIEVAL_API_TIMEOUT_S
        )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    results = data.get("results")
    return results if isinstance(results, list) else []


# ---------------------------------------------------------------------
# QueryState (agentic.contracts, shape A) -> RA's query_state dict
# ---------------------------------------------------------------------

_PRICE_MAX_OPERATORS = {"LTE", "LT"}
_PRICE_MIN_OPERATORS = {"GTE", "GT"}


def _hard_constraints_to_ra(
    constraints: Sequence[Any],
) -> tuple[dict[str, Any], float | None, list[str]]:
    """Best-effort translation into RA's flat {max_price, category, size,
    stock_status} dict (AGENTIC_INTEGRATION.md section 6's mapping).
    Anything that doesn't fit those four keys is reported back in
    `dropped` rather than sent as an unknown key -- RA's own sql_filter.py
    rejects an unrecognized key with a 400, it does not ignore it.

    min_price has no RA-side equivalent at all (RA only supports a max_
    price ceiling) -- returned separately so search() can apply it as a
    post-fetch filter against the real offers.price_paise this adapter
    already reads, instead of silently dropping a hard constraint.
    """
    ra: dict[str, Any] = {}
    min_price: float | None = None
    dropped: list[str] = []
    for constraint in constraints:
        field_id = constraint.field_id
        operator = getattr(constraint.operator, "value", constraint.operator)
        values = constraint.values
        if field_id == "price" and operator in _PRICE_MAX_OPERATORS and values:
            ra["max_price"] = float(values[0])
        elif field_id == "price" and operator in _PRICE_MIN_OPERATORS and values:
            min_price = float(values[0])
        elif field_id in {"category", "taxonomy_node_id"} and values:
            ra["category"] = str(values[0])
        elif field_id == "size" and values:
            ra["size"] = str(values[0])
        elif field_id in {"stock", "availability"} and values:
            mapped = {"AVAILABLE": "in_stock", "UNAVAILABLE": "out_of_stock"}.get(str(values[0]))
            if mapped:
                ra["stock_status"] = mapped
            else:
                dropped.append(field_id)
        else:
            dropped.append(field_id)
    return ra, min_price, dropped


def _soft_query_text(query_state: Any) -> str:
    terms = list(query_state.query_terms)
    for preference in query_state.soft_preferences:
        terms.extend(str(value) for value in preference.values)
    text = " ".join(term for term in terms if term)
    return text or query_state.compact_goal_summary or "products"


# ---------------------------------------------------------------------
# sku_id -> real binding + display data (live MySQL)
# ---------------------------------------------------------------------

_ROW_QUERY_TEMPLATE = """
    SELECT s.sku_id, s.product_id, o.offer_id, o.catalog_version,
           o.price_paise, o.availability_status, o.quantity,
           pm.product_name, pm.brand, pm.category, pm.subcategory_path,
           pm.material, pm.size, pm.rating
    FROM skus s
    JOIN offers o ON o.sku_id = s.sku_id AND o.catalog_version = s.catalog_version
    JOIN products p ON p.product_id = s.product_id AND p.catalog_version = s.catalog_version
    LEFT JOIN product_metadata pm ON pm.sku_id = s.sku_id
    WHERE s.catalog_version = %s AND s.status = 'ACTIVE'
      AND o.status = 'ACTIVE' AND p.status = 'ACTIVE'
      AND s.sku_id IN ({placeholders})
"""


def _load_rows(sku_ids: Sequence[str], catalog_version: str) -> dict[str, dict[str, Any]]:
    """Batch-resolve sku_id -> the row this adapter needs for both binding
    construction (offer_id/product_id/catalog_version) and Fact display
    (product_metadata columns). Returns {} on any DB failure rather than
    raising -- a dead DB must degrade the port to typed NOT_FOUND/
    UNAVAILABLE results, not crash the turn."""
    if not sku_ids:
        return {}
    try:
        conn = _connect()
    except Exception:
        return {}
    try:
        placeholders = ",".join(["%s"] * len(sku_ids))
        with conn.cursor() as cursor:
            cursor.execute(
                _ROW_QUERY_TEMPLATE.format(placeholders=placeholders),
                (catalog_version, *sku_ids),
            )
            rows = cursor.fetchall()
    except Exception:
        return {}
    finally:
        conn.close()
    return {row["sku_id"]: row for row in rows}


def _load_one(sku_id: str, catalog_version: str) -> dict[str, Any] | None:
    return _load_rows([sku_id], catalog_version).get(sku_id)


def _evidence_ref(field_path: str, binding: ProductBinding) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=_stable_id("ev", {"offer": binding.offer_id, "field": field_path}),
        entity_type="offer",
        entity_id=binding.offer_id,
        field_path=field_path,
        source_type="mysql_offers_products",
        source_id=binding.offer_id,
        version=binding.catalog_version,
    )


_ATTRIBUTE_FIELDS = (
    ("brand", "brand"),
    ("category", "category"),
    ("subcategory_path", "subcategory_path"),
    ("material", "material"),
    ("size", "size"),
    ("rating", "rating"),
)


def _facts_from_row(row: dict[str, Any], binding: ProductBinding) -> list[Fact]:
    """Fact labels are lowercase and pinned to what the real frontend
    reads by name (see module docstring) -- not left to convention."""
    facts = [
        Fact(
            fact_id=_stable_id("fact", {"offer": binding.offer_id, "field": "title"}),
            label="title",
            typed_value=row.get("product_name") or binding.product_id,
            status=TruthStatus.VERIFIED,
            scope=FactScope.CATALOG,
            provenance_type="RETRIEVAL_DB",
            evidence_refs=[_evidence_ref("product_name", binding)],
        ),
        Fact(
            fact_id=_stable_id("fact", {"offer": binding.offer_id, "field": "price"}),
            label="price",
            typed_value={"amount_paise": int(row["price_paise"]), "currency": "INR"},
            status=TruthStatus.VERIFIED,
            scope=FactScope.CATALOG,
            provenance_type="RETRIEVAL_DB",
            evidence_refs=[_evidence_ref("price_paise", binding)],
        ),
    ]
    for label, key in _ATTRIBUTE_FIELDS:
        value = row.get(key)
        if value in (None, ""):
            continue
        facts.append(
            Fact(
                fact_id=_stable_id("fact", {"offer": binding.offer_id, "field": key}),
                label=label,
                typed_value=str(value),
                status=TruthStatus.VERIFIED,
                scope=FactScope.CATALOG,
                provenance_type="RETRIEVAL_DB",
                evidence_refs=[_evidence_ref(key, binding)],
            )
        )
    availability = _availability_vocab(row.get("availability_status"))
    facts.append(
        Fact(
            fact_id=_stable_id("fact", {"offer": binding.offer_id, "field": "availability"}),
            label="availability",
            typed_value=availability,
            status=TruthStatus.VERIFIED if availability != "UNKNOWN" else TruthStatus.UNKNOWN,
            scope=FactScope.CATALOG,
            provenance_type="RETRIEVAL_DB",
            evidence_refs=[_evidence_ref("availability_status", binding)],
        )
    )
    return facts


class RetrievalCatalogAdapter(CatalogSearchPort):
    """Real CatalogSearchPort implementation -- RA Retrieval API for
    ranking, live MySQL for binding resolution and display facts. See the
    module docstring for the full design, confirmed facts, and known
    out-of-scope items.
    """

    def __init__(self) -> None:
        self.search_calls = 0
        self.detail_calls = 0
        self.compare_calls = 0
        self.availability_calls = 0
        self.eligibility_calls = 0

    def search(self, request: SearchRequest, deadline_ms: int) -> SearchResult:
        # deadline_ms is accepted (CatalogSearchPort requires it) but not
        # used to bound the Retrieval API call -- see module docstring's
        # latency note. Turn-level timeout policy belongs to the caller.
        del deadline_ms
        self.search_calls += 1
        hard_filter_hash = _stable_id(
            "hfh", [c.model_dump(mode="json") for c in request.query_state.hard_constraints]
        )
        if request.compatibility_tuple.catalog_version != EXPECTED_CATALOG_VERSION:
            return SearchResult(
                status=ToolStatus.STALE,
                entries=[],
                eligible_count=0,
                hard_filter_hash=hard_filter_hash,
                warnings=["CATALOG_VERSION_MISMATCH"],
            )

        ra_hard_constraints, min_price, dropped = _hard_constraints_to_ra(
            request.query_state.hard_constraints
        )
        payload = {
            "soft_query_text": _soft_query_text(request.query_state),
            "hard_constraints": ra_hard_constraints,
            # Over-fetch: some candidates will be dropped by DB resolution,
            # exclusions, or the min_price post-filter RA can't apply itself.
            "top_n": min(50, max(20, request.top_k * 5)),
        }
        candidates = _call_retrieval_api(payload)
        if candidates is None:
            return SearchResult(
                status=ToolStatus.UNAVAILABLE,
                entries=[],
                eligible_count=0,
                hard_filter_hash=hard_filter_hash,
                warnings=["RETRIEVAL_API_UNAVAILABLE"],
            )

        seen: set[str] = set()
        sku_ids: list[str] = []
        for candidate in candidates:
            sku_id = candidate.get("sku_id")
            if sku_id and sku_id not in seen:
                seen.add(sku_id)
                sku_ids.append(sku_id)
        rows = _load_rows(sku_ids, EXPECTED_CATALOG_VERSION)

        excluded = set(request.exclusions)
        picked: list[tuple[dict[str, Any], dict[str, Any], ProductBinding]] = []
        unresolved = 0
        for candidate in candidates:
            sku_id = candidate.get("sku_id")
            row = rows.get(sku_id) if sku_id else None
            if row is None:
                unresolved += 1
                continue
            if min_price is not None and row["price_paise"] < int(round(min_price * 100)):
                continue
            binding = ProductBinding(
                product_id=row["product_id"],
                sku_id=row["sku_id"],
                offer_id=row["offer_id"],
                catalog_version=row["catalog_version"],
            )
            if excluded & {binding.product_id, binding.sku_id, binding.offer_id}:
                continue
            picked.append((candidate, row, binding))
            if len(picked) >= request.top_k:
                break

        result_set_id = _stable_id(
            "rs", {"bindings": [item[2].model_dump(mode="json") for item in picked]}
        )
        entries: list[SearchEntry] = []
        for position, (candidate, row, binding) in enumerate(picked, start=1):
            entry_id = _stable_id(
                "entry", {"result_set_id": result_set_id, "binding": binding.model_dump(mode="json")}
            )
            rerank_score = float(candidate.get("rerank_score", 0.0))
            matched_terms: list[str] = []
            for evidence in candidate.get("evidence", []) or []:
                matched_terms.extend(evidence.get("query_terms", []) or [])
            entries.append(
                SearchEntry(
                    result_entry_id=entry_id,
                    display_position=position,
                    binding=binding,
                    title=row.get("product_name") or binding.product_id,
                    facts=_facts_from_row(row, binding),
                    score_components={"total": rerank_score, "rerank_score": rerank_score},
                    matched_criteria=sorted(set(matched_terms))[:8],
                    unknown_criteria=[],
                )
            )

        warnings: list[str] = []
        if dropped:
            warnings.append("UNSUPPORTED_HARD_CONSTRAINT")
        if unresolved:
            warnings.append("SKU_NOT_RESOLVED")
        top_score = max((float(c.get("rerank_score", 0.0)) for c in candidates), default=0.0)
        return SearchResult(
            status=ToolStatus.OK if entries else ToolStatus.NOT_FOUND,
            result_set_id=result_set_id,
            entries=entries,
            eligible_count=len(entries),
            confidence_signals={
                "top_score": min(1.0, max(0.0, top_score)),
                "eligible_count": float(len(entries)),
            },
            hard_filter_hash=hard_filter_hash,
            warnings=warnings,
        )

    def get_details(
        self, binding: ProductBinding, compatibility: CompatibilityTuple, deadline_ms: int
    ) -> ProductDetails:
        del deadline_ms
        self.detail_calls += 1
        if compatibility.catalog_version != EXPECTED_CATALOG_VERSION:
            return ProductDetails(status=ToolStatus.STALE, binding=binding)
        row = _load_one(binding.sku_id, binding.catalog_version)
        if row is None or row["offer_id"] != binding.offer_id or row["product_id"] != binding.product_id:
            return ProductDetails(status=ToolStatus.NOT_FOUND, binding=binding)
        return ProductDetails(
            status=ToolStatus.OK,
            binding=binding,
            title=row.get("product_name") or binding.product_id,
            facts=_facts_from_row(row, binding),
            evidence_refs=[_evidence_ref("row", binding)],
        )

    def compare(
        self,
        bindings: Sequence[ProductBinding],
        compatibility: CompatibilityTuple,
        deadline_ms: int,
    ) -> Comparison:
        del deadline_ms
        self.compare_calls += 1
        if compatibility.catalog_version != EXPECTED_CATALOG_VERSION:
            return Comparison(status=ToolStatus.STALE, bindings=list(bindings))
        rows: dict[str, dict[str, Any]] = {}
        for binding in bindings:
            row = _load_one(binding.sku_id, binding.catalog_version)
            if row is None or row["offer_id"] != binding.offer_id:
                return Comparison(status=ToolStatus.NOT_FOUND, bindings=list(bindings))
            rows[binding.offer_id] = row

        field_labels = (
            ("price", "price"),
            ("brand", "brand"),
            ("category", "category"),
            ("rating", "rating"),
            ("availability", "availability"),
        )
        result_rows: list[ComparisonRow] = []
        for field_id, label in field_labels:
            cells: list[ComparisonCell] = []
            for binding in bindings:
                row = rows[binding.offer_id]
                if field_id == "price":
                    value: Any = {"amount_paise": int(row["price_paise"]), "currency": "INR"}
                    status = TruthStatus.VERIFIED
                elif field_id == "availability":
                    value = _availability_vocab(row.get("availability_status"))
                    status = TruthStatus.VERIFIED if value != "UNKNOWN" else TruthStatus.UNKNOWN
                else:
                    raw = row.get(field_id)
                    value = str(raw) if raw not in (None, "") else None
                    status = TruthStatus.VERIFIED if value is not None else TruthStatus.UNKNOWN
                cells.append(
                    ComparisonCell(
                        field_id=field_id,
                        value=value,
                        status=status,
                        evidence_refs=[_evidence_ref(field_id, binding)],
                    )
                )
            result_rows.append(ComparisonRow(field_id=field_id, label=label, cells=cells))
        return Comparison(status=ToolStatus.OK, bindings=list(bindings), rows=result_rows)

    def check_availability(
        self, binding: ProductBinding, compatibility: CompatibilityTuple, deadline_ms: int
    ) -> Availability:
        del deadline_ms
        self.availability_calls += 1
        if compatibility.catalog_version != EXPECTED_CATALOG_VERSION:
            return Availability(
                status=ToolStatus.STALE,
                binding=binding,
                availability_status="UNKNOWN",
                truth_status=TruthStatus.UNKNOWN,
            )
        row = _load_one(binding.sku_id, binding.catalog_version)
        if row is None or row["offer_id"] != binding.offer_id:
            return Availability(
                status=ToolStatus.NOT_FOUND,
                binding=binding,
                availability_status="UNKNOWN",
                truth_status=TruthStatus.UNKNOWN,
            )
        status = _availability_vocab(row.get("availability_status"))
        return Availability(
            status=ToolStatus.OK,
            binding=binding,
            availability_status=status,
            quantity=int(row["quantity"]) if row.get("quantity") is not None else None,
            truth_status=TruthStatus.VERIFIED if status != "UNKNOWN" else TruthStatus.UNKNOWN,
            scope=FactScope.CATALOG,
            provenance_type="RETRIEVAL_DB",
            evidence_refs=[_evidence_ref("availability_status", binding)],
        )

    def check_eligibility(
        self, binding: ProductBinding, purpose: str, deadline_ms: int
    ) -> CommerceEligibility:
        del deadline_ms
        self.eligibility_calls += 1
        if binding.catalog_version != EXPECTED_CATALOG_VERSION:
            return CommerceEligibility(
                eligible=False,
                binding=binding,
                policy_status="STALE",
                reasons=["CATALOG_VERSION_MISMATCH"],
            )
        row = _load_one(binding.sku_id, binding.catalog_version)
        if row is None or row["offer_id"] != binding.offer_id or row["product_id"] != binding.product_id:
            return CommerceEligibility(
                eligible=False,
                binding=binding,
                policy_status="BLOCKED",
                reasons=["BINDING_NOT_FOUND"],
            )
        availability = _availability_vocab(row.get("availability_status"))
        price = Money(amount_paise=int(row["price_paise"]))
        # Purpose-aware, matching DeterministicCatalog's own CART_UPDATE
        # rule: DETAILS/other purposes don't require in-stock, adding to
        # the real cart does.
        if purpose == "CART_UPDATE" and availability != "AVAILABLE":
            return CommerceEligibility(
                eligible=False,
                binding=binding,
                policy_status="UNAVAILABLE",
                price=price,
                availability_status=availability,
                reasons=["OUT_OF_STOCK"],
            )
        return CommerceEligibility(
            eligible=True,
            binding=binding,
            policy_status="ALLOWED",
            price=price,
            availability_status=availability,
            reasons=[],
        )


__all__ = ["RetrievalCatalogAdapter", "EXPECTED_CATALOG_VERSION", "RETRIEVAL_API_URL"]
