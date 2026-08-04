"""Milestone 10: proves DatabaseCartAdapter actually satisfies CartPort as
seen by the real TurnOrchestrator, not just in isolation. A new file, not a
modification of test_agentic_workflow.py -- that file is Manan's, and stays
untouched per the standing agreement in this project.

Uses FakeModelGateway/FakeCatalogPort/etc. for everything except cart,
matching test_agentic_workflow.py's own orchestrator() helper shape exactly
-- no LLM involved anywhere, consistent with this whole project's
Ollama/DeepInfra-free constraint. The one thing genuinely swapped is
cart=DatabaseCartAdapter(...) instead of cart=FakeCartPort(...).

Important, deliberately surfaced caveat: this test pins its
CompatibilityTuple's catalog_version to the REAL catalog ("flipkart_v1"),
not runtime.py's demo_compatibility()'s "catalog-fixture-v1". That's not
an oversight -- it's the actual integration gap. runtime.py's live search
path (self.tooling.shopper_catalog) still returns synthetic fixture
products under catalog_version="catalog-fixture-v1", completely separate
from the real Flipkart catalog DatabaseCartAdapter validates against under
"flipkart_v1". Enabling FKGRID_CART_MODE=database in the real app today
would mean every ADD_ITEM gets OFFER_UNAVAILABLE, because search-returned
bindings never match anything in the real offers table, until Track 2's
real retrieval subsystem is wired into self.tooling.shopper_catalog too.
This test sidesteps that by constructing its own FakeCatalogPort seeded
with entries that use real catalog_version/product/sku/offer IDs, proving
the orchestrator<->DatabaseCartAdapter contract in isolation from that
separate, already-known gap.
"""

from __future__ import annotations

import unittest
import uuid

import pymysql
import pymysql.cursors

from fkgrid.agentic.contracts import (
    Action,
    ActiveResultBinding,
    CompatibilityTuple,
    Fact,
    FactScope,
    Money,
    ProductBinding,
    QueryState,
    SearchEntry,
    TerminalState,
    TruthStatus,
    TurnRequest,
    TurnSnapshot,
    UiAction,
)
from fkgrid.agentic.fakes import (
    DeterministicEnhancer,
    FakeCatalogPort,
    FakeClock,
    FakeMarkdownPipeline,
    FakeReferenceResolver,
    FakeRecoveryPort,
    FakeResearchPort,
    FakeSuggestionPort,
    FakeTraceSink,
    InMemorySessionState,
    SequentialIds,
    empty_cart,
)
from fkgrid.agentic.gateway import FakeModelGateway
from fkgrid.agentic.orchestrator import TurnOrchestrator
from fkgrid.cart.adapter import DatabaseCartAdapter

DB_HOST = "127.0.0.1"
DB_PORT = 3307
DB_USER = "flipkart_user"
DB_PASSWORD = "flipkart_pass"
DB_NAME = "flipkart"


def _database_available() -> bool:
    try:
        connection = pymysql.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            connect_timeout=1,
        )
    except Exception:
        return False
    connection.close()
    return True


DATABASE_AVAILABLE = _database_available()


def _connect():
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD,
        database=DB_NAME, cursorclass=pymysql.cursors.DictCursor,
    )


def _real_offer() -> dict:
    conn = _connect()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT o.catalog_version AS catalog_version, p.product_id AS product_id, "
                "p.title AS title, s.sku_id AS sku_id, o.offer_id AS offer_id, "
                "o.price_paise AS price_paise FROM offers o "
                "JOIN skus s ON s.catalog_version = o.catalog_version AND s.sku_id = o.sku_id "
                "JOIN products p ON p.catalog_version = s.catalog_version AND p.product_id = s.product_id "
                "WHERE o.price_paise IS NOT NULL AND o.status = 'ACTIVE' LIMIT 1"
            )
            return cursor.fetchone()
    finally:
        conn.close()


def _compatibility() -> CompatibilityTuple:
    # Pinned to the real catalog_version -- see the module docstring.
    return CompatibilityTuple(
        contract_schema_version="agentic-contracts-v1",
        catalog_version="flipkart_v1",
        index_version="index-demo-v1",
        taxonomy_version="taxonomy-demo-v1",
        category_schema_version="schema-demo-v1",
        lexicon_version="lexicon-demo-v1",
        rank_policy_version="rank-v1",
        gate_policy_version="gate-v1",
        intent_prompt_version="3",
        intent_model_alias="gemma-4-26b-a4b-it",
        response_template_version="response-v1",
        commerce_policy_version="commerce-v1",
        research_policy_version="research-v1",
        suggestion_policy_version="suggestions-v1",
        memory_schema_version="memory-v1",
        query_enhancement_policy_version="enhancement-v1",
    )


@unittest.skipUnless(DATABASE_AVAILABLE, "requires the configured MySQL cart database")
class CartOrchestratorIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session_id = f"session_{uuid.uuid4().hex}"
        self.cart_id = f"cart_{self.session_id}"
        self.offer = _real_offer()
        self.compat = _compatibility()
        self.binding = ProductBinding(
            product_id=self.offer["product_id"], sku_id=self.offer["sku_id"],
            offer_id=self.offer["offer_id"], catalog_version=self.offer["catalog_version"],
        )
        self.entry = SearchEntry(
            result_entry_id="entry_1",
            display_position=1,
            binding=self.binding,
            title=self.offer["title"],
            facts=[
                Fact(
                    fact_id="fact_1", label="price",
                    typed_value=Money(amount_paise=self.offer["price_paise"]),
                    status=TruthStatus.VERIFIED, scope=FactScope.CATALOG,
                    provenance_type="MOCK_CATALOG",
                )
            ],
        )

    def tearDown(self) -> None:
        conn = _connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM cart_operation_events WHERE cart_id = %s", (self.cart_id,))
                cursor.execute("DELETE FROM cart_items WHERE cart_id = %s", (self.cart_id,))
                cursor.execute("DELETE FROM carts WHERE cart_id = %s", (self.cart_id,))
            conn.commit()
        finally:
            conn.close()

    def _build_orchestrator(self) -> tuple[TurnOrchestrator, InMemorySessionState]:
        state_ = QueryState(
            state_version=0,
            catalog_version=self.compat.catalog_version,
            index_version=self.compat.index_version,
            lexicon_version=self.compat.lexicon_version,
            compact_goal_summary="integration test",
        )
        snapshot = TurnSnapshot(
            session_id=self.session_id,
            state=state_,
            state_version=0,
            cart_version=0,
            cart=empty_cart(self.session_id, self.compat),
            acknowledged_result_set_id="result_set_1",
            acknowledged_entries=[
                ActiveResultBinding(
                    result_entry_id=self.entry.result_entry_id,
                    display_position=self.entry.display_position,
                    binding=self.entry.binding,
                )
            ],
            compatibility_tuple=self.compat,
        )
        state = InMemorySessionState(snapshot)
        clock = FakeClock()
        ids = SequentialIds()
        orchestrator = TurnOrchestrator(
            state=state,
            enhancer=DeterministicEnhancer(clock, ids),
            gateway=FakeModelGateway(),
            catalog=FakeCatalogPort([self.entry]),
            recovery=FakeRecoveryPort(),
            references=FakeReferenceResolver(),
            cart=DatabaseCartAdapter(session_snapshot_provider=lambda: state.snapshot),
            research=FakeResearchPort(clock),
            suggestions=FakeSuggestionPort(),
            markdown=FakeMarkdownPipeline(),
            clock=clock,
            ids=ids,
            trace_sink=FakeTraceSink(),
        )
        return orchestrator, state

    def test_show_cart_through_the_real_orchestrator_returns_empty_cart(self) -> None:
        app, _ = self._build_orchestrator()

        result = app.handle(
            TurnRequest(
                session_id=self.session_id,
                client_turn_id="turn_show",
                idempotency_key="key_show",
                expected_state_version=0,
                expected_cart_version=0,
                ui_action=UiAction(action=Action.SHOW_CART),
            )
        )

        self.assertEqual(result.status, "COMPLETED")
        assert result.response is not None
        self.assertEqual(result.response.terminal_state, TerminalState.CART_SHOWN)
        self.assertEqual(result.response.cart.items, [])

    def test_add_item_through_the_real_orchestrator_persists_to_real_db(self) -> None:
        app, state = self._build_orchestrator()

        result = app.handle(
            TurnRequest(
                session_id=self.session_id,
                client_turn_id="turn_add",
                idempotency_key="key_add",
                expected_state_version=0,
                expected_cart_version=0,
                ui_action=UiAction(
                    action=Action.UPDATE_CART,
                    payload={
                        "operations": [
                            {
                                "type": "ADD_ITEM",
                                "operation_id": "op-add",
                                "result_entry_id": self.entry.result_entry_id,
                                "quantity": 2,
                            }
                        ]
                    },
                ),
            )
        )

        self.assertEqual(result.status, "COMPLETED")
        assert result.response is not None
        self.assertEqual(result.response.terminal_state, TerminalState.CART_UPDATED)
        self.assertEqual(len(result.response.cart.items), 1)
        item = result.response.cart.items[0]
        self.assertEqual(item.binding.offer_id, self.offer["offer_id"])
        self.assertEqual(item.quantity, 2)
        self.assertEqual(item.unit_price.amount_paise, self.offer["price_paise"])

        # The orchestrator's own commit path wrote this back into session
        # state -- confirms DatabaseCartAdapter's read-through contract
        # (it never writes session state itself) actually round-trips
        # correctly through the real commit flow, not just in my own
        # adapter-level tests.
        self.assertEqual(state.snapshot.cart_version, 1)
        self.assertEqual(state.snapshot.cart.item_count, 1)

        # And it's real, persisted DB state, not just in-memory.
        conn = _connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) c FROM cart_items WHERE cart_id = %s", (self.cart_id,))
                self.assertEqual(cursor.fetchone()["c"], 1)
        finally:
            conn.close()

    def test_show_cart_after_add_reflects_the_persisted_line(self) -> None:
        app, _ = self._build_orchestrator()
        add_result = app.handle(
            TurnRequest(
                session_id=self.session_id,
                client_turn_id="turn_add",
                idempotency_key="key_add",
                expected_state_version=0,
                expected_cart_version=0,
                ui_action=UiAction(
                    action=Action.UPDATE_CART,
                    payload={
                        "operations": [
                            {
                                "type": "ADD_ITEM",
                                "operation_id": "op-add",
                                "result_entry_id": self.entry.result_entry_id,
                                "quantity": 1,
                            }
                        ]
                    },
                ),
            )
        )
        self.assertEqual(add_result.status, "COMPLETED")

        show_result = app.handle(
            TurnRequest(
                session_id=self.session_id,
                client_turn_id="turn_show",
                idempotency_key="key_show",
                expected_state_version=1,
                expected_cart_version=1,
                ui_action=UiAction(action=Action.SHOW_CART),
            )
        )

        self.assertEqual(show_result.status, "COMPLETED")
        assert show_result.response is not None
        self.assertEqual(len(show_result.response.cart.items), 1)


if __name__ == "__main__":
    unittest.main()
