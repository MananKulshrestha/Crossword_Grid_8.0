# Quick Start — 5 Minutes to LightRAG Queries

**TL;DR: Everything is ready. Follow these 7 steps.**

---

## Setup (One-Time, ~2 minutes)

```bash
# 1. Go to RA folder
cd /path/to/Vatsalya\ Version/RA

# 2. Create config
cp .env.example .env
# (Edit .env if you want DeepInfra for queries, otherwise leave as-is)

# 3. Install Python packages
pip install -r requirements.txt

# 4. Start databases (if not running)
docker start flipkart-qdrant flipkart-mysql

# 5. Verify setup
curl http://localhost:6333/collections | jq .  # Qdrant working?
mysql -h 127.0.0.1 -P 3307 -u flipkart_user -pflipkart_pass flipkart -e "SELECT 1"  # MySQL working?
```

---

## Query (Use It)

### Via CLI
```bash
python query.py "What cotton shirts are available?"
```

### Via Web UI (Prettier)
```bash
./webui.sh
# Opens http://localhost:8000 in browser
```

### Via Python Code (For Agents)
```python
import asyncio
from ingest import build_rag
from lightrag import QueryParam

async def search(query):
    rag = await build_rag()
    result = await rag.aquery(query, param=QueryParam(mode="mix", only_need_context=True))
    await rag.finalize_storages()
    return result

asyncio.run(search("What cotton shirts are available?"))
```

### Via Agent (Recommended Pattern)
```python
# Save as agent.py

import asyncio
import sys
import os
sys.path.insert(0, "RA")
os.chdir("RA")

from sql_filter import eligible_skus
from bm25_index import search as bm25_search
from ingest import build_rag
from lightrag import QueryParam

async def search_products(query, max_price=None):
    # SQL: hard filters
    constraints = {"max_price": max_price} if max_price else {}
    eligible = eligible_skus(constraints)
    
    # BM25: keyword search
    bm25_results = bm25_search(query, top_n=10)
    
    # LightRAG: semantic search
    rag = await build_rag()
    semantic_context = await rag.aquery(query, param=QueryParam(mode="mix", only_need_context=True))
    await rag.finalize_storages()
    
    return {
        "eligible_skus": len(eligible),
        "bm25_results": bm25_results,
        "semantic": semantic_context,
    }

result = asyncio.run(search_products("What cotton shirts?", max_price=1500))
print(f"Found {result['eligible_skus']} eligible SKUs")
print(f"BM25 matched: {len(result['bm25_results'])} products")
```

Run it:
```bash
python agent.py
```

---

## If Ingestion Needs to Run (Adding New Products)

```bash
# Requires: every server in servers.txt running with LLM_MODEL pulled

python ingest.py  # ~1-2 hours for full catalog
# Already-processed SKUs are skipped automatically
# Safe to interrupt and re-run anytime
```

---

## Troubleshooting

| Problem | Quick Fix |
|---------|-----------|
| "Qdrant refused" | `docker start flipkart-qdrant` |
| "MySQL refused" | `docker start flipkart-mysql` |
| "ModuleNotFoundError: config" | `cd RA` then run your script |
| "Empty query results" | Run `python check_ingestion_status.py` — if Qdrant has 0 points, run `python ingest.py` first |
| "DEEPINFRA_API_KEY error" | Edit `.env`, add your DeepInfra key from https://deepinfra.com/dash/api_keys |

---

## What's Integrated

✅ **SQL filtering** — Hard constraints (price, category, stock)  
✅ **BM25 search** — Keyword matching  
✅ **LightRAG** — Knowledge graph + vector embeddings (Qdrant)  
✅ **DeepInfra** — Optional hosted LLM for queries (leave blank to use local Ollama)  
✅ **Ingestion** — Ollama cluster via `servers.txt`, never DeepInfra  
✅ **Backup/restore** — `./backup.sh` and `./import.sh`  

---

## Full Docs

- **Setup details**: See `README.md` (MySQL, Qdrant, Docker)
- **Integration for agents**: See `AGENTIC_PIPELINE_SETUP.md`
- **Verification checklist**: See `INTEGRATION_VERIFICATION.md`
- **Design deep-dive**: See `integration.md`, `IMPLEMENTATION.md`, `lightrag-implementation.md`

---

## API Quick Reference

```python
# SQL: hard constraints
from sql_filter import eligible_skus
skus = eligible_skus({"max_price": 1500, "stock_status": "in_stock"})

# BM25: keyword search
from bm25_index import search
results = search("cotton shirt", top_n=50)  # [(sku_id, score), ...]

# LightRAG: semantic search (must be async)
from ingest import build_rag
from lightrag import QueryParam
rag = await build_rag()
context = await rag.aquery("question", param=QueryParam(mode="mix", only_need_context=True))
await rag.finalize_storages()
```

---

That's it. You're ready. 🚀

