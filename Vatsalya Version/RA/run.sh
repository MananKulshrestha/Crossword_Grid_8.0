#!/usr/bin/env bash
# Runs the full RA pipeline: install deps, confirm Ollama is reachable,
# pull models if missing, ingest the test batch, then run a sample query.
#
# Usage:
#   ./run.sh                 # ingest + sample query
#   ./run.sh ingest          # ingest only
#   ./run.sh query "your question"

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11345}"
LLM_MODEL="${LIGHTRAG_LLM_MODEL:-gemma4:e4b}"
EMBED_MODEL="${LIGHTRAG_EMBED_MODEL:-nomic-embed-text}"
# Must match config.py's EMBED_BACKEND default -- "local" runs embedding via
# sentence-transformers on this machine (local_embed.py) and needs no Ollama
# embedding server at all, so its reachability/model-pull checks below are
# skipped in that mode.
EMBED_BACKEND="${LIGHTRAG_EMBED_BACKEND:-local}"

step() { echo -e "\n=== $1 ===\n"; }

#step "Installing Python dependencies"
#pip3 install -q -r requirements.txt

ACTION="${1:-all}"

# ingest.py ALWAYS uses the Ollama extraction cluster (servers.txt), regardless
# of INFERENCE_BACKEND/EMBED_BACKEND -- see config.py's own comments. query.py
# only touches Ollama at all when QUERY_EMBED_BACKEND or INFERENCE_BACKEND is
# explicitly set to "ollama" (defaults are "local" embedding + "deepinfra"
# inference, needing neither). So these checks only make sense for actions
# that actually ingest -- gating them on ACTION="query" would otherwise block
# a perfectly runnable query against an already-built index just because no
# Ollama server happens to be up.
if [ "$ACTION" != "query" ]; then
  step "Checking extraction servers (servers.txt)"
  if [ ! -f servers.txt ]; then
    echo "servers.txt not found -- ingest.py's multi-server load balancer"
    echo "requires it. Create it with one Ollama server URL per line first."
    exit 1
  fi
  EXTRACTION_SERVERS=()
  while IFS= read -r line; do
    EXTRACTION_SERVERS+=("$line")
  done < <(grep -vE '^\s*(#|$)' servers.txt)
  if [ "${#EXTRACTION_SERVERS[@]}" -eq 0 ]; then
    echo "servers.txt exists but lists no server URLs (only comments/blank lines)."
    exit 1
  fi
  echo "Note: each server should be started with OLLAMA_NUM_PARALLEL=4 (or higher)"
  echo "so it can actually serve concurrent requests in parallel, e.g.:"
  echo "  OLLAMA_NUM_PARALLEL=4 OLLAMA_HOST=0.0.0.0:11345 ollama serve &"
  for server in "${EXTRACTION_SERVERS[@]}"; do
    if ! curl -sf "${server}/api/tags" > /dev/null; then
      echo "Extraction server not reachable at ${server} (listed in servers.txt)."
      echo "Start it first, e.g.: OLLAMA_HOST=0.0.0.0:<port> ollama serve &"
      exit 1
    fi
    echo "  OK: ${server}"
  done

  if [ "$EMBED_BACKEND" = "ollama" ]; then
    step "Checking embedding server at ${OLLAMA_HOST}"
    if ! curl -sf "${OLLAMA_HOST}/api/tags" > /dev/null; then
      echo "Embedding server not reachable at ${OLLAMA_HOST}."
      echo "Start it first, e.g.: OLLAMA_HOST=0.0.0.0:11345 ollama serve &"
      exit 1
    fi
  else
    step "Embedding backend is 'local' (sentence-transformers) -- no Ollama embedding server needed"
  fi

  step "Checking required models are pulled"
  for model in "$LLM_MODEL"; do
    for server in "${EXTRACTION_SERVERS[@]}"; do
      if ! curl -sf "${server}/api/tags" | grep -q "\"${model}\""; then
        echo "Pulling missing model on ${server}: ${model}"
        OLLAMA_HOST="${server#http://}" ollama pull "${model}"
      fi
    done
  done
  if [ "$EMBED_BACKEND" = "ollama" ] && ! curl -sf "${OLLAMA_HOST}/api/tags" | grep -q "\"${EMBED_MODEL}\""; then
    echo "Pulling missing model: ${EMBED_MODEL}"
    OLLAMA_HOST="${OLLAMA_HOST#http://}" ollama pull "${EMBED_MODEL}"
  fi
fi

case "$ACTION" in
  ingest)
    step "Ingesting documents into LightRAG"
    python ingest.py
    ;;
  query)
    shift
    step "Querying LightRAG (mix mode)"
    python query.py "$@"
    ;;
  all|"")
    step "Ingesting documents into LightRAG"
    python ingest.py
    step "Running sample query"
    python query.py "What kinds of footwear are in this catalog?"
    ;;
  *)
    echo "Unknown action: $ACTION"
    echo "Usage: ./run.sh [ingest|query \"question\"]"
    exit 1
    ;;
esac
