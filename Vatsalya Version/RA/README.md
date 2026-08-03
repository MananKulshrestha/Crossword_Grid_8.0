# RA — LightRAG over the Flipkart catalog

Builds a LightRAG index (knowledge graph + vector DB, local file storage —
NanoVectorDB + NetworkX, no Qdrant for now, this is the testing/prototype
tier) from `../flipkart_lightrag_corpus.md` only, then queries it in
`mix` mode. This implements the semantic/graph retrieval branch described
in `retrieval-architecture.md` — see `IMPLEMENTATION.md` for exactly what
was built, why, and what a future SQL/BM25 integration needs to plug into.

**Corpus is description-only, by design.** `load_documents.py` feeds
LightRAG exactly `product_name + description` per SKU, identical to what's
in `flipkart_lightrag_corpus.md` — no brand/category/material/price
folded in. Those already live in `product_metadata`
(`../flipkart_metadata.sql`) and are never duplicated into the text
corpus; LightRAG's own entity extraction is what's responsible for
pulling brand/category/material *out of* the description text, scoped to
a fixed entity-type list (see below) rather than guessed structured
fields fed in ahead of time.

## How the text turns into a graph

LightRAG does not build the graph directly from the raw catalog rows — it
builds it from the *documents* we hand it (see `load_documents.py`: each
product's description + a short facts line). For each document:

1. **Chunking** — the document is split into token-sized chunks (LightRAG
   default `chunk_token_size=1200`, most of our product docs are well
   under that so they usually stay as a single chunk).
2. **Entity/relationship extraction** — each chunk is sent to the LLM
   (`LLM_MODEL`) with a structured extraction prompt asking it to pull out
   entities (e.g. `Alisha` — Brand, `Clothing` — Category, `Cycling
   Shorts` — Product) and relationships between them (e.g. `Alisha Solid
   Womens Cycling Shorts` —`belongs to`→ `Clothing`). This can run more
   than once per chunk ("gleaning") if the model reports it missed
   something.
3. **Merging** — the same entity name showing up across many products
   (e.g. the `Clothing` category node, or a brand like `Alisha` appearing
   on multiple SKUs) gets merged into a single graph node with combined
   evidence, instead of duplicate nodes per document. This is what makes
   the graph connect products together rather than staying 20,000
   disconnected islands.
4. **Storage** — entities/relations go into the graph store (NetworkX,
   local file), while the original chunk text *and* every extracted
   entity/relation description get embedded (`EMBED_MODEL`) into the
   vector store (nano-vectordb, local file) for similarity search.
5. **Query time (`mode="mix"`)** — a query pulls matches from both: vector
   similarity search over chunks/entities, *and* graph traversal from
   entities matched in the query (e.g. asking about "footwear brands"
   pulls the `Footwear` category node and walks its edges to connected
   brand/product nodes), then merges both result sets before the LLM
   synthesizes an answer.

So: entities/relations come entirely from what the LLM extracts out of the
description text — nothing is pre-seeded. Extraction is constrained to a
fixed entity-type list (`ENTITY_TYPES` in `config.py`: `PRODUCT`, `BRAND`,
`CATEGORY`, `MATERIAL`, `OCCASION`, `STYLE`) passed via LightRAG's
`addon_params={"entity_types": [...]}`, so the graph doesn't fill up with
LightRAG's generic default ontology (person, organization, location,
event...), which is the wrong shape for a product catalog.

## Estimated time (150-product test batch)

Each product chunk needs **1 LLM call minimum** for extraction (possibly
2 if gleaning triggers), plus 1 embedding call. With `LLM_MAX_ASYNC=4`
concurrent requests against a 27B model on your cluster's GPU, a single
extraction call typically takes somewhere in the **5–15 second** range
depending on chunk length and how much the model finds to extract.

Rough math for 150 products at 4-way concurrency:
`150 calls / 4 concurrent × ~8s avg ≈ 5–8 minutes`, plus embedding calls
(much faster, seconds total) and index write overhead. Treat this as a
ballpark — first run will tell you the real throughput on your hardware,
and you can extrapolate to the full 20,000-product run from there
(roughly ~13x this batch's wall time at the same concurrency, i.e.
expect somewhere around 1–2 hours for the full catalog — bump
`LLM_MAX_ASYNC`/`OLLAMA_NUM_PARALLEL` higher if the GPU has room, since
48GB VRAM likely supports more than 4 concurrent 27B requests).

## 0. Provision Qdrant

Tier 3 target per `retrieval-architecture.md` is Qdrant-backed vector
storage (the ingestion/query code below doesn't use it yet — that's
Step 3 of `plan.md`, tracked separately). Same Docker pattern as the
MySQL container in `../README.md`:

```bash
docker run -d \
  --name flipkart-qdrant \
  -p 6333:6333 \
  -p 6334:6334 \
  qdrant/qdrant
```

No persistent volume — data is lost if the container is removed, same
caveat as the MySQL container.

| Field | Value |
|---|---|
| Host | `127.0.0.1` (or `localhost`) |
| REST port | `6333` |
| gRPC port | `6334` |

Verify it's up:

```bash
curl http://localhost:6333/collections
```

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
  chain-of-thought and answers directly (otherwise extraction over 150+
  chunks would be considerably slower).

Given a 48GB-VRAM budget, either fits comfortably at `num_ctx=16384`
alongside `nomic-embed-text`. If graph quality looks weak on the test
batch (few entities/relations extracted), try switching to `qwen3.6` —
it tends to follow structured-extraction instructions more reliably than
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

## 3. Ingest a test batch

```bash
python ingest.py
```

Ingests the first 150 products (`BATCH_SIZE` in `config.py`) that have a
non-empty description, 4 at a time. Bump `BATCH_SIZE` once this test
batch works end-to-end.

Storage is written to `RA/lightrag_storage/` (graph, vector DB, KV store —
all local files, no external DB required).

Documents are inserted one at a time (concurrency capped at
`LLM_MAX_ASYNC`), not as a single batched call — each SKU's success or
failure is tracked individually. At the end you get an explicit summary:

```
=== Ingestion summary ===
Attempted:            150
Succeeded:            148
Failed:               2
Skipped (no desc.):   0
Storage:              /path/to/RA/lightrag_storage

Failed sku_ids:
  SBEEH3QGU7MFYJFY: TimeoutError(...)
  ...
```

If anything failed, the script exits non-zero and lists exactly which
`sku_id`s failed and why — it does **not** substitute a placeholder/empty
entry for a failed document and continue as if nothing happened. Re-run
`ingest.py` to retry; there's no partial-failure state hidden anywhere.

## 4. Query

```bash
python query.py "What waterproof footwear brands are available?"
```

Uses `mode="mix"` — combines the knowledge graph (entities like brand/
category and their relations) with vector similarity search over chunks.
Runs with `only_need_context=True`, so it prints the raw retrieved
context (chunks/entities/relationships + source SKU references), not an
LLM-generated prose answer — this matches what a `search_catalog`
integration would actually consume; response generation belongs to the
outer chat layer, not this retrieval step.

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
  `ingest.py` prints it in the summary (2 in the current dataset). A
  malformed corpus block (missing `Product ID:` or `## Description`)
  raises `CorpusParseError` immediately rather than being skipped — that's
  treated as a real data-integrity bug, not an expected gap.
- No soft fallbacks/defaults on data: `query.py` requires an explicit
  question argument (errors with a usage message otherwise, no default
  question substituted), and a failed ingestion never gets replaced with
  placeholder content — see the ingestion summary behavior above.
- SQL hard-filtering (`sql_filter.py`) and BM25 lexical search
  (`bm25_index.py`) are also now built in this folder, each tested against
  real data (`tests/test_sql_filter.py`, `tests/test_bm25.py`) — see
  `IMPLEMENTATION.md` for details. Only the union/intersect/rerank merge
  step that combines all three branches, and the `search_catalog(query_state)`
  entrypoint itself, are still missing.
