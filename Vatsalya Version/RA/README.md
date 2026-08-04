# RA — retrieval subsystem (SQL + BM25 + LightRAG/Qdrant)

Builds and queries all three retrieval branches from `retrieval-architecture.md`:
SQL hard-filtering (`sql_filter.py`, full catalog), BM25 lexical search
(`bm25_index.py`, full catalog), and the semantic/graph branch — LightRAG,
Qdrant-backed (`ingest.py` / `query.py`), full catalog for chunk-vector
search, a coverage-chosen subset (`graph_sampling.py`) for the knowledge
graph. See `IMPLEMENTATION.md` for design decisions and what's built, and
`lightrag-implementation.md` for the graph-subset design and the
entity-extraction prompt in depth.

**Corpus is description-only, by design.** `load_documents.py` feeds
LightRAG exactly `product_name + description` per SKU, identical to what's
in `flipkart_lightrag_corpus.md` — no brand/category/material/price
folded in. Those already live in `product_metadata`
(`../flipkart_metadata.sql`) and are never duplicated into the text
corpus; LightRAG's own entity extraction is what's responsible for
pulling brand/category/material *out of* the description text.

## Quick Start — Interactive Web UI

The easiest way to explore the graph and query products is with the web UI:

```bash
./webui.sh
```

This script will:
- ✅ Start the Flask web server automatically
- ✅ Open your browser to `http://localhost:8000`
- ✅ Display the interactive graph visualizer and query interface

**Features:**
- 🔍 **Query Interface** — Type natural language queries to search product listings (e.g., "What cotton shirts are available?", "Find me blue products with round neck...")
- 📊 **Graph Visualization** — Interactive knowledge graph with 7,000+ nodes showing relationships between products, brands, categories, materials, and features
- ⛶ **Fullscreen Mode** — Click the fullscreen button to expand the graph for detailed exploration (press Esc or click Exit to return)
- 🔎 **Node Search** — Search for specific entities in the sidebar
- 📦 **Mixed Mode Retrieval** — Queries use both Qdrant vector search and knowledge graph traversal for comprehensive product discovery

### Query Examples

- "What cotton shirts are available?"
- "Show me blue products"
- "Find products with round neck"
- "What are the waterproof footwear brands?"
- "Which products are suitable for casual wear?"

## How the text turns into a graph

LightRAG does not build the graph directly from the raw catalog rows — it
builds it from the *documents* we hand it (see `load_documents.py`: each
product's `product_name + description`). For each document:

1. **Chunking** — the document is split into token-sized chunks (LightRAG
   default `chunk_token_size=1200`, most of our product docs are well
   under that so they usually stay as a single chunk).
2. **Chunk embedding** — every document's chunk gets embedded (`EMBED_MODEL`)
   into Qdrant's `chunks` collection, **regardless of whether it's in the
   graph-extraction subset**. This is what gives full-catalog vector search
   even though the graph itself only covers a subset (see below).
3. **Entity/relationship extraction — subset only** — for SKUs in
   `graph_sampling_output/full_extraction_skus.txt`, the chunk is sent to
   the LLM (`LLM_MODEL`) with a structured extraction prompt (`config.py`'s
   `ENTITY_TYPES_GUIDANCE`) asking it to pull out entities (e.g. `Alisha` —
   Brand, `Cotton` — Material) and relationships between them, constrained
   to a fixed relationship vocabulary (`HAS_BRAND`, `HAS_MATERIAL`, ...).
   For every other SKU, this step is skipped entirely via LightRAG's
   native `skip_kg` process option — no LLM call, no graph presence, but
   still fully embedded from step 2.
4. **Merging** — the same entity name showing up across many products
   (e.g. a brand appearing on multiple SKUs) gets merged into a single
   graph node with combined evidence, instead of duplicate nodes per
   document.
5. **Storage** — entities/relations go into the graph store (NetworkX,
   local file); chunk, entity, and relationship embeddings go into three
   separate Qdrant collections (`chunks`, `entities`, `relationships`).
6. **Query time (`mode="mix"`)** — a query pulls matches from three passes:
   raw chunk-vector search (full catalog, works for every SKU), entity-level
   match (`local`, subset only), and relationship/thematic match (`global`,
   subset only).

**Why only a subset gets the graph.** Full entity/relation extraction over
all ~20,000 products is estimated at multiple days of LLM calls. Instead,
`graph_sampling.py` picks a much smaller subset by *coverage* — every
category, every brand with enough SKUs to ever form a relationship, every
common material, and an occasion/style keyword scan — rather than randomly
or by taking the first N products. Measured result on the real catalog:
**2,673 SKUs (13.4%)** give full coverage of all 86 categories, all 1,448
qualifying brands, 97/99 materials, and every occasion/style keyword. Full
reasoning in `lightrag-implementation.md`, section 5.

**The entity-type prompt is a real fix, not cosmetic.** An earlier version
of this project passed `addon_params={"entity_types": [...]}` to LightRAG —
a key `lightrag-hku` (confirmed on the installed 1.5.5 release) never reads
at all. Every extraction would have silently used LightRAG's generic
default ontology (Person, Organization, Location, Event...) instead of a
product-catalog one. Fixed by using the mechanism LightRAG actually
resolves: `addon_params={"entity_types_guidance": ENTITY_TYPES_GUIDANCE}`
in `ingest.py`'s `build_rag()`, with the guidance text defined inline in
`config.py` (`Product`/`Brand`/`Category`/`Material`/`Color`/`Feature`/
`Technology`/`CompatibleItem`/`Audience`/`Certification`/`Warranty`, plus
a fixed 5-field relationship format and keyword vocabulary). Details in
`lightrag-implementation.md`, section 6.

## 0. Provision Qdrant and MySQL

Both containers should run with a restart policy and a persistent volume —
without one, a container recreation silently wipes all data (this is a real
risk Qdrant itself warns about at startup, not a hypothetical one):

```bash
docker volume create flipkart_qdrant_data

docker run -d \
  --name flipkart-qdrant \
  --restart unless-stopped \
  -p 6333:6333 \
  -p 6334:6334 \
  -v flipkart_qdrant_data:/qdrant/storage \
  qdrant/qdrant
```

| Field | Value |
|---|---|
| Host | `127.0.0.1` (or `localhost`) |
| REST port | `6333` |
| gRPC port | `6334` |
| Env var LightRAG reads | `QDRANT_URL` (default `http://localhost:6333`, see `config.py`) |

Verify it's up and actually working (not just that the port answers):

```bash
curl http://localhost:6333/collections
```

If the container already exists but is stopped (e.g. after closing Docker
Desktop or a reboot — `--restart unless-stopped` prevents this going
forward, but won't retroactively fix a container created without it):

```bash
docker start flipkart-qdrant
```

MySQL (`flipkart-mysql`, per `Vatsalya Version/README.md`) should be
running the same way — check with `docker ps -a --filter "name=flipkart"`
and `docker start` anything that's `Exited`.

## 1. Install dependencies

```bash
pip install -r requirements.txt
```

Or use `uv` for faster installation:

```bash
uv sync
```

## 2. Start Ollama and pull models

Embedding runs **locally** by default (`EMBED_BACKEND="local"` in
`config.py`) via `sentence-transformers`/`torch` on this machine (Apple
Silicon MPS if available, else CPU) — see "Embedding backend" below. No
Ollama server or model pull is needed for embedding in this mode.

Extraction (the expensive part) **round-robins across every server
listed in `servers.txt`** via `multi_ollama.py` — see "Multi-server
extraction" below for why and how to add more than one.
**`OLLAMA_NUM_PARALLEL` must be set on each server** or Ollama will queue
requests one at a time regardless of how many concurrent calls LightRAG
sends:

```bash
OLLAMA_NUM_PARALLEL=4 OLLAMA_HOST=0.0.0.0:11435 ollama serve &

ollama pull gemma4:e4b        # LLM_MODEL in config.py — or qwen3.6, gemma4:27b, see below
```

Confirm the exact tag name with `ollama list` once pulled — adjust
`LLM_MODEL` in `config.py` (or via the `LIGHTRAG_LLM_MODEL` env var) if it
differs.

If you'd rather keep embedding on the Ollama cluster instead (e.g. no
local GPU/MPS available), set `EMBED_BACKEND=ollama` and pull the
embedding model too:

```bash
export LIGHTRAG_EMBED_BACKEND=ollama
OLLAMA_NUM_PARALLEL=4 OLLAMA_HOST=0.0.0.0:11345 ollama serve &
ollama pull nomic-embed-text  # EMBED_MODEL in config.py
```

### Embedding backend (`EMBED_BACKEND`)

Set via `config.py`'s `EMBED_BACKEND` (or `LIGHTRAG_EMBED_BACKEND` env
var), one config switch for the whole pipeline (`ingest.py` and
`query.py` both read it through `build_rag()`):

- **`local`** (default) — runs `nomic-ai/nomic-embed-text-v1.5` (the same
  weights Ollama's `nomic-embed-text` is built from, same 768-dim output)
  via `sentence-transformers` in-process, see `local_embed.py`. Needs
  `sentence-transformers` + `einops` (`requirements.txt`) installed, no
  Ollama embedding server. Frees the Ollama cluster's GPUs entirely for
  extraction — no more embedding-vs-extraction contention over VRAM.
- **`ollama`** — calls `OLLAMA_HOST` for embeddings the same way
  extraction calls `servers.txt`, requires `nomic-embed-text` pulled there.

`EMBEDDING_MAX_ASYNC` only meaningfully applies to `EMBED_BACKEND=ollama`
— local encode() calls are serialized internally regardless (see
`local_embed.py`'s module docstring for why concurrent local calls don't
parallelize and can actually be slower).

### Multi-server extraction (`servers.txt`)

Entity/relation extraction (`ingest.py`'s LLM calls) is the part actually
worth spreading across more than one GPU/server — embedding, with the
default `EMBED_BACKEND="local"`, doesn't touch this cluster at all (see
"Embedding backend" above). `servers.txt` lists one Ollama server URL per
line (`#` comments and blank lines ignored); `ingest.py` reads it via
`multi_ollama.load_servers()` and dispatches every extraction call
round-robin across whatever's listed, via `MultiOllamaLoadBalancer`.
Ships with a multi-server default, so nothing extra is required to get
started — add more lines to parallelize further:

```
http://127.0.0.1:11435
http://127.0.0.1:11436  # second `ollama serve`, different GPU/port
```

Each URL should be its own `ollama serve` process — ideally pinned to a
distinct GPU (or GPU pair) via `CUDA_VISIBLE_DEVICES` so they don't
contend for the same VRAM, each started with its own `OLLAMA_NUM_PARALLEL`.
No code changes needed to add a server: `ingest.py` re-reads `servers.txt`
on every run via `build_rag()`. `run.sh` checks every server listed here is
reachable (and has `LLM_MODEL` pulled) before ingesting.

**Hard-fails, no silent fallback**: if `servers.txt` is missing or has no
URLs, `ingest.py` raises immediately rather than silently falling back to a
single server — matching this folder's existing no-soft-fallback
discipline (`IMPLEMENTATION.md` #5).

If raising `LLM_MAX_ASYNC` (below) to actually push more concurrent
extraction requests through multiple servers, remember it's a *total*
across all servers, distributed round-robin — not per-server.

### Which model to use

- **`gemma4:e4b`** (default here) — fits on a single GPU/card (unlike
  `gemma4:27b`, which tensor-splits across two), so it supports higher
  concurrency per server when extraction is spread across `servers.txt`.
  No "thinking" step, so every call goes straight to output — simpler and
  faster per-call for a structured-extraction task like this, where you
  don't need visible reasoning, just clean entity/relationship output.
- **`gemma4:27b`** — same no-thinking behavior as `e4b`, more capable but
  needs more VRAM (tensor-splits across 2 GPUs on an 11GB-class card).
- **`qwen3.6`** — stronger general reasoning/instruction-following, but
  it's a thinking model by default; `DISABLE_THINKING = True` in
  `config.py` forwards `think: false` to Ollama so it skips
  chain-of-thought and answers directly (otherwise extraction over
  thousands of chunks would be considerably slower).

All three fit comfortably at `num_ctx=8192`+ per server. If graph quality
looks weak on a first small run (few entities/relations extracted), try
switching to `qwen3.6` or `gemma4:27b` — both tend to follow
structured-extraction instructions more reliably than `gemma4:e4b` at its
smaller size, at the cost of concurrency/VRAM headroom.

To switch models:

```bash
export LIGHTRAG_LLM_MODEL=qwen3.6
```

### Context length

Set via `NUM_CTX` in `config.py` (default **8192**, override with
`LIGHTRAG_NUM_CTX`). Why 8192:

- LightRAG's entity-extraction system prompt (`ENTITY_TYPES_GUIDANCE`) is
  itself ~2–3k tokens.
- Chunk text adds up to `chunk_token_size` (1200 tokens by default).
- The model's own output (entities + relationships + gleaning passes)
  needs room too.

8192 is the bare minimum that won't silently truncate the prompt — the
condensed `ENTITY_TYPES_GUIDANCE` (a fixed relationship-keyword vocabulary
+ strict 5-field format instead of one bullet + worked example per type)
keeps the prompt itself small enough that 8192 has real headroom left for
chunk text and gleaning passes, without needing 16384. Raise it via
`LIGHTRAG_NUM_CTX` if you see truncation warnings in the logs.

### Concurrency (4 workers)

`LLM_MAX_ASYNC=4` and `EMBEDDING_MAX_ASYNC=4` in `config.py` tell
LightRAG to issue up to 4 concurrent requests to Ollama. This only
actually parallelizes if the **server** is also configured to accept
that many concurrent requests — that's what `OLLAMA_NUM_PARALLEL=4` in
the `ollama serve` command above is for. Override via
`LIGHTRAG_LLM_MAX_ASYNC` / `LIGHTRAG_EMBEDDING_MAX_ASYNC` env vars if you
want to push this higher given the available VRAM.

## 3. The graph-extraction subset (already generated, no LLM needed)

`graph_sampling_output/full_extraction_skus.txt` (2,673 SKU IDs) and
`coverage_report.json` are already committed — generated by
`graph_sampling.py`, which only reads structured metadata, no LLM
involved. You don't need to re-run this to start ingesting. Re-run it only
if the catalog itself changes:

```bash
python graph_sampling.py
```

`ingest.py` reads this file to decide, per SKU, whether to run full
extraction (`process_options=""`) or skip it (`process_options="!"`,
LightRAG's native `skip_kg` flag) — see `lightrag-implementation.md`,
section 5, for how the subset was chosen and why.

## 4. Ingest the catalog

```bash
python ingest.py
```

By default (`BATCH_SIZE` unset in `config.py`) this processes **every**
SKU with a usable description: all of them get chunk-embedded into Qdrant,
and the ~2,673 in the subset additionally get full entity/relation
extraction. Set `LIGHTRAG_BATCH_SIZE` to a smaller number for a quick
smoke test first — but note a small batch takes documents in *file order*,
which will under-sample the subset (it's scattered across the catalog by
design, not front-loaded), so a partial run's "chosen for full extraction"
count will look low. That's expected for a partial run, not a bug.

**Rough time estimate** (measure your own hardware on a small run before
committing to the full one — these are ballparks, not measured numbers):
- Chunk embedding, full catalog (~20,000 chunks, `EMBEDDING_MAX_ASYNC=4`,
  no LLM reasoning, just an embedding call): expect this to be the faster
  of the two passes per-item, but at 20,000 items it's still real wall
  time — time it on a partial run rather than assuming "seconds total"
  the way a 150-item test batch would suggest.
- Entity/relation extraction, subset only (~2,673 chunks, `LLM_MAX_ASYNC=4`,
  ~5–15s per call including possible gleaning passes): roughly
  `2,673 / 4 × ~8s ≈ 1.5 hours`, ballpark.
- These two stages run per-document (each document's own chunk-embed step,
  then conditionally its extraction step), with multiple documents in
  flight up to each stage's concurrency limit — so total wall time isn't
  simply the sum of the two estimates above, but bump
  `LLM_MAX_ASYNC`/`EMBEDDING_MAX_ASYNC`/`OLLAMA_NUM_PARALLEL` higher if the
  GPU has headroom, since 48GB VRAM likely supports more than 4 concurrent
  requests of either kind.

Storage: the graph/KV/doc-status data is written to `RA/lightrag_storage/`
(local files); chunk/entity/relationship **embeddings go to Qdrant**
(`QDRANT_URL`, default `http://localhost:6333`), in three collections
named `lightrag_vdb_{chunks,entities,relationships}_<embed-model>_<dim>d`.

Uses `apipeline_enqueue_documents` + `apipeline_process_enqueue_documents`
(not the `rag.ainsert()` convenience wrapper), since only that pair accepts
the per-document `process_options` selector graph_sampling's subset needs.
At the end you get an explicit summary read back from LightRAG's own
`doc_status` store:

```
Resumed 2 previously FAILED document(s) -- reset to PENDING for retry.

=== Ingestion summary ===
Attempted this run:    19996
Resumed from FAILED:   2
Processed (total):     19994
Failed (total):        2
Skipped (no desc.):    2
Qdrant:                http://localhost:6333
Storage (checkpoint):  /path/to/RA/lightrag_storage

Failed sku_ids:
  SBEEH3QGU7MFYJFY: TimeoutError(...)
  ...
```

If anything failed, the script exits non-zero and lists exactly which
`sku_id`s failed and why — it does **not** substitute a placeholder/empty
entry for a failed document and continue as if nothing happened.

### Checkpoint / resume — hard interrupts don't lose work, failures don't get ignored

`RA/lightrag_storage/` (the "Storage" line above) **is** the checkpoint —
every document's progress is tracked there via LightRAG's own `doc_status`
store (`PENDING → PROCESSING → PROCESSED` / `FAILED`), persisted to disk on
every status transition, not just at the end. This means:

- **Hard interrupt (Ctrl+C, `kill -9`, crash, power loss) mid-run**: whatever
  was already `PROCESSED` stays processed. Whatever was killed mid-flight
  (stuck in `PROCESSING`/`PARSING`/`ANALYZING`) is automatically detected and
  reset to `PENDING` by LightRAG itself the next time you run `python
  ingest.py` — you do not need to do anything special, just re-run the same
  command.
- **A document that failed** (the LLM call raised, a timeout, malformed
  extraction output, etc.) is recorded as `FAILED` with its error message —
  but LightRAG's default behavior is to leave a `FAILED` document `FAILED`
  forever unless something explicitly asks for a retry. `ingest.py` closes
  that gap itself: **every run** first resets every currently-`FAILED`
  document back to `PENDING` (`resume_failed_documents()` in `ingest.py`,
  using LightRAG's own public reset helpers), so a failed SKU is retried on
  the very next invocation instead of being silently skipped forever. If it
  fails again, it's reported as failed again — nothing is hidden.
- **Already-`PROCESSED` documents are never redone.** Re-running `ingest.py`
  after any of the above only pays for what's still `PENDING` or was reset
  from `FAILED`/interrupted — not a full re-ingest.
- This is why a hard interrupt is always safe to just re-run from: `python
  ingest.py` again picks up exactly where it left off, with nothing double
  counted and nothing quietly dropped.

`graph_sampling.py` and `bm25_index.py` don't need this kind of resume —
they're single, fast, in-memory passes with no LLM calls and no per-item
failure mode (a run either completes or, on a hard interrupt, simply
produces no new output file — the previous good one is untouched, since both
scripts write via a temp file + atomic `os.replace()`, never in place). Just
re-run them; there's nothing to "resume" mid-pass.

## 5. Query

### Command-line query (raw context):

```bash
python query.py "What waterproof footwear brands are available?"
```

Uses `mode="mix"` — combines the knowledge graph (entities like brand/
category and their relations, subset SKUs only) with vector similarity
search over chunks (every SKU, via Qdrant). Runs with
`only_need_context=True`, so it prints the raw retrieved context
(chunks/entities/relationships + source SKU references), not an
LLM-generated prose answer — response generation belongs to the outer
chat layer, not this retrieval step.

### Interactive Web UI (queries + visualization):

```bash
./webui.sh
```

Starts the Flask web UI server and opens it in your browser at `http://localhost:8000`.
The UI provides:
- 🔍 **Natural language query interface** for product discovery
- 📊 **Interactive graph visualization** of the knowledge graph
- 🔎 **Entity search** in the sidebar
- ⛶ **Fullscreen graph view** for detailed exploration
- 📦 **Mixed mode retrieval** combining vector search and graph traversal

## Checking how many samples each branch has actually ingested

Each branch prints its own summary when you run it; there's no single
combined status command (there's no shared "job" concept across the three
independent scripts). Where to look:

| Branch | How to check | What it tells you |
|---|---|---|
| SQL (`sql_filter.py`) | Reads `product_metadata` directly at query time — no separate ingest/index step, so there's no "samples ingested" count for it. | N/A |
| BM25 (`bm25_index.py`) | `python bm25_index.py --build` | SKUs indexed vs. skipped (no usable description) |
| Graph sampling (`graph_sampling.py`) | `python graph_sampling.py`, or read `graph_sampling_output/coverage_report.json` if already run | Total catalog size, SKUs chosen for full extraction, coverage stats |
| LightRAG (`ingest.py`) | End-of-run summary (above), or re-run `python ingest.py` any time — the summary always reflects the true current state of `lightrag_storage/`, not just this run | Processed / Failed / Resumed / Skipped counts, total across all runs so far |

As of the last run in this environment (no Ollama/Qdrant available in this
session, so LightRAG itself has **not** been ingested yet — `lightrag_storage/`
does not exist):

- **SQL**: N/A (queried live, not ingested).
- **BM25**: not yet built in this environment (`rank_bm25` isn't installed
  in `.venv` here) — run `pip install -r requirements.txt && python
  bm25_index.py --build` to get a real count; expect ~19,996–19,998 (every
  SKU with a usable description, per `graph_sampling_output/coverage_report.json`'s
  `skus_with_usable_description`).
- **Graph sampling**: already run — `graph_sampling_output/coverage_report.json`
  shows **2,673 of 20,000 SKUs (13.4%)** chosen for full graph extraction,
  covering 86/86 categories, 1,448/1,448 qualifying brands, 97/99 materials.
- **LightRAG**: **0 processed so far** — `ingest.py` has never been run
  end-to-end in this environment (no `lightrag_storage/` present). Run it
  once Ollama + Qdrant are reachable; the summary it prints is the
  authoritative "how many samples ingested" number going forward, and stays
  accurate across as many interrupted/resumed runs as it takes.

## Run everything with one command

```bash
./webui.sh              # Open interactive web UI (recommended)
./run.sh                # ingest + sample query (command-line)
./run.sh ingest         # ingest only (command-line)
./run.sh query "your question here"  # query only (command-line)
```

`run.sh` installs dependencies, checks the Ollama server is reachable,
pulls any missing models, then runs the requested step.

## Notes

- `load_documents.py` reads `product_name + description` straight out of
  `flipkart_lightrag_corpus.md` — no structured fields folded in. See
  `../IMPLEMENTATION.md` for why the RAG corpus stays free-text-only, and
  `IMPLEMENTATION.md` (this folder) for what was built here specifically.
- Products with no description are **counted and reported**, not silently
  dropped — `build_documents()` returns the skip count explicitly and
  `ingest.py` prints it in the summary. A malformed corpus block (missing
  `Product ID:` or `## Description`) raises `CorpusParseError` immediately
  rather than being skipped — that's treated as a real data-integrity bug,
  not an expected gap.
- No soft fallbacks/defaults on data: `query.py` requires an explicit
  question argument (errors with a usage message otherwise), a failed
  ingestion never gets replaced with placeholder content, and `ingest.py`
  hard-fails with a clear message if `graph_sampling_output/full_extraction_skus.txt`
  doesn't exist yet, rather than silently treating every SKU as skip_kg.
- Every branch's on-disk output (LightRAG's `doc_status` store,
  `graph_sampling_output/*.txt`/`.json`, `bm25_storage/index.pkl`) is a real
  checkpoint, not a soft cache: a hard interrupt never corrupts it (writes
  are atomic or transition-by-transition), a re-run never silently skips a
  `FAILED` document, and nothing is double-processed. See "Checkpoint /
  resume" above.
- SQL hard-filtering (`sql_filter.py`) and BM25 lexical search
  (`bm25_index.py`) are fully built, full-catalog, and tested against real
  data (`tests/test_sql_filter.py`, `tests/test_bm25.py`) — see
  `IMPLEMENTATION.md` for details. The union/intersect/rerank merge step
  that combines all three branches, and the `search_catalog(query_state)`
  entrypoint itself, are still missing.
