#!/bin/bash

# Backup script for LightRAG project
# Creates a zip file with all LightRAG storage, Qdrant data, LLM cache, and configuration

set -e

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"

# Backup destination
BACKUP_DIR="$PROJECT_ROOT/backups"
mkdir -p "$BACKUP_DIR"

# Timestamp for backup filename
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="$BACKUP_DIR/lightrag_backup_${TIMESTAMP}.zip"

echo "=========================================="
echo "LightRAG Backup Script"
echo "=========================================="
echo "Backup time: $TIMESTAMP"
echo "Backup file: $BACKUP_FILE"
echo ""

# Create a temporary directory for staging
TEMP_BACKUP_DIR=$(mktemp -d)
trap "rm -rf $TEMP_BACKUP_DIR" EXIT

echo "Staging backup files..."

# 1. LightRAG storage (includes LLM cache in kv_store_llm_response_cache.json)
if [ -d "$PROJECT_ROOT/lightrag_storage" ]; then
    echo "  ✓ LightRAG storage"
    cp -r "$PROJECT_ROOT/lightrag_storage" "$TEMP_BACKUP_DIR/"
else
    echo "  ⚠ LightRAG storage not found at $PROJECT_ROOT/lightrag_storage"
fi

# 2. Graph sampling output (subset selection)
if [ -d "$PROJECT_ROOT/graph_sampling_output" ]; then
    echo "  ✓ Graph sampling output"
    cp -r "$PROJECT_ROOT/graph_sampling_output" "$TEMP_BACKUP_DIR/"
else
    echo "  ⚠ Graph sampling output not found"
fi

# 3. Configuration files
CONFIG_FILES=(
    "config.py"
    "servers.txt"
)
for file in "${CONFIG_FILES[@]}"; do
    if [ -f "$PROJECT_ROOT/$file" ]; then
        echo "  ✓ $file"
        cp "$PROJECT_ROOT/$file" "$TEMP_BACKUP_DIR/"
    fi
done

# 4. Back up Qdrant data. README.md's setup uses a named Docker volume
# (flipkart_qdrant_data mounted at /qdrant/storage in the flipkart-qdrant
# container), NOT a bind-mounted host directory -- so checking for
# ./qdrant_storage or ../qdrant_storage on disk (the old logic here) always
# missed it, and the Docker-detection fallback below it only *printed a
# note* without copying any bytes, while still setting QDRANT_FOUND=true.
# That meant every past backup silently shipped zero vector data while
# reporting success. Fixed to actually pull the data out via `docker exec
# ... tar` (streams a tarball of /qdrant/storage out of the running
# container -- works against the volume's real contents without needing a
# bind mount, and preserves permissions correctly across machines, unlike a
# raw `docker cp` of a volume path).
echo "Looking for Qdrant data..."
QDRANT_FOUND=false

# Case 1: a bind-mounted host directory (older/alternate setups).
if [ -d "$PROJECT_ROOT/qdrant_storage" ]; then
    echo "  ✓ Qdrant storage (local bind mount)"
    cp -r "$PROJECT_ROOT/qdrant_storage" "$TEMP_BACKUP_DIR/"
    QDRANT_FOUND=true
elif [ -d "$PROJECT_ROOT/../qdrant_storage" ]; then
    echo "  ✓ Qdrant storage (parent dir bind mount)"
    cp -r "$PROJECT_ROOT/../qdrant_storage" "$TEMP_BACKUP_DIR/qdrant_storage_parent/"
    QDRANT_FOUND=true
fi

# Case 2: named Docker volume via a running container (README.md's actual
# setup: `docker run --name flipkart-qdrant -v flipkart_qdrant_data:/qdrant/storage ...`).
if [ "$QDRANT_FOUND" = false ] && command -v docker &> /dev/null; then
    QDRANT_CONTAINER=$(docker ps --filter "ancestor=qdrant/qdrant" --format "{{.ID}}" 2>/dev/null | head -1)
    if [ -n "$QDRANT_CONTAINER" ]; then
        echo "  ℹ Found running Qdrant container: $QDRANT_CONTAINER -- extracting /qdrant/storage..."
        if docker exec "$QDRANT_CONTAINER" tar czf - -C /qdrant/storage . > "$TEMP_BACKUP_DIR/qdrant_data.tar.gz"; then
            QDRANT_SIZE=$(du -h "$TEMP_BACKUP_DIR/qdrant_data.tar.gz" | cut -f1)
            echo "  ✓ Qdrant storage (Docker volume, ${QDRANT_SIZE} compressed)"
            QDRANT_FOUND=true
        else
            echo "  ✗ Failed to extract Qdrant data from container $QDRANT_CONTAINER"
            rm -f "$TEMP_BACKUP_DIR/qdrant_data.tar.gz"
        fi
    fi
fi

if [ "$QDRANT_FOUND" = false ]; then
    echo "  ⚠ Qdrant storage not found or not running -- this backup will NOT include vector data."
    echo "    Start Qdrant (see README.md) and re-run this script before relying on this backup."
fi

# 5. Create metadata file
echo "  ✓ Creating backup metadata"
cat > "$TEMP_BACKUP_DIR/BACKUP_INFO.txt" << EOF
LightRAG Backup
===============
Created: $TIMESTAMP
Hostname: $(hostname)
User: $(whoami)

Contents:
---------
- lightrag_storage/           All LightRAG data including LLM cache
- graph_sampling_output/      Graph sampling subset and reports
- config.py                   Configuration (models, async settings, paths)
- servers.txt                 Ollama server list for extraction
- BACKUP_INFO.txt             This file

LLM Cache Location:
- lightrag_storage/kv_store_llm_response_cache.json

Restore Instructions:
---------------------
Run: ./import.sh $(basename "$BACKUP_FILE")
(import.sh restores lightrag_storage/, config, servers.txt, AND recreates
the Qdrant Docker volume + container from qdrant_data.tar.gz -- see
import.sh's own comments for what it does step by step.)

Notes:
------
- This backup does NOT include the source data files (flipkart_lightrag_corpus.md, flipkart_catalog_structured.jsonl)
- Qdrant data IS included as qdrant_data.tar.gz (tar of the Docker volume's /qdrant/storage), when Qdrant was running at backup time -- see "Looking for Qdrant data" output above for whether this succeeded
- LLM cache (kv_store_llm_response_cache.json) is included to avoid re-running extractions

EOF

# 6. Create the zip file
echo ""
echo "Creating zip file..."
cd "$TEMP_BACKUP_DIR"
zip -r -q "$BACKUP_FILE" .
cd - > /dev/null

# Get file size
SIZE=$(du -h "$BACKUP_FILE" | cut -f1)

echo "=========================================="
echo "✓ Backup completed successfully!"
echo "=========================================="
echo "File: $BACKUP_FILE"
echo "Size: $SIZE"
echo ""
echo "To restore, extract this zip in your project directory:"
echo "  unzip $BACKUP_FILE"
echo ""
