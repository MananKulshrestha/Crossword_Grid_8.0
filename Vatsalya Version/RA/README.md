# RA — LightRAG over the Flipkart catalog

Builds a LightRAG index (knowledge graph + vector DB, local file storage)
from `../flipkart_lightrag_corpus.md` (descriptions) and
`../flipkart_catalog_structured.jsonl` (brand/category/price facts), then
queries it in `mix` mode.

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

So: more distinct entities per doc (brand, category, material) → richer
graph. Pure free-text descriptions alone under-produce graph edges, which
is why `load_documents.py` prepends the short facts line.

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

## 4. Query

```bash
python query.py "What waterproof footwear brands are available?"
```

Uses `mode="mix"` — combines the knowledge graph (entities like brand/
category and their relations) with vector similarity search over chunks.

## Run everything with one command

```bash
./run.sh              # ingest + sample query
./run.sh ingest        # ingest only
./run.sh query "your question here"
```

`run.sh` installs dependencies, checks the Ollama server is reachable,
pulls any missing models, then runs the requested step.

## Notes

- `load_documents.py` joins each product's cleaned description (from the
  `.md` corpus) with a short structured "facts" line (brand, category,
  subcategory, material, prices) pulled from the `.jsonl`. This facts line
  exists only to give the graph extractor concrete entities to anchor
  on — it deliberately does not include everything from the structured
  record (see `../IMPLEMENTATION.md` for why the RAG corpus stays
  free-text-first).
- Products with no description are skipped (2 in the current dataset).
