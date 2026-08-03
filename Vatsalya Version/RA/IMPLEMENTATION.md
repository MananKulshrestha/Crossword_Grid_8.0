# Implementation Notes — `RA/` LightRAG Semantic/Graph Branch

Implements **one of three** retrieval branches described in
`retrieval-architecture.md` for the `search_catalog` tool's internals:
the **semantic/graph branch**, via LightRAG. Does not implement the SQL
hard-filter branch or the BM25 lexical branch.

Read `../IMPLEMENTATION.md` first — it explains the upstream pipeline
(`flipkart_to_lightrag.py`) that produces the three source files this
folder consumes.

## What exists and what doesn't

**Built:**
- LightRAG ingestion (`ingest.py`) over `../flipkart_lightrag_corpus.md`
  → knowledge graph + vector index via local file storage.
- A `mix`-mode query script (`query.py`) returning raw grounded context.
- Ollama as the LLM/embedding backend (local, no API key/cost).
- NanoVectorDB + NetworkX for vector/graph storage (not Qdrant). Swapping
  to Qdrant later means changing `vector_storage="QdrantVectorDBStorage"`
  in `ingest.py`'s `LightRAG(...)` call — nothing else depends on the
  storage backend.

**Not built:** SQL hard-filter branch, BM25 lexical branch, the
union/intersect/rerank merge step, cross-encoder reranking, wiring into
an actual `search_catalog(query_state)` function. This folder is a
standalone ingestion/query harness, not yet plugged into a larger
pipeline.

## Design decisions and why

### 1. Corpus is description-only

`load_documents.py` builds each document as `product_name + "\n\n" +
description`, parsed straight out of `flipkart_lightrag_corpus.md` — it
does **not** pull in brand/category/material/price from
`flipkart_catalog_structured.jsonl`. Those live in `product_metadata` and
are never duplicated into the text corpus (`retrieval-architecture.md`'s
"one text corpus, two search engines" rule) — duplicating them risks the
graph disagreeing with the relational store, and risks BM25/LightRAG
indexing different content for the same SKU once BM25 is added.

**If extending**: any field you're tempted to add to the LightRAG input
text should first be checked against `flipkart_metadata.sql` — if it's
already a column there, it doesn't belong in the text corpus.

### 2. Entity extraction is scoped to a fixed type list, via the right addon_params key

`config.py`'s `ENTITY_TYPES_GUIDANCE` is passed into `LightRAG(...,
addon_params={"entity_types_guidance": ENTITY_TYPES_GUIDANCE})`. Without
this, extraction defaults to LightRAG's generic ontology (Person,
Organization, Location, Event, Concept, Artifact...) that's the wrong
shape for a product catalog, *and* tends to extract generic attribute
labels instead of specific values.

**Bug that was live for a while**: this folder originally passed
`addon_params={"entity_types": ENTITY_TYPES}` (a plain list of type
names) — but no code path in this LightRAG version reads an
`entity_types` addon_param at all; only `entity_types_guidance` (a full
guidance text string) is read (see `lightrag/lightrag.py`'s `__post_init__`,
which raises on the *old* `ENTITY_TYPES` env var and explicitly points at
`entity_types_guidance` as the replacement). Passing the wrong key fails
silently — no error, just a silent fallback to the generic default
ontology — which is why early extraction output looked generic/off-topic
(flat single-word entities like "Polyester", "Festive" instead of typed
values like "Round Neck" / Neckline, "Maroon" / Color). Fixed by
switching to `entity_types_guidance` and writing it as a rich guidance
string that both lists the schema *and* instructs the model to extract
specific values, not category labels.

**If this stops working again**: verify with
`lightrag.prompt.resolve_entity_extraction_prompt_profile({"entity_types_guidance": ...}, use_json=False)`
that the returned profile's `entity_types_guidance` actually matches what
was passed — don't just trust that the addon_params key you're using is
correct, this class of bug (right-shaped dict, wrong key, no error) is
exactly what caused this.

Matches `retrieval-architecture.md`'s entity/relationship schema in
spirit — don't diverge without updating that doc too.

### 3. Query returns raw context, not a generated answer

`query.py` uses `QueryParam(mode="mix", only_need_context=True)`. Without
that flag, LightRAG re-calls its LLM to synthesize prose — wrong here,
since response generation belongs to the outer chat layer and
`search_catalog` needs ranked candidates, not display text.

### 4. Ollama, not a hosted API

All model calls (`config.py`: `LLM_MODEL`, `EMBED_MODEL`) go through a
local Ollama server. If this later moves to a hosted model, only
`ingest.py`'s `llm_model_func`/`embedding_func` wiring needs to change.

**Footgun to not reintroduce**: `lightrag.llm.ollama.ollama_embed` is
already decorated with `@wrap_embedding_func_with_attrs(embedding_dim=1024,
...)` (tuned for `bge-m3`) — it's an `EmbeddingFunc` instance, not a plain
function. Calling `ollama_embed(...)` directly and wrapping *that* in our
own `EmbeddingFunc(embedding_dim=EMBED_DIM, ...)` double-wraps it: the
inner, already-decorated function validates its own output against its
own baked-in 1024 before our declared dim is ever consulted, so real
768-dim `nomic-embed-text` output fails with `ValueError: Embedding
dimension mismatch...`. Fix: use `ollama_embed.func(...)` (the unwrapped
function) so only our outer `EmbeddingFunc` validates. If `EMBED_MODEL`
changes, confirm its real output dim (`curl $OLLAMA_HOST/api/embed -d
'{"model":"...","input":"test"}'`) against `config.py`'s `EMBED_DIM`.

### 5. Per-document error tracking, no soft fallbacks

`LightRAG.ainsert()` awaits its own pipeline
(`apipeline_enqueue_documents` → `apipeline_process_enqueue_documents`),
but that pipeline catches extraction/embedding failures internally, logs
them, and marks the doc `FAILED` in `doc_status` — it does **not**
re-raise. A clean `ainsert()` return is not proof of success.

`ingest.py` enqueues the whole batch in one `ainsert(texts, ids=doc_ids)`
call, then reads the real outcome for every `doc_id` back from
`rag.doc_status` (`get_docs_by_status(DocStatus.PROCESSED)` /
`FAILED`) — that's the only source of truth, not exception presence. A
`doc_id` in neither set (stuck `PENDING`/`PROCESSING`) is treated as
`unaccounted`, counted as a failure.

*Don't hand-roll per-document concurrency* with a semaphore around
individual `ainsert()` calls — `apipeline_process_enqueue_documents()`
already drains and concurrency-limits whatever's enqueued, so concurrent
`ainsert()` calls race each other over one shared queue instead of giving
isolated inserts, which also breaks the exception-watching approach above
(it never fires on the failures that matter).

Every attempted SKU ends the run in one of four states: succeeded
(`PROCESSED`), failed (`FAILED`, `error_msg` printed), unaccounted
(treated as failure), or skipped upstream (no description, duplicate
sku_id, or duplicate content — see below — all counted by
`load_documents.build_documents()`). `ingest.py` exits non-zero and lists
every failed/unaccounted `sku_id` if anything went wrong — never
substitutes a placeholder. `reset.sh` wipes `lightrag_storage/` for a
clean retry.

**Content-hash duplicates are deduped upstream, not left to LightRAG.**
~2,462 rows in the source data share byte-identical `product_name`+
description text under a different `sku_id` (apparent duplicate/near-
duplicate listings — a known issue in scraped catalog data, distinct from
the 2 literal `sku_id` collisions handled the same way). LightRAG has its
own content-hash dedup in `apipeline_enqueue_documents`, but it stores
the duplicate under a synthetic `dup-<hash>` doc_id with status `FAILED`
— **not** under the original `sku-{sku_id}` id `ingest.py` tracks. That
means our own `doc_status` lookups never see a terminal state for that
`sku_id` under the id we're watching, so it would misreport as
`unaccounted` (an unresolved failure) on every single run, and since
`sku-{sku_id}` never becomes a known `doc_status` key, LightRAG would
re-detect and re-log the same duplicate on every resume, forever, instead
of ever resolving it. `load_documents.build_documents()` dedupes by
`sha256(text)` before anything reaches LightRAG, exactly mirroring the
`sku_id` dedup precedent, so this never reaches LightRAG's opaque side
channel at all.

Same discipline elsewhere: `load_documents.load_md_blocks()` raises
`CorpusParseError` on a malformed block (missing `Product ID:` or `##
Description`) rather than skipping it silently — that's a data-integrity
bug, distinct from the expected/counted case of an empty description.
`query.py` requires an explicit question argument, no default question.

**If extending**: don't add a `.get(key, default)` or `try/except: pass`
that quietly substitutes a value on missing/failed data. Either surface
the count explicitly (expected gap) or raise/hard-fail loudly with
context (unexpected failure).

### 6. Model and concurrency are coupled settings

`config.py`'s `LLM_MODEL`/`LLM_MAX_ASYNC`/`EMBEDDING_MAX_ASYNC` comments
are the source of truth for current values — this section explains why
they're coupled, not what they currently are.

The cluster is 4x 11GB GPUs, shared with other jobs. A 31B-class model
tensor-splits across two cards, leaving less VRAM headroom for
concurrency (a `CUDA error: illegal memory access` crash was hit running
a large model at high concurrency). A smaller model (e.g. `gemma4:e4b`)
fits one card with headroom for higher concurrency, but was tried and
reverted: it frequently failed LightRAG's strict 5-field `RELATION`
format (`LLM output format error; found 4/5 fields...`), and every
relation failing that check gets **silently dropped** by LightRAG itself
(`lightrag/operate.py` returns `None` on a field-count mismatch) — real
graph data was lost, not just extraction running slower.

**If changing `LLM_MODEL`**: check the `LLM output format error` warning
rate in the logs before trusting the result, not just wall-clock time.
**If changing concurrency**: don't raise it without confirming the model
in use fits on a single card — check `config.py`'s comments for the
current safe pairing.

`NUM_CTX` (default 16384) covers the ~2-3k token extraction system prompt
plus chunk text plus output/gleaning headroom. `DISABLE_THINKING = True`
forwards `think: false` to Ollama so reasoning models skip
chain-of-thought during extraction.

## File map

| File | Role |
|---|---|
| `config.py` | Single source of truth for all tunables: Ollama host, model names, context length, concurrency, entity types, batch size, source file paths |
| `load_documents.py` | Parses `../flipkart_lightrag_corpus.md` into `(sku_id, product_name, description)` tuples, builds the LightRAG document text |
| `ingest.py` | Initializes `LightRAG(...)`, inserts the document batch, tracks per-doc outcomes |
| `query.py` | Runs a `mode="mix"`, `only_need_context=True` query against the built index |
| `run.sh` | Reads defaults from `config.py`, checks Ollama reachability + pulled models, runs ingest and/or query |
| `reset.sh` | Wipes `lightrag_storage/` for a clean retry |
| `lightrag_storage/` (generated) | LightRAG's local storage: graph (NetworkX), vector DB (NanoVectorDB), KV store, doc status |

## Next steps for whoever builds on this

1. `BATCH_SIZE` defaults to `None` (full ~20,000-product corpus) and is
   resumable across interruptions (§5) — confirm graph/vector output
   looks sane on a `LIGHTRAG_BATCH_SIZE=150` test batch first if you
   haven't already, before committing to the full run's wall-clock time.
2. Build the SQL hard-filter branch against `product_metadata` — doesn't
   depend on anything in this folder.
3. Build the BM25 branch (`rank_bm25` over the same corpus — reuse
   `load_documents.load_md_blocks()` rather than re-parsing separately).
4. Build the union/intersect/rerank merge step combining all three
   branches.
5. Only after that's working, consider a Qdrant/hosted-model swap.
