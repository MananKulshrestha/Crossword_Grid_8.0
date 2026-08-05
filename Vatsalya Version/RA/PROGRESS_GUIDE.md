# Understanding Ingestion Progress

## What you're seeing

### Duplicate content_hash warnings

```
WARNING: Duplicate document detected (content_hash): sku-STIE9F5U55AWF2YH
```

**This is expected and normal.** It means:
- The exact same product description appears under multiple SKU IDs
- This happens when a product (e.g., a t-shirt) has multiple color/size variants with identical descriptions
- LightRAG **handles this automatically** by deduplicating internally based on content hash
- The graph/embeddings are still correct and efficient — duplicates don't bloat the index
- The warning is informational only, not an error

### Progress bar behavior

```
Processing documents:  12%|███░░░░░░░░░░░░░░░░░░░░░░░░░░░░| 2462/19996 [00:18<00:35, 490.28docs/s]
```

The progress bar may appear "stuck" at certain percentages because:

1. **Batch processing**: LightRAG processes documents in batches, so you may see jumps of many documents at once rather than steady increments
2. **Two sequential stages**:
   - **Chunk embedding** (fast, parallelized across 4 embedding workers, full catalog ~20k docs)
   - **Entity/relation extraction** (slow, only subset ~2.7k docs, depends on LLM speed)
3. **Polling latency**: The progress monitor polls every 2 seconds, so updates may batch up and show as jumps

### "Stuck for a long time"

If the progress bar doesn't move for 30+ seconds, the monitor will print:

```
⚠️  No progress for 1.5m (2462 processed, 0 failed, 17534 remaining). 
Check server logs or network connectivity.
```

**What this means:**
- The processing pipeline isn't updating doc_status
- Likely causes:
  1. **Embedding phase is slow** — if embedding 19.9k documents at ~100-200 docs/min, it's working correctly but will appear stuck
  2. **Network issue** — Qdrant or Ollama unreachable, check `docker ps` and logs
  3. **Genuine hang** — a request timed out or crashed, check Ollama server logs

**What to do:**
1. Check Ollama server logs: `docker logs -f <container_name>`
2. Check Qdrant is up: `curl http://localhost:6333/collections`
3. Wait — if it's just slow embedding, this is expected (19.9k docs × ~0.5-1s per doc = 3-5 hours)
4. If actually hung, Ctrl+C and run `./reset.sh` to clear, then investigate

---

## Restarting from scratch

Use the provided `reset.sh` script to clear all storage and start fresh:

```bash
./reset.sh
```

This **safely deletes**:
- ✓ LightRAG storage (`lightrag_storage/`)
- ✓ BM25 index (`bm25_storage/`)
- ✓ Qdrant collections (via REST API)
- ✓ Temporary files (`.tmp` from atomic writes)

This **preserves** (re-run these if needed, they're fast):
- `graph_sampling_output/` (pre-generated, no LLM needed)
- `product_metadata.sql` and corpus files

After reset:
```bash
./run.sh          # Full ingest from scratch
# or
python ingest.py  # Just ingest, assumes graph_sampling already done
```

---

## Performance expectations

With 4 Ollama servers, each with `OLLAMA_NUM_PARALLEL=4` and `LLM_MAX_ASYNC=4`:

| Stage | Documents | Time (ballpark) | Notes |
|---|---|---|---|
| **Chunk embedding** | 19,998 | 30-60 min | Fast, parallelized, no LLM reasoning |
| **Entity/relation extraction** | 2,673 (subset) | 1-2 hours | Slow, depends on LLM, `DISABLE_THINKING=True` helps |
| **Total** | 19,998 | 2-4 hours | Depends heavily on VRAM, model choice, network latency |

**Why so long?**
- 19.9k documents need chunk-embedding (~0.5-1s each via sentence-transformers)
- 2.7k need LLM extraction (~3-10s per document for entity/relation extraction)
- No "stuck" — just real wall time at this volume

---

## Monitoring the actual processing

If you want to see real-time activity:

```bash
# Terminal 1: Watch Ollama requests
docker logs -f <ollama_container> | grep -i "request\|error"

# Terminal 2: Watch Qdrant
curl -s http://localhost:6333/collections | python -m json.tool | grep -A5 "points_count"

# Terminal 3: Check doc_status from Python REPL
python -c "
from lightrag import LightRAG
import asyncio
async def check():
    rag = LightRAG(working_dir='lightrag_storage', ...)
    await rag.initialize_storages()
    counts = await rag.get_processing_status()
    print(f'Processed: {counts.get(\"processed\")}, Failed: {counts.get(\"failed\")}')
asyncio.run(check())
"
```

---

## Checkpoint/Resume mechanics

Every run of `python ingest.py` automatically:

1. **Resumes interrupted docs**: Any PROCESSING/PARSING/ANALYZING → PENDING (auto-detected)
2. **Retries failed docs**: Any FAILED → PENDING (explicit reset before enqueue)
3. **Skips processed**: Already-PROCESSED docs are never re-indexed
4. **Reports counts**: Summary shows `Resumed from FAILED: N`

So you can:
- Ctrl+C anytime, lose no work
- Re-run, pick up exactly where you left off
- Only pay for what's still pending

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| "No Ollama server at http://127.0.0.1:11435" | Ollama not running | `OLLAMA_NUM_PARALLEL=4 OLLAMA_HOST=0.0.0.0:11345 ollama serve &` |
| "Connection refused" to Qdrant | Qdrant container stopped | `docker start flipkart-qdrant` |
| "IDs must be unique" | Duplicate SKU in corpus | Fixed in load_documents.py; report if still occurring |
| Progress bar stuck 30s+ | See "Stuck for a long time" above | Check logs, wait, or reset |
| "ValueError: process_options must be..." | Subset file mismatch | Run `python graph_sampling.py` to regenerate |
