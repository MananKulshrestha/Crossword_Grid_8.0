"""Ingests a batch of Flipkart product documents into LightRAG, building
both the knowledge graph and vector DB (local file-based storage under
WORKING_DIR) so queries can later run in "mix" mode.

Requires an Ollama server reachable at OLLAMA_HOST (see config.py) with
LLM_MODEL and EMBED_MODEL already pulled.
"""

import asyncio
import logging

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
    )
    await rag.initialize_storages()
    await initialize_pipeline_status()
    return rag


async def main():
    documents = build_documents(limit=BATCH_SIZE)
    print(f"Loaded {len(documents)} documents for ingestion (batch limit {BATCH_SIZE})")

    rag = await build_rag()
    texts = [text for _, text in documents]
    ids = [f"sku-{sku_id}" for sku_id, _ in documents]

    await rag.ainsert(texts, ids=ids)

    print(f"Ingested {len(texts)} documents into LightRAG at {WORKING_DIR}")
    await rag.finalize_storages()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
