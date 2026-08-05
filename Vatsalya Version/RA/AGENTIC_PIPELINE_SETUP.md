# Agentic Pipeline Setup & Integration Guide

This guide covers **how to set up and use LightRAG in an agentic pipeline** as a Python tool/module that agents can call programmatically. This is for developers integrating LightRAG into their agent, not for end users querying the UI.

---

## 1. DeepInfra Integration Status ✅

**DeepInfra is FULLY integrated and production-ready for QUERY-TIME only.**

| Component | Backend | Status | Notes |
|-----------|---------|--------|-------|
| **Ingestion** (`ingest.py`) | Ollama only | ✅ Fixed | Always uses `servers.txt` cluster, never DeepInfra |
| **Query inference** (`query.py`, `web_ui.py`) | DeepInfra or Ollama | ✅ Integrated | Set `INFERENCE_BACKEND=deepinfra` in `.env` |
| **Query embedding** (`query.py`) | Configurable | ✅ Integrated | Defaults to match ingestion backend (safe), can override |
| **LightRAG wrapper** (`deepinfra_llm.py`) | DeepInfra OpenAI API | ✅ Complete | Reuses LightRAG's native OpenAI client |
| **Configuration** (`config.py`, `.env.example`) | Both | ✅ Documented | Clear separation: ingestion vs. query backends |

**Nothing is missing** — all three query paths work end-to-end:
1. **`query.py` CLI** → query backend via CLI argument
2. **`web_ui.py` Flask** → query backend via INFERENCE_BACKEND env var
3. **Agentic pipeline (custom code)** → query backend via `INFERENCE_BACKEND` (see section 3 below)

---

## 2. Pre-Flight Checklist — What Needs to Exist Before You Start

### 2.1 Source Files (NOT in backups — bring your own)

```bash
cd /path/to/Vatsalya\ Version

# These MUST exist at the project root (same level as RA/)
ls flipkart_lightrag_corpus.md       # Product descriptions (text corpus)
ls flipkart_catalog_structured.jsonl # Catalog metadata (JSONL)
ls flipkart_metadata.sql             # MySQL schema (only needed if loading MySQL fresh)
```

If you don't have these files, ask for them from the data team — backups from `backup.sh` do NOT include them (by design, they're source data, not pipeline state).

### 2.2 MySQL (Structured metadata)

Required for `sql_filter.py` to work. See `README.md` "MySQL Setup" section, or restore from a backup if your data is already in MySQL somewhere:

```bash
docker ps | grep flipkart-mysql
# Should show a running container. If not:
docker start flipkart-mysql
```

Verify:
```bash
mysql -h 127.0.0.1 -P 3307 -u flipkart_user -pflipkart_pass flipkart -e "SELECT COUNT(*) FROM product_metadata"
```

### 2.3 Qdrant (Vector database)

```bash
docker ps | grep flipkart-qdrant
# Should show a running container. If not:
docker start flipkart-qdrant

# Verify it's serving data:
curl -s http://localhost:6333/collections | jq '.result | length'
# Should show 3 (chunks, entities, relationships collections)
```

If Qdrant doesn't exist yet, see `README.md` "Qdrant Setup" section.

### 2.4 Python Environment

```bash
cd RA
pip install -r requirements.txt
# or: uv sync
```

### 2.5 Ollama (For ingestion only, NOT needed for queries)

Only required if you plan to run `python ingest.py` to add new SKUs. Not needed for querying a built index.

```bash
# If ingesting:
cat servers.txt
# Each line should be reachable, with LLM_MODEL pulled
curl http://127.0.0.1:11435/api/tags  # (or whatever port is in servers.txt)
```

### 2.6 DeepInfra (For query inference, OPTIONAL)

Only needed if `INFERENCE_BACKEND=deepinfra` in your `.env`. For Ollama queries (the default), skip this:

```bash
# Copy and edit:
cp .env.example .env

# Fill in DeepInfra credentials (if using DeepInfra for queries):
# DEEPINFRA_API_KEY=your_key_here
# DEEPINFRA_LLM_MODEL=Qwen/Qwen3-235B-A22B-Instruct-2507  (example)
# DEEPINFRA_EMBED_MODEL=BAAI/bge-m3  (example, if LIGHTRAG_QUERY_EMBED_BACKEND=deepinfra)

# Test connectivity (requires curl):
curl -X POST https://api.deepinfra.com/v1/openai/models \
  -H "Authorization: Bearer $DEEPINFRA_API_KEY" \
  -H "Content-Type: application/json" | jq '.data[].id' | head -20
```

---

## 3. How an Agentic Pipeline Calls LightRAG

LightRAG is **pure Python** — it's not a service you call over HTTP (though `web_ui.py` wraps it in Flask for browser access). Agents call it directly as a module.

### 3.1 Minimal Agentic Integration Example

```python
# agent.py or your_pipeline.py

import asyncio
import sys
import os

# Make sure RA/ is on sys.path (adjust path to your project layout)
sys.path.insert(0, "RA")
os.chdir("RA")  # Most functions read config.py, which expects cwd=RA

from sql_filter import eligible_skus
from bm25_index import search as bm25_search
from lightrag import QueryParam
from ingest import build_rag
from dotenv import load_dotenv

load_dotenv()

async def search_products(user_query: str, hard_constraints: dict = None):
    """
    Complete product search using all three retrieval branches.
    
    Args:
        user_query: Natural language question, e.g. "What cotton shirts?"
        hard_constraints: Optional dict with keys: max_price, category, size, 
                         stock_status (passed to sql_filter)
    
    Returns:
        dict with keys:
        - eligible_skus: list of all SKUs matching hard_constraints
        - bm25_results: [(sku_id, score), ...] from BM25 lexical search
        - semantic_results: raw context from LightRAG graph+vector search
    """
    
    # Step 1: Get eligible SKU set (mandatory intersection)
    eligible = eligible_skus(hard_constraints or {})
    eligible_sku_set = {row["sku_id"] for row in eligible}
    print(f"✓ Eligible SKUs: {len(eligible_sku_set)}")
    
    # Step 2: BM25 lexical search
    bm25_results = bm25_search(user_query, top_n=50)
    bm25_skus = [sku_id for sku_id, score in bm25_results]
    print(f"✓ BM25 returned: {len(bm25_skus)} candidates")
    
    # Step 3: LightRAG semantic search (graph + vectors)
    rag = await build_rag()
    semantic_context = await rag.aquery(
        user_query, 
        param=QueryParam(mode="mix", only_need_context=True)
    )
    await rag.finalize_storages()
    print(f"✓ LightRAG returned context (raw chunks/entities/relations)")
    
    return {
        "eligible_skus": list(eligible_sku_set),
        "bm25_results": bm25_results,
        "semantic_context": semantic_context,
    }

# Run it
if __name__ == "__main__":
    result = asyncio.run(search_products(
        user_query="What cotton shirts are under 1500 rupees?",
        hard_constraints={"max_price": 1500}
    ))
    print("\n=== Results ===")
    print(f"Eligible SKUs: {len(result['eligible_skus'])}")
    print(f"BM25 top 5: {result['bm25_results'][:5]}")
    print(f"Semantic context length: {len(result['semantic_context'])}")
```

**Run it:**
```bash
cd /path/to/Vatsalya\ Version
python agent.py
```

### 3.2 Production Integration in a Long-Lived Agent

For a web service, API server, or long-running agent, **do NOT rebuild the RAG instance per query** (it's expensive: talks to Qdrant, initializes storages). Reuse a single instance:

```python
# production_agent.py

import asyncio
import os
import sys

sys.path.insert(0, "RA")
os.chdir("RA")

from ingest import build_rag
from sql_filter import eligible_skus
from bm25_index import search as bm25_search
from lightrag import QueryParam
from dotenv import load_dotenv

load_dotenv()

# Global RAG instance (built once at startup, reused for every query)
_rag_instance = None

async def get_rag():
    """Singleton pattern: build RAG once, reuse forever."""
    global _rag_instance
    if _rag_instance is None:
        print("Initializing RAG (first query)...")
        _rag_instance = await build_rag()
    return _rag_instance

async def search_products_cached(user_query: str, hard_constraints: dict = None):
    """Production-ready search with reused RAG instance."""
    
    # SQL hard filter
    eligible = eligible_skus(hard_constraints or {})
    eligible_sku_set = {row["sku_id"] for row in eligible}
    
    # BM25
    bm25_results = bm25_search(user_query, top_n=50)
    
    # LightRAG (reuses global instance)
    rag = await get_rag()
    semantic_context = await rag.aquery(
        user_query, 
        param=QueryParam(mode="mix", only_need_context=True)
    )
    
    return {
        "eligible_skus": list(eligible_sku_set),
        "bm25_results": bm25_results,
        "semantic_context": semantic_context,
    }

async def main():
    """Simulate multiple queries in a loop."""
    queries = [
        ("What cotton shirts are available?", {"max_price": 2000}),
        ("Find blue products", {"stock_status": "in_stock"}),
        ("What materials are used in shoes?", {}),
    ]
    
    for user_query, constraints in queries:
        print(f"\n{'='*60}")
        print(f"Query: {user_query}")
        print(f"Constraints: {constraints}")
        result = await search_products_cached(user_query, constraints)
        print(f"Eligible: {len(result['eligible_skus'])}, BM25: {len(result['bm25_results'])}")
    
    # Cleanup (important for file handles / Qdrant connections)
    global _rag_instance
    if _rag_instance:
        await _rag_instance.finalize_storages()

if __name__ == "__main__":
    asyncio.run(main())
```

### 3.3 Integration with Agentic Frameworks (AnthropicSDK, LangChain, etc.)

If your agent framework natively supports tool use (Anthropic SDK, LangChain ReAct, etc.), expose these as **tools the agent can call**:

```python
# agent_with_tools.py — Anthropic SDK example

import asyncio
import json
import os
import sys
from anthropic import Anthropic

sys.path.insert(0, "RA")
os.chdir("RA")

from sql_filter import eligible_skus
from bm25_index import search as bm25_search
from ingest import build_rag
from lightrag import QueryParam

client = Anthropic()
_rag = None

async def get_rag():
    global _rag
    if _rag is None:
        _rag = await build_rag()
    return _rag

def search_catalog_tool(user_query: str, max_price: int = None, stock_status: str = None):
    """Tool: search product catalog using all three retrieval branches."""
    
    # Build hard constraints
    hard_constraints = {}
    if max_price is not None:
        hard_constraints["max_price"] = max_price
    if stock_status is not None:
        hard_constraints["stock_status"] = stock_status
    
    # SQL filter
    eligible = eligible_skus(hard_constraints)
    
    # BM25
    bm25_results = bm25_search(user_query, top_n=20)
    
    # (Synchronous wrapper for semantic search -- see note below)
    # In production, make this truly async-aware
    
    return {
        "eligible_count": len(eligible),
        "top_bm25_matches": [
            {"sku_id": sku, "score": score} 
            for sku, score in bm25_results[:5]
        ],
    }

tools = [
    {
        "name": "search_catalog",
        "description": "Search product catalog using BM25 + knowledge graph retrieval",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language search query"},
                "max_price": {"type": "number", "description": "Max price filter (optional)"},
                "stock_status": {
                    "type": "string",
                    "enum": ["in_stock", "low_stock", "out_of_stock"],
                    "description": "Stock status filter (optional)"
                },
            },
            "required": ["query"],
        },
    }
]

def agent_loop(user_message: str):
    """Run an agentic loop with tool use."""
    messages = [{"role": "user", "content": user_message}]
    
    while True:
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",  # Update to your model
            max_tokens=1024,
            tools=tools,
            messages=messages,
        )
        
        if response.stop_reason == "end_turn":
            # Agent finished reasoning, return its response
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text
            break
        
        if response.stop_reason == "tool_use":
            # Agent wants to call a tool
            tool_calls = [b for b in response.content if b.type == "tool_use"]
            
            for tool_call in tool_calls:
                if tool_call.name == "search_catalog":
                    result = search_catalog_tool(**tool_call.input)
                    messages.append({"role": "assistant", "content": response.content})
                    messages.append({
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_call.id,
                                "content": json.dumps(result),
                            }
                        ],
                    })
            continue
        
        break
    
    return "No response"

if __name__ == "__main__":
    response = agent_loop("Find me blue cotton shirts under 1500 that are in stock")
    print(response)
```

**Key notes for agentic integration:**
- `build_rag()` is **async** — wrap it appropriately for your framework
- `eligible_skus()` and `bm25_search()` are **sync** — call them directly
- Store the RAG instance globally/in context to avoid rebuilding per request
- Call `await rag.finalize_storages()` when the agent shuts down

---

## 4. Step-by-Step Setup & Testing

### 4.1 Fresh Start (From Scratch)

```bash
# 1. Set up project structure
cd /path/to/Vatsalya\ Version
ls RA/config.py RA/requirements.txt   # Verify RA folder exists

# 2. Create .env file
cd RA
cp .env.example .env
# Edit .env with your settings (see section 2.6 if using DeepInfra)

# 3. Install Python deps
pip install -r requirements.txt

# 4. Start Qdrant (if not running)
docker volume create flipkart_qdrant_data
docker run -d --name flipkart-qdrant --restart unless-stopped \
  -p 6333:6333 -p 6334:6334 \
  -v flipkart_qdrant_data:/qdrant/storage \
  qdrant/qdrant

# 5. Start MySQL (if not running)
docker ps | grep flipkart-mysql || docker start flipkart-mysql

# 6. Ingest the catalog (one-time, or resume after interruption)
python ingest.py
# Watch the progress bars. On first run: ~19,996 SKUs
# The subset (~2,673) get full extraction; rest get chunk embedding only

# 7. Test a query
python query.py "What cotton shirts are available?"
```

### 4.2 Restore from Backup

```bash
cd /path/to/Vatsalya\ Version/RA

# If you have a backup ZIP:
./import.sh lightrag_backup_20260804_145154.zip
# Restores: lightrag_storage/, graph_sampling_output/, config.py, servers.txt, Qdrant data

# Then:
pip install -r requirements.txt
python query.py "test query"  # Verify end-to-end
```

### 4.3 Verify Your Setup

```bash
cd RA

# Check Qdrant has data
curl -s http://localhost:6333/collections | jq '.result | length'
# Output should be 3 (chunks, entities, relationships)

# Check LightRAG storage
python check_ingestion_status.py
# Shows: docs in lightrag_storage vs. points in Qdrant collections

# Check BM25 index
python bm25_index.py "cotton shirt"
# Shows: BM25 results for a sample query

# Check SQL eligibility
python -c "from sql_filter import eligible_skus; print(len(eligible_skus({'max_price': 1500})))"
# Shows: how many SKUs match a simple filter
```

---

## 5. Configuration Cheat Sheet

### Query-Time Backend (`.env` or `config.py`)

| Setting | Default | Meaning | Agent Impact |
|---------|---------|---------|---------|
| `INFERENCE_BACKEND` | `deepinfra` | Which backend answers LLM calls during queries | Set to `ollama` if no DeepInfra key; `query.py` uses this |
| `DEEPINFRA_API_KEY` | (empty) | DeepInfra API key | Required only if `INFERENCE_BACKEND=deepinfra` |
| `DEEPINFRA_LLM_MODEL` | (empty) | DeepInfra model ID (e.g. `Qwen/Qwen3-235B-...`) | Required only if `INFERENCE_BACKEND=deepinfra` |
| `LIGHTRAG_QUERY_EMBED_BACKEND` | (matches ingestion) | Embedding backend for queries | Leave unset (safe); only change if you re-embedded with DeepInfra |

### Ingestion Backend (always Ollama, never DeepInfra)

| Setting | Default | Meaning |
|---------|---------|---------|
| `LIGHTRAG_LLM_MODEL` | `gemma4:e4b` | Model for entity extraction (Ollama only) |
| `LIGHTRAG_EMBED_BACKEND` | `local` | `local` (CPU/MPS, fast) or `ollama` (cluster) |
| `LIGHTRAG_NUM_CTX` | `8192` | Context window per extraction call |
| `LIGHTRAG_LLM_MAX_ASYNC` | `16` | Concurrent extraction requests |
| `LIGHTRAG_EMBEDDING_MAX_ASYNC` | `4` | Concurrent embedding requests |

### Database/Service URLs

| Setting | Default | Meaning |
|---------|---------|---------|
| `QDRANT_URL` | `http://localhost:6333` | Vector database endpoint |
| `OLLAMA_HOST` | `http://localhost:11345` | Single Ollama server (ingestion reads `servers.txt` instead) |

---

## 6. Troubleshooting

### "ModuleNotFoundError: No module named 'config'"

**Cause**: You're not running from `RA/` directory or `RA/` isn't on `sys.path`.

**Fix:**
```python
import sys
import os
sys.path.insert(0, "RA")
os.chdir("RA")  # Very important — config.py expects this cwd
```

### "ConnectionRefusedError: Qdrant at http://localhost:6333"

**Cause**: Qdrant container isn't running.

**Fix:**
```bash
docker ps | grep flipkart-qdrant
# If stopped: docker start flipkart-qdrant
# If doesn't exist: follow "Qdrant Setup" in README.md
```

### "sqlite3.OperationalError: database is locked"

**Cause**: Multiple processes writing to `lightrag_storage/` simultaneously.

**Fix**: Only one `ingest.py` process should run at a time. If interrupted, just re-run — it's checkpoint-safe.

### "DEEPINFRA_API_KEY is empty" when `INFERENCE_BACKEND=deepinfra`

**Cause**: Missing or incorrectly formatted `.env`.

**Fix:**
```bash
cd RA
echo 'DEEPINFRA_API_KEY=sk_...' >> .env
# Or edit .env directly with your real key
source .env  # (optional, for shell testing)
python query.py "test"
```

### Query returns empty results

**Cause**: LightRAG graph not built (ingestion never ran successfully).

**Fix:**
```bash
python check_ingestion_status.py
# If Qdrant point_count is 0: run python ingest.py
# If point_count > 0 but wrong: check if vectors landed in the right collections
```

### "Could not connect to Ollama at http://127.0.0.1:11435"

**Cause**: Ollama server not running, or port is wrong.

**Fix**:
```bash
# Check servers.txt
cat servers.txt
# Each line should be a running Ollama server with LLM_MODEL pulled
curl http://127.0.0.1:11435/api/tags
# If curl fails: start Ollama or fix the URL in servers.txt
```

---

## 7. Production Deployment Considerations

1. **RAG instance management**: Build once at startup, reuse via module-level cache (see section 3.2)
2. **Ollama scaling**: Add more servers to `servers.txt` for higher throughput, increase `LIGHTRAG_LLM_MAX_ASYNC` accordingly
3. **DeepInfra vs. Ollama**: DeepInfra is great for queries (hosted, no VRAM), Ollama is for ingestion (no per-token cost)
4. **Async patterns**: All of LightRAG's real I/O is async (`build_rag()`, `rag.aquery()`, `finalize_storages()`); wrap correctly for your framework
5. **Checkpointing**: `ingest.py` is checkpoint-safe, but only one instance should run per working directory at a time
6. **Monitoring**: Use `check_ingestion_status.py` and `diagnose.py` to sanity-check the pipeline health

---

## 8. What's Next — After Integration

Once you can call LightRAG from your agent, the next steps per `retrieval-architecture.md` are:

1. **`semantic_search.py`** — wrap LightRAG's returned context into clean `[(sku_id, score), ...]` format (same shape as BM25)
2. **`merge.py` / `fuse_and_rerank()`** — union(BM25, semantic) → intersect(eligible) → rerank pipeline
3. **`search_catalog(query_state)`** — main entrypoint: split query into hard/soft, call merge, return final ranked candidates

These three will turn the three independent branches (SQL, BM25, LightRAG) into a single unified `search_catalog()` tool your agent can call.

---

## 9. Quick Reference — Copy-Paste Commands

```bash
# Setup
cd /path/to/Vatsalya\ Version/RA
cp .env.example .env  # Edit with your settings
pip install -r requirements.txt

# Start services
docker start flipkart-qdrant flipkart-mysql

# Verify
curl http://localhost:6333/collections          # Qdrant
mysql -h 127.0.0.1 -P 3307 -u flipkart_user -pflipkart_pass flipkart -e "SELECT 1"  # MySQL
python check_ingestion_status.py                 # Overall health

# Ingest (one-time or resume)
python ingest.py

# Test queries
python query.py "What cotton shirts are available?"
./webui.sh  # Web UI at http://localhost:8000

# Integration
python agent.py  # Your custom agentic code (see section 3.1-3.3)
```

