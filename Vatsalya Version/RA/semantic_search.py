"""Semantic/graph candidate generator (retrieval-architecture.md, "The three
retrieval branches" #3; plan.md Step 6) -- wraps the already-built LightRAG
index as a pure candidate-list function returning the same Candidate
currency merge.py expects from every branch.

Does not modify ingest.py or query.py. Uses query.build_query_rag() (not
ingest.build_rag()) since this is a query-time caller, not an ingestion
one -- build_query_rag() is what respects config.INFERENCE_BACKEND
(DeepInfra by default), while ingest.build_rag() is ingestion-only and
always hits the local Ollama cluster (servers.txt) regardless of that
setting (see query.py's module docstring).

No numeric similarity score is available from LightRAG's public query API.
Confirmed by reading lightrag/operate.py directly (installed lightrag-hku
1.5.5, matching this project's uv.lock pin): chunks_vdb.query()'s raw
vector-store hits do carry a similarity/distance value internally, but
naive_query() (operate.py, ~line 4590-4607) copies only content/file_path/
chunk_id/source_type into the chunk dict that survives onward -- the
similarity number is discarded before it ever reaches aquery_data()'s
public output, and the same is true of the entity/relationship passes
(their "weight" field is extraction-time confidence from our own
ENTITY_TYPES_GUIDANCE prompt, not a query-relevance score). What IS
preserved is result order -- chunks_vdb.query() returns top_k pre-sorted by
relevance and nothing downstream re-sorts before token-budget truncation --
so branch_signal here is a rank-derived value (1/rank), not a real
similarity score: ordinal within this branch only, never compared to
BM25's score (see candidate.BranchHit's docstring).

SKU resolution: documents were inserted by ingest.py with
ids=f"sku-{sku_id}", so every LightRAG-internal doc id strips back to a real
sku_id via SKU_ID_PREFIX. A chunk hit maps to exactly one SKU (its
full_doc_id, read back via the public rag.text_chunks.get_by_id(chunk_id)
KV lookup) -- unambiguous, so chunk hits are the primary signal. An entity
or relationship's source_id can reference many chunks across many different
products (GRAPH_FIELD_SEP-joined) once LightRAG has merged that entity
across the catalog -- e.g. "Cotton" appearing on hundreds of SKUs -- so
entity/relationship hits are a weaker per-SKU signal and are only used to
fill in SKUs the chunk pass missed entirely, never to outrank a chunk hit.
"""

import asyncio
import threading

from lightrag import QueryParam
from lightrag.constants import GRAPH_FIELD_SEP

from candidate import BranchHit, Candidate
from query import build_query_rag

SKU_ID_PREFIX = "sku-"

# Process-wide cached RAG instance + single persistent background event loop,
# same pattern (and same reason) as web_ui.py's run_async_in_thread(): LightRAG
# binds its internal LLM/embedding worker pools and shared-storage async locks
# to whichever event loop was running the first time the instance was built.
# An earlier version of this module called build_query_rag() fresh inside a
# plain asyncio.run() on every single search() call -- the first request
# worked (it built those pools/locks on its own throwaway loop), but every
# request after that failed with "<Lock ...> is bound to a different event
# loop" as soon as LightRAG's shared_storage tried to reuse a lock created on
# a now-closed loop. It was also enormously wasteful: every call reloaded the
# full graph/Qdrant/KV stack from disk. Keeping one rag instance and one loop
# alive for the process lifetime fixes both.
_rag_instance = None
_bg_loop = None
_bg_loop_thread = None
_bg_loop_ready = threading.Event()


async def get_rag():
    global _rag_instance
    if _rag_instance is None:
        _rag_instance = await build_query_rag()
    return _rag_instance


def _run_background_loop():
    global _bg_loop
    _bg_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_bg_loop)
    _bg_loop_ready.set()
    _bg_loop.run_forever()


def run_async_in_thread(coro):
    """Run a coroutine on the single persistent background event loop and
    block until it completes -- see the module-level comment above for why
    this can't just be asyncio.run() per call."""
    global _bg_loop_thread
    if _bg_loop_thread is None:
        _bg_loop_thread = threading.Thread(target=_run_background_loop, daemon=True)
        _bg_loop_thread.start()
        _bg_loop_ready.wait()

    future = asyncio.run_coroutine_threadsafe(coro, _bg_loop)
    return future.result()


def _sku_id_from_doc_id(doc_id: str) -> str:
    if not doc_id.startswith(SKU_ID_PREFIX):
        raise ValueError(
            f"Unexpected LightRAG doc id shape: {doc_id!r} (expected "
            f"'{SKU_ID_PREFIX}<sku_id>' -- ingest.py's insert convention may "
            f"have changed; this wrapper's SKU resolution depends on it)"
        )
    return doc_id[len(SKU_ID_PREFIX):]


def _split_source_ids(source_id: str) -> list[str]:
    return [chunk_id for chunk_id in source_id.split(GRAPH_FIELD_SEP) if chunk_id]


async def _resolve_sku_id(rag, chunk_id: str) -> str | None:
    """Reads LightRAG's own chunk KV store (a public, documented read-only
    method -- lightrag/base.py's BaseKVStorage.get_by_id) to recover the
    doc id a chunk belongs to. Returns None if the chunk id is stale/unknown
    rather than raising -- a single dangling reference from a query-time
    race isn't grounds to fail the whole search."""
    chunk_record = await rag.text_chunks.get_by_id(chunk_id)
    if not chunk_record or "full_doc_id" not in chunk_record:
        return None
    return _sku_id_from_doc_id(chunk_record["full_doc_id"])


async def _search_async(query_text: str, top_n: int) -> list[Candidate]:
    """Uses the cached process-wide rag instance (get_rag()) -- no per-call
    initialize_storages()/finalize_storages(), see the module-level comment
    on _rag_instance for why: the instance and its LightRAG-internal async
    locks live for the process lifetime, not per search() call."""
    rag = await get_rag()
    result = await rag.aquery_data(query_text, param=QueryParam(mode="mix"))
    if result.get("status") != "success":
        return []

    data = result.get("data", {})
    candidates: dict[str, Candidate] = {}
    rank = 0

    # Primary signal: chunk vector matches.
    for chunk in data.get("chunks", []):
        chunk_id = chunk.get("chunk_id")
        if not chunk_id:
            continue
        sku_id = await _resolve_sku_id(rag, chunk_id)
        if sku_id is None:
            continue
        rank += 1
        candidates.setdefault(sku_id, Candidate(sku_id=sku_id)).hits.append(
            BranchHit(
                branch="semantic",
                rank=rank,
                branch_signal=1.0 / rank,
                evidence={
                    "match_type": "chunk",
                    "chunk_id": chunk_id,
                    "snippet": chunk.get("content", "")[:280],
                },
            )
        )

    # Secondary signal: entities -- only fills in SKUs the chunk pass missed.
    for entity in data.get("entities", []):
        for chunk_id in _split_source_ids(entity.get("source_id", "")):
            sku_id = await _resolve_sku_id(rag, chunk_id)
            if sku_id is None or sku_id in candidates:
                continue
            rank += 1
            candidates.setdefault(sku_id, Candidate(sku_id=sku_id)).hits.append(
                BranchHit(
                    branch="semantic",
                    rank=rank,
                    branch_signal=1.0 / rank,
                    evidence={
                        "match_type": "entity",
                        "entity_name": entity.get("entity_name", ""),
                        "entity_type": entity.get("entity_type", ""),
                        "description": entity.get("description", ""),
                    },
                )
            )

    # Secondary signal: relationships -- same rule, fills gaps only.
    for relation in data.get("relationships", []):
        for chunk_id in _split_source_ids(relation.get("source_id", "")):
            sku_id = await _resolve_sku_id(rag, chunk_id)
            if sku_id is None or sku_id in candidates:
                continue
            rank += 1
            candidates.setdefault(sku_id, Candidate(sku_id=sku_id)).hits.append(
                BranchHit(
                    branch="semantic",
                    rank=rank,
                    branch_signal=1.0 / rank,
                    evidence={
                        "match_type": "relationship",
                        "src_id": relation.get("src_id", ""),
                        "tgt_id": relation.get("tgt_id", ""),
                        "keywords": relation.get("keywords", ""),
                        "description": relation.get("description", ""),
                    },
                )
            )

    return list(candidates.values())[:top_n]


def search(query_text: str, top_n: int = 50) -> list[Candidate]:
    """Returns up to top_n Candidates from the semantic/graph branch, in
    LightRAG's own retrieval order (chunk matches first, then entity/
    relationship-only fills -- see module docstring). Mirrors
    bm25_index.search()'s role as a pure candidate generator, but returns
    Candidate objects directly rather than raw tuples, since this branch's
    evidence is inherently structured (chunk snippets / entity / relationship
    provenance) -- see candidate.py.

    Dispatches onto the single persistent background event loop
    (run_async_in_thread) rather than asyncio.run() -- safe to call
    repeatedly, including from multiple Flask request threads, since the
    cached rag instance and its LightRAG-internal locks/worker pools all
    live on that one loop for the process lifetime."""
    return run_async_in_thread(_search_async(query_text, top_n))


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} \"your query\"")
        sys.exit(1)
    for candidate in search(" ".join(sys.argv[1:])):
        top_hit = candidate.hits[0]
        print(f"{candidate.sku_id}  rank={top_hit.rank}  match_type={top_hit.evidence.get('match_type')}")
