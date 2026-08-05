# RA search_catalog API — how it works, and how to call it

This document explains `api_server.py`: an HTTP wrapper around this
folder's retrieval pipeline (`search_catalog.py`), listening on **port
8002**. It's written for a coding agent (or a human) that needs to call
this service from outside the `RA/` folder without importing its Python
code directly.

If you just want to start it and send a query, skip to
["Running it"](#running-it) and ["Calling it"](#calling-it).

---

## 1. What this actually is

Three independent retrieval branches feed one reranker:

```
                     ┌─────────────────┐
 hard_constraints ──►│ sql_filter.py    │──► eligible SKUs (MySQL,
 (max_price,         │ (MySQL)          │     mandatory gate, never
  category, size,    └─────────────────┘     relaxed)
  stock_status)

                     ┌─────────────────┐
 soft_query_text ───►│ bm25_index.py    │──┐
                     │ (lexical)        │  │
                     └─────────────────┘  │   union by sku_id
                                           ├──► ∩ eligible SKUs ──► cross-
                     ┌─────────────────┐  │      encoder rerank
 soft_query_text ───►│ semantic_search  │──┘      (merge.py)
                     │ .py (LightRAG:   │
                     │  Qdrant vectors  │
                     │  + knowledge     │
                     │  graph, "mix"    │
                     │  mode)           │
                     └─────────────────┘
```

- **`sql_filter.py`** — the *only* hard gate. Anything that fails
  `max_price` / `category` / `size` / `stock_status` is dropped
  unconditionally, regardless of text-match score. Talks to MySQL directly
  (`FLIPKART_DB_*` env vars), a fresh connection per call.
- **`bm25_index.py`** — exact-token lexical search (`rank_bm25`) over the
  full product catalog text. No LLM cost, so it covers every SKU, not just
  the ones with full LightRAG graph extraction.
- **`semantic_search.py`** — wraps the LightRAG index built by
  `ingest.py` (Qdrant vector store + knowledge graph) in "mix" mode, and
  adapts its chunk/entity/relationship hits into the same candidate
  currency BM25 uses.
- **`merge.py`** — unions BM25 ∪ semantic by `sku_id` (a SKU only needs
  *one* branch to hit), intersects with the SQL-eligible set, then runs a
  single cross-encoder pass (`BAAI/bge-reranker-base` by default) over the
  survivors against the full query text. **This cross-encoder score is the
  only thing that orders the final output** — BM25 and semantic scores are
  never compared to each other, only kept as evidence.
- **`search_catalog.py`** — the one function meant to be called from
  outside this folder. Everything above is an internal building block;
  `search_catalog(query_state, top_n)` is the contract. `api_server.py`
  does nothing but parse an HTTP request into that dict and serialize the
  result back to JSON — it adds no logic of its own.

Full design rationale: `retrieval-architecture.md` and `plan.md` in this
folder. The already-written spec for how an *outside* system (an agentic
chat orchestrator) should consume this is `AGENTIC_INTEGRATION.md` —
section 7 of that doc leaves "in-process import vs. vendored copy vs. HTTP
microservice boundary" as an open question; `api_server.py` is the HTTP
microservice option.

---

## 2. Why warm-up exists (and what it does / doesn't cover)

`web_ui.py` (the existing graph-visualizer + query UI on port 8000) builds
its LightRAG instance once, synchronously, **before** `app.run()`:

```python
# web_ui.py, __main__ block
print("Warming up query engine (first-time model import/load can take a minute or two)...")
run_async_in_thread(get_rag())
print("Query engine ready.\n")
```

The reason (from `web_ui.py`'s own comments): a cold `torch` +
`sentence-transformers` import plus the first local embedding model load
measured **~90-100 seconds** on this host (site-packages on a FUSE-mounted
volume). Without warm-up, whichever HTTP request happens to arrive first
eats that entire cost and looks like a hang or a timeout; with warm-up, it
happens once at process start and every real request only pays actual
inference time.

`api_server.py` follows the exact same pattern in `warm_up()`, called
before `app.run()`. It preloads:

| What | Why it's slow cold | Module |
|---|---|---|
| Local sentence-transformers embedder | `torch`/`sentence-transformers` import + HF model load (~90-100s cold) | `local_embed.py` (only if `EMBED_BACKEND=="local"`, the default) |
| BM25 index | Parses the full product corpus and builds `BM25Okapi` from scratch if no up-to-date pickle exists | `bm25_index.py` |
| SKU → corpus text cache | Same corpus parse, separate cache, used by the reranker to build candidate text | `candidate.py` |
| Cross-encoder reranker | Loads `BAAI/bge-reranker-base` (or `LIGHTRAG_RERANK_MODEL`) weights | `merge.py` |

**What warm-up deliberately does *not* cover**, and why that matters for
request latency:

- **MySQL (`sql_filter.py`)** — connects fresh on every call already
  (`_connect()` opens and the caller closes it per request); there's no
  persistent connection to pre-warm.
- **The semantic/LightRAG branch (`semantic_search.py`)** — unlike
  `web_ui.py`, which caches one `_rag_instance` for the process lifetime,
  `semantic_search._search_async()` calls `ingest.build_rag()` and then
  `rag.finalize_storages()` **on every single call**. That means every
  `/api/search` request — not just the first one — pays LightRAG storage
  initialization (Qdrant connection, KV store reads, and if
  `INFERENCE_BACKEND`/`QUERY_EMBED_BACKEND` involve Ollama, a fresh
  `MultiOllamaLoadBalancer` built from `servers.txt`). This is existing
  behavior in `semantic_search.py`, unchanged by `api_server.py` — see
  "Known limitations" below if you're deciding whether to fix this.

---

## 3. Running it

```bash
cd "RA"
./api.sh
```

`api.sh` mirrors `webui.sh`'s launcher: frees port 8002 if something's
already listening there, prefers `uv run` if available, otherwise a
venv/`python3`, and passes `SIGINT`/`SIGTERM` through to the server
process. It does **not** open a browser (this is an API, not a page).

Or run it directly:

```bash
python3 api_server.py            # port 8002
API_PORT=9000 python3 api_server.py   # override the port
```

**Prerequisites** (same as `web_ui.py`/`query.py`, see `README.md` for
setup): MySQL reachable via `FLIPKART_DB_*` env vars, Qdrant reachable at
`QDRANT_URL`, the BM25/LightRAG indexes already built (`ingest.py` has
been run at least once — `bm25_index.py` will build its own index on
first warm-up if missing, but the LightRAG/Qdrant index is not something
this server builds), and, depending on `.env`'s `INFERENCE_BACKEND` /
`QUERY_EMBED_BACKEND`, either DeepInfra credentials or reachable Ollama
servers (`servers.txt`).

Startup prints progress through each warm-up step, then:

```
Starting server on http://0.0.0.0:8002
  POST /api/search   {"soft_query_text": "...", "hard_constraints": {}, "top_n": 20}
  GET  /health
```

---

## 4. Calling it

### `GET /health`

```bash
curl http://localhost:8002/health
# {"status": "ok"}
```

### `POST /api/search`

Request body — this is `search_catalog()`'s `query_state` dict, unchanged:

| Field | Type | Required | Notes |
|---|---|---|---|
| `soft_query_text` | string | **yes** (alias: `query`) | Drives BM25 + semantic search. Never put hard-constraint values here. |
| `full_query_text` | string | no (defaults to `soft_query_text`) | What the cross-encoder scores the merged candidates against. |
| `hard_constraints` | object | no (default `{}`) | Any of `max_price` (number), `category` (string), `size` (string), `stock_status` (string). An unknown key returns `400`. |
| `top_n` | integer | no (default `20`) | Max results returned, post-rerank. |

```bash
curl -X POST http://localhost:8002/api/search \
  -H 'Content-Type: application/json' \
  -d '{
        "soft_query_text": "blue cotton round neck shirt",
        "hard_constraints": {"max_price": 1500, "stock_status": "in_stock"},
        "top_n": 10
      }'
```

Response — a thin wrapper around `search_catalog()`'s own return shape
(see `search_catalog.py`'s docstring for the authoritative field list):

```json
{
  "query": "blue cotton round neck shirt",
  "results": [
    {
      "sku_id": "SKU12345",
      "rerank_score": 5.83,
      "metadata": {
        "sku_id": "SKU12345",
        "product_name": "...",
        "brand": "...",
        "category": "...",
        "material": "Cotton",
        "size": "M",
        "retail_price": 1999.0,
        "discounted_price": 1299.0,
        "rating": 4.2,
        "stock_status": "in_stock",
        "quantity": 12
      },
      "branches": ["bm25", "semantic"],
      "evidence": [
        {"branch": "bm25", "rank": 3, "branch_signal": 8.21, "match_type": "lexical", "query_terms": ["blue", "cotton", "round", "neck", "shirt"]},
        {"branch": "semantic", "rank": 1, "branch_signal": 1.0, "match_type": "chunk", "chunk_id": "...", "snippet": "..."}
      ]
    }
  ]
}
```

Notes for a caller:

- Results are **already sorted** by `rerank_score` descending — no
  further client-side ranking needed.
- An empty `results` list is the only "nothing matched" signal — there is
  no `status` field (this mirrors `search_catalog()`'s own contract:
  "raises on genuine failures rather than returning a degraded/partial
  result").
- `branches` tells you whether a hit came from lexical match, semantic/graph
  match, or both — useful for building an "why was this shown" explanation.
- No timeout/deadline parameter exists at any layer of this pipeline. If
  you need a request budget, enforce it client-side (e.g. an HTTP client
  timeout) — see `AGENTIC_INTEGRATION.md` section 1 for why this is called
  out explicitly there too.

### Errors

| Status | Cause |
|---|---|
| `400` | Missing/invalid `soft_query_text`, non-object `hard_constraints`, non-integer `top_n`, or an unknown `hard_constraints` key (surfaced from `sql_filter.eligible_skus()`'s own `ValueError`). |
| `500` | Anything else — most likely MySQL/Qdrant/Ollama unreachable, or a DeepInfra API error. The server logs the exception + traceback to stdout before responding. |

---

## 5. For a coding agent integrating this elsewhere

- **Treat `/api/search`'s request/response shape as identical to calling
  `search_catalog.search_catalog(query_state, top_n)` directly in Python**
  — because it is; `api_server.py` does no translation. If you're wiring
  this into the agentic chat orchestrator described in
  `AGENTIC_INTEGRATION.md` (its `CatalogSearchPort.search(request,
  deadline_ms) -> SearchResult` seam), you still need the same
  `_query_state_to_ra` / `_ra_result_to_chat` translation functions that
  document proposes in section 5 — just have them call this HTTP endpoint
  instead of importing `search_catalog` in-process. Nothing else about
  that document's recommendation changes.
- **Start the server once, reuse it.** Warm-up is what makes repeated
  calls fast; don't spawn a fresh `api_server.py` process per request. If
  you're scripting against this from another process/agent, poll
  `GET /health` until it responds before sending real queries — warm-up
  can take a minute or two on a cold cache.
- **`hard_constraints` keys are an exact allowlist**, not a general filter
  DSL: `max_price`, `category`, `size`, `stock_status` only (see
  `sql_filter.py`'s `_CONSTRAINT_CLAUSES`). If a caller's query implies a
  constraint outside this set, don't invent a new key — it will `400`.
  Fold it into `soft_query_text` instead, or extend `sql_filter.py` first.
- **This process is stateless per request** except for the warmed-up
  models/indexes — no session state, no query history. Multi-turn
  behavior (refinement, exclusions, etc.) is the caller's responsibility,
  same as `search_catalog()`'s own contract already implies.

---

## 6. Known limitations (not fixed by this API layer)

Carried over unchanged from the underlying pipeline — worth knowing before
you optimize the wrong thing:

- **Semantic branch has no persistent connection.** As noted in §2, every
  `/api/search` call rebuilds and tears down a LightRAG instance
  (`ingest.build_rag()` → `finalize_storages()`) inside
  `semantic_search.py`. This is the dominant per-request latency cost
  after warm-up, and it's not something `api_server.py` changes — fixing
  it would mean caching a `rag` instance the way `web_ui.py` does with
  `_rag_instance`/`get_rag()`, which is a `semantic_search.py` change, not
  an API-layer one.
- **`semantic_search.py` uses `ingest.build_rag()`, not
  `query.py`'s `build_query_rag()`.** Per that module's own docstring,
  this means the semantic branch's query-time LLM calls always go through
  the Ollama cluster (`servers.txt`), not through
  `config.INFERENCE_BACKEND`'s DeepInfra option the way `query.py`/
  `web_ui.py`'s queries do. Confirm `servers.txt` is reachable even if
  your `.env` sets `INFERENCE_BACKEND=deepinfra`.
- **No auth, no rate limiting.** This is a local/internal service, same
  trust model as `web_ui.py` on port 8000 (`host="0.0.0.0"`, no
  authentication). Don't expose port 8002 directly to an untrusted
  network without adding some in front of it.
