# Implementation Notes — `RA/` LightRAG Semantic/Graph Branch

This folder implements **one of three** retrieval branches described in
`retrieval-architecture.md` (see `~/Downloads/retrieval-architecture.md`,
or wherever that doc lives in the project repo) for the `search_catalog`
tool's internals: the **semantic/graph branch**, via LightRAG. It does
not implement the SQL hard-filter branch or the BM25 lexical branch —
those are explicitly out of scope for this folder, planned for later.

Read `../IMPLEMENTATION.md` first — it explains the upstream pipeline
(`flipkart_to_lightrag.py`) that produces the three source files this
folder consumes. This document picks up from there.

## What exists and what doesn't

**Built:**
- LightRAG ingestion (`ingest.py`) over `../flipkart_lightrag_corpus.md`,
  producing a knowledge graph + vector index via local file storage.
- A `mix`-mode query script (`query.py`) returning raw grounded context.
- Ollama as the LLM/embedding backend (local, no API key, no external
  cost) — deliberately **not** using Gemini or any hosted API, even
  though the source architecture doc mentions Gemini 2.5 Flash as the
  project's default model. This was an explicit choice for this testing
  phase: stay fully local via the project's Ollama cluster.
- NanoVectorDB + NetworkX (LightRAG's local-file defaults) for vector and
  graph storage — **not** Qdrant. The architecture doc's Tier 3 target is
  Qdrant-backed; this folder is closer to a Tier 1/2-equivalent semantic
  branch used to validate the LightRAG mechanics cheaply before any
  Qdrant/infra decision is made. Swapping to Qdrant later means only
  changing `vector_storage="QdrantVectorDBStorage"` in `ingest.py`'s
  `LightRAG(...)` call plus pointing it at a running Qdrant instance —
  nothing else in this folder's design depends on the storage backend.

**Not built (explicitly deferred by the user, "later"):**
- SQL hard-filter branch (query `product_metadata` for price/stock/
  category/size eligibility).
- BM25 lexical branch (`rank_bm25` over the same corpus).
- The union/intersect/rerank merge step that combines all three branches
  (`retrieval-architecture.md`'s "Candidate fusion & rerank" section).
- Cross-encoder reranking.
- Any wiring into an actual `search_catalog(query_state)` function —
  this folder is a standalone ingestion/query harness, not yet plugged
  into a larger agent/tool pipeline.

Whoever picks this up next: don't assume LightRAG alone constitutes
`search_catalog`. It's one input branch. The other two branches and the
merge step still need to be built and unioned with what's here, per
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

### 2. Entity extraction is scoped to a fixed type list

`config.py` defines `ENTITY_TYPES = ["PRODUCT", "BRAND", "CATEGORY",
"MATERIAL", "OCCASION", "STYLE"]`, passed into `LightRAG(...,
addon_params={"entity_types": ENTITY_TYPES})` in `ingest.py`.

Without this, LightRAG's extraction defaults to a generic ontology
(person, organization, location, event...) which doesn't fit a shopping
catalog and would pollute the graph with irrelevant node types pulled out
of product description text. This list matches
`retrieval-architecture.md`'s "Entity/relationship schema" table exactly
— don't diverge from it without updating that doc too, since other
branches/agents in the wider project are expected to rely on the same
entity vocabulary.

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

All model calls (`config.py`: `LLM_MODEL`, `EMBED_MODEL`) go through a
local Ollama server (`OLLAMA_HOST`, default `http://localhost:11345`).
This is intentional for this phase — no API keys, no external cost, uses
the project's own GPU cluster (48GB VRAM headroom). If this later moves
to a hosted model (e.g. matching the wider project's Gemini 2.5 Flash
default per `retrieval-architecture.md`), only `ingest.py`'s
`llm_model_func` / `embedding_func` wiring needs to change — LightRAG's
model interface is pluggable, nothing else in this folder assumes Ollama
specifically.

### 5. Concurrency and context length

- `LLM_MAX_ASYNC` / `EMBEDDING_MAX_ASYNC` (`config.py`, default 4) —
  concurrent requests LightRAG issues to Ollama. Only actually
  parallelizes if the Ollama **server** was also started with
  `OLLAMA_NUM_PARALLEL` set to at least that value — see README.md.
- `NUM_CTX` (`config.py`, default 16384) — LightRAG's extraction system
  prompt alone runs ~2-3k tokens; 16384 leaves headroom for chunk text +
  gleaning passes + output without needing per-model tuning. Affordable
  at 48GB VRAM for both `gemma4:27b` and `qwen3.6`.
- `DISABLE_THINKING` (`config.py`, `True`) — forwards `think: false` to
  Ollama's chat call so reasoning models (qwen3-family) don't spend time
  on chain-of-thought during extraction/query, which would otherwise slow
  down a 150+ chunk ingestion run considerably.

## File map

| File | Role |
|---|---|
| `config.py` | All tunables: Ollama host, model names, context length, concurrency, entity types, batch size, source file paths |
| `load_documents.py` | Parses `../flipkart_lightrag_corpus.md` into `(sku_id, product_name, description)` tuples, builds the LightRAG document text |
| `ingest.py` | Initializes `LightRAG(...)` (Ollama LLM/embedding funcs, local storage, concurrency, entity-type constraint), inserts the document batch |
| `query.py` | Runs a `mode="mix"`, `only_need_context=True` query against the built index |
| `run.sh` | Installs deps, checks Ollama reachability + pulled models, runs ingest and/or query |
| `README.md` | Setup/run instructions, model/context/concurrency rationale, time estimates |
| `lightrag_storage/` (generated) | LightRAG's local storage: graph (NetworkX), vector DB (NanoVectorDB), KV store, doc status — created on first `ingest.py` run |

## Next steps for whoever builds on this

1. Run `ingest.py` against the 150-product test batch once Ollama is
   live, confirm graph/vector output looks sane (check
   `lightrag_storage/` contents, run a few `query.py` calls) before
   raising `BATCH_SIZE` toward the full 20,000-product catalog.
2. Build the SQL hard-filter branch against `product_metadata`
   (`../flipkart_metadata.sql` / the MySQL container from
   `../README.md`) as a separate, independent component — it doesn't
   depend on anything in this folder.
3. Build the BM25 branch (`rank_bm25` over the *same*
   `flipkart_lightrag_corpus.md` corpus this folder reads — reuse
   `load_documents.load_md_blocks()` rather than re-parsing the file a
   third way).
4. Build the union/intersect/rerank merge step
   (`retrieval-architecture.md`'s "Candidate fusion & rerank" section)
   that combines this branch's output with the other two.
5. Only after that's working, consider the Qdrant swap and/or a hosted
   model swap — neither is needed to validate correctness of the
   retrieval logic itself.
