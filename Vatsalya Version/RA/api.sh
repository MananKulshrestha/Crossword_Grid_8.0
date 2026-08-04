#!/usr/bin/env bash
# Start the RA search_catalog HTTP API (api_server.py) on port 8002.
# Mirrors webui.sh's launcher pattern (port-conflict check, venv detection,
# background process + signal passthrough) minus the browser auto-open --
# this is an API, not a page to view.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

PORT="${API_PORT:-8002}"

echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}🚀 RA search_catalog API Launcher${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""

if lsof -Pi ":${PORT}" -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo -e "${YELLOW}⚠️  Port ${PORT} is already in use${NC}"
    echo "Killing existing process..."
    lsof -ti ":${PORT}" | xargs kill -9 2>/dev/null || true
    sleep 2
fi

if [ -z "$VIRTUAL_ENV" ] && ! command -v uv &> /dev/null; then
    echo -e "${YELLOW}⚠️  No virtual environment detected. Installing dependencies...${NC}"
    python3 -m pip install -q -r requirements.txt
fi

echo -e "${GREEN}✓ Starting API server (this includes model warm-up -- can take a minute or two on first run)...${NC}"
echo ""

if command -v uv &> /dev/null; then
    uv run python api_server.py &
else
    python3 api_server.py &
fi

SERVER_PID=$!
echo -e "${GREEN}✓ Server process ID: $SERVER_PID${NC}"

trap "kill $SERVER_PID 2>/dev/null; exit 0" SIGINT SIGTERM

wait $SERVER_PID
