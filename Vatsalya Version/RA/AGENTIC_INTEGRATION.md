# Retrieval ↔ Agentic Chat Integration

## Purpose & scope

This document maps how the RA retrieval subsystem (this folder) connects to
the "Agentic Chat" orchestrator repo (`chat-agentic-docs`, package
`fkgrid`). It is written for whoever wires the two together.

**What this is:** a precise map of (1) RA's actual external contract today,
(2) the actual, live integration seam in the agentic repo today, (3) the
existing pattern in that repo (the cart subsystem's real-adapter) that a new
catalog adapter should be modeled on, and (4) a recommended adapter design
grounded in that pattern — plus an explicit list of gaps that recommendation
does not resolve.

**What this is not:** no code in either repo was changed to produce this
document. Section 6 is a *recommendation*, not existing code — every claim
in this document is labeled either **Confirmed (code today)** or
**Recommendation** / **Open decision**, and nothing is invented as fact.

All `chat-agentic-docs` file paths below are relative to that repo's root
(`E:\chat-agentic-docs`); all other paths are relative to this folder
(`Vatsalya Version/RA/`) unless stated otherwise.

---

## 1. RA side today — `search_catalog`'s real contract

**Confirmed (code today).** The only function meant to be called from
outside this folder is `search_catalog()` (`search_catalog.py:18-58`):

```python
def search_catalog(query_state: dict, top_n: int = 20) -> list[dict]
```

`query_state` (a plain dict, not a class):

| Key | Type | Required | Meaning |
|---|---|---|---|
| `hard_constraints` | `dict` | optional (default `{}`) | Passed straight to `sql_filter.eligible_skus()`. Valid keys: `max_price`, `category`, `size`, `stock_status` (`sql_filter.py:23-28`) — an unknown key raises `ValueError` immediately (`sql_filter.py:52-54`), it is never silently ignored. |
| `soft_query_text` | `str` | **required** | Drives BM25 (`bm25_index.search`) and the semantic branch (`semantic_search.search`) — never the hard-constraint fields. |
| `full_query_text` | `str` | optional (defaults to `soft_query_text`) | What the cross-encoder scores the merged candidate set against (`search_catalog.py:37`). |

Return value: a list of dicts, one per surviving candidate, **already
sorted by `rerank_score` descending** (`merge.py:132`):

| Key | Type | Meaning |
|---|---|---|
| `sku_id` | `str` | Primary key into `product_metadata`. |
| `rerank_score` | `float` | Raw cross-encoder output (see §8, calibration is unresolved). |
| `metadata` | `dict \| None` | The full `product_metadata` SQL row for this SKU (see schema below). |
| `branches` | `list[str]` | Which branch(es) found it: `["bm25"]`, `["semantic"]`, or both. |
| `evidence` | `list[dict]` | One entry per branch hit: `{"branch", "rank", "branch_signal", ...match-specific fields}` (`search_catalog.py:47-55`). |

`metadata`'s real columns (`Vatsalya Version/flipkart_metadata.sql`,
`Vatsalya Version/IMPLEMENTATION.md`):

```
sku_id, product_name, brand, category, category_is_fallback,
subcategory_path, material, size, retail_price, discounted_price,
rating, stock_status, quantity
```

**No `catalog_version`, `offer_id`, or `product_id`-distinct-from-`sku_id`
column exists.** This matters for §7/§8.

`search_catalog` is fully synchronous, has no timeout/deadline parameter,
and raises on genuine failures (e.g. a MySQL connection error) rather than
returning a degraded/partial result — there is no `status` field in its
output at all; an empty list is the only "nothing found" signal.

---

## 2. Agentic side today — the live integration seam

**Confirmed (code today).** The orchestrator is **not** an LLM
function-calling system. The model returns exactly one structured
`IntentDeltaV1` JSON payload per turn; every provider call sends
`"tools": []` literally and strips it if empty
(`src/fkgrid/agentic/gateway.py:396-397,764`). All "tool" calls are the
orchestrator's own deterministic Python dispatch.

The real seam is a `Protocol` (structural interface, not a base class):

```python
# src/fkgrid/agentic/ports.py:134-150
class CatalogSearchPort(Protocol):
    def search(self, request: SearchRequest, deadline_ms: int) -> SearchResult: ...
    def get_details(self, binding, compatibility, deadline_ms) -> ProductDetails: ...
    def compare(self, bindings, compatibility, deadline_ms) -> Comparison: ...
    def check_availability(self, binding, compatibility, deadline_ms) -> Availability: ...
    def check_eligibility(self, binding, purpose, deadline_ms) -> CommerceEligibility: ...
```

Called from `TurnOrchestrator._route` (`orchestrator.py:787-886`), for
`Action.SEARCH`/`Action.REFINE`:

```python
# orchestrator.py:801-811
result = self.catalog.search(
    SearchRequest(session_id=..., query_state=proposed_state, top_k=5,
                  exclusions=[], compatibility_tuple=compatibility),
    self.config.local_tool_budget_ms,
)
```

immediately followed by the confidence gate (`orchestrator.py:846`,
`self.recovery.assess(result, proposed_state)`) and, on a `RECOVER`
decision, `self.recovery.recover(...)` (`orchestrator.py:864-877`).

**Both `TurnOrchestrator.handle` (`orchestrator.py:151`) and the FastAPI
route (`api/main.py:900`) are plain `def`, not `async def`** — confirmed
directly. This means RA's `semantic_search.py`, which internally calls
`asyncio.run(...)`, is safe to call from this chain as-is: there is no
already-running event loop in the calling thread, so no
"`asyncio.run()` cannot be called from a running event loop" conflict.

**Current implementation of the seam:** `self.catalog` is
`CatalogSearchPortAdapter` (`src/fkgrid/tools/compat.py:158-208`), which
wraps `DeterministicCatalog` (`src/fkgrid/tools/catalog.py:45,272-357`, an
in-memory keyword-scoring fixture over ~600 synthetic records), composed in
`build_runtime_tooling()` (`src/fkgrid/tools/integration.py:210-250`) and
injected in `ApiRuntime.create_session` (`api/runtime.py:401-419`, as
`catalog=self.tooling.shopper_catalog`).

**A second, parallel mechanism exists but is not live:**
`src/fkgrid/tools/registry.py` defines a `ToolRegistry` allowlist (73
specs, including `search_catalog` at line 173, bound to `catalog.search` at
line 668). Confirmed via a full-repo grep: `.call()` on this registry is
never invoked from the live request path — only from
`scripts/contract_audit.py` (a standalone smoke-test script) and tests.
**Do not treat this registry as the integration point** — it is an
audit/inventory artifact only.

---

## 3. Two `QueryState`/`SearchResult` schemas already coexist in that repo

**Confirmed (code today).** This is directly relevant because it is the
existing precedent for the translation work a new adapter needs to do.

**A — the orchestrator's own, typed, session-level shape**
(`agentic/contracts.py:271-283`, `792-820`):

```python
class QueryState(StrictModel):
    state_version: int
    hard_constraints: list[Constraint]      # field_id, operator, values, provenance_turn_id, explicit
    soft_preferences: list[Preference]       # field_id, operator, values, weight, comparative_anchor
    query_terms: list[str]
    ...
    catalog_version: str
    index_version: str
    lexicon_version: str

class SearchEntry(StrictModel):
    result_entry_id: str
    display_position: int                    # 1..10
    binding: ProductBinding                  # product_id, sku_id, offer_id, catalog_version
    title: str
    facts: list[Fact]                        # label, typed_value, status, scope, evidence_refs
    score_components: dict[str, float]
    matched_criteria: list[str]
    unknown_criteria: list[str]

class SearchResult(StrictModel):
    status: ToolStatus
    result_set_id: str | None
    entries: list[SearchEntry]
    eligible_count: int
    confidence_signals: dict[str, float]
    hard_filter_hash: str
    warnings: list[str]
    evidence_refs: list[EvidenceRef]
```

`Constraint.operator` is one of `EQ/IN/ALL_OF/GTE/GT/LTE/LT/RANGE`
(`agentic/contracts.py:245-250`).

**B — the simpler, dict-ish shape the current catalog implementation and
the confidence gate actually consume** (`tools/contracts.py:84-125`):

```python
class QueryState(StrictModel):
    text: str = ""
    normalized_terms: list[str] = []
    hard_filters: dict[str, Any] = {}
    soft_terms: list[str] = []
    excluded_terms: list[str] = []
    category_id: str | None = None
    result_set_id: str | None = None

class SearchEntry(StrictModel):
    rank: int
    binding: ProductBinding
    title: str
    category_id: str
    score: float
    score_components: dict[str, float] = {}
    attributes: dict[str, str] = {}
    price: float | None = None
    availability: Literal["AVAILABLE", "UNAVAILABLE", "UNKNOWN"] = "UNKNOWN"
    evidence_refs: list[EvidenceRef] = []

class SearchResult(StrictModel):
    status: Literal["OK", "NO_MATCH", "DEGRADED", "UNAVAILABLE"]
    result_set_id: str
    eligible_count: int
    entries: list[SearchEntry] = []
    confidence_signals: dict[str, float] = {}
    unknown_terms: list[str] = []
    degraded_components: list[str] = []
    query_hash: str
```

`assess_retrieval_confidence()` (`tools/recovery.py:26-64`, confirmed
directly) reads shape **B**: `result.confidence_signals.get("top_score",
0.0)`, `result.unknown_terms`, `query_state.hard_filters`.

**Existing translation functions between A and B**
(`tools/interop.py`, both confirmed directly):

- `query_state_from_worktree(value)` (lines 185-277) — flattens A's typed
  `hard_constraints`/`soft_preferences` into B's `hard_filters` dict/
  `soft_terms` list. E.g. a `price`/`LTE` constraint becomes
  `hard_filters["max_price"]`; a `category`/`ALL_OF` (multi-value) becomes
  an `"unsupported_constraints"` entry instead of silently dropped.
- `search_result_to_chat(result, hard_filter_hash=None)` (lines 381-479) —
  turns a **B**-shaped `SearchResult` into the plain-dict shape the
  orchestrator's Pydantic model **A** validates against. Per entry: builds
  `result_entry_id = stable_id("entry", {"result_set_id":...,
  "binding":...})` (a deterministic hash, **not** the raw sku/product ID),
  `display_position = entry.rank`, and one `Fact` dict per structured field
  (title, price, category, each attribute, availability), each carrying
  `status="VERIFIED"` (or `"UNKNOWN"` for unknown availability),
  `scope` (`"CATALOG"` or `"PROTOTYPE"`), and `provenance_type=
  "MOCK_CATALOG"` — that last literal is fixture-specific (see §8).

This A↔B translation pair is the direct precedent for how a new
RA-targeting translation layer should be shaped — not something to invent
from scratch.

---

## 4. The cart subsystem's adapter pattern — the reuse template

**Confirmed (code today).** `DatabaseCartAdapter`
(`src/fkgrid/cart/adapter.py:279-757`) is the one existing example in this
repo of a **real, external-store-backed** adapter satisfying a Port
directly (as opposed to `CatalogSearchPortAdapter`, which wraps an
in-memory fixture through the `tools/` translation layer). Its shape:

1. **Implements the Port directly**, no intermediate layer:
   `class DatabaseCartAdapter(CartPort):` implementing
   `show_cart`/`update_cart` (`CartPort`, `agentic/ports.py:171-174`)
   straight against MySQL — it does not route through `tools/contracts.py`
   or `tools/interop.py` at all.
2. **Connection config via env vars with defaults**
   (`cart/adapter.py:82-86`): `FLIPKART_DB_HOST` (default `127.0.0.1`),
   `FLIPKART_DB_PORT` (default `3307`), `FLIPKART_DB_USER`,
   `FLIPKART_DB_PASSWORD`, `FLIPKART_DB_NAME` (default `flipkart`) — a
   fresh `pymysql.connect(...)` per call, no held connection
   (`_connect()`, lines 89-93). **The adapter's own module docstring
   states this explicitly matches `RA/sql_filter.py`'s `_connect()`-per-call
   pattern** (`cart/adapter.py:53-56`) — i.e. the two repos already
   cross-reference each other's conventions; RA's `sql_filter.py` uses the
   identical env var names and connection pattern today.
3. **Constructed with a closure over session state**, not a cached value:
   `session_snapshot_provider: Callable[[], TurnSnapshot]`
   (`cart/adapter.py:280-298`), read fresh on every call via
   `self._session_snapshot_provider().state_version` — required, not
   optional, since a real adapter has no independent way to know current
   session state.
4. **Selected via a plain string mode switch** inside
   `ApiRuntime.create_session` (`api/runtime.py:393-399`):
   ```python
   if self.cart_mode == "database":
       cart_port = DatabaseCartAdapter(session_snapshot_provider=lambda: session_state.snapshot)
   else:
       cart_port = FixtureCartPort(cart, legacy_catalog, session_snapshot_provider=lambda: session_state.snapshot)
   ```
   `self.cart_mode` itself comes from a constructor parameter
   (`ApiRuntime.__init__`, default `"fixture"`), settable via
   `FKGRID_CART_MODE`.

**What is *not* part of the reusable pattern:** `DatabaseCartAdapter`'s
idempotency-key replay, request-hash deduplication, and optimistic
(`state_version`/`cart_version`) concurrency control (`adapter.py:309-339,
375-412`) exist because cart writes must be safe to retry/replay. Catalog
search is a pure read with no mutation to make idempotent — that machinery
has no analog for the recommendation in §5.

---

## 5. Recommendation — not yet built

Everything in this section is a **proposed design**, grounded entirely in
the pattern confirmed in §4, applied to the seam confirmed in §2. None of
it exists in either repo today.

**A new adapter class**, e.g. `RetrievalCatalogAdapter`, living alongside
the cart adapter's own module (e.g. `src/fkgrid/catalog/adapter.py`,
mirroring `src/fkgrid/cart/adapter.py`'s location), implementing
`CatalogSearchPort` **directly** — bypassing `tools/compat.py`'s
`CatalogSearchPortAdapter` → `DeterministicCatalog` chain the same way
`DatabaseCartAdapter` bypasses `FixtureCartPort`/`tools/`:

```python
class RetrievalCatalogAdapter(CatalogSearchPort):
    def search(self, request: SearchRequest, deadline_ms: int) -> SearchResult:
        query_state = _query_state_to_ra(request.query_state)   # new, see below
        candidates = ra_search_catalog(query_state, top_n=request.top_k)  # RA's search_catalog()
        return _ra_result_to_chat(candidates, request)          # new, see below
```

**Two new translation functions**, modeled directly on §3's existing pair
(`query_state_from_worktree` / `search_result_to_chat`) but targeting RA's
actual dict shapes instead of shape B:

- `_query_state_to_ra(state: QueryState) -> dict` — builds RA's
  `{hard_constraints, soft_query_text, full_query_text}` from the
  orchestrator's typed `QueryState`. See §7's input table for the field-by-
  field mapping this function would implement.
- `_ra_result_to_chat(candidates: list[dict], request: SearchRequest) ->
  SearchResult` — builds a `SearchEntry` per RA candidate (`display_position`
  = list index + 1, since RA's output is already rank-ordered by
  `rerank_score`; `facts` built per `metadata` column following
  `search_result_to_chat`'s existing per-field `Fact`-building pattern) and
  one `SearchResult` wrapping them. See §7's output table.

**Composition**: a new mode switch parallel to `cart_mode`, e.g.
`catalog_mode` (env var `FKGRID_CATALOG_MODE`, default `"fixture"`),
read inside `ApiRuntime.create_session` alongside the existing `cart_mode`
branch (`api/runtime.py:393-399`) and `build_runtime_tooling()`
(`tools/integration.py:210-250`) — an `"retrieval"` (or similarly named)
option would construct `RetrievalCatalogAdapter()` and assign it to
`shopper_catalog` in place of the fixture-backed
`CatalogSearchPortAdapter`.

**Deliberately excluded from this recommendation, unlike the cart
adapter**: idempotency-key replay, request-hash dedup, optimistic
concurrency control. Catalog search is a pure read — there is nothing to
make safely retryable in the way a cart mutation needs to be.

---

## 6. Field-by-field mapping

### Input: orchestrator `QueryState` → RA `query_state` dict

| Agentic field | RA field | Status |
|---|---|---|
| `hard_constraints` where `field_id="price"`, `operator` LTE/LT | `hard_constraints["max_price"]` | **Confirmed mapping** (matches `query_state_from_worktree`'s existing price-operator handling, `tools/interop.py:212-230`) |
| `hard_constraints` where `field_id in {"category","taxonomy_node_id"}` | `hard_constraints["category"]` | **Open decision** — see §7, category identity space differs |
| `hard_constraints` where `field_id="size"` | `hard_constraints["size"]` | **Confirmed mapping** (direct) |
| `hard_constraints` where `field_id="stock"`/`availability` | `hard_constraints["stock_status"]` | **Open decision** — value vocabularies not yet confirmed to match (`in_stock`/`low_stock`/`out_of_stock` vs. agentic's `AVAILABLE`/`UNAVAILABLE`/`UNKNOWN`) |
| `soft_preferences[].values` + `query_terms` | `soft_query_text` (joined string) | **Open decision** — RA expects free text, agentic's `query_terms` is a max-10-token list; needs a defined join rule |
| full raw user message / merged terms | `full_query_text` | **Open decision** — no single existing agentic field is "the full original query text" verbatim; `compact_goal_summary` (`agentic/contracts.py:283`) is the closest candidate but is a summary, not the literal utterance |
| `top_k` | `search_catalog`'s `top_n` parameter | **Confirmed mapping** (direct pass-through) |

### Output: RA candidate dict → agentic `SearchEntry`/`Fact`

| RA field | Agentic field | Status |
|---|---|---|
| list index (already rank-ordered) | `display_position` | **Confirmed mapping** (RA's output is pre-sorted by `rerank_score` descending, `merge.py:132`) |
| `sku_id` | `binding.sku_id` | **Confirmed mapping** |
| — (no such field in RA) | `binding.product_id`, `binding.offer_id` | **Open decision** — RA's schema has no distinct product_id or offer_id concept; likely `product_id = sku_id`, `offer_id` has no source at all |
| — (no such field in RA) | `binding.catalog_version` | **Open decision** — see §8, no source value exists in RA's schema today |
| `metadata["product_name"]` | `title`, and a `Fact` (`label="Title"`) | **Confirmed mapping**, following `search_result_to_chat`'s existing title-`Fact` pattern (`tools/interop.py:389-402`) |
| `metadata["discounted_price"]` | a price `Fact` | **Confirmed mapping**, following the existing price-`Fact` pattern (`tools/interop.py:403-420`) — note agentic's price `Fact` uses `amount_paise` (integer paise), RA's is a decimal rupee value; needs a unit conversion, not just a rename |
| `metadata["category"]`, `["brand"]`, `["material"]`, `["subcategory_path"]` | attribute `Fact`s | **Confirmed mapping**, following the existing attribute-`Fact` pattern (`tools/interop.py:436-450`) |
| `metadata["stock_status"]` | availability `Fact` | **Open decision** — vocabulary mismatch, same as the input-side row above |
| `rerank_score` | `score_components["total"]` or similar, and `confidence_signals["top_score"]` | **Open decision** — see §8, calibration unresolved |
| `branches`, `evidence` | `matched_criteria`, `Fact.evidence_refs` | **Open decision** — RA's `evidence` dicts have no `EvidenceRef`-shaped data (`evidence_id`/`source_type`/`version`); would need new synthetic evidence IDs, not a direct field copy |
| — | `Fact.provenance_type` | **Open decision** — existing fixture uses the literal `"MOCK_CATALOG"` (`tools/interop.py:396`); a real adapter needs a different, not-yet-chosen value |
| (implicit: candidate list non-empty/empty) | `SearchResult.status` (`"OK"`/`"NO_MATCH"`) | **Confirmed mapping** for the empty/non-empty case; **open decision** for `"DEGRADED"`/`"UNAVAILABLE"`, since RA has no way to signal a partial failure today (it either returns a list or raises) |
| (none — RA computes no such value) | `SearchResult.result_set_id` | **Open decision**, though a reasonable default exists: hash the returned `sku_id` list, following `CatalogSearchPortAdapter`'s own existing `stable_id("rs", {...})` pattern (`tools/compat.py:180-190`) |

---

## 7. Confirmed gaps / open design decisions

Kept separate from §5's recommendation deliberately — none of these are
resolved by "wrap it in an adapter."

- **Confidence-score calibration.** RA's `rerank_score` is a raw BGE
  cross-encoder output (`merge.py:126-130`) — not guaranteed to fall in
  `[0,1]`. `assess_retrieval_confidence()`'s thresholds (`0.25`, `0.9`,
  `tools/recovery.py:38-51`) were calibrated against the fixture's
  `top_hit_score/4.0` heuristic (`tools/catalog.py`, per the earlier
  exploration of that file). Piping `rerank_score` in unnormalized would
  likely miscalibrate every `ACCEPT`/`CLARIFY`/`RECOVER` decision. Needs a
  normalization step (e.g. min-max or sigmoid) — not yet decided where it
  should live (the new adapter vs. RA's own `Candidate`).
- **`catalog_version`/`offer_id`/distinct `product_id`.** Absent from
  RA's `product_metadata` schema entirely (§1). This directly collides
  with an already-known, already-documented bug in the agentic repo:
  `api/runtime.py:174-185`'s comment states the real MySQL-backed cart
  validates `catalog_version="flipkart_v1"` while the fixture catalog
  returns `"catalog-fixture-v1"`, so every `ADD_ITEM` from a live search
  result is rejected `OFFER_UNAVAILABLE` until this is resolved. Wiring in
  RA without addressing this reproduces that exact failure mode.
- **`unknown_terms`.** RA computes nothing resembling "which query terms
  matched nothing," which `assess_retrieval_confidence`'s `RECOVER` path
  depends on (`tools/recovery.py:38-44`). Not yet decided whether to
  approximate it (e.g. diff BM25 tokens against matched evidence) or pass
  `[]` (silently disabling that recovery path).
- **Category identity.** Agentic's `hard_filters["category_id"]` reads as
  a taxonomy-node reference; RA's SQL filter matches a plain `category`
  string (with a `category_is_fallback` flag, `product_metadata` schema,
  §1). Not confirmed to be the same identifier space in either repo.
- **Latency / `deadline_ms`.** `CatalogSearchPort.search` takes a
  `deadline_ms` budget (§2); RA's `search_catalog()` has no timeout
  awareness at all, and its semantic branch rebuilds the entire LightRAG
  object (Qdrant connection, storage init) on every call
  (`semantic_search.py`). Not a correctness blocker (§2's async/sync
  finding), but a real latency gap against the budget the orchestrator
  expects to enforce.
- **Packaging boundary — the most load-bearing open question.** Neither
  repo shows any evidence today of how `chat-agentic-docs` would actually
  reach RA's Python code at runtime: no cross-repo dependency declaration,
  no HTTP client anywhere pointing at a retrieval service, no shared
  installable package. This document does not invent an answer (in-process
  import via a path dependency, a vendored copy, or an HTTP microservice
  boundary are all structurally possible) — it must be decided before
  §5's adapter can be written for real.
