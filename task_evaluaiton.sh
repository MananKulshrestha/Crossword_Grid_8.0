#!/usr/bin/env bash
# Runs the task-completion-rate evaluation (search / add-to-cart / compare /
# check-availability) against a live fkgrid API server.
# See scripts/task_evaluation.py for details.
set -euo pipefail
cd "$(dirname "$0")"

BASE_URL="${FKGRID_API_BASE_URL:-http://127.0.0.1:8000}"

STARTED_SERVER=0
if ! curl -s -o /dev/null -f "$BASE_URL/healthz"; then
    echo "Server not running at $BASE_URL - starting it..."
    ./run_server.sh > /tmp/fkgrid_task_eval_server.log 2>&1 &
    SERVER_PID=$!
    STARTED_SERVER=1

    for _ in $(seq 1 60); do
        if curl -s -o /dev/null -f "$BASE_URL/healthz"; then
            break
        fi
        sleep 1
    done

    if ! curl -s -o /dev/null -f "$BASE_URL/healthz"; then
        echo "Server failed to start - see /tmp/fkgrid_task_eval_server.log"
        kill "$SERVER_PID" 2>/dev/null || true
        exit 1
    fi
fi

cleanup() {
    if [ "$STARTED_SERVER" -eq 1 ]; then
        kill "$SERVER_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

.venv/bin/python3 scripts/task_evaluation.py "$@"
