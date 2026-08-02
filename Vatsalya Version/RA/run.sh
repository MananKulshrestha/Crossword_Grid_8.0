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
LLM_MODEL="${LIGHTRAG_LLM_MODEL:-gemma4:27b}"
EMBED_MODEL="${LIGHTRAG_EMBED_MODEL:-nomic-embed-text}"

step() { echo -e "\n=== $1 ===\n"; }

step "Installing Python dependencies"
pip install -q -r requirements.txt

step "Checking Ollama server at ${OLLAMA_HOST}"
echo "Note: make sure the server was started with OLLAMA_NUM_PARALLEL=4 (or higher)"
echo "so it can actually serve LightRAG's 4 concurrent requests in parallel,"
echo "e.g.: OLLAMA_NUM_PARALLEL=4 OLLAMA_HOST=0.0.0.0:11345 ollama serve &"
if ! curl -sf "${OLLAMA_HOST}/api/tags" > /dev/null; then
  echo "Ollama server not reachable at ${OLLAMA_HOST}."
  echo "Start it first, e.g.: OLLAMA_HOST=0.0.0.0:11345 ollama serve &"
  exit 1
fi

step "Checking required models are pulled"
for model in "$LLM_MODEL" "$EMBED_MODEL"; do
  if ! curl -sf "${OLLAMA_HOST}/api/tags" | grep -q "\"${model}\""; then
    echo "Pulling missing model: ${model}"
    OLLAMA_HOST="${OLLAMA_HOST#http://}" ollama pull "${model}"
  fi
done

ACTION="${1:-all}"

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
