# RA — LightRAG over the Flipkart catalog

Builds a LightRAG index (knowledge graph + vector DB, local file storage —
NanoVectorDB + NetworkX, no Qdrant for now) from `../flipkart_lightrag_corpus.md`
only, then queries it in `mix` mode. This implements the semantic/graph
retrieval branch described in `retrieval-architecture.md` — see
`IMPLEMENTATION.md` for what was built, why, and what a future SQL/BM25
integration needs to plug into.

**Corpus is description-only, by design.** `load_documents.py` feeds
LightRAG exactly `product_name + description` per SKU — no brand/category/
material/price folded in. Those already live in `product_metadata`
(`../flipkart_metadata.sql`) and are never duplicated into the text
corpus; LightRAG's own entity extraction pulls brand/category/material
*out of* the description text, scoped to a fixed entity-type list.

## How the text turns into a graph

1. **Chunking** — each document is split into token-sized chunks.
2. **Entity/relationship extraction** — each chunk goes to the LLM
   (`LLM_MODEL`) with a structured extraction prompt, pulling out entities
   (e.g. `Round Neck` — Neckline, `Maroon` — Color) and relationships (e.g.
   `Alisha Solid Womens Cycling Shorts` —`belongs to`→ `Clothing`).
   Extraction is constrained and steered toward specific attribute values
   (not generic labels) via `ENTITY_TYPES_GUIDANCE` in `config.py`, passed
   as `addon_params={"entity_types_guidance": "..."}` — **not**
   `addon_params={"entity_types": [...]}`, which this LightRAG version
   silently ignores (falls back to the generic default ontology with no
   error if you pass the wrong key).
3. **Merging** — the same entity name across multiple products merges
   into one graph node instead of duplicating, which is what connects
   products together in the graph.
4. **Storage** — entities/relations go into the graph store (NetworkX),
   chunk text and extracted entity/relation descriptions get embedded into
   the vector store (NanoVectorDB). Embedding runs **locally on this
   Mac** via `sentence-transformers`/torch (`local_embed.py`, MPS if
   available) by default — not on the remote Ollama cluster — so it
   never competes with extraction for the shared GPUs. Set
   `LIGHTRAG_EMBED_BACKEND=ollama` to embed via `EMBED_MODEL` on the
   remote server instead.
5. **Query time (`mode="mix"`)** — pulls matches from both vector
   similarity search and graph traversal, merges both result sets.

## Config

**`config.py` is the single source of truth for model, concurrency, and
context-length settings — read its comments for current values and why.**
`run.sh` reads its defaults directly from `config.py`, so nothing else
needs editing when you change a setting there.

Cluster is 4x 11GB GPUs (2080 Ti / 1080 Ti), shared with other jobs — a
31B-class model tensor-splits across two cards and needs lower
concurrency; a smaller model fits one card but showed measurably worse
extraction quality (frequent `LLM output format error` warnings — every
relation that fails LightRAG's format check gets silently dropped, not
just extracted slower). `config.py`'s `LLM_MODEL`/`LLM_MAX_ASYNC` comments
record which pairings are safe — don't change one without checking the
other's comment.

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

## 2. Start Ollama servers (multi-GPU)

Two Ollama servers run on different GPU pairs to utilize all 4 GPUs:
- **Server 1**: GPUs 0,1 on port 11435
- **Server 2**: GPUs 2,3 on port 11436

### On the remote GPU node (gnode071):

```bash
sbatch ~/server.sh
```

This starts both servers with `OLLAMA_NUM_PARALLEL=8` each. Check status:

```bash
ollama ps
# Should show gemma4:e4b loaded on both servers
```

### Locally (SSH port forwarding):

```bash
ssh -L 11435:localhost:11435 -L 11436:localhost:11436 -J gr@ada.iiit.ac.in gr@gnode071
```

This forwards both server ports to your local machine. Leave this terminal open.

### Verify both servers are reachable:

```bash
curl http://localhost:11435/api/tags  # Server 1 (GPUs 0,1)
curl http://localhost:11436/api/tags  # Server 2 (GPUs 2,3)
```

Both should return `{"models":[...]}` with `gemma4:e4b` listed.

### Pull models on both servers (if not already loaded):

The servers auto-pull on first use, or manually:

```bash
# Server 1
curl http://localhost:11435/api/pull -d '{"name":"gemma4:e4b"}' -X POST

# Server 2
curl http://localhost:11436/api/pull -d '{"name":"gemma4:e4b"}' -X POST
```

`ollama pull <EMBED_MODEL from config.py>` is only needed if
`LIGHTRAG_EMBED_BACKEND=ollama` — the default (`local`) embeds on this
Mac instead (see below), nothing to pull on the remote servers for it.

## 3. Configure server URLs (multi-server load balancing)

Edit `servers.txt` to list your Ollama server URLs (one per line):

```
http://localhost:11435
http://localhost:11436
```

`ingest.py` automatically load-balances extraction requests across these servers
using round-robin. To add more servers (from other nodes), just append their URLs:

```
http://localhost:11435
http://localhost:11436
http://localhost:11437  # node2 server 1
http://localhost:11438  # node2 server 2
```

No code changes needed — it auto-loads and distributes.

## 4. Ingest

```bash
python ingest.py
```

Ingests `BATCH_SIZE` products (`config.py`) that have a non-empty
description — **default is `None`, meaning the entire corpus** (~20,000
products); set `LIGHTRAG_BATCH_SIZE=150` to cap it to a test batch
instead. A `tqdm` progress bar tracks completion. Storage is written to
`RA/lightrag_storage/` (local files, no external DB).

Requests are load-balanced across servers listed in `servers.txt`, with each
server handling up to `OLLAMA_NUM_PARALLEL` (8) concurrent extractions.

**Resumable across interruptions**: if `ingest.py` is killed (Ctrl+C,
crash, connection drop) partway through, just re-run `python ingest.py`
— do **not** run `reset.sh` first. LightRAG's own `doc_status` tracks
every document's state, and `apipeline_process_enqueue_documents()`
(which `ainsert()` calls) explicitly picks up pending, failed, and
abnormally-terminated-processing documents on the next call — already-
`PROCESSED` documents are skipped, not redone. This only works because
doc_ids are deterministic (`f"sku-{sku_id}"`, same every run) and
`reset.sh` is the only thing that actually discards this state — running
it means starting over, not resuming.

Documents are enqueued in one `rag.ainsert(texts, ids=doc_ids)` call —
LightRAG concurrency-limits extraction/embedding internally via
`LLM_MAX_ASYNC`/`EMBEDDING_MAX_ASYNC`.

`ainsert()` catches per-document extraction/embedding errors internally
and marks the doc `FAILED` in `doc_status` without raising — a clean
return isn't proof of success. `ingest.py` reads the real per-document
outcome back from `rag.doc_status` afterward and prints a summary:

```
=== Ingestion summary ===
Attempted:            150
Succeeded:            148
Failed:               2
Unaccounted:          0
Skipped (no desc.):   0
Storage:              /path/to/RA/lightrag_storage

Failed sku_ids:
  SBEEH3QGU7MFYJFY: ConnectionError('Failed to connect to Ollama...')
```

`Unaccounted` = neither `PROCESSED` nor `FAILED` (run killed mid-flight),
treated as a failure. If anything failed or is unaccounted for, the
script exits non-zero and lists the `sku_id`s — no placeholder
substitution. Re-run `ingest.py` to retry (already-`PROCESSED` ids are
skipped). Run `./reset.sh` first if you want a clean slate instead of
resuming on top of partial state.

## 5. Query

```bash
python query.py "What waterproof footwear brands are available?"
```

Uses `mode="mix"` with `only_need_context=True`, so it prints raw
retrieved context (chunks/entities/relationships + source SKU
references), not an LLM-generated answer — this matches what a
`search_catalog` integration would consume; response generation belongs
to the outer chat layer.

## 6. Inspecting what got stored

Everything lives as plain local files under `RA/lightrag_storage/`:

- **`kv_store_doc_status.json`** — per-SKU ingestion status/`error_msg`.
- **`graph_chunk_entity_relation.graphml`** — the knowledge graph
  (GraphML, open in Gephi/Cytoscape, or `networkx.read_graphml(...)`).
- **`vdb_entities.json` / `vdb_relationships.json` / `vdb_chunks.json`**
  — NanoVectorDB stores: text + embedding per entity/relation/chunk.
- **`kv_store_full_docs.json` / `kv_store_text_chunks.json`** — original
  document text and its chunked form.
- **`kv_store_llm_response_cache.json`** — cached LLM extraction
  responses, keyed by prompt hash — why re-running after a partial
  failure is cheap.

Quick status summary:

```bash
python -c "
import asyncio
from ingest import build_rag

async def main():
    rag = await build_rag()
    print(await rag.doc_status.get_status_counts())
    await rag.finalize_storages()

asyncio.run(main())
"
```

## Run everything with one command

```bash
./run.sh              # ingest + sample query
./run.sh ingest        # ingest only
./run.sh query "your question here"
./reset.sh             # wipe lightrag_storage/ and start clean
```

`run.sh` installs dependencies, checks the Ollama server is reachable,
pulls any missing models, then runs the requested step. `reset.sh` asks
for confirmation before deleting `lightrag_storage/` (`-y` to skip).

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
- Embedding defaults to local (`local_embed.py`, `sentence-transformers`
  running `nomic-ai/nomic-embed-text-v1.5` on this Mac's CPU/MPS) — set
  `LIGHTRAG_EMBED_BACKEND=ollama` to use `EMBED_MODEL` on the remote
  server instead. If you do, note `ingest.py`'s ollama branch calls
  `ollama_embed.func(...)`, not `ollama_embed(...)` — `ollama_embed` is
  already a decorated `EmbeddingFunc` with its own baked-in
  `embedding_dim` (for a different default model). Calling it directly
  and re-wrapping it double-wraps the dimension check and fails on real
  output. See `IMPLEMENTATION.md` §4.
