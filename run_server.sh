#!/usr/bin/env bash
# Starts the fkgrid chat API. Swagger UI at http://127.0.0.1:8000/docs
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}"

# DeepInfra-hosted model - same provider used throughout this project.
export FKGRID_MODEL_PROTOCOL="${FKGRID_MODEL_PROTOCOL:-deepinfra}"
export FKGRID_MODEL_ENDPOINT="${FKGRID_MODEL_ENDPOINT:-https://api.deepinfra.com/v1/openai/chat/completions}"
export FKGRID_MODEL_ALIAS="${FKGRID_MODEL_ALIAS:-google/gemma-4-26b-a4b-it}"
export DEEPINFRA_API_KEY="${DEEPINFRA_API_KEY:-rl3DKKuo2Yxvr0Nr3HwOmibgagXRkTfO}"

exec .venv/bin/uvicorn fkgrid.api:app --host 127.0.0.1 --port 8000 --reload
