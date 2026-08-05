"""Interactive chat CLI - talks to a running fkgrid API server over HTTP.

Start the server first (./run_server.sh), then run this in another
terminal (./run_cli.sh). Every request you send here shows up in the
server's logs, and every turn's response is printed stage-by-stage
(query_extractor -> query_enhancer -> reranker_search -> catalog_*/cart_*
-> followups) with exact input/output.

The reranker call alone can take up to 60s and the LLM calls add more on
top, so a turn can take a while - this prints progress as it waits rather
than going silent.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

BASE_URL = os.environ.get("FKGRID_API_BASE_URL", "http://127.0.0.1:8000")


def _post(path: str, body: dict | None = None) -> dict:
    data = json.dumps(body or {}).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(body_text)
        except json.JSONDecodeError:
            detail = body_text
        return {"status": "ERROR", "message": "HTTP error from server", "error_code": str(exc.code),
                "trace": [], "_detail": detail}
    except urllib.error.URLError as exc:
        print(f"\ncould not reach the server at {BASE_URL}: {exc.reason}")
        print("start it first with ./run_server.sh")
        sys.exit(1)


def _dump(value: dict) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def _print_step(index: int, step: dict) -> None:
    marker = "OK" if step.get("ok", True) else "FAILED"
    print(f"\n--- [{index}] {step['stage']} ({marker}) ---")
    print("input:")
    print(_dump(step.get("input", {})))
    print("output:")
    print(_dump(step.get("output", {})))


class _Waiter:
    """Prints a heartbeat while a slow request is in flight, so the CLI
    never looks frozen even when the reranker/LLM calls take a while."""

    def __init__(self, label: str) -> None:
        self._label = label
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        elapsed = 0
        while not self._stop.wait(5):
            elapsed += 5
            print(f"  ... {self._label} ({elapsed}s elapsed, reranker can take up to 60s)")

    def __enter__(self) -> "_Waiter":
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._stop.set()
        self._thread.join()


def run() -> None:
    print(f"connecting to {BASE_URL} ...")
    session = _post("/v1/sessions")
    if "session_id" not in session:
        print("failed to create a session - is the server running? (./run_server.sh)")
        sys.exit(1)
    session_id = session["session_id"]
    print(f"session: {session_id}")
    print("type a message, or 'exit'\n")

    while True:
        try:
            message = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not message:
            continue
        if message.lower() in {"exit", "quit"}:
            break

        started = time.monotonic()
        with _Waiter("waiting on the server"):
            result = _post(f"/v1/sessions/{session_id}/turns", {"message": message})
        elapsed = time.monotonic() - started

        for index, step in enumerate(result.get("trace", []), start=1):
            _print_step(index, step)

        print(f"\n=== result: {result.get('status')} ({elapsed:.1f}s) ===")
        print(result.get("message"))
        if result.get("status") == "ERROR":
            print(f"error_code: {result.get('error_code')}")
            if "_detail" in result:
                print(f"detail: {result['_detail']}")
        for suggestion in result.get("followups", []):
            print(f"  - {suggestion['label']} [{suggestion['action']}]")
        print()


if __name__ == "__main__":
    sys.exit(run() or 0)
