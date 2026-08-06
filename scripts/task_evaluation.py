"""Task-completion-rate evaluation: 50 short shopper tasks against a live
fkgrid API server (search / add-to-cart / compare / check-availability).

Each task is a short sequence of chat turns sent to one session. A task
passes if the final TurnResult (or, for multi-turn tasks, the turn that
matters) satisfies the task's own success check - e.g. "cart contains a
line item" or "comparison covers both skus" - not just "status == OK".

Runs against the real server over HTTP (same contract as cli.py), 4 tasks
concurrently at a time, and prints a final completion-rate summary.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from tqdm import tqdm

BASE_URL = os.environ.get("FKGRID_API_BASE_URL", "http://127.0.0.1:8000")
CONCURRENCY = 2


def _post(path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body or {}).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(body_text)
        except json.JSONDecodeError:
            detail = {"raw": body_text}
        return exc.code, {"status": "ERROR", "detail": detail}
    except urllib.error.URLError as exc:
        raise RuntimeError(f"could not reach the server at {BASE_URL}: {exc.reason}") from exc


@dataclass
class Task:
    task_id: str
    kind: str  # search | add_to_cart | compare | check_availability
    turns: list[str]
    # (turn_result_dict) -> bool. Checked on the result of the LAST turn.
    check: "callable" = field(repr=False)


def _search_ok(result: dict) -> bool:
    sr = result.get("search_result") or {}
    return result.get("status") == "OK" and len(sr.get("entries") or []) > 0


def _cart_has_item(result: dict) -> bool:
    cart = result.get("cart") or {}
    return result.get("status") == "OK" and int(cart.get("item_count") or 0) > 0


def _compare_ok(result: dict) -> bool:
    comparison = result.get("comparison") or {}
    rows = comparison.get("rows") or []
    if result.get("status") != "OK" or not rows:
        return False
    skus = {cell.get("sku_id") for row in rows for cell in row.get("cells", [])}
    return len(skus) >= 2


def _availability_ok(result: dict) -> bool:
    availability = result.get("availability") or {}
    return result.get("status") == "OK" and availability.get("found") is True


# --------------------------------------------------------------------------
# Task set: 50 tasks, roughly 20 search / 15 add-to-cart / 8 compare / 7
# availability. Search/compare/availability tasks are self-contained (one
# concrete query mentioning a real category from product_metadata);
# add-to-cart and compare tasks search first, then act on "the first one" /
# "the first two" so they don't depend on hardcoded sku_ids matching the DB.
# --------------------------------------------------------------------------

_SEARCH_QUERIES = [
    "show me cotton t-shirts for men",
    "I want a blue casual shirt",
    "looking for a cotton bra for women",
    "find slim fit jeans",
    "show me a gold necklace set",
    "I need a diamond ring",
    "gold bangle set please",
    "blue running shoes under 1500",
    "leather boots for men",
    "casual loafers",
    "party heels for women",
    "mobile phone back cover",
    "tablet book cover",
    "bike helmet",
    "decorative showpiece for home",
    "analog wall clock",
    "face cream for dry skin",
    "matte lipstick shade",
    "eyelet door curtain",
    "sofa cushion cover",
    "ceramic coffee mug",
    "wireless computer mouse",
    "wifi router",
    "digital wrist watch",
    "cartoon toy for kids",
]

_ADD_TO_CART_SEARCHES = [
    "search for cricket sports kit",
    "find a casual backpack",
    "show me black leather wallets",
    "I want a pencil box set",
    "gardening tool kit please",
    "baby room wall sticker",
    "black analog watch",
    "steel kitchen storage set",
    "cotton t-shirt for men",
    "slim fit jeans",
    "party heels for women",
    "face cream for dry skin",
    "ceramic coffee mug",
    "wifi router",
    "casual loafers",
]

_COMPARE_SEARCHES = [
    "search for running shoes",
    "show me wrist watches",
    "find backpacks",
    "cotton t-shirts for men",
    "gold necklace sets",
    "wireless computer mice",
    "sofa cushion covers",
    "analog wall clocks",
]

_AVAILABILITY_SEARCHES = [
    "search for cricket sports kit",
    "find a casual backpack",
    "wifi router",
    "digital wrist watch",
    "ceramic coffee mug",
    "gardening tool kit please",
    "matte lipstick shade",
]


def build_tasks() -> list[Task]:
    tasks: list[Task] = []

    for i, query in enumerate(_SEARCH_QUERIES, start=1):
        tasks.append(Task(f"search-{i:02d}", "search", [query], _search_ok))

    for i, query in enumerate(_ADD_TO_CART_SEARCHES, start=1):
        tasks.append(
            Task(
                f"add_to_cart-{i:02d}",
                "add_to_cart",
                [query, "add the first one to my cart"],
                _cart_has_item,
            )
        )

    for i, query in enumerate(_COMPARE_SEARCHES, start=1):
        tasks.append(
            Task(
                f"compare-{i:02d}",
                "compare",
                [query, "compare the first two"],
                _compare_ok,
            )
        )

    for i, query in enumerate(_AVAILABILITY_SEARCHES, start=1):
        tasks.append(
            Task(
                f"availability-{i:02d}",
                "check_availability",
                [query, "is the first one in stock?"],
                _availability_ok,
            )
        )

    return tasks


@dataclass
class TaskOutcome:
    task_id: str
    kind: str
    passed: bool
    error: str | None = None
    last_result: dict | None = None


def run_task(task: Task) -> TaskOutcome:
    try:
        status, session_resp = _post("/v1/sessions")
        if status != 200:
            return TaskOutcome(task.task_id, task.kind, False, error="could not create session")
        session_id = session_resp["session_id"]

        result: dict = {}
        for message in task.turns:
            status, result = _post(f"/v1/sessions/{session_id}/turns", {"message": message})
            if status != 200:
                return TaskOutcome(
                    task.task_id, task.kind, False,
                    error=f"HTTP {status}: {result.get('detail')}", last_result=result,
                )

        passed = bool(task.check(result))
        return TaskOutcome(task.task_id, task.kind, passed, last_result=result)
    except Exception as exc:  # noqa: BLE001 - report, don't crash the batch
        return TaskOutcome(task.task_id, task.kind, False, error=repr(exc))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--concurrency", type=int, default=CONCURRENCY)
    parser.add_argument("--verbose", action="store_true", help="print per-task pass/fail as it happens")
    args = parser.parse_args()

    tasks = build_tasks()
    print(f"Running {len(tasks)} tasks against {BASE_URL} ({args.concurrency} at a time)...\n")

    outcomes: list[TaskOutcome] = []
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {pool.submit(run_task, task): task for task in tasks}
        with tqdm(total=len(tasks), unit="task") as bar:
            for future in as_completed(futures):
                outcome = future.result()
                outcomes.append(outcome)
                marker = "PASS" if outcome.passed else "FAIL"
                line = f"[{marker}] {outcome.task_id}"
                if not outcome.passed and outcome.error:
                    line += f" - {outcome.error}"
                tqdm.write(line)
                if args.verbose and outcome.last_result:
                    tqdm.write(json.dumps(outcome.last_result, indent=2, default=str)[:1000])
                bar.update(1)
    elapsed = time.monotonic() - start

    outcomes.sort(key=lambda o: o.task_id)
    by_kind: dict[str, list[TaskOutcome]] = {}
    for outcome in outcomes:
        by_kind.setdefault(outcome.kind, []).append(outcome)

    total = len(outcomes)
    passed = sum(1 for o in outcomes if o.passed)

    print("\n" + "=" * 50)
    print("Task completion rate by category:")
    for kind, items in sorted(by_kind.items()):
        kind_passed = sum(1 for o in items if o.passed)
        print(f"  {kind:<20} {kind_passed}/{len(items)}")

    print("=" * 50)
    print(f"TOTAL: {passed}/{total} tasks passed ({elapsed:.1f}s)")
    print(f"COMPLETION RATE: {passed / total * 100:.1f}%")
    print("=" * 50)

    return 0


if __name__ == "__main__":
    sys.exit(main())
