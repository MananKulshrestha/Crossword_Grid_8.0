#!/usr/bin/env bash
# Start LightRAG Web UI and open in browser

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}🚀 LightRAG Web UI Launcher${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""

# Check if port 8000 is in use
if lsof -Pi :8000 -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo -e "${YELLOW}⚠️  Port 8000 is already in use${NC}"
    echo "Killing existing process..."
    lsof -ti :8000 | xargs kill -9 2>/dev/null || true
    sleep 2
fi

# Check if running in virtual environment
if [ -z "$VIRTUAL_ENV" ] && ! command -v uv &> /dev/null; then
    echo -e "${YELLOW}⚠️  No virtual environment detected. Installing dependencies...${NC}"
    python3 -m pip install -q -r requirements.txt
fi

echo -e "${GREEN}✓ Starting Flask server...${NC}"
echo ""

# Start the server in background
if command -v uv &> /dev/null; then
    uv run python web_ui.py &
else
    python3 web_ui.py &
fi

SERVER_PID=$!
echo -e "${GREEN}✓ Server process ID: $SERVER_PID${NC}"

# Wait for server to start
echo -e "${YELLOW}⏳ Waiting for server to start...${NC}"
sleep 3

# Check if server is running
if ! kill -0 $SERVER_PID 2>/dev/null; then
    echo -e "${RED}✗ Failed to start server${NC}"
    exit 1
fi

# Get the appropriate browser command
if command -v open &> /dev/null; then
    # macOS
    BROWSER_CMD="open"
elif command -v xdg-open &> /dev/null; then
    # Linux
    BROWSER_CMD="xdg-open"
elif command -v start &> /dev/null; then
    # Windows
    BROWSER_CMD="start"
else
    BROWSER_CMD=""
fi

echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}🌐 Web UI is running!${NC}"
echo ""
echo -e "${GREEN}📍 Open your browser to:${NC}"
echo -e "   ${BLUE}http://localhost:8000${NC}"
echo ""
echo -e "${GREEN}Features:${NC}"
echo "   • Query product listings with natural language"
echo "   • Mixed mode search (Qdrant + Knowledge Graph)"
echo "   • Visualize the knowledge graph"
echo "   • Search nodes in the sidebar"
echo ""
echo -e "${YELLOW}To stop the server, press Ctrl+C${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""

# Open browser if possible
if [ -n "$BROWSER_CMD" ]; then
    sleep 1
    $BROWSER_CMD "http://localhost:8000" 2>/dev/null || true
fi

# Keep script running and pass through signals to server
trap "kill $SERVER_PID 2>/dev/null; exit 0" SIGINT SIGTERM

wait $SERVER_PID
