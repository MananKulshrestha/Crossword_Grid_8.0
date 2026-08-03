"""Ingests a batch of Flipkart product documents into LightRAG, building
both the knowledge graph and vector DB (local file storage under
WORKING_DIR) so queries can later run in "mix" mode.

Requires an Ollama server reachable at OLLAMA_HOST (see config.py) with
LLM_MODEL and EMBED_MODEL already pulled.

`ainsert()` catches per-document errors internally and marks the doc
FAILED in doc_status without raising -- a clean return isn't proof of
success. This script enqueues the whole batch in one call, then reads the
real per-document outcome back from `rag.doc_status` afterward. See
IMPLEMENTATION.md #5 for why.
"""

import asyncio
import logging
import sys

from tqdm import tqdm

from lightrag import LightRAG
from lightrag.base import DocStatus
from lightrag.kg.shared_storage import initialize_pipeline_status
from lightrag.llm.ollama import ollama_embed, ollama_model_complete
from lightrag.utils import EmbeddingFunc, setup_logger

from config import (
    BATCH_SIZE,
    DISABLE_THINKING,
    EMBED_BACKEND,
    EMBED_DIM,
    EMBED_MODEL,
    EMBEDDING_MAX_ASYNC,
    ENTITY_TYPES_GUIDANCE,
    LLM_MAX_ASYNC,
    LLM_MODEL,
    NUM_CTX,
    OLLAMA_HOST,
    WORKING_DIR,
)
from load_documents import build_documents
from multi_ollama import load_servers, MultiOllamaLoadBalancer

setup_logger("lightrag", level="INFO")


def llm_kwargs():
    kwargs = {"host": OLLAMA_HOST, "options": {"num_ctx": NUM_CTX}}
    if DISABLE_THINKING:
        kwargs["think"] = False
    return kwargs


def build_embedding_func():
    if EMBED_BACKEND == "local":
        from local_embed import local_embed

        return EmbeddingFunc(
            embedding_dim=EMBED_DIM, max_token_size=8192, func=local_embed
        )
    if EMBED_BACKEND == "ollama":
        # Use .func (unwrapped) not ollama_embed directly -- it's already an
        # EmbeddingFunc with its own baked-in dim, double-wrapping breaks
        # dimension validation. See IMPLEMENTATION.md #4.
        return EmbeddingFunc(
            embedding_dim=EMBED_DIM,
            max_token_size=8192,
            func=lambda texts: ollama_embed.func(
                texts, embed_model=EMBED_MODEL, host=OLLAMA_HOST
            ),
        )
    raise ValueError(
        f"Unknown EMBED_BACKEND {EMBED_BACKEND!r} -- expected 'local' or 'ollama'"
    )


async def build_rag():
    import os

    os.makedirs(WORKING_DIR, exist_ok=True)

    # Load servers and create load balancer
    servers = load_servers("servers.txt")
    lb = MultiOllamaLoadBalancer(servers)

    rag = LightRAG(
        working_dir=WORKING_DIR,
        llm_model_func=lb,  # Use load balancer instead of single host
        llm_model_name=LLM_MODEL,
        llm_model_kwargs={"options": {"num_ctx": NUM_CTX}, "think": not DISABLE_THINKING},
        llm_model_max_async=LLM_MAX_ASYNC,
        embedding_func=build_embedding_func(),
        embedding_func_max_async=EMBEDDING_MAX_ASYNC,
        addon_params={"entity_types_guidance": ENTITY_TYPES_GUIDANCE},
    )
    await rag.initialize_storages()
    await initialize_pipeline_status()
    return rag


async def poll_progress(rag, attempted, interval_seconds=1.0):
    """Drives a tqdm bar off rag.doc_status since ainsert() has no per-doc callback."""
    with tqdm(total=attempted, desc="Ingesting", unit="doc") as bar:
        done_so_far = 0
        while True:
            counts = await rag.doc_status.get_status_counts()
            done = counts.get(DocStatus.PROCESSED.value, 0) + counts.get(
                DocStatus.FAILED.value, 0
            )
            if done > done_so_far:
                bar.update(done - done_so_far)
                done_so_far = done
            if done_so_far >= attempted:
                break
            await asyncio.sleep(interval_seconds)


ENTITY_RELATION_SAMPLE_SIZE = 50  # cap terminal output; full data lives in lightrag_storage/


async def main():
    (
        documents,
        skipped_empty_description,
        skipped_duplicate_sku,
        skipped_duplicate_content,
    ) = build_documents(limit=BATCH_SIZE)
    attempted = len(documents)
    batch_desc = f"batch limit {BATCH_SIZE}" if BATCH_SIZE else "no limit -- full corpus"
    print(
        f"Loaded {attempted} documents for ingestion ({batch_desc}); "
        f"{skipped_empty_description} skipped for no usable description, "
        f"{skipped_duplicate_sku} skipped for a duplicate sku_id, "
        f"{skipped_duplicate_content} skipped for duplicate product_name+description "
        f"text under a different sku_id (all excluded from this count, not silently "
        f"included)"
    )

    if attempted == 0:
        print("Nothing to ingest -- exiting without touching LightRAG storage.")
        sys.exit(1)

    rag = await build_rag()

    doc_ids = [f"sku-{sku_id}" for sku_id, _ in documents]
    texts = [text for _, text in documents]

    progress_task = asyncio.create_task(poll_progress(rag, attempted))
    try:
        await rag.ainsert(texts, ids=doc_ids)
    finally:
        progress_task.cancel()
        try:
            await progress_task
        except asyncio.CancelledError:
            pass
        processed = await rag.doc_status.get_docs_by_status(DocStatus.PROCESSED)
        failed = await rag.doc_status.get_docs_by_status(DocStatus.FAILED)
        all_nodes = await rag.chunk_entity_relation_graph.get_all_nodes()
        all_edges = await rag.chunk_entity_relation_graph.get_all_edges()
        await rag.finalize_storages()

    succeeded_ids = {doc_id for doc_id in doc_ids if doc_id in processed}
    failed_ids = {doc_id for doc_id in doc_ids if doc_id in failed}
    unaccounted_ids = [
        doc_id for doc_id in doc_ids if doc_id not in processed and doc_id not in failed
    ]

    print("\n=== Ingestion summary ===")
    print(f"Attempted:            {attempted}")
    print(f"Succeeded:            {len(succeeded_ids)}")
    print(f"Failed:               {len(failed_ids)}")
    print(f"Unaccounted:          {len(unaccounted_ids)}")
    print(f"Skipped (no desc.):   {skipped_empty_description}")
    print(f"Skipped (dup. sku):   {skipped_duplicate_sku}")
    print(f"Skipped (dup. text):  {skipped_duplicate_content}")
    print(f"Storage:              {WORKING_DIR}")

    print(
        f"\n=== Extracted entities ({len(all_nodes)} total, "
        f"showing first {min(len(all_nodes), ENTITY_RELATION_SAMPLE_SIZE)}) ==="
    )
    for node in all_nodes[:ENTITY_RELATION_SAMPLE_SIZE]:
        print(
            f"  {node.get('id')} | type={node.get('entity_type')} | "
            f"description={node.get('description')}"
        )

    print(
        f"\n=== Extracted relations ({len(all_edges)} total, "
        f"showing first {min(len(all_edges), ENTITY_RELATION_SAMPLE_SIZE)}) ==="
    )
    for edge in all_edges[:ENTITY_RELATION_SAMPLE_SIZE]:
        print(
            f"  {edge.get('source')} ~ {edge.get('target')} | "
            f"description={edge.get('description')} | "
            f"keywords={edge.get('keywords')} | weight={edge.get('weight')}"
        )

    if failed_ids:
        print("\nFailed sku_ids:")
        for doc_id in doc_ids:
            if doc_id in failed_ids:
                print(f"  {doc_id}: {failed[doc_id].error_msg}")

    if unaccounted_ids:
        print("\nUnaccounted-for sku_ids (neither processed nor failed -- run did "
              "not finish cleanly, re-run ingest.py):")
        for doc_id in unaccounted_ids:
            print(f"  {doc_id}")

    if failed_ids or unaccounted_ids:
        print(
            f"\n{len(failed_ids)} failed / {len(unaccounted_ids)} unaccounted-for "
            f"out of {attempted} documents -- exiting non-zero. Nothing here was "
            f"substituted with placeholder data; re-run ingest.py to retry "
            f"(LightRAG's doc-status tracking skips already-PROCESSED ids)."
        )
        sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
