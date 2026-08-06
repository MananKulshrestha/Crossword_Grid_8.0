#!/usr/bin/env bash
# Runs the reranker nDCG@{1,3,5} + hallucination-rate evaluation.
# See scripts/reranker_eval/evaluate.py for details.
set -euo pipefail
cd "$(dirname "$0")"

exec .venv/bin/python3 scripts/reranker_eval/evaluate.py "$@"
