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

# Read defaults from config.py itself instead of duplicating them here --
# config.py is the single source of truth for model/concurrency values,
# this way run.sh can never drift out of sync with it.
eval "$(python3 -c '
import config
print(f"CFG_OLLAMA_HOST={config.OLLAMA_HOST}")
print(f"CFG_LLM_MODEL={config.LLM_MODEL}")
print(f"CFG_EMBED_MODEL={config.EMBED_MODEL}")
print(f"CFG_LLM_MAX_ASYNC={config.LLM_MAX_ASYNC}")
print(f"CFG_EMBED_BACKEND={config.EMBED_BACKEND}")
')"

OLLAMA_HOST="${OLLAMA_HOST:-$CFG_OLLAMA_HOST}"
LLM_MODEL="${LIGHTRAG_LLM_MODEL:-$CFG_LLM_MODEL}"
EMBED_MODEL="${LIGHTRAG_EMBED_MODEL:-$CFG_EMBED_MODEL}"
EMBED_BACKEND="${LIGHTRAG_EMBED_BACKEND:-$CFG_EMBED_BACKEND}"

step() { echo -e "\n=== $1 ===\n"; }

step "Installing Python dependencies"
pip install -q -r requirements.txt

step "Checking Ollama server at ${OLLAMA_HOST}"
echo "Note: make sure the server was started with OLLAMA_NUM_PARALLEL=${CFG_LLM_MAX_ASYNC} (or higher)"
echo "so it can actually serve LightRAG's ${CFG_LLM_MAX_ASYNC} concurrent requests in parallel,"
echo "e.g. on the remote box: OLLAMA_NUM_PARALLEL=${CFG_LLM_MAX_ASYNC} ollama serve &"
echo "and locally: ssh -L 11435:localhost:11434 -J gr@ada.iiit.ac.in gr@gnode071"
if ! curl -sf "${OLLAMA_HOST}/api/tags" > /dev/null; then
  echo "Ollama server not reachable at ${OLLAMA_HOST}."
  echo "Make sure the SSH tunnel is up and Ollama is running on the remote node."
  exit 1
fi

step "Checking required models are pulled"
MODELS_TO_CHECK=("$LLM_MODEL")
if [ "$EMBED_BACKEND" = "ollama" ]; then
  MODELS_TO_CHECK+=("$EMBED_MODEL")
else
  echo "EMBED_BACKEND=${EMBED_BACKEND} -- embedding runs locally, nothing to pull for it."
fi
for model in "${MODELS_TO_CHECK[@]}"; do
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
