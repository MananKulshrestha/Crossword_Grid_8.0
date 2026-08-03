#!/usr/bin/env bash
# Wipes RA/lightrag_storage/ so the next ingest.py run starts from a clean
# slate -- useful after a broken/partial run (e.g. Ollama connection errors
# mid-ingestion) where you don't want stale PENDING/PROCESSING/FAILED
# doc_status entries or partial graph/vector data hanging around.
#
# Usage:
#   ./reset.sh          # ask for confirmation, then delete
#   ./reset.sh -y        # skip confirmation

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

STORAGE_DIR="lightrag_storage"

if [ ! -d "$STORAGE_DIR" ]; then
  echo "Nothing to reset -- $STORAGE_DIR does not exist."
  exit 0
fi

if [ "${1:-}" != "-y" ]; then
  read -r -p "This will permanently delete ${STORAGE_DIR}/ (graph, vector DB, doc status). Continue? [y/N] " reply
  case "$reply" in
    [yY]|[yY][eE][sS]) ;;
    *) echo "Aborted."; exit 1 ;;
  esac
fi

rm -rf "$STORAGE_DIR"
echo "Removed ${STORAGE_DIR}/. Next ingest.py run will start from a clean index."
