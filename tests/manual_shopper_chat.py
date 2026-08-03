"""Interactive console runner for the shopper-facing fake catalog.

This is deliberately a manual test harness, not an API or frontend. It keeps
the database/retrieval/markdown owners behind the existing fake ports so a
developer can exercise the shopper turn contract with their own messages.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))
sys.path.insert(0, str(REPOSITORY_ROOT / "tests"))

from test_agentic_workflow import fixture, orchestrator  # noqa: E402

from fkgrid.agentic.contracts import Fact, Money, SearchEntry, TurnRequest  # noqa: E402
from fkgrid.agentic.gateway import Gemma4ModelAdapter  # noqa: E402
from fkgrid.agentic.orchestrator import OrchestratorConfig  # noqa: E402


def _money(value: Any) -> str:
    if isinstance(value, Money):
        return f"INR {value.amount_paise / 100:,.2f}"
    return str(value)


def _fact_text(fact: Fact) -> str:
    return f"{fact.label}={_money(fact.typed_value)} ({fact.status.value})"


def print_catalog(entries: list[SearchEntry]) -> None:
    print("\nTesting catalog: catalog-demo-v1")
    print("Only these three mock products are available to the fake adapters:")
    for entry in entries:
        facts = ", ".join(_fact_text(fact) for fact in entry.facts) or "no modeled facts"
        print(
            f"  {entry.display_position}. {entry.title} | "
            f"entry={entry.result_entry_id} | product={entry.binding.product_id} | {facts}"
        )
    print(
        "Identity references accepted by the fake resolver: first/second/third, "
        "entry_* or product_*."
    )


def print_help() -> None:
    print(
        """
Commands:
  /catalog  show the exact mock products and IDs
  /state    show current state/cart versions
  /help     show this help
  /quit     exit the manual runner

Example shopper messages:
  Find a shirt size m
  Show details for the first one
  Compare the first and second one
  Is the first one available?
  Add the first one to my cart
  Show my cart
  Find something cheaper
""".strip()
    )


def print_response(result: Any, markdown_handoff_count: int) -> None:
    print(f"\nstatus={result.status} http_status={result.http_status}")
    if result.response is None:
        print(f"No shopper response. Trace events: {len(result.trace.events)}")
        return

    response = result.response
    print(f"terminal_state={response.terminal_state.value}")
    print(f"action={response.action.value}")
    print(f"summary={response.summary}")
    if response.result_set_id:
        print(f"result_set_id={response.result_set_id}")
    if response.search_entries:
        print("results:")
        for entry in response.search_entries:
            print(
                f"  {entry.display_position}. {entry.title} | "
                f"entry={entry.result_entry_id} | product={entry.binding.product_id}"
            )
    if response.clarification:
        print(f"clarification={response.clarification.question}")
    if response.cart:
        print(
            f"cart_items={response.cart.item_count} "
            f"total_quantity={response.cart.total_quantity} "
            f"subtotal={_money(response.cart.subtotal)}"
        )
    if response.warnings:
        print("warnings=" + ", ".join(response.warnings))
    print(
        "markdown_handoff="
        + ("READY_FOR_MARKDOWN" if markdown_handoff_count else "NOT_CREATED")
        + " (this harness does not render markdown)"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        choices=("fake", "live"),
        default="fake",
        help="Use deterministic routing fake (default) or the configured DeepInfra adapter.",
    )
    parser.add_argument(
        "--intent-budget-ms",
        type=int,
        default=1800,
        help="Intent deadline; use 5000 only for a diagnostic Gemma 4 26B run.",
    )
    args = parser.parse_args()

    snapshot, entries = fixture()
    app, dependencies = orchestrator(snapshot, entries)
    if args.model == "live":
        os.environ.setdefault("FKGRID_MODEL_PROTOCOL", "deepinfra")
        os.environ.setdefault("FKGRID_MODEL_ALIAS", "google/gemma-4-26b-a4b-it")
        os.environ.setdefault(
            "FKGRID_MODEL_ENDPOINT",
            "https://api.deepinfra.com/v1/openai/chat/completions",
        )
        app.gateway = Gemma4ModelAdapter.from_environment()
        app.config = OrchestratorConfig(intent_budget_ms=args.intent_budget_ms)

    print_catalog(entries)
    print(
        f"\nMode: {args.model}; intent budget: {args.intent_budget_ms} ms. "
        "Type /help for examples."
    )
    if args.model == "live" and args.intent_budget_ms <= 1800:
        print("Note: hosted Gemma 4 26B may safely fall back when it exceeds the 1.8s budget.")

    turn_number = 0
    while True:
        try:
            message = input("\nshopper> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not message:
            continue
        command = message.casefold()
        if command in {"/quit", "/exit"}:
            return 0
        if command == "/catalog":
            print_catalog(entries)
            continue
        if command == "/help":
            print_help()
            continue
        if command == "/state":
            state = dependencies["state"].snapshot  # type: ignore[attr-defined]
            print(
                f"state_version={state.state_version} cart_version={state.cart_version} "
                f"active_result_set={state.acknowledged_result_set_id} "
                f"cart_items={state.cart.item_count}"
            )
            continue

        turn_number += 1
        state = dependencies["state"].snapshot  # type: ignore[attr-defined]
        result = app.handle(
            TurnRequest(
                session_id=state.session_id,
                client_turn_id=f"manual-turn-{turn_number}",
                idempotency_key=f"manual-key-{turn_number}",
                expected_state_version=state.state_version,
                expected_cart_version=state.cart_version,
                message=message,
            )
        )
        print_response(result, len(dependencies["markdown"].handoffs))  # type: ignore[attr-defined]


if __name__ == "__main__":
    raise SystemExit(main())
