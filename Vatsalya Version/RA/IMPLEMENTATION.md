# Implementation Notes — `RA/` LightRAG Semantic/Graph Branch

This folder now holds all **three** retrieval branches described in
`retrieval-architecture.md` (see `~/Downloads/retrieval-architecture.md`,
or wherever that doc lives in the project repo) for the `search_catalog`
tool's internals. It started as just the **semantic/graph branch**, via
LightRAG (see below for that part specifically) — the SQL hard-filter
branch (`sql_filter.py`) and the BM25 lexical branch (`bm25_index.py`)
were added afterward, as separate, independent components per
`plan.md`. The rest of this document, written when only the LightRAG
piece existed, still describes that piece's design decisions accurately;
read `sql_filter.py` and `bm25_index.py` directly for the other two —
their docstrings cover the same ground.

Read `../IMPLEMENTATION.md` first — it explains the upstream pipeline
(`flipkart_to_lightrag.py`) that produces the three source files this
folder consumes. This document picks up from there.

## What exists and what doesn't

**Built:**
- LightRAG ingestion (`ingest.py`) over `../flipkart_lightrag_corpus.md`,
  producing a knowledge graph + vector index via local file storage.
  Checkpoint/resume-safe: a hard interrupt is recovered automatically on
  the next run, and every run explicitly resets any `FAILED` document back
  to `PENDING` first, so a failure is retried on the next invocation
  instead of silently staying failed forever (see design decision #5,
  "Per-document error tracking, no soft fallbacks").
- A `mix`-mode query script (`query.py`) returning raw grounded context.
- Ollama as the LLM/embedding backend (local, no API key, no external
  cost) — deliberately **not** using Gemini or any hosted API, even
  though the source architecture doc mentions Gemini 2.5 Flash as the
  project's default model. This was an explicit choice for this testing
  phase: stay fully local via the project's Ollama cluster.
- **Qdrant-backed vector storage** — `ingest.py`'s `LightRAG(...)` call sets
  `vector_storage="QdrantVectorDBStorage"`. Confirmed working end-to-end
  (construction, `initialize_storages()`, real collection creation, a
  persistence check across a container restart) against a real local Qdrant
  container — verified without needing Ollama at all, since storage
  initialization is independent of the LLM/embedding calls. Three
  collections get created: `lightrag_vdb_chunks_<model>_<dim>d`,
  `..._entities_...`, `..._relationships_...` — see
  `lightrag-implementation.md` section 3.1 for why LightRAG uses three
  separate vector stores, not one.
- **Coverage-based graph-extraction subset** (`graph_sampling.py`) — rather
  than running full entity/relation extraction over all ~20,000 products
  (estimated multiple days of LLM calls), a 2,673-SKU subset (13.4% of the
  catalog) is chosen by *coverage* over structured metadata, not randomly.
  `ingest.py` reads this subset (`graph_sampling_output/full_extraction_skus.txt`)
  to set each SKU's `process_options` — `""` for full extraction, `"!"`
  (LightRAG's native `skip_kg` flag) for everyone else, who still get
  chunk-embedded into Qdrant, just not graph-extracted. Full design and
  measured coverage numbers in `lightrag-implementation.md`, section 5.
- **A real, fixed entity-extraction prompt.** An earlier version passed
  `addon_params={"entity_types": [...]}` — a key `lightrag-hku` never reads
  at all (confirmed by a zero-match source search on the installed 1.5.5
  release), so every extraction would have silently used LightRAG's generic
  default ontology instead of ours. Fixed via
  `addon_params={"entity_types_guidance": ENTITY_TYPES_GUIDANCE}`
  (`config.py`'s `ENTITY_TYPES_GUIDANCE`, wired in `ingest.py`'s
  `build_rag()`) — the key LightRAG's prompt resolver actually reads, no
  YAML file/`PROMPT_DIR` indirection needed. Full before/after example in
  `lightrag-implementation.md`, section 6.
- SQL hard-filter branch (`sql_filter.py`) — `eligible_skus(hard_constraints)`
  against `product_metadata`, tested against the real Docker MySQL
  container (`tests/test_sql_filter.py`).
- BM25 lexical branch (`bm25_index.py`) — indexes every product with a
  usable description (19998 SKUs, all of them, not just a LightRAG test
  batch), persisted under `bm25_storage/`, tested against the real index
  (`tests/test_bm25.py`) with real exact-token queries.

**Not built:**
- The union/intersect/rerank merge step that combines all three branches
  (`retrieval-architecture.md`'s "Candidate fusion & rerank" section).
- Cross-encoder reranking.
- Any wiring into an actual `search_catalog(query_state)` function —
  this folder is still a set of standalone components, not yet plugged
  into a larger agent/tool pipeline.
- **Actual ingestion has still never been run end-to-end** (no
  `lightrag_storage/` or real Qdrant data survives between sessions) — the
  code path, Qdrant wiring, and prompt profile are all verified correct and
  reachable, but the real multi-hour `ingest.py` run itself is blocked on
  the team's shared Ollama server being reachable. That's the one thing
  left for whoever has Ollama access: pull the models (README.md section 2)
  and run `python ingest.py`.

Whoever picks this up next: don't assume LightRAG alone constitutes
`search_catalog`. SQL and BM25 are real, tested branches now — the merge
step still needs to be built to combine all three, per
`retrieval-architecture.md`.

## Design decisions and why (read before changing anything)

### 1. Corpus is description-only — no structured fields folded in

`load_documents.py` builds each document as exactly
`product_name + "\n\n" + description`, parsed straight out of
`flipkart_lightrag_corpus.md`. It does **not** pull in brand, category,
material, or price from `flipkart_catalog_structured.jsonl`.

This was a deliberate fix applied after an earlier version of this
folder *did* inject a "facts line" (brand/category/material/price) ahead
of the description into LightRAG's input text. That was wrong per
`retrieval-architecture.md`'s "One text corpus, two search engines"
section: structured facts live in `product_metadata` and are read from
there, never duplicated into the text corpus that LightRAG (and,
eventually, BM25) index. Duplicating them risks the semantic branch's
graph disagreeing with what the relational store says is authoritative,
and — once BM25 is added — risks the two text-search branches indexing
different content for the same SKU, which the architecture doc calls out
as a real bug class to avoid.

**If you're extending this**: any new field you're tempted to add to the
LightRAG input text should first be checked against
`flipkart_metadata.sql` — if it's already a column there, it does not
belong in the text corpus. The text corpus exists only for what
structured fields can't answer (loose phrasing, use-case language,
descriptive text never worth promoting to a column).

### 2. Entity extraction is scoped to a fixed type list, via the real mechanism

`config.py`'s `ENTITY_TYPES_GUIDANCE` defines the entity-type guidance
(`Product`, `Brand`, `Category`, `Material`, `Color`, `Feature`,
`Technology`, `CompatibleItem`, `Audience`, `Certification`, `Warranty`)
plus a strict 5-field relationship format and a fixed
`relationship_keyword` vocabulary (`HAS_BRAND`, `HAS_CATEGORY`,
`HAS_MATERIAL`, `HAS_COLOR`, `HAS_FEATURE`, `USES_TECHNOLOGY`,
`COMPATIBLE_WITH`, `TARGETED_AT`, `HAS_CERTIFICATION`, `HAS_WARRANTY`).
`ingest.py` passes it straight through as
`addon_params={"entity_types_guidance": ENTITY_TYPES_GUIDANCE}` — an
inline string, no YAML file or `PROMPT_DIR` path resolution involved.

Without this, LightRAG's extraction defaults to a generic ontology
(person, organization, location, event...) which doesn't fit a shopping
catalog and would pollute the graph with irrelevant node types pulled out
of product description text — and that's exactly what would have happened
here: an earlier version of this file passed
`addon_params={"entity_types": ENTITY_TYPES}` (a bare list of type names),
which **lightrag-hku 1.5.5 does not read at all** — confirmed by a
full-package source search returning zero matches for that key anywhere in
the library. The only key LightRAG's prompt resolver actually checks for
this is `addon_params["entity_types_guidance"]` — see
`lightrag-implementation.md` section 6 for the full investigation, and for
a worked before/after example showing what the generic fallback would have
produced on a real product description.

Don't diverge from this type/keyword vocabulary without updating
`retrieval-architecture.md` too, since other branches/agents in the wider
project are expected to rely on the same entity vocabulary.

### 3. Query returns raw context, not a generated answer

`query.py` calls `rag.aquery(question, param=QueryParam(mode="mix",
only_need_context=True))`. Without `only_need_context=True`, LightRAG
would call its LLM again to synthesize prose from the retrieved context
— wrong for this use case, since (per the architecture doc)
response generation belongs to the outer chat/response-writer layer, and
`search_catalog` itself needs to return ranked *candidates*, not text a
user reads directly. Downstream code that eventually merges this branch
with SQL/BM25 candidates should consume `query.py`'s output as retrieval
context, not display it verbatim.

### 4. Ollama, not a hosted API

Extraction (`config.py`: `LLM_MODEL`) goes through local Ollama servers.
This is intentional for this phase — no API keys, no external cost, uses
the project's own GPU cluster. Embedding (`EMBED_MODEL`) may or may not
touch Ollama at all, depending on `EMBED_BACKEND` (below).

**Extraction load-balances across multiple servers; embedding, by
default, doesn't touch Ollama at all.** `multi_ollama.py`'s
`MultiOllamaLoadBalancer` round-robins every `llm_model_func` call
(entity/relation extraction — the expensive, GPU-bound part) across
whichever server URLs are listed in `servers.txt`, one `ollama serve`
process per GPU/GPU-pair. `servers.txt` ships with a multi-server
default, so this works unchanged with as few as one server; adding
more is a `servers.txt` edit only, no code change (`ingest.py`'s
`build_rag()` re-reads it every run). See README.md's "Multi-server
extraction" section for setup.

Embedding is controlled independently by `config.py`'s `EMBED_BACKEND`,
read by `ingest.py`'s `embedding_func()`:
- `"local"` (default) — runs `nomic-ai/nomic-embed-text-v1.5` via
  `sentence-transformers`/`torch` in-process (`local_embed.py`), on this
  machine's MPS/CPU. Removes embedding entirely from the Ollama
  cluster's GPU budget, eliminating any embedding-vs-extraction VRAM
  contention.
- `"ollama"` — calls `OLLAMA_HOST` (`config.py`) for embeddings, pinned
  to that single server regardless of how many extraction servers are
  configured (comparatively cheap, doesn't need the same fan-out).

If this later moves to a hosted model (e.g. matching the wider project's
Gemini 2.5 Flash default per `retrieval-architecture.md`), only
`ingest.py`'s `llm_model_func` / `embedding_func()` wiring needs to
change — LightRAG's model interface is pluggable, nothing else in this
folder assumes Ollama specifically.

**Why `llm_model_kwargs` must not carry `"host"` once the load balancer is
wired in**: LightRAG merges `llm_model_kwargs` into every call to
`llm_model_func`. `MultiOllamaLoadBalancer.__call__` sets `host=` itself
from its own round-robin pick before forwarding to
`ollama_model_complete`; if `llm_model_kwargs` also carried `"host"`
(the pre-load-balancer wiring did, via `llm_kwargs()`), the call would
raise `got multiple values for keyword argument 'host'`. `ingest.py`'s
`llm_kwargs()` was updated to drop it — carries only `options`/`think` now.
If this stops working again (e.g. someone re-adds a static `host` to
`llm_kwargs()` for a one-off debug), that's the exact symptom to expect.

### 5. Per-document error tracking, no soft fallbacks

`ingest.py` uses `apipeline_enqueue_documents` + `apipeline_process_enqueue_documents`
(not the `rag.ainsert()` convenience wrapper) — the only pair that accepts
a per-document `process_options` selector, which is how each SKU gets
marked full-extraction (`""`) vs. `skip_kg` (`"!"`) per `graph_sampling.py`'s
subset. This is also how the LightRAG server itself ingests (per
`ainsert()`'s own docstring), not a workaround.

Because this pipeline manages its own per-document status internally
(LightRAG's `doc_status` store: `PENDING` → `PROCESSING` → `PROCESSED` /
`FAILED`), failure accounting no longer comes from a Python-level
try/except per call site — it comes from reading that store back after
the run: `rag.get_processing_status()` for counts, `rag.get_docs_by_status(DocStatus.FAILED)`
for which SKUs failed and why (`.error_msg`). The discipline is the same as
before, just sourced differently: every attempted SKU ends the run in
exactly one of three accounted-for states: processed, failed (with its
error message printed), or skipped upstream (no usable description,
counted by `load_documents.build_documents()` and reported, not silently
dropped).

`ingest.py` exits non-zero and prints every failed `sku_id` with its error
if anything failed — it never continues past a failure by substituting a
placeholder/empty value for that SKU. It also hard-fails immediately
(`FileNotFoundError`, not a silent "treat everyone as skip_kg" fallback) if
`graph_sampling_output/full_extraction_skus.txt` doesn't exist yet.

**Checkpoint/resume, and why `FAILED` needed its own fix.** LightRAG's
`doc_status` store already gives crash-safety for free for two of the three
possible interrupted states: a doc killed mid-`PROCESSING`/`PARSING`/
`ANALYZING` (hard interrupt, crash, OOM-kill) is detected and reset to
`PENDING` automatically, inside `apipeline_process_enqueue_documents()`
itself (see `_validate_and_fix_document_consistency` in
`lightrag/pipeline.py`) — no code in this repo needed for that case, and a
plain re-run of `python ingest.py` already resumed correctly for it before
this change.

`FAILED` is the one state LightRAG does **not** auto-resume. By design (per
`lightrag/api/routers/document_routes.py`'s `/reprocess_failed` docstring),
a document that reaches `FAILED` stays `FAILED` until something explicitly
requests a retry — "each request grants at most ONE retry attempt per
FAILED document." Without that explicit request, a plain re-run of
`ingest.py` would enqueue only new/`PENDING` documents and silently leave
every previously-failed SKU failed forever — exactly the kind of silent
data loss this folder's no-soft-fallback discipline exists to prevent, just
manifesting as an omission instead of a substitution.

The library's own explicit-retry path (`/reprocess_failed`, or the
SDK-internal `apipeline_reset_failed_for_scan`) is built for a **concurrent
multi-writer server**: it freezes new ingress, drains the pipeline to idle,
and pages the `FAILED → PENDING` reset behind an owner-checked exclusive
lock, so a live server processing other requests never races the reset.
`ingest.py` is a one-shot, single-process script against its own
`WORKING_DIR` with nothing else writing to it concurrently — none of that
ceremony has anything to protect against here. `resume_failed_documents()`
(top of `ingest.py`) instead re-implements just the core reset logic
directly, using LightRAG's own **public** module-level helpers from
`lightrag.utils_pipeline` (`chunk_fields_from_status_doc`,
`resolve_doc_file_path`, `doc_status_reset_metadata`,
`doc_status_custom_chunk_patch`) — the same functions the library's own
manual-retry path calls internally to build the reset row — so the reset
semantics (preserve `created_at`, clear `error_msg`, drop stale per-attempt
metadata, skip anything with an in-flight custom-chunk journal) match
exactly, without depending on the server-only freeze/drain machinery.

`main()` calls `resume_failed_documents(rag)` unconditionally, before
enqueueing, on **every** invocation — so a `FAILED` SKU gets retried on the
very next `python ingest.py`, not just when a human remembers to ask for a
retry. The count reset is printed and included in the summary
(`Resumed from FAILED:`). If a resumed SKU fails again, it's reported as
failed again in that run's `Failed sku_ids:` list — nothing is hidden by
the retry, it just guarantees the failure is never silently left behind on
a routine re-run.

`graph_sampling.py` and `bm25_index.py` have no equivalent per-item
resume, because they have no per-item failure mode to resume from: each is
a single fast in-memory pass (no LLM/network calls) over the full catalog,
so a hard interrupt just means "re-run the whole thing," which is cheap at
this catalog size. What they DO need — and now have — is a guarantee that a
crash mid-write never corrupts the checkpoint file `ingest.py` and
`bm25_index.search()` depend on: both now write to a `.tmp` file and
`os.replace()` (atomic on POSIX) rather than writing the real path in
place, so `graph_sampling_output/full_extraction_skus.txt`,
`coverage_report.json`, and `bm25_storage/index.pkl` are always either the
previous good version or the new complete one, never a truncated
half-write. `bm25_index.py --build` also now prints an explicit
SKUs-indexed / skipped summary, matching the other two scripts' pattern of
never finishing silently.

The same no-soft-fallback discipline applies elsewhere in this folder:

- `load_documents.load_md_blocks()` raises `CorpusParseError` immediately
  if a block in `flipkart_lightrag_corpus.md` doesn't have a parseable
  `Product ID:` or `## Description` section, rather than skipping the
  malformed block silently. A missing description is an expected,
  already-known data gap (mirrors `flipkart_to_lightrag.py`'s own
  `empty_description_count`); a missing `Product ID:` line is not
  expected and means the corpus format changed or the file is corrupt —
  those are different failure classes and are handled differently on
  purpose.
- `query.py` requires an explicit question argument and exits with a
  usage message if none is given, instead of silently querying a
  default/placeholder question.

**If you extend this folder**: don't add a `.get(key, default)` or
`try/except: pass` pattern that quietly substitutes a value when data is
missing or a call fails. Either the missing/failed case is an expected,
already-counted condition (like empty descriptions) — surface the count
explicitly to the caller — or it's unexpected and should raise/hard-fail
loudly with enough context (which SKU, what error) to debug it, not get
papered over with a default.

### 6. Concurrency and context length

- `LLM_MAX_ASYNC` / `EMBEDDING_MAX_ASYNC` (`config.py`, default 4) —
  concurrent requests LightRAG issues to Ollama. Only actually
  parallelizes if the Ollama **server** was also started with
  `OLLAMA_NUM_PARALLEL` set to at least that value — see README.md.
  `EMBEDDING_MAX_ASYNC` only applies when `EMBED_BACKEND="ollama"`;
  local encode() calls (`local_embed.py`) are serialized internally
  regardless of this value.
- `NUM_CTX` (`config.py`, default 8192) — LightRAG's extraction system
  prompt (`ENTITY_TYPES_GUIDANCE`) alone runs ~2-3k tokens; 8192 leaves
  enough headroom for chunk text + gleaning passes + output given the
  condensed guidance text (fixed keyword vocabulary + strict format
  instead of per-type worked examples). Raise via `LIGHTRAG_NUM_CTX` if
  truncation warnings show up in the logs.
- `DISABLE_THINKING` (`config.py`, `True`) — forwards `think: false` to
  Ollama's chat call so reasoning models (qwen3-family) don't spend time
  on chain-of-thought during extraction/query, which would otherwise slow
  down a 150+ chunk ingestion run considerably.

## File map

| File | Role |
|---|---|
| `config.py` | All tunables: Ollama host, model names, embedding backend, context length, concurrency, Qdrant URL, entity-extraction guidance text, graph-sampling subset path, batch size, source file paths |
| `local_embed.py` | Local `sentence-transformers`/`torch` embedding backend (`nomic-embed-text-v1.5`, MPS/CPU), used when `EMBED_BACKEND="local"` |
| `load_documents.py` | Parses `../flipkart_lightrag_corpus.md` into `(sku_id, product_name, description)` tuples, builds the LightRAG document text |
| `graph_sampling.py` | Coverage-based selection of which SKUs get full graph extraction (see `lightrag-implementation.md` section 5); outputs to `graph_sampling_output/` |
| `sql_filter.py` | SQL hard-filter branch — `eligible_skus(hard_constraints)` against `product_metadata` |
| `bm25_index.py` | BM25 lexical branch — full-catalog index over the same description-only corpus; `python bm25_index.py --build` prints an index-build summary and is the checkpoint-safe (atomic-replace) build entrypoint |
| `multi_ollama.py` | `load_servers()` + `MultiOllamaLoadBalancer` — round-robins extraction calls across every server in `servers.txt`; unaffected by `EMBED_BACKEND` |
| `servers.txt` | One Ollama extraction-server URL per line, `#`/blank ignored; ships with a multi-server default, edit to add more (no code change needed) |
| `ingest.py` | Initializes `LightRAG(...)` (load-balanced Ollama LLM func, `embedding_func()` per `EMBED_BACKEND`, Qdrant vector storage, entity-type prompt profile), ingests via `apipeline_enqueue_documents` + `apipeline_process_enqueue_documents` with per-SKU `process_options` from `graph_sampling.py`'s subset |
| `query.py` | Runs a `mode="mix"`, `only_need_context=True` query against the built index |
| `run.sh` | Installs deps, checks every `servers.txt` extraction server is reachable (and the embedding server too, only if `EMBED_BACKEND=ollama`), checks pulled models, runs ingest and/or query |
| `README.md` | Setup/run instructions (Qdrant/MySQL provisioning, models, ingest, query), time estimates |
| `lightrag-implementation.md` | Deep dive: graph-subset sampling design + measured results, entity-extraction prompt investigation and fix |
| `lightrag_storage/` (generated, gitignored) | LightRAG's local storage: graph (NetworkX), KV store, doc status — created on first `ingest.py` run. Vector embeddings go to Qdrant, not here. |

## Next steps for whoever builds on this

1. **Run `ingest.py` for real, against live Ollama** — the only piece
   that's actually still pending. Everything else (Qdrant wiring, the
   graph-extraction subset, the entity-type prompt fix) is built, tested
   without needing an LLM, and ready. Pull the models (README.md section
   2), confirm Qdrant + MySQL containers are up (README.md section 0), then
   `python ingest.py`.
2. Build the union/intersect/rerank merge step
   (`retrieval-architecture.md`'s "Candidate fusion & rerank" section)
   that combines SQL, BM25, and the LightRAG/Qdrant branch's output — can
   be scaffolded against a stubbed semantic-branch output now, finished
   once step 1's real query output shape is confirmed.
3. Wrap the semantic branch as a plain candidate function
   (`semantic_search.py` per `plan.md` step 6) once step 1 confirms what
   `only_need_context=True` actually returns.
4. Wire all three branches into the actual `search_catalog(query_state)`
   entrypoint.

## Web UI and Interactive Visualization

### Query Interface (`web_ui.py`)

A Flask-based web UI (`web_ui.py`) provides interactive querying and graph visualization:

```bash
./webui.sh  # Opens browser to http://localhost:8000
```

The UI offers:
- **Query Interface** — Natural language queries against the LightRAG index in mixed mode (`mode="mix"`, `only_need_context=True`)
- **Graph Visualization** — Interactive vis.js network graph showing all 7,000+ nodes and relationships
- **Fullscreen Mode** — Click the ⛶ button to expand the graph for detailed exploration (press Esc or click Exit to return)
- **Node Search** — Sidebar search to find and highlight specific entities
- **Stats Dashboard** — Real-time view of document counts, entities, relations, and document status

### Implementation Details

**Query Execution:**
- `web_ui.py`'s `/api/query` POST endpoint accepts natural language queries
- Runs `rag.aquery()` in a separate thread (async-to-sync wrapper) to avoid blocking Flask's event loop
- Returns raw context via `QueryParam(mode="mix", only_need_context=True)` — same format as `query.py`
- Displays results in a scrollable panel with truncation for readability

**Graph Rendering:**
- Frontend uses vis.js for interactive network visualization
- Limited to first 500 nodes for performance (full node count shown)
- Physics simulation enabled for organic layout; pan/zoom/click to explore
- Node selection shows connected edges and metadata in sidebar

**Fullscreen Implementation:**
- CSS class `.fullscreen-mode` hides sidebar/header/query section
- Button toggles `isFullscreen` state and appends/removes close button
- Keyboard shortcut (Esc key) provides quick exit
- Network re-fits to viewport on toggle for optimal view

### Data Flow

1. `load_graph_data()` parses `lightrag_storage/graph_chunk_entity_relation.graphml` once on server start
2. `load_stats()` reads JSON checkpoints (`doc_status`, `entities`, `relations`) for sidebar
3. On query: `run_async_in_thread()` wraps the async `rag.aquery()` call
4. Flask returns JSON results; frontend formats and displays them

### Configuration

- **Port**: 8000 (hardcoded, can be changed in `__main__` or `webui.sh`)
- **Graph Storage**: Points to `STORAGE_DIR / "graph_chunk_entity_relation.graphml"` from `config.py`
- **RAG Instance**: Lazily initialized on first query, reused for subsequent queries
- **Threading**: One event loop per query to avoid conflicts with Flask's own event loop

### webui.sh Launcher

`webui.sh` is a bash script that:
- Checks port 8000 is free (kills existing process if needed)
- Installs dependencies (`uv sync`)
- Starts the server in background
- Auto-opens browser using `open` (macOS) / `xdg-open` (Linux) / `start` (Windows)
- Displays a formatted startup banner with features and URL
- Handles Ctrl+C gracefully to shut down the server

See README.md's "Quick Start — Interactive Web UI" section for usage examples.

