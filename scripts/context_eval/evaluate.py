"""Evaluates the query extractor + enhancer's hard/soft context tracking
(SIMMC-2.0-style: hard = slot-filled constraints, soft = free-text intent)
across the multi-turn conversations in dataset.json.

Pipeline exercised per turn (real, live):
    fkgrid.llm.extract_query          -> LLM call, one per turn
    fkgrid.enhancer.build_reranker_request -> deterministic hard/soft build

The reranker itself (catalog.search / the RA proxy) is deliberately NEVER
called - this evaluates context tracking only, not search quality. Session
carry-forward (chat_history, last_reranker_request) is maintained by hand via
fkgrid.memory exactly as orchestrator.handle_turn would, minus the actual
search/cart/catalog side effects.

Scoring per turn (0-100%):
    hard_score = fraction of (expected UNION actual) hard_constraint keys
                 whose value matches exactly (an extra/missing/wrong key
                 each cost one point out of the union size)
    soft_score = token-level Jaccard overlap between expected and actual
                 soft_query_text (lowercased, whitespace-split)
    turn_score = round(100 * (0.5 * hard_score + 0.5 * soft_score))

Usage:
    python3 evaluate.py [--dataset dataset.json] [--limit N]
Writes a timestamped .log (human-readable, turn-by-turn) and .json (full
machine-readable results) under results/.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

_ENV_PATH = os.path.join(os.path.dirname(__file__), "..", "..", ".env")


def _load_dotenv(path: str) -> None:
    """Minimal .env loader (no python-dotenv dependency) - only fills vars
    not already present in the environment."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


_load_dotenv(_ENV_PATH)

from fkgrid import enhancer, memory  # noqa: E402
from fkgrid.contracts import Role  # noqa: E402
from fkgrid.llm import LLMError, extract_query  # noqa: E402


def _tokens(text: str) -> set[str]:
    return set(text.lower().split())


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _hard_score(expected: dict, actual: dict) -> float:
    keys = set(expected) | set(actual)
    if not keys:
        return 1.0
    matches = sum(1 for k in keys if expected.get(k) == actual.get(k))
    return matches / len(keys)


def run_turn(session_id: str, user_message: str) -> dict:
    session = memory.get_session(session_id)
    if session is None:
        session = memory.create_session(session_id)

    memory.append_turn(session_id, Role.USER, user_message)

    try:
        extraction = extract_query(user_message, session.chat_history)
    except LLMError as exc:
        return {"error": str(exc)}

    reranker_request = enhancer.build_reranker_request(user_message, extraction, session)

    # Carry forward for the next REFINE turn, same as orchestrator.handle_turn
    # does after a real search - but skipping the actual reranker/catalog call.
    memory.set_last_results(session_id, [], reranker_request)
    memory.append_turn(session_id, Role.ASSISTANT, f"[stub] {len(reranker_request.hard_constraints)} constraint(s)")

    return {
        "action": extraction.action.value,
        "hard": reranker_request.hard_constraints,
        "soft": reranker_request.soft_query_text,
    }


def evaluate_conversation(conv: dict) -> dict:
    session_id = f"ctxeval-{uuid.uuid4().hex[:12]}"
    memory.create_session(session_id)

    turn_results = []
    for turn in conv["turns"]:
        actual = run_turn(session_id, turn["user"])
        expected = turn["expected"]

        if "error" in actual:
            turn_results.append({
                "user": turn["user"],
                "expected": expected,
                "actual": actual,
                "hard_score": 0.0,
                "soft_score": 0.0,
                "turn_score": 0,
                "error": actual["error"],
            })
            continue

        hard_score = _hard_score(expected["hard"], actual["hard"])
        soft_score = _jaccard(_tokens(expected["soft"]), _tokens(actual["soft"]))
        turn_score = round(100 * (0.5 * hard_score + 0.5 * soft_score))

        turn_results.append({
            "user": turn["user"],
            "expected": expected,
            "actual": {"action": actual["action"], "hard": actual["hard"], "soft": actual["soft"]},
            "hard_score": round(hard_score, 3),
            "soft_score": round(soft_score, 3),
            "turn_score": turn_score,
        })

    conv_score = round(sum(t["turn_score"] for t in turn_results) / len(turn_results)) if turn_results else 0
    return {
        "conversation_id": conv["conversation_id"],
        "pattern": conv.get("pattern"),
        "turns": turn_results,
        "conversation_score": conv_score,
    }


def format_turn_log(idx: int, t: dict) -> str:
    lines = [f"  Turn {idx + 1}: \"{t['user']}\""]
    if "error" in t:
        lines.append(f"    ERROR: {t['error']}  -> 0%")
        return "\n".join(lines)
    lines.append(f"    expected: hard={t['expected']['hard']}  soft={t['expected']['soft']!r}")
    lines.append(f"    actual:   hard={t['actual']['hard']}  soft={t['actual']['soft']!r}  (action={t['actual']['action']})")
    verdict = "CORRECT" if t["turn_score"] == 100 else ("PARTIAL" if t["turn_score"] > 0 else "WRONG")
    lines.append(
        f"    hard_score={t['hard_score']:.2f}  soft_score={t['soft_score']:.2f}  "
        f"-> {verdict}, turn score = {t['turn_score']}%"
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=os.path.join(os.path.dirname(__file__), "dataset.json"))
    parser.add_argument("--limit", type=int, default=None, help="evaluate only the first N conversations")
    args = parser.parse_args()

    with open(args.dataset) as f:
        conversations = json.load(f)
    if args.limit:
        conversations = conversations[: args.limit]

    ts = int(time.time())
    log_path = os.path.join(os.path.dirname(__file__), "logs", f"context_eval_{ts}.log")
    json_path = os.path.join(os.path.dirname(__file__), "results", f"context_eval_{ts}.json")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    os.makedirs(os.path.dirname(json_path), exist_ok=True)

    results = []
    log_lines = [
        "=" * 78,
        "Context (hard/soft) tracking evaluation",
        f"Run at: {datetime.now(timezone.utc).isoformat()}",
        f"Conversations: {len(conversations)}",
        "=" * 78,
        "",
    ]

    for i, conv in enumerate(conversations, 1):
        result = evaluate_conversation(conv)
        results.append(result)
        log_lines.append(f"[{i}/{len(conversations)}] {result['conversation_id']} ({result['pattern']})")
        for idx, t in enumerate(result["turns"]):
            log_lines.append(format_turn_log(idx, t))
        log_lines.append(f"  -> conversation score: {result['conversation_score']}%")
        log_lines.append("")
        print(f"[{i}/{len(conversations)}] {result['conversation_id']}: {result['conversation_score']}%")

    all_turn_scores = [t["turn_score"] for r in results for t in r["turns"]]
    all_conv_scores = [r["conversation_score"] for r in results]
    overall_turn_avg = round(sum(all_turn_scores) / len(all_turn_scores), 1) if all_turn_scores else 0
    overall_conv_avg = round(sum(all_conv_scores) / len(all_conv_scores), 1) if all_conv_scores else 0
    exact_turns = sum(1 for s in all_turn_scores if s == 100)

    summary = (
        f"OVERALL: {len(results)} conversations, {len(all_turn_scores)} turns\n"
        f"  Mean turn score:         {overall_turn_avg}%\n"
        f"  Mean conversation score: {overall_conv_avg}%\n"
        f"  Turns scored 100%:       {exact_turns}/{len(all_turn_scores)} "
        f"({round(100 * exact_turns / len(all_turn_scores), 1) if all_turn_scores else 0}%)\n"
    )
    log_lines.append("=" * 78)
    log_lines.append(summary)
    log_lines.append("=" * 78)

    with open(log_path, "w") as f:
        f.write("\n".join(log_lines))
    with open(json_path, "w") as f:
        json.dump(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "overall": {
                    "num_conversations": len(results),
                    "num_turns": len(all_turn_scores),
                    "mean_turn_score": overall_turn_avg,
                    "mean_conversation_score": overall_conv_avg,
                    "turns_scored_100": exact_turns,
                },
                "conversations": results,
            },
            f,
            indent=2,
        )

    print()
    print(summary)
    print(f"Log written to:  {log_path}")
    print(f"JSON written to: {json_path}")


if __name__ == "__main__":
    main()
