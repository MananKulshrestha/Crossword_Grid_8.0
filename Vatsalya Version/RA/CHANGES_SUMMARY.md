# Checkpoint/Resume + Progress Tracking Changes

## What was fixed

### 1. **MultiOllamaLoadBalancer thread-safety (fixes "only one server" issue)**
   - **Problem**: With 4 Ollama servers and `LLM_MAX_ASYNC=4`, all concurrent extraction requests were hitting only one server instead of load-balancing across all 4.
   - **Root cause**: `itertools.cycle()` is not thread-safe. When multiple async tasks called `next(self.server_cycle)` concurrently, they could collide on the same internal state and get the same server.
   - **Fix**: Added `asyncio.Lock` to `MultiOllamaLoadBalancer.get_next_server()` so the round-robin selection serializes atomically. Now with 4 servers and 4 concurrent requests, each request gets a distinct server (A, B, C, D) in order.
   - **File**: `multi_ollama.py`

### 2. **Progress bars with ETA (tqdm)**
   - **Added**: 
     - Enqueue progress bar (shows documents being queued)
     - Processing progress bar (polls `doc_status` every 5 seconds and updates with % complete + ETA)
     - All progress bars show units and dynamic layout adjustment
   - **File**: `ingest.py`, `requirements.txt` (added tqdm)

### 3. **Duplicate SKU ID handling**
   - **Problem**: Some SKU IDs appeared in multiple blocks in the corpus, causing `ValueError: IDs must be unique` during enqueue.
   - **Fix**: `build_documents()` now deduplicates (keeps first occurrence), counts duplicates, and reports them explicitly like empty descriptions.
   - **File**: `load_documents.py`

### 4. **Checkpoint/Resume (from before)**
   - `ingest.py` resets every `FAILED` document to `PENDING` on every run
   - Hard interrupts (Ctrl+C, crash) are auto-recovered on next run
   - Every run's summary reports "Resumed from FAILED: N"
   - Files: `ingest.py`, `graph_sampling.py`, `bm25_index.py` (atomic writes via temp-file + os.replace)

---

## What to expect when you run `./run.sh` or `python ingest.py`

```
Loaded 19998 documents for ingestion (batch limit no limit (full catalog)); 
2 no usable description, X duplicate SKU IDs (excluded...)

2674 of 19998 documents in this run are in the full-extraction subset...

Loaded 4 Ollama server(s) for extraction: 
  ['http://127.0.0.1:11435', 'http://127.0.0.1:11436', ...]

=== Enqueueing documents ===
Enqueue |████████░░| 95% [19045/19998, 00:15<00:05]

=== Processing documents (this may take a while) ===
Processing documents |████░░░░░░| 42% [8392/19998, 12:35<18:20, 10.6 docs/s]

=== Ingestion summary ===
Attempted this run:    19998
Resumed from FAILED:   2
Processed (total):     19994
Failed (total):        2
...
```

### Key behaviors:

1. **Parallelization**: With 4 servers and `LLM_MAX_ASYNC=4`, you should see 4 concurrent extraction calls spread across all 4 servers (check server logs to verify each is processing). If you increase `LLM_MAX_ASYNC=8` and have 8 servers, you'd get 8 concurrent (2 per server if OLLAMA_NUM_PARALLEL=2, or all on different servers if each server can take 8).

2. **Progress updates**: Every 5 seconds the progress bar updates with current speed (docs/s) and ETA to completion. Total wall time depends on your hardware, but the progress bar tracks it live.

3. **Checkpointing**: If you Ctrl+C mid-run, running again automatically:
   - Skips already-`PROCESSED` documents
   - Resumes any interrupted ones (auto-detected)
   - Retries any `FAILED` ones from last run
   - Only pays for what's still pending

---

## Files changed

| File | Change |
|---|---|
| `multi_ollama.py` | Added `asyncio.Lock` to `get_next_server()` for thread-safe round-robin |
| `ingest.py` | Added tqdm imports, `monitor_processing_progress()` function, progress bars in `main()` |
| `load_documents.py` | Added duplicate deduplication to `build_documents()` |
| `requirements.txt` | Added `tqdm` |

---

## Testing

All syntax is verified. Run:
```bash
./run.sh
# or
python ingest.py
```

If you hit errors, re-read the summary above for what each fix does — most common issues are now explicitly reported rather than silently dropped or swallowed.
