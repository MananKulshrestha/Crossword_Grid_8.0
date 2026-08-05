#!/usr/bin/env bash
# Interactive chat CLI - talks to a running fkgrid API server over HTTP.
# Start the server first: ./run_server.sh (in another terminal), then run
# this. Every message shows up in the server's logs, and every response
# prints each orchestration stage (query_extractor, query_enhancer,
# reranker_search, catalog_*/cart_*, followups) with exact input/output.
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}"
export FKGRID_API_BASE_URL="${FKGRID_API_BASE_URL:-http://127.0.0.1:8000}"
exec .venv/bin/python3 -m fkgrid.cli
