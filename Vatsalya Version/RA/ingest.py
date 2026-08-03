"""Ingests Flipkart product documents into LightRAG, building a knowledge
graph (subset only) and chunk-vector index (full catalog, Qdrant-backed) so
queries can later run in "mix" mode.

Requires an Ollama server reachable at OLLAMA_HOST (see config.py) with
LLM_MODEL and EMBED_MODEL already pulled, and the Qdrant container from
README.md's "Qdrant Setup" section running and reachable at QDRANT_URL.

Uses apipeline_enqueue_documents + apipeline_process_enqueue_documents
(not the rag.ainsert() convenience wrapper) because only the low-level
pair accepts a per-document process_options selector -- this is how each
SKU gets marked either for full entity/relation extraction ("") or
LightRAG's native skip_kg flag ("!"), per graph_sampling.py's coverage-based
subset (see lightrag-implementation.md, sections 3.2 and 5). Every SKU still
gets chunk-embedded into Qdrant either way; only the LLM extraction step is
skipped for non-subset SKUs. Failure accounting reads back LightRAG's own
doc_status store (get_processing_status / get_docs_by_status) rather than
catching exceptions per call, since the low-level pipeline manages its own
per-document status -- but the same discipline as before applies: no
placeholder substitution on failure, exit non-zero and name every failed
SKU with its error.
"""

import asyncio
import logging
import os
import sys

from lightrag import LightRAG
from lightrag.base import DocStatus
from lightrag.kg.shared_storage import initialize_pipeline_status
from lightrag.llm.ollama import ollama_embed, ollama_model_complete
from lightrag.utils import EmbeddingFunc, setup_logger

from config import (
    BATCH_SIZE,
    DISABLE_THINKING,
    EMBED_DIM,
    EMBED_MODEL,
    EMBEDDING_MAX_ASYNC,
    ENTITY_TYPE_PROMPT_FILE,
    GRAPH_SAMPLING_SUBSET_PATH,
    LLM_MAX_ASYNC,
    LLM_MODEL,
    NUM_CTX,
    OLLAMA_HOST,
    PROMPT_DIR,
    QDRANT_URL,
    WORKING_DIR,
)
from load_documents import build_documents

setup_logger("lightrag", level="INFO")

# QdrantVectorDBStorage and the entity-type prompt loader both read these
# straight from os.environ at their own initialize/construction time, not
# from LightRAG(...) constructor kwargs (confirmed by reading
# lightrag/kg/qdrant_impl.py and lightrag/prompt.py directly) -- setdefault
# so an already-exported value (a teammate's own Qdrant instance, a custom
# PROMPT_DIR) is respected rather than silently overridden.
os.environ.setdefault("QDRANT_URL", QDRANT_URL)
os.environ.setdefault("PROMPT_DIR", PROMPT_DIR)


def llm_kwargs():
    kwargs = {"host": OLLAMA_HOST, "options": {"num_ctx": NUM_CTX}}
    if DISABLE_THINKING:
        kwargs["think"] = False
    return kwargs


async def build_rag():
    os.makedirs(WORKING_DIR, exist_ok=True)

    rag = LightRAG(
        working_dir=WORKING_DIR,
        vector_storage="QdrantVectorDBStorage",
        llm_model_func=ollama_model_complete,
        llm_model_name=LLM_MODEL,
        llm_model_kwargs=llm_kwargs(),
        llm_model_max_async=LLM_MAX_ASYNC,
        embedding_func=EmbeddingFunc(
            embedding_dim=EMBED_DIM,
            max_token_size=8192,
            model_name=EMBED_MODEL,  # Qdrant collection-naming/workspace isolation
            func=lambda texts: ollama_embed(
                texts, embed_model=EMBED_MODEL, host=OLLAMA_HOST
            ),
        ),
        embedding_func_max_async=EMBEDDING_MAX_ASYNC,
        addon_params={"entity_type_prompt_file": ENTITY_TYPE_PROMPT_FILE},
    )
    await rag.initialize_storages()
    await initialize_pipeline_status()
    return rag


def load_full_extraction_skus():
    """SKU IDs that should get full LightRAG entity/relation extraction,
    per graph_sampling.py's coverage-based subset. Every other SKU still
    gets chunk-embedded (skip_kg only skips extraction, never embedding).
    Hard-fails if the subset hasn't been generated yet -- there is no
    sensible fallback (e.g. "treat everyone as skip_kg") that wouldn't
    silently produce an empty graph."""
    if not os.path.exists(GRAPH_SAMPLING_SUBSET_PATH):
        raise FileNotFoundError(
            f"{GRAPH_SAMPLING_SUBSET_PATH} does not exist. Run "
            "`python graph_sampling.py` first to generate the full-extraction "
            "subset (see lightrag-implementation.md, section 5) before ingesting."
        )
    with open(GRAPH_SAMPLING_SUBSET_PATH, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


async def main():
    documents, skipped_empty_description = build_documents(limit=BATCH_SIZE)
    attempted = len(documents)
    batch_desc = "no limit (full catalog)" if BATCH_SIZE is None else str(BATCH_SIZE)
    print(
        f"Loaded {attempted} documents for ingestion (batch limit {batch_desc}); "
        f"{skipped_empty_description} products skipped upstream for having no "
        f"usable description (excluded from this count, not silently included)"
    )

    if attempted == 0:
        print("Nothing to ingest -- exiting without touching LightRAG storage.")
        sys.exit(1)

    full_extraction_skus = load_full_extraction_skus()
    ids = [f"sku-{sku_id}" for sku_id, _ in documents]
    texts = [text for _, text in documents]
    process_options = [
        "" if sku_id in full_extraction_skus else "!" for sku_id, _ in documents
    ]
    full_extraction_count = sum(1 for opt in process_options if opt == "")
    print(
        f"{full_extraction_count} of {attempted} documents in this run are in the "
        f"full-extraction subset ({len(full_extraction_skus)} total subset size "
        f"per {GRAPH_SAMPLING_SUBSET_PATH}); the rest get skip_kg (chunk-embedded "
        f"into Qdrant only, no LLM extraction call)."
    )

    rag = await build_rag()
    await rag.apipeline_enqueue_documents(texts, ids=ids, process_options=process_options)
    await rag.apipeline_process_enqueue_documents()
    await rag.finalize_storages()

    counts = await rag.get_processing_status()
    failed_docs = await rag.get_docs_by_status(DocStatus.FAILED)

    print("\n=== Ingestion summary ===")
    print(f"Attempted:            {attempted}")
    print(f"Processed:            {counts.get('processed', 0)}")
    print(f"Failed:               {counts.get('failed', 0)}")
    print(f"Skipped (no desc.):   {skipped_empty_description}")
    print(f"Qdrant:               {os.environ['QDRANT_URL']}")
    print(f"Storage:              {WORKING_DIR}")

    if failed_docs:
        print("\nFailed sku_ids:")
        for doc_id, status in failed_docs.items():
            print(f"  {doc_id}: {status.error_msg}")
        print(
            f"\n{len(failed_docs)} of {attempted} documents failed to ingest -- "
            f"exiting non-zero. Re-run to retry (LightRAG's doc-status tracking "
            f"skips already-PROCESSED SKUs on the next pass); this run did not "
            f"substitute placeholder data for the failures."
        )
        sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
