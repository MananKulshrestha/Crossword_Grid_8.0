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
  `addon_params={"entity_type_prompt_file": "ecommerce_catalog.yml"}`
  (`prompts/entity_type/ecommerce_catalog.yml`), which LightRAG's own
  loader validates at startup. Full before/after example in
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

`prompts/entity_type/ecommerce_catalog.yml` defines the entity-type
guidance (`PRODUCT`, `BRAND`, `CATEGORY`, `MATERIAL`, `OCCASION`, `STYLE`,
matching `retrieval-architecture.md`'s "Entity/relationship schema" table
exactly) plus two real, worked extraction examples from this catalog.
`ingest.py` passes `addon_params={"entity_type_prompt_file":
"ecommerce_catalog.yml"}`, and `config.py`'s `PROMPT_DIR` (an absolute
path) is set via `os.environ.setdefault(...)` so LightRAG finds it
regardless of the working directory `ingest.py` is launched from.

Without this, LightRAG's extraction defaults to a generic ontology
(person, organization, location, event...) which doesn't fit a shopping
catalog and would pollute the graph with irrelevant node types pulled out
of product description text — and that's exactly what would have happened
here: an earlier version of this file passed
`addon_params={"entity_types": ENTITY_TYPES}` (a bare list of type names),
which **lightrag-hku 1.5.5 does not read at all** — confirmed by a
full-package source search returning zero matches for that key anywhere in
the library. The only keys LightRAG's prompt resolver actually checks are
`entity_types_guidance` (inline text) and `entity_type_prompt_file` (a YAML
profile, validated at load time) — see `lightrag-implementation.md` section
6 for the full investigation, and for a worked before/after example showing
what the generic fallback would have produced on a real product
description.

Don't diverge from the six-type schema without updating
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

All model calls (`config.py`: `LLM_MODEL`, `EMBED_MODEL`) go through a
local Ollama server (`OLLAMA_HOST`, default `http://localhost:11345`).
This is intentional for this phase — no API keys, no external cost, uses
the project's own GPU cluster (48GB VRAM headroom). If this later moves
to a hosted model (e.g. matching the wider project's Gemini 2.5 Flash
default per `retrieval-architecture.md`), only `ingest.py`'s
`llm_model_func` / `embedding_func` wiring needs to change — LightRAG's
model interface is pluggable, nothing else in this folder assumes Ollama
specifically.

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
`graph_sampling_output/full_extraction_skus.txt` doesn't exist yet. The
same no-soft-fallback discipline applies elsewhere in this folder:

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
| `config.py` | All tunables: Ollama host, model names, context length, concurrency, Qdrant URL, prompt-profile path, graph-sampling subset path, batch size, source file paths |
| `load_documents.py` | Parses `../flipkart_lightrag_corpus.md` into `(sku_id, product_name, description)` tuples, builds the LightRAG document text |
| `graph_sampling.py` | Coverage-based selection of which SKUs get full graph extraction (see `lightrag-implementation.md` section 5); outputs to `graph_sampling_output/` |
| `prompts/entity_type/ecommerce_catalog.yml` | Entity-type guidance + worked examples for LightRAG's extraction prompt (see `lightrag-implementation.md` section 6) |
| `sql_filter.py` | SQL hard-filter branch — `eligible_skus(hard_constraints)` against `product_metadata` |
| `bm25_index.py` | BM25 lexical branch — full-catalog index over the same description-only corpus |
| `ingest.py` | Initializes `LightRAG(...)` (Ollama LLM/embedding funcs, Qdrant vector storage, entity-type prompt profile), ingests via `apipeline_enqueue_documents` + `apipeline_process_enqueue_documents` with per-SKU `process_options` from `graph_sampling.py`'s subset |
| `query.py` | Runs a `mode="mix"`, `only_need_context=True` query against the built index |
| `run.sh` | Installs deps, checks Ollama reachability + pulled models, runs ingest and/or query |
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
