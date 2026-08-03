# Retrieval Build Plan

Step-by-step plan for finishing the retrieval subsystem exactly as
specified in `retrieval-architecture.md` (root of the repo). This plan
takes the already-built semantic/graph branch (this folder, `RA/`, running
on Ollama + LightRAG's local storage) all the way to Tier 3 (Qdrant-backed),
adds the SQL hard-filter branch and the BM25 lexical branch, and assembles
the `search_catalog(query_state)` entrypoint — every branch, every
guardrail, and the exact merge/rerank logic the doc specifies.

**Ground rules for every step below:**
- Build the piece → test it against the real running dependency it needs
  (Docker MySQL, Docker Qdrant, live Ollama, or a completed ingest) → only
  commit once the test passes → push straight to `Data` (matches how the
  two prior RA commits were pushed — no feature branch/PR for this track).
- A step is never "done" without a green test where a test applies. Don't
  move to the next step on a failing one.
- `Vatsalya Version/` is the canonical copy of every data/schema file.
  There are stale, untracked duplicates at the repo root — ignore them,
  don't build against them.

## Decisions locked in before writing any code (read first)

1. **Going straight to Tier 3, skipping Chroma.** `retrieval-architecture.md`'s
   "Tiered scope" section describes Tier 1 (SQL+BM25+Chroma) → Tier 2
   (+rerank) → Tier 3 (Qdrant+LightRAG) as a rollout order, but its own
   **Status** section calls Tier 3 "the target... decided," and frames
   Tier 1/2 as "the fallback if Qdrant/LightRAG don't fit the build
   calendar." Since LightRAG is already built and working (past Chroma),
   this plan skips Chroma entirely and finishes Tier 3 directly: SQL + BM25
   + Qdrant-backed LightRAG + cross-encoder rerank.
2. **Keeping Ollama, not switching to Gemini.** The doc's LightRAG
   ingestion section says to point extraction at "Gemini 2.5 Flash." The
   already-built code deliberately uses Ollama instead (see
   `RA/IMPLEMENTATION.md` — intentional, temporary, cost-saving, with a
   stated migration path). This plan keeps Ollama. This is the **one**
   place the finished code will read "Ollama" where the doc's prose says
   "Gemini 2.5 Flash" — a deliberate, documented exception, not a silent
   drift.
3. Everything else in this plan matches the doc exactly: the SQL
   hard-filter branch, the BM25 branch (same tokenizer/`rank_bm25`/
   query-time snippet the doc gives), the union → intersect-with-eligible →
   cross-encoder-rerank merge order (doc's diagram and step table,
   verbatim), the entity/relationship schema (already matches, unchanged),
   `only_need_context=True`, and the `search_catalog(query_state)` external
   contract from `architecture.md`.
4. No `CLAUDE.md` exists anywhere in this repo. The doc's Open Decisions
   #1 ("update `CLAUDE.md`'s stack line") and #2 ("LightRAG dependency
   sign-off via `CLAUDE.md`") can't literally be executed — there's no file
   to edit. Treat the planning conversation that approved this plan as the
   sign-off for LightRAG, Qdrant, and `rank_bm25` as approved dependencies,
   in place of a `CLAUDE.md` entry.

---

## Step 0 — Housekeeping + Open Decisions

- Add `RA/.gitignore`:
  ```
  lightrag_storage/
  bm25_storage/
  __pycache__/
  *.pyc
  .pytest_cache/
  ```
- Fix `retrieval-architecture.md`'s embedded `product_metadata` schema
  block: it still lists both `stock_quantity` and `quantity` columns — the
  real, built table (`Vatsalya Version/flipkart_metadata.sql`) collapsed
  these into a single `quantity` column (see
  `Vatsalya Version/IMPLEMENTATION.md`). The doc's own Status section calls
  the SQL store "already built... authoritative," so the real schema wins;
  this is a one-line transcription fix, not a design change.
- Don't add every dependency to `requirements.txt` up front — each gets
  added in the step that first needs it, so the file always reflects what's
  actually in use.

No test (documentation/config only). Commit: `RA: housekeeping + resolve doc schema drift`

---

## Step 1 — Validate existing LightRAG/Ollama mechanics (pre-Qdrant)

Nothing new here — the ingestion/query code has never actually been run
end-to-end (no `lightrag_storage/` exists yet). Validating it now, against
cheap local storage, isolates "is LightRAG/Ollama working" from "is Qdrant
working" as two separate checkpoints in Step 3, instead of debugging both
at once.

1. Start the Ollama server reachable at `OLLAMA_HOST`, with `LLM_MODEL` and
   `EMBED_MODEL` (see `config.py`) already pulled.
2. `./run.sh ingest` — ingests the 150-product test batch (`BATCH_SIZE` in
   `config.py`). Confirm the printed `=== Ingestion summary ===` block
   shows `Failed: 0`. If anything failed, fix it before moving on.
3. `./run.sh query "your question"` a few times with representative
   shopper questions. Sanity-check that the returned context references
   real, relevant products.

No automated test — infra/model validation checkpoint. Commit only if
something had to be fixed.

---

## Step 2 — Provision Qdrant

Same Docker pattern already used for MySQL (`Vatsalya Version/README.md`):

```bash
docker run -d \
  --name flipkart-qdrant \
  -p 6333:6333 \
  -p 6334:6334 \
  qdrant/qdrant
```

Document connection details (host `127.0.0.1`, REST port `6333`, gRPC port
`6334`) in a new "Qdrant Setup" section of `RA/README.md`, matching the
style of `Vatsalya Version/README.md`'s MySQL section.

**Test:** `curl http://localhost:6333/collections` returns a valid (empty)
JSON response — confirms the container is actually reachable before any
code gets wired against it.

Commit & push: `RA: document and provision Qdrant`

---

## Step 3 — Swap LightRAG to Qdrant-backed vector storage

Edit `ingest.py`'s `LightRAG(...)` call: add
`vector_storage="QdrantVectorDBStorage"` plus whatever connection kwargs
LightRAG's Qdrant backend needs (host/port from Step 2), exactly as the doc
specifies ("set `vector_storage=\"QdrantVectorDBStorage\"` and point it at
a Qdrant instance instead of LightRAG's default `NanoVectorDBStorage`").

Re-run `./run.sh ingest` against the same 150-product batch — this time
vectors land in Qdrant instead of local `NanoVectorDBStorage`.

**Test:** `RA/tests/test_qdrant_ingest.py`
- After ingest, query Qdrant directly via `qdrant_client` and assert the
  expected collection exists with a point count matching the successfully-
  ingested SKU count from the ingestion summary.
- Re-run the same sample queries from Step 1 and confirm comparable
  results (same/similar top candidates) now that retrieval reads from
  Qdrant instead of local storage.

**Dependency to add:** `qdrant-client`

Commit & push: `RA: swap LightRAG to Qdrant-backed vector storage`

---

## Step 4 — SQL hard-filter branch

**File:** `RA/sql_filter.py`

Connects to the Docker MySQL container (`Vatsalya Version/README.md` has
the connection details — host `127.0.0.1`, port `3307`, db `flipkart`,
table `product_metadata`; read these from env vars with those values as
defaults, same pattern `config.py` already uses for `OLLAMA_HOST`).

```python
def eligible_skus(hard_constraints: dict) -> list[dict]:
    """hard_constraints keys (all optional): max_price, category, size,
    stock_status. Returns matching product_metadata rows (as dicts) --
    never returns a row that violates any given constraint. Matches the
    doc's step 1: price cap, stock_status != 'out_of_stock', category,
    size."""
```

Build the `WHERE` clause parametrically (never string-format user values
into SQL) — one clause per constraint key present in the dict, `AND`ed
together. No constraints given → returns everything. Uses the real,
collapsed schema (single `quantity` column, per Step 0's fix).

**Test:** `RA/tests/test_sql_filter.py` (pytest)
- Price cap only → every returned row's `discounted_price` <= cap.
- Category + `stock_status='in_stock'` → every row matches both.
- No constraints → row count equals the table's total row count.
- A constraint combination that should exclude a known SKU
  (deterministically found via a direct query first) → confirm it's absent.

**Dependencies to add:** `PyMySQL`, `pytest`

Commit & push: `RA: add SQL hard-filter branch`

---

## Step 5 — BM25 lexical branch

**File:** `RA/bm25_index.py`

Reuses `load_documents.load_md_blocks()` (already returns
`[(sku_id, product_name, description), ...]`) so BM25 indexes the exact
same description-only text LightRAG does — the doc's "one text corpus, two
search engines" rule.

Implements the doc's exact query-time shape:

```python
scores = bm25_index.get_scores(tokenize(soft_query_text))
top_bm25_candidates = top_n(scores, n=50)
```

as:

```python
def search(query_text: str, top_n: int = 50) -> list[tuple[str, float]]:
    """Returns [(sku_id, bm25_score), ...] sorted descending by score."""
```

Build a `rank_bm25.BM25Okapi` index over tokenized `product_name +
description` per SKU (lowercase, strip punctuation, whitespace split — no
stemming needed at this catalog size, per the doc). Persist it under
`RA/bm25_storage/` (pickle) so it isn't rebuilt on every process start —
the same local-persistence pattern the doc compares it to for Chroma.

**Test:** `RA/tests/test_bm25.py`
- Build the index against the same 150-product batch.
- Pick 2-3 SKUs, grab a distinctive keyword straight out of their actual
  description (a brand name, a material term), query for it, assert that
  SKU appears in the top handful of results — matches the doc's stated
  purpose (catching exact tokens semantic search can smooth over).

**Dependency to add:** `rank_bm25`

Commit & push: `RA: add BM25 lexical branch`

---

## Step 6 — Wrap the Qdrant-backed semantic branch as a candidate function

**File:** `RA/semantic_search.py`

Same call `query.py` already makes (`rag.query(soft_query_text,
param=QueryParam(mode="mix", only_need_context=True))`), now reading from
Qdrant per Step 3, wrapped to return the same shape Step 5 returns:

```python
def search(query_text: str, top_n: int = 50) -> list[tuple[str, float]]:
    """Returns [(sku_id, rank_or_score), ...]."""
```

**Resolve this first, before writing the rest of the file:**
`only_need_context=True` returns raw retrieved context (chunks / entities /
relations), not a clean SKU list. Documents were inserted with
`ids=f"sku-{sku_id}"` (see `ingest.py`) — run one real query, print/inspect
the actual returned context structure, and confirm exactly which field
carries that ID back (chunk source ID, entity source-doc reference, etc.)
and how to strip the `sku-` prefix reliably. Write that finding as a short
comment at the top of the file once confirmed.

**Test:** `RA/tests/test_semantic_search.py` — same shape as Step 5's
test: a few representative queries, assert real/relevant SKUs come back.

Commit & push: `RA: wrap Qdrant-backed semantic branch as a candidate function`

---

## Step 7 — Candidate fusion & rerank

**File:** `RA/merge.py`

Implements the doc's diagram and step table exactly, in order:

```python
def fuse_and_rerank(query_text: str, hard_constraints: dict,
                     top_n: int = 20) -> list[dict]:
```

1. `eligible = sql_filter.eligible_skus(hard_constraints)` → the eligible
   `sku_id` set (Step 4).
2. `bm25_candidates = bm25_index.search(soft_query_text)` (Step 5).
3. `semantic_candidates = semantic_search.search(soft_query_text)` (Step 6).
4. `union = bm25_candidates ∪ semantic_candidates`, de-duplicated by
   `sku_id` — **union, not intersection**: per the doc, "a SKU only needs
   to be found by one of the two text-search branches to be considered."
5. `surviving = union ∩ eligible` — **the one mandatory, non-negotiable
   intersection**. Anything not SQL-eligible is dropped here,
   unconditionally, regardless of lexical/semantic score.
6. If `surviving` is empty → return empty (caller runs
   `assess_retrieval_confidence`).
7. Cross-encoder rerank (`sentence_transformers.CrossEncoder`, a BGE
   reranker model) `surviving` against the **full original query text**
   (not just `soft_query_text`) — exactly as the doc specifies, so hard-
   constraint phrasing still influences final ranking. Return the top
   `top_n`.

RRF (Reciprocal Rank Fusion) is **deliberately not built here** — the doc
flags it only as a future lever if full reranking becomes too slow at
catalog scale. Leave a code comment noting this, don't build it
speculatively.

**Test:** `RA/tests/test_merge.py`
- Construct a query where the lexical/semantic branches would surface an
  item that a hard constraint (e.g. `max_price`) should exclude. Assert
  that item is absent from the final output.
- Assert the final ordering differs from the raw BM25-only or
  semantic-only order for at least one query (proves the rerank step is
  actually doing something, not just passing candidates through).

**Dependency to add:** `sentence-transformers`

Commit & push: `RA: add candidate fusion and cross-encoder rerank`

---

## Step 8 — `search_catalog(query_state)` entrypoint

**File:** `RA/search_catalog.py`

The actual external-contract function from `architecture.md` — unchanged
signature and output shape:

```python
def search_catalog(query_state: dict) -> list[dict]:
    """query_state carries hard constraints + soft/semantic query text.
    Returns ranked eligible candidates: sku_id, rank/score, matched-
    attribute evidence, source pass (sql/bm25/semantic). Output shape
    identical to today's search_catalog contract, per the doc's step 7."""
```

Splits `query_state` into `hard_constraints` (→ `sql_filter`) and
`soft_query_text` (→ `merge.fuse_and_rerank`), calls it, and shapes the
output into the candidate format `retrieval-architecture.md` specifies.

**Test:** `RA/tests/test_search_catalog.py` — 3-4 representative
`query_state` fixtures run through the real end-to-end function (e.g.
"cotton formal shirt under ₹1500, size M"), asserting hard constraints are
respected and results are non-empty and plausible.

Commit & push: `RA: add search_catalog entrypoint`

---

## Step 9 — End-to-end smoke pass + docs reconciliation

- `pytest RA/tests/` — the full suite together, with Docker MySQL, Docker
  Qdrant, Ollama, and the built BM25/LightRAG indexes all up
  simultaneously. Confirms nothing that passed in isolation breaks once
  everything runs together (resource contention, import-order issues,
  stale-index-vs-live-DB mismatches).
- Update `RA/README.md` and `RA/IMPLEMENTATION.md`: move SQL/BM25/merge/
  Qdrant off the "not built" list, add a short description of
  `sql_filter.py`, `bm25_index.py`, `semantic_search.py`, `merge.py`,
  `search_catalog.py` in the same style already used for the existing
  files, and update the "Ollama, not Gemini" note to reference this plan's
  Decision #2 explicitly rather than leaving it as an open migration item.
- Update `retrieval-architecture.md`'s **Status** section: mark Tier 3 as
  built (not just "the target"), note that Tier 1/2 (Chroma) were
  deliberately skipped per this plan's Decision #1, and note that Open
  Decisions #1/#2 were resolved via the planning conversation rather than a
  `CLAUDE.md` edit (Decision #4).

Commit & push: `RA: end-to-end retrieval smoke pass + docs reconciliation`

---

## Reference

- Target design: `retrieval-architecture.md` (repo root)
- Existing semantic branch code: `load_documents.py`, `ingest.py`,
  `query.py`, `config.py` (this folder)
- SQL connection details: `Vatsalya Version/README.md`
- Current status / design decisions already made: `RA/IMPLEMENTATION.md`
