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
   the LLM (`LLM_MODEL`) with a structured extraction prompt (see
   `prompts/entity_type/ecommerce_catalog.yml`) asking it to pull out
   entities (e.g. `Alisha` — BRAND, `Cycling Shorts` — CATEGORY) and
   relationships between them. For every other SKU, this step is skipped
   entirely via LightRAG's native `skip_kg` process option — no LLM call,
   no graph presence, but still fully embedded from step 2.
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
default ontology (Person, Organization, Location, Event...) instead of
`PRODUCT`/`BRAND`/`CATEGORY`/`MATERIAL`/`OCCASION`/`STYLE`. Fixed by using
the mechanism LightRAG actually resolves: `addon_params={"entity_type_prompt_file":
"ecommerce_catalog.yml"}`, pointing at `prompts/entity_type/ecommerce_catalog.yml`
(guidance text + two real, worked examples from this catalog, not
placeholder-only ones). Details and a before/after example in
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

## 2. Start Ollama and pull models

Server is expected at `http://localhost:11345` (set in `config.py` /
`OLLAMA_HOST` env var — adjust if your cluster uses a different port).
**`OLLAMA_NUM_PARALLEL` must be set on the server** or Ollama will queue
requests one at a time regardless of how many concurrent calls LightRAG
sends:

```bash
OLLAMA_NUM_PARALLEL=4 OLLAMA_HOST=0.0.0.0:11345 ollama serve &

ollama pull gemma4:27b        # LLM_MODEL in config.py — or qwen3.6, see below
ollama pull nomic-embed-text  # EMBED_MODEL in config.py
```

Confirm the exact tag names with `ollama list` once pulled — adjust
`LLM_MODEL` / `EMBED_MODEL` in `config.py` (or via `LIGHTRAG_LLM_MODEL` /
`LIGHTRAG_EMBED_MODEL` env vars) if they differ.

### Which model to use

- **`gemma4:27b`** (default here) — no "thinking" step, so every call
  goes straight to output. Simpler and faster per-call for a
  structured-extraction task like this, where you don't need visible
  reasoning, just clean entity/relationship output.
- **`qwen3.6`** — stronger general reasoning/instruction-following, but
  it's a thinking model by default; `DISABLE_THINKING = True` in
  `config.py` forwards `think: false` to Ollama so it skips
  chain-of-thought and answers directly (otherwise extraction over
  thousands of chunks would be considerably slower).

Given a 48GB-VRAM budget, either fits comfortably at `num_ctx=16384`
alongside `nomic-embed-text`. If graph quality looks weak on a first small
run (few entities/relations extracted), try switching to `qwen3.6` — it
tends to follow structured-extraction instructions more reliably than
Gemma at similar size.

To switch models:

```bash
export LIGHTRAG_LLM_MODEL=qwen3.6
```

### Context length

Set via `NUM_CTX` in `config.py` (default **16384**, override with
`LIGHTRAG_NUM_CTX`). Why 16384 and not less:

- LightRAG's entity-extraction system prompt is itself ~2–3k tokens.
- Chunk text adds up to `chunk_token_size` (1200 tokens by default).
- The model's own output (entities + relationships + gleaning passes)
  needs room too.

8192 is the bare minimum that won't risk silently truncating the prompt;
16384 leaves headroom for gleaning passes and longer product descriptions
without needing to tune further. With 48GB VRAM this is affordable for
both `gemma4:27b` and `qwen3.6`.

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
=== Ingestion summary ===
Attempted:            19996
Processed:            19994
Failed:               2
Skipped (no desc.):   2
Qdrant:               http://localhost:6333
Storage:              /path/to/RA/lightrag_storage

Failed sku_ids:
  SBEEH3QGU7MFYJFY: TimeoutError(...)
  ...
```

If anything failed, the script exits non-zero and lists exactly which
`sku_id`s failed and why — it does **not** substitute a placeholder/empty
entry for a failed document and continue as if nothing happened. Re-run
`ingest.py` to retry; LightRAG's own doc-status tracking skips
already-`PROCESSED` SKUs on the next pass.

## 5. Query

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

## Run everything with one command

```bash
./run.sh              # ingest + sample query
./run.sh ingest        # ingest only
./run.sh query "your question here"
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
- SQL hard-filtering (`sql_filter.py`) and BM25 lexical search
  (`bm25_index.py`) are fully built, full-catalog, and tested against real
  data (`tests/test_sql_filter.py`, `tests/test_bm25.py`) — see
  `IMPLEMENTATION.md` for details. The union/intersect/rerank merge step
  that combines all three branches, and the `search_catalog(query_state)`
  entrypoint itself, are still missing.
