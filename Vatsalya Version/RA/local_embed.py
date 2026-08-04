"""Local embedding backend using sentence-transformers/torch on this Mac
(Apple Silicon MPS if available, else CPU) instead of Ollama.

Moves embedding calls off the shared GPU cluster entirely -- they'd
otherwise compete with concurrent extraction calls (servers.txt) for the
same physical GPUs. Extraction (the LLM calls) stays on the Ollama
cluster; only embedding moves here.

Uses the HF checkpoint nomic-ai/nomic-embed-text-v1.5, the same weights
Ollama's "nomic-embed-text" model is built from, at the same 768
dimension (config.py's EMBED_DIM) -- output should be numerically very
close to the Ollama-served embeddings, but not guaranteed byte-identical
(different serving stack, no GGUF quantization here). Matches the
no-prefix behavior of the Ollama embedding call this replaces (neither
uses the "search_query: "/"search_document: " asymmetric prefixes Nomic
recommends), so switching backends mid-corpus doesn't change whatever's
already been embedded relative to what's embedded from here on.

If mixing embeddings from both backends across one corpus ever looks
inconsistent at query time, LightRAG ships an offline rebuild tool
(python -m lightrag.tools.rebuild_vdb) that re-embeds every vector from
the graph/KV data using whichever embedding_func is configured when it's
run -- run it once after switching to normalize the whole corpus onto one
backend, without re-running extraction.
"""

import asyncio
import threading

import numpy as np

_model = None
_model_lock = threading.Lock()
# Serializes the actual encode() calls too, not just model loading. Local
# inference is compute-bound on one CPU/MPS device -- concurrent calls
# don't parallelize, they just contend for the same resource. Under
# EMBEDDING_MAX_ASYNC>1, letting multiple encode() calls run at once via
# asyncio.to_thread (separate OS threads) causes severe slowdowns, vs one
# call at a time -- serializing is both correct and faster here, unlike
# the Ollama backend where concurrency helps by overlapping network
# latency.
_encode_lock = threading.Lock()


def _get_model():
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        # Double-checked locking: a second thread that was blocked on the
        # lock above must not redo the load once the first thread finishes.
        if _model is None:
            import torch
            from sentence_transformers import SentenceTransformer

            device = "mps" if torch.backends.mps.is_available() else "cpu"
            _model = SentenceTransformer(
                "nomic-ai/nomic-embed-text-v1.5", trust_remote_code=True, device=device
            )
    return _model


def _encode_sync(texts):
    model = _get_model()
    with _encode_lock:
        return model.encode(texts)


async def local_embed(texts):
    """Async embedding function matching LightRAG's EmbeddingFunc contract.

    SentenceTransformer.encode() is synchronous/blocking -- run it in a
    thread so it doesn't block the asyncio event loop (needed since it
    runs alongside async Ollama extraction calls in the same process).
    Model loading and the encode call itself are both serialized via
    threading.Lock (see module-level comments) -- concurrent calls queue
    behind the lock rather than racing to load/execute simultaneously.
    """
    embeddings = await asyncio.to_thread(_encode_sync, texts)
    return np.asarray(embeddings, dtype=np.float32)


async def warmup():
    """Forces the model to load (download from HuggingFace on first-ever
    run, then load into memory) before any real embedding call happens.

    Why this matters: LightRAG wraps every embedding_func call in its own
    internal worker timeout (60s, not configurable from this codebase's
    config.py -- it's a LightRAG-side constant). _get_model()'s cold load
    -- HuggingFace download (slower still with no HF_TOKEN, unauthenticated
    rate limits) plus SentenceTransformer initialization -- routinely takes
    well over 60s on a fresh environment, well within the ordinary range
    for a ~500MB-1GB model over a consumer connection. Without a warmup,
    the FIRST real ingest.py document to reach the embedding step eats that
    entire cold-load cost inside LightRAG's timeout window and fails with
    "Worker execution timeout after 60s" -- not a real data/logic problem,
    just bad luck about which call happens to pay the one-time load cost.

    Calling this once, synchronously, before apipeline_enqueue_documents()/
    apipeline_process_enqueue_documents() ensures the model is already
    resident in memory by the time any real document reaches the embedding
    step, so every real call only pays actual encode() time (well under a
    second), never the load time.
    """
    await asyncio.to_thread(_get_model)
