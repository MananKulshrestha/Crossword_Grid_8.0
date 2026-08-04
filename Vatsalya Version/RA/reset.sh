#!/bin/bash

# Full reset of all ingestion state: storage, queue, logs, caches.
# Run this before a clean re-run of ./run.sh / python ingest.py.
#
# Clears:
# - LightRAG local storage (graph, KV stores, doc_status, and the pipeline's
#   own ingress/queue state -- all of it lives under lightrag_storage/,
#   there is no separate queue file elsewhere)
# - BM25 index
# - Qdrant collections (via REST API)
# - Log files (lightrag.log)
# - Python bytecode cache (__pycache__)
# - Any leftover atomic-write temp files (*.tmp)
#
# Does NOT clear graph_sampling_output/ (no LLM needed to regenerate, kept
# by default -- pass --all to also wipe it).

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

WIPE_GRAPH_SAMPLING=false
if [ "$1" == "--all" ]; then
    WIPE_GRAPH_SAMPLING=true
fi

echo -e "${YELLOW}=== Full reset: LightRAG storage, queue, BM25 index, Qdrant, logs, caches ===${NC}"
if [ "$WIPE_GRAPH_SAMPLING" == "true" ]; then
    echo -e "${YELLOW}--all passed: graph_sampling_output/ will ALSO be deleted${NC}"
fi
echo "This will DELETE all ingested data and logs. Continue? (type 'yes' to confirm)"
read -r confirm
if [ "$confirm" != "yes" ]; then
    echo "Aborted."
    exit 0
fi

# LightRAG local storage -- this IS the queue: doc_status (PENDING/PROCESSING/
# PROCESSED/FAILED tracking), the graph, KV stores, and any pipeline_status/
# ingress state LightRAG keeps between runs. There is nothing to reset
# separately for "the queue" -- deleting this directory clears it entirely.
echo -e "${YELLOW}Clearing LightRAG storage (includes doc-status queue)...${NC}"
if [ -d "lightrag_storage" ]; then
    rm -rf lightrag_storage
    echo -e "${GREEN}✓ Deleted lightrag_storage/${NC}"
else
    echo "lightrag_storage/ not found (already clean)"
fi

# BM25 index
echo -e "${YELLOW}Clearing BM25 index...${NC}"
if [ -d "bm25_storage" ]; then
    rm -rf bm25_storage
    echo -e "${GREEN}✓ Deleted bm25_storage/${NC}"
else
    echo "bm25_storage/ not found (already clean)"
fi

# Log files
echo -e "${YELLOW}Clearing logs...${NC}"
if [ -f "lightrag.log" ]; then
    rm -f lightrag.log
    echo -e "${GREEN}✓ Deleted lightrag.log${NC}"
else
    echo "lightrag.log not found (already clean)"
fi
find . -maxdepth 1 -iname "*.log" -not -name "lightrag.log" -exec rm -f {} \; -exec echo "  Also deleted: {}" \;

# Python bytecode cache -- stale .pyc files have caused confusing "old code
# still running" symptoms before; safe to wipe, Python regenerates it.
echo -e "${YELLOW}Clearing __pycache__...${NC}"
if [ -d "__pycache__" ]; then
    rm -rf __pycache__
    echo -e "${GREEN}✓ Deleted __pycache__/${NC}"
fi
find . -maxdepth 2 -iname "__pycache__" -not -path "./.venv/*" -exec rm -rf {} \; 2>/dev/null || true

# Leftover atomic-write temp files (should never survive a clean run, but a
# hard kill mid-write to bm25_storage/index.pkl.tmp etc. could leave one)
echo -e "${YELLOW}Clearing temp files...${NC}"
find . -maxdepth 2 -iname "*.tmp" -not -path "./.venv/*" -exec rm -f {} \; -exec echo "  Deleted: {}" \;
echo -e "${GREEN}✓ Cleared temp files${NC}"

# Qdrant collections via REST API
echo -e "${YELLOW}Clearing Qdrant collections...${NC}"
QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"
for collection in "lightrag_vdb_chunks_nomic_embed_text_768d" "lightrag_vdb_entities_nomic_embed_text_768d" "lightrag_vdb_relationships_nomic_embed_text_768d"; do
    if curl -s -X DELETE "$QDRANT_URL/collections/$collection" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Deleted Qdrant collection: $collection${NC}"
    else
        echo "  (Qdrant collection $collection not found or not reachable, skipping)"
    fi
done

if [ "$WIPE_GRAPH_SAMPLING" == "true" ]; then
    echo -e "${YELLOW}Clearing graph_sampling_output/ (--all)...${NC}"
    if [ -d "graph_sampling_output" ]; then
        rm -rf graph_sampling_output
        echo -e "${GREEN}✓ Deleted graph_sampling_output/${NC}"
    fi
fi

echo -e "${GREEN}=== Reset complete ===${NC}"
echo ""
echo "Next steps:"
if [ "$WIPE_GRAPH_SAMPLING" == "true" ]; then
    echo "  1. Run: python graph_sampling.py   (subset was wiped, regenerate first)"
    echo "  2. Then: ./run.sh"
else
    echo "  1. Run: ./run.sh"
    echo "  2. Or: python ingest.py"
    echo ""
    echo "Note: graph_sampling_output/ was NOT deleted (pass --all to also wipe it)"
fi
