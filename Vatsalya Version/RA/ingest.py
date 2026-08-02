"""Ingests a batch of Flipkart product documents into LightRAG, building
both the knowledge graph and vector DB (local file-based storage under
WORKING_DIR) so queries can later run in "mix" mode.

Requires an Ollama server reachable at OLLAMA_HOST (see config.py) with
LLM_MODEL and EMBED_MODEL already pulled.

Ingests one document at a time (concurrency capped by a semaphore at
LLM_MAX_ASYNC) rather than a single batched rag.ainsert() call, so a
failure on one SKU is caught, attributed, and counted individually
instead of being invisible inside a batch call or silently aborting the
whole run. At the end, every attempted SKU is accounted for: succeeded,
failed (with its exception), or skipped upstream (no description). If
anything failed, the run exits non-zero and prints exactly which SKUs
failed and why -- no silent partial success.
"""

import asyncio
import logging
import sys

from lightrag import LightRAG
from lightrag.kg.shared_storage import initialize_pipeline_status
from lightrag.llm.ollama import ollama_embed, ollama_model_complete
from lightrag.utils import EmbeddingFunc, setup_logger

from config import (
    BATCH_SIZE,
    DISABLE_THINKING,
    EMBED_DIM,
    EMBED_MODEL,
    EMBEDDING_MAX_ASYNC,
    ENTITY_TYPES,
    LLM_MAX_ASYNC,
    LLM_MODEL,
    NUM_CTX,
    OLLAMA_HOST,
    WORKING_DIR,
)
from load_documents import build_documents

setup_logger("lightrag", level="INFO")


def llm_kwargs():
    kwargs = {"host": OLLAMA_HOST, "options": {"num_ctx": NUM_CTX}}
    if DISABLE_THINKING:
        kwargs["think"] = False
    return kwargs


async def build_rag():
    import os

    os.makedirs(WORKING_DIR, exist_ok=True)

    rag = LightRAG(
        working_dir=WORKING_DIR,
        llm_model_func=ollama_model_complete,
        llm_model_name=LLM_MODEL,
        llm_model_kwargs=llm_kwargs(),
        llm_model_max_async=LLM_MAX_ASYNC,
        embedding_func=EmbeddingFunc(
            embedding_dim=EMBED_DIM,
            max_token_size=8192,
            func=lambda texts: ollama_embed(
                texts, embed_model=EMBED_MODEL, host=OLLAMA_HOST
            ),
        ),
        embedding_func_max_async=EMBEDDING_MAX_ASYNC,
        addon_params={"entity_types": ENTITY_TYPES},
    )
    await rag.initialize_storages()
    await initialize_pipeline_status()
    return rag


async def insert_one(rag, semaphore, sku_id, text, errors, lock):
    async with semaphore:
        try:
            await rag.ainsert(text, ids=f"sku-{sku_id}")
        except Exception as exc:  # noqa: BLE001 -- must catch and attribute, not crash the batch
            async with lock:
                errors.append((sku_id, repr(exc)))
            logging.error("Failed to ingest sku_id=%s: %r", sku_id, exc)


async def main():
    documents, skipped_empty_description = build_documents(limit=BATCH_SIZE)
    attempted = len(documents)
    print(
        f"Loaded {attempted} documents for ingestion (batch limit {BATCH_SIZE}); "
        f"{skipped_empty_description} products skipped upstream for having no "
        f"usable description (excluded from this count, not silently included)"
    )

    if attempted == 0:
        print("Nothing to ingest -- exiting without touching LightRAG storage.")
        sys.exit(1)

    rag = await build_rag()
    semaphore = asyncio.Semaphore(LLM_MAX_ASYNC)
    lock = asyncio.Lock()
    errors = []

    await asyncio.gather(
        *(
            insert_one(rag, semaphore, sku_id, text, errors, lock)
            for sku_id, text in documents
        )
    )
    await rag.finalize_storages()

    succeeded = attempted - len(errors)
    print("\n=== Ingestion summary ===")
    print(f"Attempted:            {attempted}")
    print(f"Succeeded:            {succeeded}")
    print(f"Failed:               {len(errors)}")
    print(f"Skipped (no desc.):   {skipped_empty_description}")
    print(f"Storage:              {WORKING_DIR}")

    if errors:
        print("\nFailed sku_ids:")
        for sku_id, error in errors:
            print(f"  {sku_id}: {error}")
        print(
            f"\n{len(errors)} of {attempted} documents failed to ingest -- "
            f"exiting non-zero. Re-run to retry (LightRAG's doc-status "
            f"tracking will skip already-succeeded SKUs on the next pass "
            f"once incremental re-ingestion is wired in; for now this run "
            f"inserted whatever succeeded and left the rest out, it did "
            f"not substitute placeholder data for the failures)."
        )
        sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
