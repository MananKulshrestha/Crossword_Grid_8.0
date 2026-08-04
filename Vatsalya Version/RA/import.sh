#!/bin/bash

# Import/restore script for the RA LightRAG pipeline -- companion to
# backup.sh. Takes a lightrag_backup_*.zip produced by backup.sh and
# restores everything needed to resume the pipeline on another machine:
# lightrag_storage/ (doc status, graph, KV stores), graph_sampling_output/,
# config.py, servers.txt, and the Qdrant vector data (recreated as a fresh
# Docker volume + container matching README.md's setup).
#
# Usage:
#   ./import.sh lightrag_backup_20260804_145154.zip
#
# What this does NOT restore (must come from elsewhere, see README.md):
#   - flipkart_lightrag_corpus.md / flipkart_catalog_structured.jsonl
#     (source data -- backup.sh deliberately excludes these, they're not
#     pipeline state)
#   - Ollama servers themselves (servers.txt lists URLs, but each
#     `ollama serve` process/model pull is the receiving machine's own setup)
#   - Python environment (run `pip install -r requirements.txt` /
#     `uv sync` yourself first)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"

QDRANT_CONTAINER_NAME="flipkart-qdrant"
QDRANT_VOLUME_NAME="flipkart_qdrant_data"

if [ $# -lt 1 ]; then
    echo "Usage: $0 <backup_zip_file>"
    echo "Example: $0 lightrag_backup_20260804_145154.zip"
    exit 1
fi

BACKUP_FILE="$1"
if [ ! -f "$BACKUP_FILE" ]; then
    # Also try resolving relative to backups/ (where backup.sh writes them),
    # so a bare filename copied from backup.sh's own output works as-is.
    if [ -f "$PROJECT_ROOT/backups/$BACKUP_FILE" ]; then
        BACKUP_FILE="$PROJECT_ROOT/backups/$BACKUP_FILE"
    else
        echo "Backup file not found: $BACKUP_FILE"
        exit 1
    fi
fi

echo "=========================================="
echo "LightRAG Import Script"
echo "=========================================="
echo "Backup file: $BACKUP_FILE"
echo ""

TEMP_RESTORE_DIR=$(mktemp -d)
trap "rm -rf $TEMP_RESTORE_DIR" EXIT

echo "Extracting backup..."
unzip -q "$BACKUP_FILE" -d "$TEMP_RESTORE_DIR"

if [ -f "$TEMP_RESTORE_DIR/BACKUP_INFO.txt" ]; then
    echo ""
    echo "--- Backup info ---"
    cat "$TEMP_RESTORE_DIR/BACKUP_INFO.txt"
    echo "-------------------"
    echo ""
fi

# 1. lightrag_storage/ -- refuse to silently clobber an existing one with
# real state; this mirrors backup.sh's own no-silent-data-loss discipline.
if [ -d "$TEMP_RESTORE_DIR/lightrag_storage" ]; then
    if [ -d "$PROJECT_ROOT/lightrag_storage" ] && [ -n "$(ls -A "$PROJECT_ROOT/lightrag_storage" 2>/dev/null)" ]; then
        echo "⚠ $PROJECT_ROOT/lightrag_storage already exists and is non-empty."
        read -p "  Overwrite it with the backup's contents? [y/N] " -n 1 -r
        echo ""
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            echo "  Skipping lightrag_storage restore."
        else
            rm -rf "$PROJECT_ROOT/lightrag_storage"
            cp -r "$TEMP_RESTORE_DIR/lightrag_storage" "$PROJECT_ROOT/"
            echo "  ✓ lightrag_storage restored"
        fi
    else
        cp -r "$TEMP_RESTORE_DIR/lightrag_storage" "$PROJECT_ROOT/"
        echo "  ✓ lightrag_storage restored"
    fi
else
    echo "  ⚠ Backup has no lightrag_storage/ -- nothing to restore there"
fi

# 2. graph_sampling_output/
if [ -d "$TEMP_RESTORE_DIR/graph_sampling_output" ]; then
    cp -r "$TEMP_RESTORE_DIR/graph_sampling_output" "$PROJECT_ROOT/"
    echo "  ✓ graph_sampling_output restored"
fi

# 3. Config files -- copied alongside, not overwriting silently if the
# destination differs, since a teammate may deliberately run a different
# servers.txt (their own Ollama hosts). Written as .fromBackup, never
# auto-applied over a local file.
for file in config.py servers.txt; do
    if [ -f "$TEMP_RESTORE_DIR/$file" ]; then
        if [ -f "$PROJECT_ROOT/$file" ] && ! diff -q "$TEMP_RESTORE_DIR/$file" "$PROJECT_ROOT/$file" > /dev/null 2>&1; then
            cp "$TEMP_RESTORE_DIR/$file" "$PROJECT_ROOT/$file.fromBackup"
            echo "  ℹ $file differs from your local copy -- saved as $file.fromBackup for you to diff/merge (servers.txt in particular is often meant to differ per machine)"
        else
            cp "$TEMP_RESTORE_DIR/$file" "$PROJECT_ROOT/$file"
            echo "  ✓ $file restored"
        fi
    fi
done

# 4. Qdrant data -- recreate the named volume from scratch and load the
# tarball into it via a throwaway alpine container (no qdrant/qdrant
# process needs to be running for this step; only the volume needs to
# exist), matching README.md's own `docker volume create` + `docker run`
# setup so the restored container is indistinguishable from a fresh
# README-driven setup with data already loaded.
if [ -f "$TEMP_RESTORE_DIR/qdrant_data.tar.gz" ]; then
    if ! command -v docker &> /dev/null; then
        echo "  ⚠ Backup includes Qdrant data but Docker is not installed -- skipping Qdrant restore."
        echo "    Install Docker, then re-run: ./import.sh $(basename "$BACKUP_FILE")"
    else
        echo ""
        echo "Restoring Qdrant data..."

        EXISTING_CONTAINER=$(docker ps -a --filter "name=^${QDRANT_CONTAINER_NAME}\$" --format "{{.ID}}" 2>/dev/null || true)
        if [ -n "$EXISTING_CONTAINER" ]; then
            echo "  ⚠ A container named '$QDRANT_CONTAINER_NAME' already exists."
            read -p "  Stop and remove it to restore into a fresh volume? [y/N] " -n 1 -r
            echo ""
            if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                echo "  Skipping Qdrant restore -- your existing container/volume is untouched."
                EXISTING_CONTAINER=""  # signal: don't proceed below
                SKIP_QDRANT=true
            else
                docker rm -f "$QDRANT_CONTAINER_NAME" > /dev/null
                docker volume rm "$QDRANT_VOLUME_NAME" > /dev/null 2>&1 || true
            fi
        fi

        if [ "${SKIP_QDRANT:-false}" != "true" ]; then
            docker volume create "$QDRANT_VOLUME_NAME" > /dev/null
            echo "  ✓ Created Docker volume: $QDRANT_VOLUME_NAME"

            docker run --rm \
                -v "$QDRANT_VOLUME_NAME:/qdrant/storage" \
                -v "$TEMP_RESTORE_DIR:/backup" \
                alpine sh -c "cd /qdrant/storage && tar xzf /backup/qdrant_data.tar.gz"
            echo "  ✓ Loaded Qdrant data into volume"

            docker run -d \
                --name "$QDRANT_CONTAINER_NAME" \
                --restart unless-stopped \
                -p 6333:6333 \
                -p 6334:6334 \
                -v "$QDRANT_VOLUME_NAME:/qdrant/storage" \
                qdrant/qdrant > /dev/null
            echo "  ✓ Started Qdrant container: $QDRANT_CONTAINER_NAME"

            echo "  Waiting for Qdrant to become reachable..."
            for i in $(seq 1 15); do
                if curl -sf http://localhost:6333/collections > /dev/null 2>&1; then
                    echo "  ✓ Qdrant is up at http://localhost:6333"
                    break
                fi
                sleep 1
            done
        fi
    fi
else
    echo "  ⚠ Backup has no qdrant_data.tar.gz -- this backup was made without Qdrant data"
    echo "    (old backups pre-dating the backup.sh Docker-volume fix, or Qdrant wasn't running at backup time)."
    echo "    You'll need README.md's Qdrant setup steps and a full re-ingest for vector data."
fi

echo ""
echo "=========================================="
echo "✓ Import complete"
echo "=========================================="
echo ""
echo "Next steps:"
echo "  1. Install Python dependencies if you haven't: pip install -r requirements.txt  (or: uv sync)"
echo "  2. Check servers.txt lists YOUR Ollama server URLs (see README.md's multi-server setup)"
echo "     -- this is machine-specific, don't assume the backup's servers.txt is correct here"
echo "  3. Verify Qdrant: curl http://localhost:6333/collections"
echo "  4. Resume/continue ingestion: python ingest.py"
echo "     (already-processed SKUs are skipped automatically -- see ingest.py's own checkpoint/resume logic)"
echo "  5. Try a query: python query.py \"your question\""
echo ""
