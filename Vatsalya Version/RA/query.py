"""Query the LightRAG index built by ingest.py, in "mix" mode (combines
knowledge-graph traversal with vector retrieval).

Returns raw grounded context (chunks/entities/relationships + source SKU
references) rather than an LLM-synthesized prose answer -- per the
retrieval-architecture doc, response generation belongs to the outer
chat/response-writer layer, not to this retrieval step. This is what a
future search_catalog integration would consume and pass into the
candidate merger, not a final answer to show a user directly.

Query-time LLM calls (keyword extraction / reasoning during retrieval) go
through config.INFERENCE_BACKEND -- "deepinfra" (default) or "ollama". This
is independent of ingest.py's build_rag(), which ALWAYS uses the Ollama
cluster regardless of this setting (see config.py's INFERENCE_BACKEND
comment) -- DeepInfra is never used for ingestion.

Usage:
    python query.py "your question here"
"""

import asyncio
import os
import sys

from lightrag import LightRAG, QueryParam
from lightrag.kg.shared_storage import initialize_pipeline_status

from config import (
    DEEPINFRA_EMBED_MODEL,
    DEEPINFRA_LLM_MODEL,
    EMBEDDING_MAX_ASYNC,
    INFERENCE_BACKEND,
    LLM_MAX_ASYNC,
    LLM_MODEL,
    QDRANT_URL,
    QUERY_EMBED_BACKEND,
    WORKING_DIR,
)
from ingest import embedding_func as ingest_embedding_func
from multi_ollama import load_servers, MultiOllamaLoadBalancer

os.environ.setdefault("QDRANT_URL", QDRANT_URL)


async def build_query_rag():
    """Builds a LightRAG instance for QUERYING (query.py) only -- distinct
    from ingest.py's build_rag(), which is ingestion-only. embedding_func
    stays pinned to QUERY_EMBED_BACKEND (defaults to matching ingestion's
    EMBED_BACKEND -- see config.py) so query vectors land in the same space
    as what's already in Qdrant; only the LLM call is free to switch to
    DeepInfra."""
    os.makedirs(WORKING_DIR, exist_ok=True)

    # An Ollama load balancer is only needed if something here actually
    # talks to the Ollama cluster: embedding via QUERY_EMBED_BACKEND=="ollama"
    # or the query LLM itself via INFERENCE_BACKEND=="ollama".
    load_balancer = None
    if QUERY_EMBED_BACKEND == "ollama" or INFERENCE_BACKEND == "ollama":
        servers = load_servers("servers.txt")
        load_balancer = MultiOllamaLoadBalancer(servers)

    if QUERY_EMBED_BACKEND == "deepinfra":
        from lightrag.utils import EmbeddingFunc

        from config import EMBED_DIM
        from deepinfra_llm import deepinfra_embed

        embed_fn = EmbeddingFunc(
            embedding_dim=EMBED_DIM,
            max_token_size=8192,
            model_name=DEEPINFRA_EMBED_MODEL,
            func=lambda texts: deepinfra_embed(texts, model=DEEPINFRA_EMBED_MODEL),
        )
    else:
        embed_fn = ingest_embedding_func(load_balancer)

    if QUERY_EMBED_BACKEND == "local":
        # Same reasoning as ingest.py's build_rag(): force the
        # sentence-transformers model to load now, synchronously, instead of
        # letting the first real query's embedding call race LightRAG's
        # internal 60s worker timeout (routinely lost on a cold model load --
        # see local_embed.warmup()). Matters even more here than in
        # ingest.py: a caller like web_ui.py builds this rag instance once
        # and reuses it for every subsequent request, so without this only
        # the very first query pays (and risks losing to) the cold-load cost.
        from local_embed import warmup

        await warmup()

    if INFERENCE_BACKEND == "deepinfra":
        from deepinfra_llm import DeepInfraLLM

        llm_func = DeepInfraLLM(DEEPINFRA_LLM_MODEL)
        llm_model_name = DEEPINFRA_LLM_MODEL
    elif INFERENCE_BACKEND == "ollama":
        llm_func = load_balancer
        llm_model_name = LLM_MODEL
    else:
        raise ValueError(
            f"Unknown INFERENCE_BACKEND: {INFERENCE_BACKEND!r} (expected 'deepinfra' or 'ollama')"
        )

    rag = LightRAG(
        working_dir=WORKING_DIR,
        vector_storage="QdrantVectorDBStorage",
        llm_model_func=llm_func,
        llm_model_name=llm_model_name,
        llm_model_max_async=LLM_MAX_ASYNC,
        embedding_func=embed_fn,
        embedding_func_max_async=EMBEDDING_MAX_ASYNC,
    )
    await rag.initialize_storages()
    await initialize_pipeline_status()
    return rag


async def main():
    if len(sys.argv) < 2:
        print("Usage: python query.py \"your question here\"", file=sys.stderr)
        sys.exit(1)
    question = " ".join(sys.argv[1:])

    rag = await build_query_rag()
    context = await rag.aquery(
        question, param=QueryParam(mode="mix", only_need_context=True)
    )
    print(context)
    await rag.finalize_storages()


if __name__ == "__main__":
    asyncio.run(main())
