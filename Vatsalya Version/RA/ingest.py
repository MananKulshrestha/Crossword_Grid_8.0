"""Ingests Flipkart product documents into LightRAG, building a knowledge
graph (subset only) and chunk-vector index (full catalog, Qdrant-backed) so
queries can later run in "mix" mode.

Requires every Ollama server listed in servers.txt reachable with both
LLM_MODEL and EMBED_MODEL pulled -- extraction AND embedding (when
EMBED_BACKEND="ollama") round-robin across all of them via the same
MultiOllamaLoadBalancer instance (multi_ollama.py; see servers.txt's own
comments) -- and the Qdrant container from README.md's "Qdrant Setup"
section running and reachable at QDRANT_URL.

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
import time

from datetime import datetime, timezone

from tqdm import tqdm

from lightrag import LightRAG
from lightrag.base import DocStatus
from lightrag.kg.shared_storage import initialize_pipeline_status
from lightrag.utils import EmbeddingFunc, setup_logger
from lightrag.utils_pipeline import (
    chunk_fields_from_status_doc,
    doc_status_custom_chunk_patch,
    doc_status_reset_metadata,
    resolve_doc_file_path,
)

from config import (
    BATCH_SIZE,
    DISABLE_THINKING,
    EMBED_BACKEND,
    EMBED_DIM,
    EMBED_MODEL,
    EMBEDDING_MAX_ASYNC,
    ENTITY_TYPES_GUIDANCE,
    GRAPH_SAMPLING_SUBSET_PATH,
    LLM_MAX_ASYNC,
    LLM_MODEL,
    NUM_CTX,
    QDRANT_URL,
    WORKING_DIR,
)
from load_documents import build_documents
from multi_ollama import load_servers, MultiOllamaLoadBalancer

setup_logger("lightrag", level="INFO")

# QdrantVectorDBStorage reads this straight from os.environ at its own
# initialize time, not from LightRAG(...) constructor kwargs (confirmed by
# reading lightrag/kg/qdrant_impl.py directly) -- setdefault so an
# already-exported value (a teammate's own Qdrant instance) is respected
# rather than silently overridden.
os.environ.setdefault("QDRANT_URL", QDRANT_URL)


def embedding_func(load_balancer):
    """Builds the EmbeddingFunc per config.EMBED_BACKEND -- "local" runs
    sentence-transformers on this machine (local_embed.py), "ollama" round-
    robins across every server in servers.txt via the same
    MultiOllamaLoadBalancer instance extraction uses (load_balancer.embed),
    instead of a single hardcoded host -- so embedding work is distributed
    across the same cluster capacity extraction already shares, not pinned
    to one server. See config.py's EMBED_BACKEND comment for why "ollama" is
    the default."""
    if EMBED_BACKEND == "local":
        from local_embed import local_embed

        func = lambda texts: local_embed(texts)
    elif EMBED_BACKEND == "ollama":
        func = lambda texts: load_balancer.embed(texts, embed_model=EMBED_MODEL)
    else:
        raise ValueError(f"Unknown EMBED_BACKEND: {EMBED_BACKEND!r} (expected 'local' or 'ollama')")

    return EmbeddingFunc(
        embedding_dim=EMBED_DIM,
        max_token_size=8192,
        model_name=EMBED_MODEL,  # Qdrant collection-naming/workspace isolation
        func=func,
    )


def llm_kwargs():
    """kwargs forwarded to every extraction call via llm_model_kwargs.
    Deliberately excludes "host" -- extraction is routed through
    MultiOllamaLoadBalancer (see build_rag()), which sets "host" per-call
    from its own round-robin pick over servers.txt. Passing "host" here
    too would collide with that (duplicate keyword argument)."""
    kwargs = {"options": {"num_ctx": NUM_CTX}}
    if DISABLE_THINKING:
        kwargs["think"] = False
    return kwargs


async def build_rag():
    os.makedirs(WORKING_DIR, exist_ok=True)

    servers = load_servers("servers.txt")
    load_balancer = MultiOllamaLoadBalancer(servers)

    rag = LightRAG(
        working_dir=WORKING_DIR,
        vector_storage="QdrantVectorDBStorage",
        llm_model_func=load_balancer,
        llm_model_name=LLM_MODEL,
        llm_model_kwargs=llm_kwargs(),
        llm_model_max_async=LLM_MAX_ASYNC,
        embedding_func=embedding_func(load_balancer),
        embedding_func_max_async=EMBEDDING_MAX_ASYNC,
        addon_params={"entity_types_guidance": ENTITY_TYPES_GUIDANCE},
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


async def monitor_processing_progress(rag, total_docs, initial_processed=0, poll_interval=2):
    """Monitor progress of document processing by polling doc_status, showing a
    progress bar with ETA. Runs until all documents are PROCESSED or FAILED.

    poll_interval (default 2 seconds): how often to check doc_status. Shorter
    intervals give more responsive progress bars but higher polling overhead."""
    start_time = time.time()
    last_update_time = start_time
    last_count = initial_processed
    stuck_threshold = 30  # seconds without progress before warning

    with tqdm(
        total=total_docs,
        initial=initial_processed,
        desc="Processing documents",
        unit="docs",
        dynamic_ncols=True,
    ) as pbar:
        while True:
            counts = await rag.get_processing_status()
            processed = counts.get("processed", 0)
            failed = counts.get("failed", 0)
            total_done = processed + failed

            now = time.time()
            update_delta = total_done - last_count

            # Update progress bar
            if update_delta > 0:
                pbar.update(update_delta)
                last_count = total_done
                last_update_time = now

            # Warn if stuck (no progress for 30+ seconds)
            time_since_progress = now - last_update_time
            if time_since_progress > stuck_threshold and total_done < total_docs:
                elapsed_mins = time_since_progress / 60
                pbar.write(
                    f"⚠️  No progress for {elapsed_mins:.1f}m ({processed} processed, "
                    f"{failed} failed, {total_docs - total_done} remaining). "
                    f"Check server logs or network connectivity."
                )

            if total_done == total_docs:
                break

            await asyncio.sleep(poll_interval)

        elapsed = time.time() - start_time
        pbar.set_postfix_str(f"done in {elapsed:.0f}s ({processed} OK, {failed} failed)")


async def resume_failed_documents(rag):
    """Resets every FAILED doc_status row back to PENDING so the next
    apipeline_process_enqueue_documents() call retries it, instead of the
    document staying FAILED forever (LightRAG's own default -- a FAILED doc
    only re-enters the pipeline via an explicit manual-retry request; see
    lightrag/api/routers/document_routes.py's /reprocess_failed docstring).

    Re-implements that reset directly against doc_status/full_docs using
    LightRAG's own public reset-field helpers (lightrag.utils_pipeline),
    rather than going through the server-only manual-retry protocol (freeze
    ingress / drain-to-idle / exclusive-reset), which exists to keep FAILED
    resets safe against a concurrent live server. This script is a one-shot,
    single-process run against its own WORKING_DIR with nothing else writing
    to it, so that concurrency ceremony has nothing to protect against here.

    Docs with an in-flight custom-chunk journal are left untouched (this
    pipeline never uses ainsert_custom_chunks, so this never fires here --
    kept because it's a hard invariant of the reset helper, not because it's
    reachable). Returns the count reset, for the checkpoint summary."""
    failed_docs = await rag.doc_status.get_docs_by_statuses([DocStatus.FAILED], strict=True)
    if not failed_docs:
        return 0

    docs_to_reset = {}
    for doc_id, status_doc in failed_docs.items():
        if doc_status_custom_chunk_patch(status_doc) is not None:
            continue
        content_data = await rag.full_docs.get_by_id(doc_id)
        if not content_data:
            continue
        chunks_list, chunks_count = chunk_fields_from_status_doc(status_doc)
        docs_to_reset[doc_id] = {
            "status": DocStatus.PENDING,
            "content_summary": status_doc.content_summary,
            "content_length": status_doc.content_length,
            "chunks_count": chunks_count,
            "chunks_list": chunks_list,
            "created_at": status_doc.created_at,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "file_path": resolve_doc_file_path(status_doc=status_doc, content_data=content_data),
            "track_id": getattr(status_doc, "track_id", ""),
            "content_hash": getattr(status_doc, "content_hash", None),
            "error_msg": "",
            "metadata": doc_status_reset_metadata(status_doc),
        }
    if docs_to_reset:
        await rag.doc_status.upsert(docs_to_reset)
    return len(docs_to_reset)


async def main():
    """Ingest documents into LightRAG with checkpoint/resume and progress tracking.

    Notes on expected warnings/logs:
    - "WARNING: Duplicate document detected (content_hash)": LightRAG detects when
      the exact same text (same product description) is being indexed under
      different SKU IDs. This is handled gracefully by LightRAG and is expected
      when a product description is genuinely reused across SKUs (e.g., a color
      variant of the same item). It does NOT indicate a problem — LightRAG
      deduplicates internally by content hash, so the graph/embedding is still
      correct and efficient. The warning is informational, not an error.
    """
    documents, (skipped_empty_description, skipped_duplicates) = build_documents(limit=BATCH_SIZE)
    loaded = len(documents)
    batch_desc = "no limit (full catalog)" if BATCH_SIZE is None else str(BATCH_SIZE)
    skip_msg = f"{skipped_empty_description} products skipped for having no usable description"
    if skipped_duplicates:
        skip_msg += f", {skipped_duplicates} duplicate SKU IDs"
    print(
        f"Loaded {loaded} documents from the corpus (batch limit {batch_desc}); "
        f"{skip_msg} (excluded from this count, not silently included)"
    )

    if loaded == 0:
        print("Nothing to ingest -- exiting without touching LightRAG storage.")
        sys.exit(1)

    full_extraction_skus = load_full_extraction_skus()
    rag = await build_rag()

    # Only enqueue SKUs LightRAG has never seen before. Every previous run of
    # this script re-submitted the ENTIRE corpus to apipeline_enqueue_documents
    # on every invocation -- LightRAG's own dedup (filename/content_hash, see
    # pipeline.py apipeline_enqueue_documents) silently absorbed the already-known
    # ones as "duplicate document" records instead of re-processing them, so no
    # already-PROCESSED doc was ever actually redone. But re-submitting tens of
    # thousands of already-known docs every run was still real, avoidable cost:
    # minutes of hashing/logging before any new work starts, tens of thousands of
    # throwaway "dup-" tracking rows accumulating in doc_status forever, and (the
    # visible symptom) the progress bar below being sized off the full resubmitted
    # corpus count instead of actual outstanding work, so once cumulative
    # processed-ever exceeded that count the bar lost its percentage/ETA display
    # entirely and looked like it was starting over. filter_keys() is the same
    # existence check the enqueue path itself uses -- doing it here means we never
    # pay for or create a duplicate record for a SKU we already know about.
    all_ids = {f"sku-{sku_id}" for sku_id, _ in documents}
    new_ids = await rag.doc_status.filter_keys(all_ids)
    new_documents = [(sku_id, text) for sku_id, text in documents if f"sku-{sku_id}" in new_ids]
    attempted = len(new_documents)
    already_known = loaded - attempted
    print(
        f"{attempted} of {loaded} are new to LightRAG ({already_known} already "
        f"enqueued/processed in a previous run -- skipped, not resubmitted)."
    )

    ids = [f"sku-{sku_id}" for sku_id, _ in new_documents]
    texts = [text for _, text in new_documents]
    process_options = [
        "" if sku_id in full_extraction_skus else "!" for sku_id, _ in new_documents
    ]
    full_extraction_count = sum(1 for opt in process_options if opt == "")
    if attempted:
        print(
            f"{full_extraction_count} of {attempted} new documents in this run are in "
            f"the full-extraction subset ({len(full_extraction_skus)} total subset size "
            f"per {GRAPH_SAMPLING_SUBSET_PATH}); the rest get skip_kg (chunk-embedded "
            f"into Qdrant only, no LLM extraction call)."
        )

    if EMBED_BACKEND == "local":
        # Forces the sentence-transformers model to load NOW, synchronously,
        # before any document reaches the embedding step. Without this, the
        # first real embedding call pays the full cold-load cost (HuggingFace
        # download + model init, routinely 60s+ on a fresh environment) inside
        # LightRAG's own internal 60s embedding-worker timeout and fails with
        # "Worker execution timeout after 60s" -- not a real data/logic
        # problem, just whichever document happens to go first eating a
        # one-time cost it shouldn't have to pay. See local_embed.warmup().
        from local_embed import warmup
        print("Warming up local embedding model (first-time download/load can take a minute)...")
        await warmup()
        print("Embedding model ready.")

    # Checkpoint/resume: a doc left PROCESSING/PARSING/ANALYZING by a hard
    # interrupt (kill -9, crash, Ctrl+C) is auto-reset to PENDING by LightRAG
    # itself the next time apipeline_process_enqueue_documents() runs (see
    # pipeline.py's _validate_and_fix_document_consistency) -- nothing to do
    # here for that case. A doc that reached FAILED (raised an exception
    # during extraction) is NOT auto-retried by LightRAG -- it is designed to
    # stay FAILED until an explicit retry request, so a plain re-run would
    # otherwise ignore it forever. resume_failed_documents() is that explicit
    # request, run unconditionally on every invocation.
    resumed_count = await resume_failed_documents(rag)
    if resumed_count:
        print(f"\nResumed {resumed_count} previously FAILED document(s) -- reset to PENDING for retry.\n")

    if attempted:
        print("=== Enqueueing documents ===")
        with tqdm(total=attempted, desc="Enqueue", unit="docs", dynamic_ncols=True) as pbar:
            await rag.apipeline_enqueue_documents(texts, ids=ids, process_options=process_options)
            pbar.update(attempted)
    else:
        print("=== No new documents to enqueue -- resuming outstanding work only ===")

    print("\n=== Processing documents (this may take a while) ===")
    # Progress-bar total/baseline come from doc_status itself, not from
    # `attempted` (this run's newly-enqueued count). `attempted` is only ever
    # the delta for THIS invocation; get_processing_status() counts every doc
    # LightRAG has ever tracked (already-PROCESSED/FAILED docs from earlier
    # runs plus this run's new PENDING ones plus anything resume_failed_documents()
    # just reset). Using `attempted` as the bar's total previously produced a
    # bar that overflowed past 100% once the cumulative processed-ever count
    # exceeded whatever this run happened to (re-)submit -- tqdm then drops the
    # percentage/ETA display and just prints a bare, unbounded rate counter,
    # which is exactly the "no ETA, looks like it's starting from the
    # beginning" symptom. Sizing the bar off doc_status's own totals keeps it
    # honest: it always reflects real outstanding work, never what this one
    # invocation happened to (re-)submit.
    status_counts = await rag.get_processing_status()
    already_done = status_counts.get("processed", 0) + status_counts.get("failed", 0)
    total_tracked = sum(status_counts.values())
    remaining = total_tracked - already_done
    print(f"Outstanding work: {remaining} document(s) not yet PROCESSED/FAILED "
          f"(of {total_tracked} tracked total).")

    # apipeline_process_enqueue_documents() does the actual work (embedding +
    # extraction); monitor_processing_progress() only polls doc_status to
    # drive the progress bar -- it never does any processing itself. Both
    # must run concurrently: awaiting the monitor alone (without the
    # processing call also running as its own task) just polls a status that
    # never changes, since nothing would be doing the work.
    #
    # processing_task is the authoritative signal for completion/failure --
    # it's awaited directly so any exception it raises propagates normally.
    # monitor_task is best-effort UI: once processing_task finishes, give the
    # monitor a brief grace period to catch the final status update (it polls
    # every 2s) and print its "done" line, then cancel it if it's still going.
    processing_task = asyncio.create_task(rag.apipeline_process_enqueue_documents())
    monitor_task = asyncio.create_task(
        monitor_processing_progress(rag, total_tracked, initial_processed=already_done)
    )
    try:
        await processing_task
    finally:
        try:
            await asyncio.wait_for(asyncio.shield(monitor_task), timeout=3)
        except asyncio.TimeoutError:
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass

    await rag.finalize_storages()

    counts = await rag.get_processing_status()
    failed_docs = await rag.get_docs_by_status(DocStatus.FAILED)
    processed = counts.get("processed", 0)
    failed = counts.get("failed", 0)

    print("\n=== Ingestion summary ===")
    print(f"Attempted this run:    {attempted}")
    print(f"Resumed from FAILED:   {resumed_count}")
    print(f"Processed (total):     {processed}")
    print(f"Failed (total):        {failed}")
    print(f"Skipped (no desc.):    {skipped_empty_description}")
    print(f"Qdrant:                {os.environ['QDRANT_URL']}")
    print(f"Storage (checkpoint):  {WORKING_DIR}")

    if failed_docs:
        print("\nFailed sku_ids:")
        for doc_id, status in failed_docs.items():
            print(f"  {doc_id}: {status.error_msg}")
        print(
            f"\n{len(failed_docs)} document(s) are in FAILED status -- exiting "
            f"non-zero. This run did not substitute placeholder data for the "
            f"failures. Re-run `python ingest.py` to retry them (this script "
            f"resets FAILED -> PENDING for every failed doc on every invocation, "
            f"so a failure is never silently left behind); already-PROCESSED "
            f"SKUs are skipped, so a retry only pays for what's still broken."
        )
        sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
