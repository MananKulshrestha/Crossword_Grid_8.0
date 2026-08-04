# Integration Guide — `RA/` Retrieval Subsystem

This document is for a coding agent that needs to **use** this folder's
retrieval subsystem — not rebuild it. It assumes no prior context beyond
being able to read Python. For design rationale/"why", see `IMPLEMENTATION.md`
and `lightrag-implementation.md`; for the target architecture this folder
implements, see `retrieval-architecture.md`. This doc only covers: what data
went where, how to stand the system up from a backup zip, and exactly which
functions to call to integrate it into a larger pipeline.

---

## 1. What was built — where the data lives and how it's retrieved

### The source corpus (one text corpus, shared by two branches)

Every product has exactly two representations, and only two:

1. **Structured facts** — `product_metadata` (a MySQL table): `sku_id`,
   `product_name`, `brand`, `category`, `category_is_fallback`,
   `subcategory_path`, `material`, `size`, `retail_price`,
   `discounted_price`, `rating`, `stock_status`
   (`in_stock`/`low_stock`/`out_of_stock`), `quantity`. This is the **only**
   authoritative source for anything checkable/filterable — price, stock,
   category, size, brand.
2. **Free text** — `flipkart_lightrag_corpus.md`, one block per SKU:
   `product_name + description` only. No brand/category/material/price is
   folded into this text. Both text-search branches below (BM25 and
   LightRAG) index this *exact same* text — never two different blobs for
   the same SKU.

`load_documents.py`'s `build_documents()` is the single parser for this
corpus; both `bm25_index.py` and `ingest.py` call it, so BM25 and LightRAG
are always looking at identical per-SKU text.

### Where ingestion put things

| Store | What's in it | Coverage | Written by |
|---|---|---|---|
| **MySQL** (`product_metadata`) | Structured fields (see above) | Full catalog (~20,000 SKUs) | External loader (`../flipkart_metadata.sql`), not this folder |
| **BM25 index** (`RA/bm25_storage/index.pkl`, pickle) | Tokenized `product_name + description` per SKU, inverted index (`rank_bm25.BM25Okapi`) | Full catalog (every SKU with a usable description, ~19,996–19,998) | `bm25_index.py` |
| **Qdrant** — 3 collections, `lightrag_vdb_{chunks,entities,relationships}_<embed-model>_<dim>d` | Vector embeddings | `chunks`: **full catalog**, every SKU's chunk gets embedded regardless of graph membership. `entities`/`relationships`: **subset only** (2,673 SKUs, 13.4% of catalog, chosen by `graph_sampling.py` for structured-metadata coverage — see below) | `ingest.py` (via LightRAG's `QdrantVectorDBStorage`) |
| **`RA/lightrag_storage/`** (local files: NetworkX graph, KV stores, `doc_status`) | The knowledge graph itself (entities + relationships, subset SKUs only), raw doc text, chunk metadata, `doc_status` (`PENDING`/`PROCESSING`/`PROCESSED`/`FAILED` per SKU), LLM response cache | Graph: subset only. `doc_status`/KV: full catalog (tracks every SKU attempted, whether or not it got graph extraction) | `ingest.py` |

**Why only a subset (2,673 SKUs) has entities/relationships**: full
extraction over all ~20,000 products was estimated at multiple days of LLM
calls. `graph_sampling.py` instead picks SKUs by *coverage* — every
category, every brand with enough SKUs to ever form a relationship, every
common material, every occasion/style keyword — so the graph still has full
representational coverage of the catalog's structure, just not every SKU as
a node. Every SKU **not** in the subset is still chunk-embedded into Qdrant
(`skip_kg` — embedding happens, LLM extraction doesn't), so vector/BM25
search still covers 100% of the catalog; only *graph traversal* (entity/
relationship matching) is subset-limited.

The subset itself is already computed and checked in:
`graph_sampling_output/full_extraction_skus.txt` (one SKU ID per line) +
`graph_sampling_output/coverage_report.json` (coverage stats). No LLM
involved in producing it — pure structured-metadata analysis. Only re-run
`python graph_sampling.py` if the underlying catalog changes.

**Entity/relationship schema actually extracted** (`config.py`'s
`ENTITY_TYPES_GUIDANCE`, enforced via LightRAG's `addon_params`):

- Entity types: `Product`, `Brand`, `Category`, `Material`, `Color`,
  `Feature`, `Technology`, `CompatibleItem`, `Audience`, `Certification`,
  `Warranty`.
- Relationship keywords (fixed vocabulary, nothing else is emitted):
  `HAS_BRAND`, `HAS_CATEGORY`, `HAS_MATERIAL`, `HAS_COLOR`, `HAS_FEATURE`,
  `USES_TECHNOLOGY`, `COMPATIBLE_WITH`, `TARGETED_AT`, `HAS_CERTIFICATION`,
  `HAS_WARRANTY`.
- Each relationship is `["source", "target", "keyword", "description", weight]`
  — `weight` is `0.9` (stated fact) or `0.7` (inferred).

If you're building downstream logic that filters/groups on relationship
type, these ten keywords are the entire vocabulary — nothing else will ever
appear.

### How retrieval works at query time

Three **independent** branches, each queryable on its own (no combined
entrypoint exists yet in this folder — see section 3's "what's NOT built"):

1. **SQL hard filter** (`sql_filter.py`) — deterministic eligibility gate.
   `eligible_skus(hard_constraints)` runs a parametrized `WHERE` against
   `product_metadata`. This is the **only mandatory intersection** — per
   `retrieval-architecture.md`, any candidate from BM25/LightRAG that isn't
   in this set must be dropped, regardless of its lexical/semantic score.
2. **BM25 lexical search** (`bm25_index.py`) — `search(query_text, top_n=50)`
   returns `[(sku_id, bm25_score), ...]` sorted descending. Exact-token
   recall (brand spellings, model codes, fabric words) that embeddings can
   smooth over or drop.
3. **Semantic/graph search** (`query.py` / `ingest.py`'s `build_rag()`) —
   LightRAG's `mode="mix"` query, which internally runs three passes: raw
   chunk-vector search (full catalog), entity-level match (`local`, subset
   only), relationship/thematic match (`global`, subset only). Called via
   `rag.aquery(question, param=QueryParam(mode="mix", only_need_context=True))`.
   `only_need_context=True` is important: it returns the **raw retrieved
   context** (chunks/entities/relationships text, not a generated answer) —
   LLM answer synthesis is explicitly left to the outer chat/response layer,
   not this retrieval step.

Per `retrieval-architecture.md`'s intended fusion (not yet built as code in
this folder — see section 3): BM25 ∪ semantic candidates (union — a SKU only
needs one branch to surface it) → intersect with the SQL eligible set
(mandatory, unconditional) → cross-encoder rerank against the full original
query text → return top-N. If you are the agent asked to build this fusion
step, that's the exact order; don't intersect BM25∩semantic, and don't skip
the SQL intersection.

---

## 2. Setting up from a `backup.sh`-produced `.zip`

`backup.sh` produces `backups/lightrag_backup_<timestamp>.zip` containing:
`lightrag_storage/` (graph + KV + doc_status + LLM cache),
`graph_sampling_output/` (the subset selection), `config.py`, `servers.txt`,
`qdrant_data.tar.gz` (a tarball of Qdrant's `/qdrant/storage`, pulled live
from the Docker volume), and `BACKUP_INFO.txt`. It does **not** include the
source corpus files (`flipkart_lightrag_corpus.md`,
`flipkart_catalog_structured.jsonl`) or the Python environment.

### Fastest path: use `import.sh`

```bash
cd RA
./import.sh lightrag_backup_20260804_145154.zip
# or, if the zip is sitting in RA/backups/, a bare filename also resolves:
./import.sh lightrag_backup_20260804_145154.zip
```

What it does, in order:

1. Extracts the zip to a temp dir, prints `BACKUP_INFO.txt`.
2. Restores `lightrag_storage/` — **refuses to silently clobber** an
   existing non-empty `lightrag_storage/`; prompts y/N first.
3. Restores `graph_sampling_output/`.
4. Restores `config.py` / `servers.txt` — if your local copy differs from
   the backup's, it does **not** overwrite; it writes
   `config.py.fromBackup` / `servers.txt.fromBackup` next to yours for a
   manual diff (this is deliberate: `servers.txt` in particular is almost
   always meant to differ per machine — it lists *your* Ollama server URLs,
   not the backup author's).
5. Restores Qdrant: creates a fresh Docker volume `flipkart_qdrant_data`,
   loads `qdrant_data.tar.gz` into it via a throwaway `alpine` container,
   then starts a `flipkart-qdrant` container (`--restart unless-stopped`,
   ports `6333`/`6334`) pointed at that volume — identical to what
   `README.md`'s manual setup produces. If a container named
   `flipkart-qdrant` already exists, it prompts before removing it.
6. Prints next steps (below).

### After `import.sh` completes

1. **Install dependencies** (not restored by the zip):
   ```bash
   pip install -r requirements.txt   # or: uv sync
   ```
2. **Point `servers.txt` at YOUR Ollama servers** — one URL per line,
   `#`/blank ignored. This machine-specific step is *not* automatic even if
   `import.sh` restored a `servers.txt` (it may have saved it as
   `servers.txt.fromBackup` instead — check). Only needed if you intend to
   run `ingest.py` again (new/failed SKUs); querying an already-built index
   doesn't need Ollama reachable unless `EMBED_BACKEND=ollama`.
3. **Verify Qdrant is actually serving the restored data**:
   ```bash
   curl http://localhost:6333/collections
   ```
   Should list three collections (`..._chunks_...`, `..._entities_...`,
   `..._relationships_...`) with non-zero `points_count`. Cross-check counts
   against `python check_ingestion_status.py` (below).
4. **Get the source corpus files** — `flipkart_lightrag_corpus.md` and
   `flipkart_catalog_structured.jsonl` must be present at the repo root
   (`../` relative to `RA/`, per `config.py`'s `SOURCE_MD`/`SOURCE_JSONL`)
   for anything that re-reads the corpus (`bm25_index.py` build,
   `graph_sampling.py`, a resumed `ingest.py`). These are **not** part of
   the backup — copy them from wherever the rest of the project keeps them.
5. **Provision MySQL** (`product_metadata`) separately — also not part of
   this backup. See `README.md`'s "Provision Qdrant and MySQL" section and
   `Vatsalya Version/README.md` for connection details
   (`FLIPKART_DB_HOST`/`PORT`/`USER`/`PASSWORD`/`NAME` env vars,
   defaults `127.0.0.1:3307`, db `flipkart`).
6. **Sanity-check the restored state**:
   ```bash
   python check_storage.py              # doc/entity/relation counts from lightrag_storage/
   python check_ingestion_status.py     # graph doc_status vs Qdrant collection point counts, side by side
   python query.py "a representative question"   # confirms end-to-end query works
   ```
7. If you need to keep ingesting (new SKUs, or resuming interrupted/failed
   ones), `ingest.py` is checkpoint/resume-safe — a plain
   `python ingest.py` picks up exactly where the backup left off; it never
   redoes already-`PROCESSED` SKUs, and re-resets any `FAILED` SKU to
   `PENDING` for retry on every invocation (see `IMPLEMENTATION.md` #5 for
   the full mechanics if something looks stuck).

### Manual setup without `import.sh` (equivalent steps, if you need control)

```bash
unzip lightrag_backup_<ts>.zip -d /tmp/restore
cp -r /tmp/restore/lightrag_storage RA/
cp -r /tmp/restore/graph_sampling_output RA/
# review config.py / servers.txt manually before copying over your own
docker volume create flipkart_qdrant_data
docker run --rm -v flipkart_qdrant_data:/qdrant/storage -v /tmp/restore:/backup \
  alpine sh -c "cd /qdrant/storage && tar xzf /backup/qdrant_data.tar.gz"
docker run -d --name flipkart-qdrant --restart unless-stopped \
  -p 6333:6333 -p 6334:6334 -v flipkart_qdrant_data:/qdrant/storage qdrant/qdrant
```

---

## 3. Function reference — what to call, when, and why

Everything below is importable directly (`from RA.<module> import <fn>` or,
if `RA/` itself is your working/importable root, `from <module> import <fn>`
— every script in this folder assumes it's run/imported with `RA/` as
`cwd`/on `sys.path`, since `config.py` and sibling modules use bare imports
like `from config import ...`).

### 3.1 SQL hard filter — `sql_filter.py`

```python
from sql_filter import eligible_skus

rows = eligible_skus({
    "max_price": 1500,        # optional: discounted_price <= 1500
    "category": "Shirts",     # optional: exact match
    "size": "M",               # optional: exact match
    "stock_status": "in_stock" # optional: exact match, one of in_stock/low_stock/out_of_stock
})
# rows: list[dict], each a full product_metadata row (sku_id, product_name,
# brand, category, ..., quantity). Empty dict {} -> returns every row.
```

**When to call**: first, on every `search_catalog`-style query, to compute
the eligible-SKU set. This result is the **mandatory intersection** every
other branch's candidates must pass through — never skip it, never relax it
downstream. **Unknown keys raise `ValueError` immediately** (not silently
ignored) — only pass the four supported keys. Reads MySQL live on every
call; there's no caching layer, so call it once per query, not per
candidate.

### 3.2 BM25 lexical search — `bm25_index.py`

```python
from bm25_index import search

results = search("cotton round neck shirt", top_n=50)
# results: list[tuple[str, float]] = [(sku_id, bm25_score), ...], sorted
# descending by score. top_n defaults to 50 if omitted.
```

**When to call**: pass it `soft_query_text` — the free-text part of the
user's query, never the hard-constraint fields (those go to `sql_filter`
only). The index lazy-loads/builds on first call in a process (module-level
cache: `_sku_ids`, `_bm25`) — first call in a fresh process pays the
load/build cost, subsequent calls in the same process are instant.

**Building/rebuilding the index** (not needed if `bm25_storage/index.pkl`
already exists and the corpus file hasn't changed — `search()` handles that
check automatically via mtime comparison):

```bash
python bm25_index.py --build   # explicit build + summary print
python bm25_index.py "some query"   # CLI query, prints score + sku_id per line
```

There is no need to call a separate "build" function before `search()` in
code — `search()` calls `_load_or_build_index()` internally on first use.
Only run `--build` explicitly if you want the summary output or want to
force a rebuild ahead of time (e.g. warm the cache before serving traffic).

### 3.3 Semantic/graph search — `ingest.py` (`build_rag`) + LightRAG's own API

There is no `semantic_search.py` wrapper yet (see "what's not built" below)
— call LightRAG directly, the same way `query.py` and `web_ui.py` already
do:

```python
import asyncio
from lightrag import QueryParam
from ingest import build_rag

async def semantic_search(question: str):
    rag = await build_rag()
    context = await rag.aquery(
        question, param=QueryParam(mode="mix", only_need_context=True)
    )
    await rag.finalize_storages()
    return context
```

**Critical details for correct integration**:

- `build_rag()` is **async** and does real I/O — it connects to every
  server in `servers.txt` (round-robin load balancer construction, cheap)
  and calls `initialize_storages()` (talks to Qdrant + reads/creates
  `lightrag_storage/` — real I/O, don't call this per-request in a hot
  path; build once, reuse the `rag` instance, see "Reusing the RAG
  instance" below).
- `mode="mix"` is required for the intended three-pass retrieval (chunk
  vector + entity + relationship). Other LightRAG modes (`local`,
  `global`, `naive`, `hybrid`) are not what this pipeline was tuned/tested
  against.
- `only_need_context=True` is required to get raw retrieval context instead
  of an LLM-synthesized answer — **do not drop this flag** unless you
  specifically want LightRAG to also do answer generation (it would call
  the LLM again, adding latency/cost, and per
  `retrieval-architecture.md` that's explicitly the outer chat layer's job,
  not this tool's).
- **Return shape is not yet a clean `[(sku_id, score), ...]` list** —
  `only_need_context=True` returns a context blob (chunks/entities/
  relationships as text/structured content, with SKU references embedded,
  e.g. via the `ids=f"sku-{sku_id}"` scheme `ingest.py` used at insert
  time). If you need a clean candidate list matching `bm25_index.search()`'s
  shape, you must parse this yourself — run one real query, inspect the
  actual returned structure, and extract the `sku-` prefixed IDs from
  whichever field carries them (chunk source ID / entity source-doc
  reference). This was flagged in `plan.md` (Step 6) as unresolved; do this
  investigation before building a merge/rerank layer on top of it.
- Always call `await rag.finalize_storages()` when done with a `rag`
  instance (releases file handles / Qdrant client cleanly) — see every
  existing entrypoint (`query.py`, `ingest.py`) for the pattern.

**Reusing the RAG instance** (avoid rebuilding per query): `web_ui.py`
demonstrates the pattern for a long-lived server —

```python
_rag_instance = None
async def get_rag():
    global _rag_instance
    if _rag_instance is None:
        _rag_instance = await build_rag()
    return _rag_instance
```

If integrating into an async web framework, build once at startup and reuse;
if integrating into a sync framework (Flask, per `web_ui.py`), you need to
run the coroutine in its own event loop per call/thread — see
`web_ui.py`'s `run_async_in_thread()` helper for a working example.

### 3.4 Ingestion — `ingest.py` (only relevant if you're adding new products)

```bash
python ingest.py
```

Not typically called by downstream integration code directly — this is a
standalone batch job, not a per-request function. Relevant if your pipeline
needs to **add new SKUs** to the index. Key behaviors an integrating agent
should know:

- Reads the entire corpus via `build_documents()`, diffs against what
  LightRAG's `doc_status` already knows (`filter_keys`) — only genuinely new
  SKUs get enqueued; nothing already `PROCESSED` is redone.
- Decides `process_options` per SKU from
  `graph_sampling_output/full_extraction_skus.txt`
  (`load_full_extraction_skus()`) — if you add new SKUs and want some of
  them in the graph-extraction subset, you'd need to re-run
  `graph_sampling.py` first (regenerates the subset from current
  structured metadata) before running `ingest.py`.
- Safe to re-run any time, including after a hard interrupt — see
  `IMPLEMENTATION.md` #5 for the full checkpoint/resume mechanics. Exits
  non-zero and prints every failed `sku_id` + error if anything failed; does
  not substitute placeholder data.
- Requires: every `servers.txt` server reachable with `LLM_MODEL` pulled
  (unless `EMBED_BACKEND="local"`, in which case embedding needs no Ollama
  server at all — see `config.py`), and Qdrant reachable at `QDRANT_URL`.

### 3.5 Status/diagnostic helpers (read-only, safe to call anytime)

| Function/script | Returns | Use for |
|---|---|---|
| `check_storage.py`'s `count_documents()`, `count_nodes()`, `count_edges()`, `count_graph_stats()` | ints (doc/entity/relation counts, graph node/edge counts) | Quick health check of `lightrag_storage/` without touching Qdrant or Ollama |
| `check_ingestion_status.py`'s `get_graph_stats()`, `get_qdrant_stats()` | dicts (full-extraction vs embedding-only doc counts; per-collection Qdrant point counts) | Confirming ingestion actually reached both the graph and Qdrant, side by side |
| `diagnose.py` (run as a script) | prints Qdrant/Ollama connectivity + sample `FAILED` doc error messages | Debugging a stuck/failed ingestion run |
| `graph_sampling.py`'s `coverage_report.json` (read directly, no function call needed) | JSON: catalog size, subset size, per-category/brand/material coverage | Understanding how much of the catalog the graph branch actually covers before trusting `local`/`global` mode results for a given SKU |

None of these require Ollama; `check_ingestion_status.py`'s Qdrant half
needs Qdrant reachable, `check_storage.py` only reads local files.

### 3.6 Visualization (human-facing, not for programmatic integration)

- `graph_visualizer.py` — Streamlit app (`streamlit run graph_visualizer.py`),
  browses the graph visually.
- `web_ui.py` — Flask app (`python web_ui.py`, serves on `:8000`), a
  browser-based query playground (`/api/query` POST endpoint if you want a
  quick HTTP-callable smoke test of `mode="mix"` queries without writing
  Python — but this is a debug tool, not a production integration surface;
  it holds one global unlocked `rag` instance behind a naive threading lock,
  not designed for concurrent production load).

### 3.7 What's NOT built yet — don't assume these exist

If asked to integrate `search_catalog(query_state)` end-to-end, know that
these pieces from `retrieval-architecture.md` / `plan.md` are **not yet
implemented** in this folder as of this writing:

- **`semantic_search.py`** — a clean wrapper around section 3.3 above
  returning `[(sku_id, score), ...]` (same shape as `bm25_index.search()`).
  You'll need to build this (resolve the "what field carries the SKU ID
  back" question first, per 3.3's note).
- **`merge.py` / `fuse_and_rerank()`** — the union(BM25, semantic) →
  intersect(eligible) → cross-encoder rerank pipeline described in section
  1. Not built. `sentence-transformers`'s `CrossEncoder` (a BGE reranker
  model) is the specified reranker, not yet wired in.
- **`search_catalog(query_state)`** itself — the actual external-contract
  entrypoint that would split `query_state` into hard constraints (→
  `sql_filter`) and soft query text (→ the merge step), and shape the
  output. Not built.

Building these three, in that order, is what turns this folder's three
standalone branches into the single tool `retrieval-architecture.md`
specifies. `plan.md` (Steps 6–8) has the exact intended signatures and test
strategy if you're picking this up.
