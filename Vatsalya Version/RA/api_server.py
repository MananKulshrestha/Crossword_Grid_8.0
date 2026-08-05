#!/usr/bin/env python3
"""HTTP API around search_catalog() -- the RA retrieval pipeline (SQL hard
filter + BM25 lexical + LightRAG semantic/graph branch, fused and reordered
by a cross-encoder reranker; see search_catalog.py and
retrieval-architecture.md).

This is the "HTTP microservice boundary" option AGENTIC_INTEGRATION.md
(section 7) leaves open for how an outside caller reaches RA's Python code:
a thin Flask wrapper around the exact same search_catalog(query_state,
top_n) contract that file documents, so translating this endpoint's
request/response shape is the same job as translating a direct Python call
would already be.

Warm-up mirrors web_ui.py's __main__ block: every heavy model load (local
sentence-transformers embedder, BM25 index build, cross-encoder reranker,
corpus text cache) happens once, synchronously, before Flask starts
accepting connections -- see warm_up()'s docstring below for why.

Run directly (`python api_server.py`) or via ./api.sh. Listens on port 8002
by default (override with API_PORT).
"""

import os
import time

from flask import Flask, jsonify, request

import bm25_index
import candidate
import merge
from config import EMBED_BACKEND
from search_catalog import search_catalog

app = Flask(__name__)

API_PORT = int(os.environ.get("API_PORT", "8002"))


def warm_up():
    """Loads every model/index search_catalog()'s BM25 and rerank stages
    depend on before the server starts accepting requests, not on whichever
    request happens to arrive first.

    Same reasoning as web_ui.py's warmup call: torch + sentence-transformers
    imports and the first-ever model load are the expensive part (measured
    ~90-100s on a host with site-packages on a FUSE-mounted volume, per
    web_ui.py's comment) -- paying that cost at startup means every real
    request only pays actual inference time.

    Covers three of the four things a request touches:
      - local_embed's sentence-transformers embedder (only if
        EMBED_BACKEND=="local" -- this is what the semantic branch's LightRAG
        query embedding uses; see local_embed.py's warmup()).
      - bm25_index's BM25Okapi index (parses the full corpus once; see
        bm25_index.py's _load_or_build_index()).
      - candidate.py's sku_id -> corpus text cache, used by merge.py's
        cross-encoder rerank step (candidate_text()) -- a separate cache
        from BM25's own, built from the same corpus parse.
      - merge.py's CrossEncoder reranker (BAAI/bge-reranker-base by
        default, config.RERANK_MODEL).

    Deliberately does NOT touch sql_filter (sql_filter.py connects to MySQL
    fresh on every call, no persistent connection to warm -- see its
    module docstring). semantic_search's LightRAG/Qdrant connection IS
    warmed here (below): semantic_search.py caches one process-wide rag
    instance (get_rag()) on a single persistent background event loop,
    same pattern as web_ui.py's _rag_instance -- see semantic_search.py's
    module-level comment for why (repeated asyncio.run() per call used to
    both reload the whole graph/Qdrant/KV stack on every request AND crash
    on the 2nd+ request with LightRAG's shared-storage locks bound to an
    already-closed event loop). Building it now, synchronously, means the
    first real request doesn't pay that cost (or risk failing fast on bad
    DeepInfra/Qdrant config) instead of every request after warm-up.
    """
    start = time.monotonic()
    print("Warming up search_catalog (first-time model import/load can take a minute or two)...")

    if EMBED_BACKEND == "local":
        import asyncio

        from local_embed import warmup as embed_warmup

        print("  - loading local embedding model (local_embed.py)...")
        asyncio.run(embed_warmup())

    print("  - loading/building BM25 index (bm25_index.py)...")
    bm25_index._load_or_build_index(verbose=True)

    print("  - loading SKU corpus text cache (candidate.py)...")
    candidate._load_sku_text_lookup()

    print("  - loading cross-encoder reranker (merge.py)...")
    merge._get_cross_encoder()

    print("  - building LightRAG query instance (semantic_search.py)...")
    import semantic_search

    semantic_search.run_async_in_thread(semantic_search.get_rag())

    print(f"Warm-up complete in {time.monotonic() - start:.1f}s. Query engine ready.\n")


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/api/search", methods=["POST"])
def api_search():
    """Request body (JSON):
      soft_query_text: str, required -- the search text (drives BM25 +
        semantic search). "query" is accepted as an alias.
      full_query_text: str, optional -- defaults to soft_query_text; what
        the cross-encoder reranks against.
      hard_constraints: dict, optional -- any of max_price, category,
        size, stock_status (sql_filter.py's _CONSTRAINT_CLAUSES). An
        unknown key is a caller bug and returns 400, matching
        sql_filter.eligible_skus()'s own ValueError-on-unknown-key
        behavior.
      top_n: int, optional, default 20.

    Response body (JSON): {"query": ..., "results": [...]}, where each
    result is exactly the dict shape search_catalog() returns (sku_id,
    rerank_score, metadata, branches, evidence) -- see search_catalog.py's
    docstring.
    """
    data = request.get_json(silent=True) or {}

    soft_query_text = data.get("soft_query_text") or data.get("query")
    if not soft_query_text or not isinstance(soft_query_text, str):
        return jsonify({"error": "soft_query_text (or query) is required"}), 400

    full_query_text = data.get("full_query_text", soft_query_text)
    hard_constraints = data.get("hard_constraints", {})
    if not isinstance(hard_constraints, dict):
        return jsonify({"error": "hard_constraints must be an object"}), 400

    top_n = data.get("top_n", 20)
    try:
        top_n = int(top_n)
    except (TypeError, ValueError):
        return jsonify({"error": "top_n must be an integer"}), 400

    try:
        results = search_catalog(
            {
                "soft_query_text": soft_query_text,
                "full_query_text": full_query_text,
                "hard_constraints": hard_constraints,
            },
            top_n=top_n,
        )
    except ValueError as e:
        # sql_filter.eligible_skus() raises ValueError on an unknown
        # hard_constraints key -- a caller bug, surfaced as 400 rather than
        # a generic 500.
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"search_catalog error: {e}")
        import traceback

        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

    return jsonify({"query": soft_query_text, "results": results})


if __name__ == "__main__":
    print(f"\n{'=' * 60}")
    print("RA search_catalog API")
    print(f"{'=' * 60}")
    warm_up()
    print(f"Starting server on http://0.0.0.0:{API_PORT}")
    print(f"  POST /api/search   {{\"soft_query_text\": \"...\", \"hard_constraints\": {{}}, \"top_n\": 20}}")
    print(f"  GET  /health\n")
    app.run(host="0.0.0.0", port=API_PORT, debug=False)
