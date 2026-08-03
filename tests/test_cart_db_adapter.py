"""Tests for DatabaseCartAdapter against the real Docker MySQL container
(same instance RA/db/schema_tier1.sql and schema_cart.sql already ran
against) -- no mocking, matching this project's own precedent for DB-backed
branches (see RA/tests/test_sql_filter.py's docstring). Requires
flipkart-mysql running and both schema_tier1.sql and schema_cart.sql already
applied.

show_cart tests (Milestone 2) insert fixture rows directly via SQL,
committed explicitly since the adapter opens its own connection per call.
update_cart tests (Milestone 3) go through the adapter itself end to end --
that's the actual thing being tested -- and only use direct SQL for reading
back what the adapter did, or for pre-seeding a conflict scenario.
"""

from __future__ import annotations

import json
import os
import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pymysql
import pymysql.cursors

from fkgrid.agentic.contracts import (
    AddItemOperation,
    ClearCartOperation,
    CompatibilityTuple,
    DecrementItemOperation,
    IncrementItemOperation,
    ProductBinding,
    QueryState,
    RemoveItemOperation,
    SetQuantityOperation,
    TurnSnapshot,
    UpdateCartRequest,
    UpdateCartResult,
)
from fkgrid.agentic.fakes import empty_cart
from fkgrid.cart.adapter import DatabaseCartAdapter

DB_HOST = os.environ.get("FLIPKART_DB_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("FLIPKART_DB_PORT", "3307"))
DB_USER = os.environ.get("FLIPKART_DB_USER", "flipkart_user")
DB_PASSWORD = os.environ.get("FLIPKART_DB_PASSWORD", "flipkart_pass")
DB_NAME = os.environ.get("FLIPKART_DB_NAME", "flipkart")


def _connect():
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD,
        database=DB_NAME, cursorclass=pymysql.cursors.DictCursor,
    )


def _compatibility() -> CompatibilityTuple:
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


def _real_offers(count: int) -> list[dict]:
    conn = _connect()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT o.catalog_version AS catalog_version, p.product_id AS product_id, "
                "s.sku_id AS sku_id, o.offer_id AS offer_id, o.price_paise AS price_paise "
                "FROM offers o "
                "JOIN skus s ON s.catalog_version = o.catalog_version AND s.sku_id = o.sku_id "
                "JOIN products p ON p.catalog_version = s.catalog_version AND p.product_id = s.product_id "
                "WHERE o.price_paise IS NOT NULL AND o.status = 'ACTIVE' LIMIT %s",
                (count,),
            )
            return cursor.fetchall()
    finally:
        conn.close()


def _binding(offer: dict) -> ProductBinding:
    return ProductBinding(
        product_id=offer["product_id"], sku_id=offer["sku_id"],
        offer_id=offer["offer_id"], catalog_version=offer["catalog_version"],
    )


def _provider(session_id: str, state_version: int):
    compat = _compatibility()
    state = QueryState(
        state_version=state_version,
        hard_constraints=[],
        soft_preferences=[],
        query_terms=[],
        selected_result_entry_ids=[],
        catalog_version=compat.catalog_version,
        index_version=compat.index_version,
        lexicon_version=compat.lexicon_version,
        compact_goal_summary="test",
    )
    snapshot = TurnSnapshot(
        session_id=session_id,
        state=state,
        state_version=state_version,
        cart_version=0,
        cart=empty_cart(session_id, compat),
        compatibility_tuple=compat,
    )
    return lambda: snapshot


class DatabaseCartAdapterShowCartTests(unittest.TestCase):
    def test_show_cart_returns_empty_snapshot_for_session_with_no_cart_row(self) -> None:
        session_id = f"session_{uuid.uuid4().hex}"
        adapter = DatabaseCartAdapter(session_snapshot_provider=_provider(session_id, state_version=3))

        snapshot = adapter.show_cart(session_id, known_cart_version=0, deadline_ms=100)

        self.assertEqual(snapshot.cart_id, f"cart_{session_id}")
        self.assertEqual(snapshot.session_id, session_id)
        self.assertEqual(snapshot.cart_version, 0)
        self.assertEqual(snapshot.state_version, 3)
        self.assertEqual(snapshot.items, [])
        self.assertEqual(snapshot.item_count, 0)
        self.assertEqual(snapshot.subtotal.amount_paise, 0)

    def test_show_cart_returns_real_items_and_current_state_version(self) -> None:
        session_id = f"session_{uuid.uuid4().hex}"
        cart_id = f"cart_{uuid.uuid4().hex}"
        conn = _connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT o.catalog_version AS catalog_version, p.product_id AS product_id, "
                    "s.sku_id AS sku_id, o.offer_id AS offer_id, o.price_paise AS price_paise, "
                    "o.availability_status AS availability_status FROM offers o "
                    "JOIN skus s ON s.catalog_version = o.catalog_version AND s.sku_id = o.sku_id "
                    "JOIN products p ON p.catalog_version = s.catalog_version AND p.product_id = s.product_id "
                    "WHERE o.price_paise IS NOT NULL LIMIT 1"
                )
                ref = cursor.fetchone()
                self.assertIsNotNone(ref, "expected at least one real priced offer in the catalog")

                cursor.execute(
                    "INSERT INTO carts (cart_id, session_id, cart_version, item_count, "
                    "total_quantity, subtotal_paise) VALUES (%s, %s, %s, %s, %s, %s)",
                    (cart_id, session_id, 2, 1, 3, ref["price_paise"] * 3),
                )
                cursor.execute(
                    "INSERT INTO cart_items (cart_item_id, cart_id, catalog_version, product_id, "
                    "sku_id, offer_id, selected_attributes_json, quantity, unit_price_paise, "
                    "availability_status, price_as_of, availability_as_of) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        "item_" + uuid.uuid4().hex, cart_id, ref["catalog_version"], ref["product_id"],
                        ref["sku_id"], ref["offer_id"], json.dumps({}), 3, ref["price_paise"],
                        ref["availability_status"], datetime.now(timezone.utc).replace(tzinfo=None),
                        datetime.now(timezone.utc).replace(tzinfo=None),
                    ),
                )
            conn.commit()

            adapter = DatabaseCartAdapter(session_snapshot_provider=_provider(session_id, state_version=7))
            snapshot = adapter.show_cart(session_id, known_cart_version=0, deadline_ms=100)

            self.assertEqual(snapshot.cart_version, 2)
            self.assertEqual(snapshot.state_version, 7)
            self.assertEqual(len(snapshot.items), 1)
            item = snapshot.items[0]
            self.assertEqual(item.binding.sku_id, ref["sku_id"])
            self.assertEqual(item.binding.offer_id, ref["offer_id"])
            self.assertEqual(item.quantity, 3)
            self.assertEqual(item.unit_price.amount_paise, ref["price_paise"])
            self.assertEqual(item.line_subtotal.amount_paise, ref["price_paise"] * 3)
            self.assertEqual(snapshot.subtotal.amount_paise, ref["price_paise"] * 3)
        finally:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM cart_items WHERE cart_id = %s", (cart_id,))
                cursor.execute("DELETE FROM carts WHERE cart_id = %s", (cart_id,))
            conn.commit()
            conn.close()

    def test_show_cart_ignores_known_cart_version_and_returns_current_truth(self) -> None:
        session_id = f"session_{uuid.uuid4().hex}"
        adapter = DatabaseCartAdapter(session_snapshot_provider=_provider(session_id, state_version=0))

        snapshot = adapter.show_cart(session_id, known_cart_version=999, deadline_ms=100)

        self.assertEqual(snapshot.cart_version, 0)


class DatabaseCartAdapterUpdateCartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session_id = f"session_{uuid.uuid4().hex}"
        self.cart_id = f"cart_{self.session_id}"

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

    def test_add_item_happy_path_creates_cart_with_real_price(self) -> None:
        offer = _real_offers(1)[0]
        adapter = DatabaseCartAdapter(session_snapshot_provider=_provider(self.session_id, state_version=0))
        request = UpdateCartRequest(
            session_id=self.session_id,
            expected_state_version=0,
            expected_cart_version=0,
            idempotency_key="key-1",
            operations=[
                AddItemOperation(
                    operation_id="op-1",
                    result_entry_id="entry-1",
                    binding=_binding(offer),
                    selected_variant_hash="hash-1",
                    quantity=2,
                )
            ],
        )

        result = adapter.update_cart(request, deadline_ms=100)

        self.assertEqual(result.status, "UPDATED")
        self.assertEqual(result.applied_operation_ids, ["op-1"])
        self.assertEqual(result.cart.cart_version, 1)
        self.assertEqual(len(result.cart.items), 1)
        item = result.cart.items[0]
        self.assertEqual(item.binding.offer_id, offer["offer_id"])
        self.assertEqual(item.quantity, 2)
        self.assertEqual(item.unit_price.amount_paise, offer["price_paise"])
        self.assertEqual(result.cart.subtotal.amount_paise, offer["price_paise"] * 2)
        self.assertEqual(result.cart.item_count, 1)
        self.assertEqual(result.cart.total_quantity, 2)

        # Confirm it actually persisted, not just returned in-memory.
        reread = adapter.show_cart(self.session_id, known_cart_version=1, deadline_ms=100)
        self.assertEqual(reread.cart_version, 1)
        self.assertEqual(len(reread.items), 1)

    def test_multiple_add_item_operations_apply_atomically(self) -> None:
        offers = _real_offers(2)
        adapter = DatabaseCartAdapter(session_snapshot_provider=_provider(self.session_id, state_version=0))
        request = UpdateCartRequest(
            session_id=self.session_id,
            expected_state_version=0,
            expected_cart_version=0,
            idempotency_key="key-multi",
            operations=[
                AddItemOperation(
                    operation_id="op-a", result_entry_id="entry-a", binding=_binding(offers[0]),
                    selected_variant_hash="hash-a", quantity=1,
                ),
                AddItemOperation(
                    operation_id="op-b", result_entry_id="entry-b", binding=_binding(offers[1]),
                    selected_variant_hash="hash-b", quantity=1,
                ),
            ],
        )

        result = adapter.update_cart(request, deadline_ms=100)

        self.assertEqual(result.status, "UPDATED")
        self.assertEqual(set(result.applied_operation_ids), {"op-a", "op-b"})
        self.assertEqual(result.cart.item_count, 2)
        self.assertEqual(
            result.cart.subtotal.amount_paise, offers[0]["price_paise"] + offers[1]["price_paise"]
        )

    def test_stale_state_version_rejects_without_mutating(self) -> None:
        offer = _real_offers(1)[0]
        adapter = DatabaseCartAdapter(session_snapshot_provider=_provider(self.session_id, state_version=5))
        request = UpdateCartRequest(
            session_id=self.session_id,
            expected_state_version=4,
            expected_cart_version=0,
            idempotency_key="key-state-conflict",
            operations=[
                AddItemOperation(
                    operation_id="op-1", result_entry_id="entry-1", binding=_binding(offer),
                    selected_variant_hash="hash-1", quantity=1,
                )
            ],
        )

        result = adapter.update_cart(request, deadline_ms=100)

        self.assertEqual(result.status, "CONFLICT")
        self.assertEqual(result.warnings, ["STATE_VERSION_CONFLICT"])
        self.assertEqual(result.cart.item_count, 0)

    def test_stale_cart_version_rejects_without_mutating(self) -> None:
        offer = _real_offers(1)[0]
        adapter = DatabaseCartAdapter(session_snapshot_provider=_provider(self.session_id, state_version=0))
        request = UpdateCartRequest(
            session_id=self.session_id,
            expected_state_version=0,
            expected_cart_version=7,
            idempotency_key="key-cart-conflict",
            operations=[
                AddItemOperation(
                    operation_id="op-1", result_entry_id="entry-1", binding=_binding(offer),
                    selected_variant_hash="hash-1", quantity=1,
                )
            ],
        )

        result = adapter.update_cart(request, deadline_ms=100)

        self.assertEqual(result.status, "CONFLICT")
        self.assertEqual(result.warnings, ["CART_VERSION_CONFLICT"])
        self.assertEqual(result.cart.item_count, 0)

    def test_unsupported_operation_type_rejected_cleanly(self) -> None:
        adapter = DatabaseCartAdapter(session_snapshot_provider=_provider(self.session_id, state_version=0))
        # All six real CartOperation types are handled now, so there's no
        # legitimately-constructible "unsupported" operation left to test
        # against. This exercises update_cart's defensive fallback for a
        # hypothetical future contract type (e.g. if Manan lands
        # UndoLastRemovalOperation before this adapter's dispatch adds a
        # branch for it) using model_construct() to skip Pydantic
        # validation -- not a fork of contracts.py, just a stand-in object
        # with the one attribute (.type) this check actually reads.
        future_operation = SimpleNamespace(type="SOME_FUTURE_OPERATION", operation_id="op-1")
        request = UpdateCartRequest.model_construct(
            session_id=self.session_id,
            expected_state_version=0,
            expected_cart_version=0,
            idempotency_key="key-unsupported",
            operations=[future_operation],
            atomic=True,
        )

        result = adapter.update_cart(request, deadline_ms=100)

        self.assertEqual(result.status, "REJECTED")
        self.assertEqual(result.warnings, ["OPERATION_TYPE_NOT_YET_SUPPORTED:SOME_FUTURE_OPERATION"])
        # No cart row should have been created at all -- rejection happens
        # before any DB mutation.
        self.assertEqual(result.cart.cart_version, 0)
        self.assertEqual(result.cart.item_count, 0)

    def test_add_item_merges_into_existing_active_line_for_same_binding(self) -> None:
        offer = _real_offers(1)[0]
        adapter = DatabaseCartAdapter(session_snapshot_provider=_provider(self.session_id, state_version=0))
        first = UpdateCartRequest(
            session_id=self.session_id,
            expected_state_version=0,
            expected_cart_version=0,
            idempotency_key="key-first",
            operations=[
                AddItemOperation(
                    operation_id="op-1", result_entry_id="entry-1", binding=_binding(offer),
                    selected_variant_hash="hash-1", quantity=2,
                )
            ],
        )
        first_result = adapter.update_cart(first, deadline_ms=100)
        self.assertEqual(first_result.status, "UPDATED")
        first_cart_item_id = first_result.cart.items[0].cart_item_id

        second = UpdateCartRequest(
            session_id=self.session_id,
            expected_state_version=0,
            expected_cart_version=1,
            idempotency_key="key-second",
            operations=[
                AddItemOperation(
                    operation_id="op-2", result_entry_id="entry-2", binding=_binding(offer),
                    selected_variant_hash="hash-2", quantity=3,
                )
            ],
        )
        second_result = adapter.update_cart(second, deadline_ms=100)

        self.assertEqual(second_result.status, "UPDATED")
        self.assertEqual(second_result.cart.cart_version, 2)
        # Still one line -- merged, not a second row.
        self.assertEqual(second_result.cart.item_count, 1)
        merged_item = second_result.cart.items[0]
        self.assertEqual(merged_item.cart_item_id, first_cart_item_id)
        self.assertEqual(merged_item.quantity, 5)
        self.assertEqual(merged_item.unit_price.amount_paise, offer["price_paise"])
        self.assertEqual(merged_item.line_subtotal.amount_paise, offer["price_paise"] * 5)
        self.assertEqual(second_result.cart.total_quantity, 5)

    def test_add_item_merge_rejects_when_quantity_would_exceed_limit(self) -> None:
        offer = _real_offers(1)[0]
        adapter = DatabaseCartAdapter(session_snapshot_provider=_provider(self.session_id, state_version=0))
        first = UpdateCartRequest(
            session_id=self.session_id,
            expected_state_version=0,
            expected_cart_version=0,
            idempotency_key="key-first",
            operations=[
                AddItemOperation(
                    operation_id="op-1", result_entry_id="entry-1", binding=_binding(offer),
                    selected_variant_hash="hash-1", quantity=95,
                )
            ],
        )
        first_result = adapter.update_cart(first, deadline_ms=100)
        self.assertEqual(first_result.status, "UPDATED")

        second = UpdateCartRequest(
            session_id=self.session_id,
            expected_state_version=0,
            expected_cart_version=1,
            idempotency_key="key-second",
            operations=[
                AddItemOperation(
                    operation_id="op-2", result_entry_id="entry-2", binding=_binding(offer),
                    selected_variant_hash="hash-2", quantity=5,
                )
            ],
        )
        second_result = adapter.update_cart(second, deadline_ms=100)

        self.assertEqual(second_result.status, "REJECTED")
        self.assertEqual(second_result.warnings, ["QUANTITY_LIMIT"])
        # Unchanged -- still 95, the merge never applied.
        self.assertEqual(second_result.cart.items[0].quantity, 95)
        self.assertEqual(second_result.cart.cart_version, 1)

    def test_add_item_merge_allows_exactly_99(self) -> None:
        offer = _real_offers(1)[0]
        adapter = DatabaseCartAdapter(session_snapshot_provider=_provider(self.session_id, state_version=0))
        first = UpdateCartRequest(
            session_id=self.session_id,
            expected_state_version=0,
            expected_cart_version=0,
            idempotency_key="key-first",
            operations=[
                AddItemOperation(
                    operation_id="op-1", result_entry_id="entry-1", binding=_binding(offer),
                    selected_variant_hash="hash-1", quantity=94,
                )
            ],
        )
        adapter.update_cart(first, deadline_ms=100)

        second = UpdateCartRequest(
            session_id=self.session_id,
            expected_state_version=0,
            expected_cart_version=1,
            idempotency_key="key-second",
            operations=[
                AddItemOperation(
                    operation_id="op-2", result_entry_id="entry-2", binding=_binding(offer),
                    selected_variant_hash="hash-2", quantity=5,
                )
            ],
        )
        second_result = adapter.update_cart(second, deadline_ms=100)

        self.assertEqual(second_result.status, "UPDATED")
        self.assertEqual(second_result.cart.items[0].quantity, 99)

    def test_bogus_offer_rejected(self) -> None:
        adapter = DatabaseCartAdapter(session_snapshot_provider=_provider(self.session_id, state_version=0))
        request = UpdateCartRequest(
            session_id=self.session_id,
            expected_state_version=0,
            expected_cart_version=0,
            idempotency_key="key-bogus",
            operations=[
                AddItemOperation(
                    operation_id="op-1", result_entry_id="entry-1",
                    binding=ProductBinding(
                        product_id="not_real", sku_id="not_real", offer_id="not_real",
                        catalog_version="flipkart_v1",
                    ),
                    selected_variant_hash="hash-1", quantity=1,
                )
            ],
        )

        result = adapter.update_cart(request, deadline_ms=100)

        self.assertEqual(result.status, "REJECTED")
        self.assertEqual(result.warnings, ["OFFER_UNAVAILABLE"])


class DatabaseCartAdapterQuantityOperationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session_id = f"session_{uuid.uuid4().hex}"
        self.cart_id = f"cart_{self.session_id}"
        self.adapter = DatabaseCartAdapter(session_snapshot_provider=_provider(self.session_id, state_version=0))
        offer = _real_offers(1)[0]
        seed = self.adapter.update_cart(
            UpdateCartRequest(
                session_id=self.session_id,
                expected_state_version=0,
                expected_cart_version=0,
                idempotency_key="seed-key",
                operations=[
                    AddItemOperation(
                        operation_id="seed-op", result_entry_id="seed-entry", binding=_binding(offer),
                        selected_variant_hash="seed-hash", quantity=10,
                    )
                ],
            ),
            deadline_ms=100,
        )
        self.assertEqual(seed.status, "UPDATED")
        self.cart_item_id = seed.cart.items[0].cart_item_id

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

    def test_set_quantity_happy_path(self) -> None:
        result = self.adapter.update_cart(
            UpdateCartRequest(
                session_id=self.session_id, expected_state_version=0, expected_cart_version=1,
                idempotency_key="set-key",
                operations=[SetQuantityOperation(operation_id="op-set", cart_item_id=self.cart_item_id, quantity=42)],
            ),
            deadline_ms=100,
        )
        self.assertEqual(result.status, "UPDATED")
        self.assertEqual(result.cart.items[0].quantity, 42)
        self.assertEqual(result.cart.cart_version, 2)

    def test_increment_item_happy_path(self) -> None:
        result = self.adapter.update_cart(
            UpdateCartRequest(
                session_id=self.session_id, expected_state_version=0, expected_cart_version=1,
                idempotency_key="inc-key",
                operations=[IncrementItemOperation(operation_id="op-inc", cart_item_id=self.cart_item_id, amount=5)],
            ),
            deadline_ms=100,
        )
        self.assertEqual(result.status, "UPDATED")
        self.assertEqual(result.cart.items[0].quantity, 15)

    def test_decrement_item_happy_path(self) -> None:
        result = self.adapter.update_cart(
            UpdateCartRequest(
                session_id=self.session_id, expected_state_version=0, expected_cart_version=1,
                idempotency_key="dec-key",
                operations=[DecrementItemOperation(operation_id="op-dec", cart_item_id=self.cart_item_id, amount=3)],
            ),
            deadline_ms=100,
        )
        self.assertEqual(result.status, "UPDATED")
        self.assertEqual(result.cart.items[0].quantity, 7)

    def test_unknown_cart_item_id_rejected(self) -> None:
        result = self.adapter.update_cart(
            UpdateCartRequest(
                session_id=self.session_id, expected_state_version=0, expected_cart_version=1,
                idempotency_key="unknown-key",
                operations=[SetQuantityOperation(operation_id="op-x", cart_item_id="does-not-exist", quantity=5)],
            ),
            deadline_ms=100,
        )
        self.assertEqual(result.status, "REJECTED")
        self.assertEqual(result.warnings, ["CART_ITEM_NOT_FOUND"])
        self.assertEqual(result.cart.items[0].quantity, 10)  # untouched

    def test_cart_item_from_a_different_cart_is_not_found(self) -> None:
        other_session_id = f"session_{uuid.uuid4().hex}"
        other_cart_id = f"cart_{other_session_id}"
        other_adapter = DatabaseCartAdapter(session_snapshot_provider=_provider(other_session_id, state_version=0))
        try:
            other_offer = _real_offers(1)[0]
            other_seed = other_adapter.update_cart(
                UpdateCartRequest(
                    session_id=other_session_id, expected_state_version=0, expected_cart_version=0,
                    idempotency_key="other-seed-key",
                    operations=[
                        AddItemOperation(
                            operation_id="other-seed-op", result_entry_id="other-seed-entry",
                            binding=_binding(other_offer), selected_variant_hash="other-seed-hash", quantity=1,
                        )
                    ],
                ),
                deadline_ms=100,
            )
            other_cart_item_id = other_seed.cart.items[0].cart_item_id

            # Target the OTHER cart's item_id from THIS session's adapter.
            result = self.adapter.update_cart(
                UpdateCartRequest(
                    session_id=self.session_id, expected_state_version=0, expected_cart_version=1,
                    idempotency_key="cross-cart-key",
                    operations=[SetQuantityOperation(operation_id="op-y", cart_item_id=other_cart_item_id, quantity=5)],
                ),
                deadline_ms=100,
            )
            self.assertEqual(result.status, "REJECTED")
            self.assertEqual(result.warnings, ["CART_ITEM_NOT_FOUND"])
        finally:
            conn = _connect()
            try:
                with conn.cursor() as cursor:
                    cursor.execute("DELETE FROM cart_operation_events WHERE cart_id = %s", (other_cart_id,))
                    cursor.execute("DELETE FROM cart_items WHERE cart_id = %s", (other_cart_id,))
                    cursor.execute("DELETE FROM carts WHERE cart_id = %s", (other_cart_id,))
                conn.commit()
            finally:
                conn.close()

    def test_increment_past_99_rejected(self) -> None:
        result = self.adapter.update_cart(
            UpdateCartRequest(
                session_id=self.session_id, expected_state_version=0, expected_cart_version=1,
                idempotency_key="inc-over-key",
                operations=[IncrementItemOperation(operation_id="op-inc2", cart_item_id=self.cart_item_id, amount=95)],
            ),
            deadline_ms=100,
        )
        self.assertEqual(result.status, "REJECTED")
        self.assertEqual(result.warnings, ["QUANTITY_LIMIT"])
        self.assertEqual(result.cart.items[0].quantity, 10)  # untouched

    def test_decrement_below_1_rejected(self) -> None:
        # Seeded quantity is 10; decrementing by 10 would hit 0, invalid.
        result = self.adapter.update_cart(
            UpdateCartRequest(
                session_id=self.session_id, expected_state_version=0, expected_cart_version=1,
                idempotency_key="dec-under-key",
                operations=[DecrementItemOperation(operation_id="op-dec2", cart_item_id=self.cart_item_id, amount=10)],
            ),
            deadline_ms=100,
        )
        self.assertEqual(result.status, "REJECTED")
        self.assertEqual(result.warnings, ["QUANTITY_LIMIT"])
        self.assertEqual(result.cart.items[0].quantity, 10)  # untouched

    def test_removed_item_is_not_found(self) -> None:
        conn = _connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE cart_items SET status='REMOVED', removed_at=NOW() WHERE cart_item_id=%s",
                    (self.cart_item_id,),
                )
            conn.commit()
        finally:
            conn.close()

        result = self.adapter.update_cart(
            UpdateCartRequest(
                session_id=self.session_id, expected_state_version=0, expected_cart_version=1,
                idempotency_key="removed-key",
                operations=[SetQuantityOperation(operation_id="op-z", cart_item_id=self.cart_item_id, quantity=5)],
            ),
            deadline_ms=100,
        )
        self.assertEqual(result.status, "REJECTED")
        self.assertEqual(result.warnings, ["CART_ITEM_NOT_FOUND"])


class DatabaseCartAdapterRemoveItemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session_id = f"session_{uuid.uuid4().hex}"
        self.cart_id = f"cart_{self.session_id}"
        self.adapter = DatabaseCartAdapter(session_snapshot_provider=_provider(self.session_id, state_version=0))
        self.offer = _real_offers(1)[0]
        seed = self.adapter.update_cart(
            UpdateCartRequest(
                session_id=self.session_id, expected_state_version=0, expected_cart_version=0,
                idempotency_key="seed-key",
                operations=[
                    AddItemOperation(
                        operation_id="seed-op", result_entry_id="seed-entry", binding=_binding(self.offer),
                        selected_variant_hash="seed-hash", quantity=4,
                    )
                ],
            ),
            deadline_ms=100,
        )
        self.assertEqual(seed.status, "UPDATED")
        self.cart_item_id = seed.cart.items[0].cart_item_id

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

    def test_remove_item_soft_deletes_and_updates_totals(self) -> None:
        result = self.adapter.update_cart(
            UpdateCartRequest(
                session_id=self.session_id, expected_state_version=0, expected_cart_version=1,
                idempotency_key="remove-key",
                operations=[RemoveItemOperation(operation_id="op-rm", cart_item_id=self.cart_item_id)],
            ),
            deadline_ms=100,
        )
        self.assertEqual(result.status, "UPDATED")
        self.assertEqual(result.cart.items, [])
        self.assertEqual(result.cart.item_count, 0)
        self.assertEqual(result.cart.subtotal.amount_paise, 0)
        self.assertEqual(result.cart.cart_version, 2)

        # Row is soft-deleted, not gone.
        conn = _connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT status, removed_at FROM cart_items WHERE cart_item_id = %s", (self.cart_item_id,)
                )
                row = cursor.fetchone()
        finally:
            conn.close()
        self.assertEqual(row["status"], "REMOVED")
        self.assertIsNotNone(row["removed_at"])

    def test_remove_unknown_item_rejected(self) -> None:
        result = self.adapter.update_cart(
            UpdateCartRequest(
                session_id=self.session_id, expected_state_version=0, expected_cart_version=1,
                idempotency_key="remove-unknown-key",
                operations=[RemoveItemOperation(operation_id="op-rm", cart_item_id="does-not-exist")],
            ),
            deadline_ms=100,
        )
        self.assertEqual(result.status, "REJECTED")
        self.assertEqual(result.warnings, ["CART_ITEM_NOT_FOUND"])

    def test_removing_the_same_item_twice_rejected_on_second_call(self) -> None:
        first = self.adapter.update_cart(
            UpdateCartRequest(
                session_id=self.session_id, expected_state_version=0, expected_cart_version=1,
                idempotency_key="remove-first-key",
                operations=[RemoveItemOperation(operation_id="op-rm1", cart_item_id=self.cart_item_id)],
            ),
            deadline_ms=100,
        )
        self.assertEqual(first.status, "UPDATED")

        second = self.adapter.update_cart(
            UpdateCartRequest(
                session_id=self.session_id, expected_state_version=0, expected_cart_version=2,
                idempotency_key="remove-second-key",
                operations=[RemoveItemOperation(operation_id="op-rm2", cart_item_id=self.cart_item_id)],
            ),
            deadline_ms=100,
        )
        self.assertEqual(second.status, "REJECTED")
        self.assertEqual(second.warnings, ["CART_ITEM_NOT_FOUND"])


class DatabaseCartAdapterUndoLastRemovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session_id = f"session_{uuid.uuid4().hex}"
        self.cart_id = f"cart_{self.session_id}"
        self.adapter = DatabaseCartAdapter(session_snapshot_provider=_provider(self.session_id, state_version=0))

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

    def _add(self, offer: dict, quantity: int, cart_version: int, key: str) -> UpdateCartResult:
        return self.adapter.update_cart(
            UpdateCartRequest(
                session_id=self.session_id, expected_state_version=0, expected_cart_version=cart_version,
                idempotency_key=key,
                operations=[
                    AddItemOperation(
                        operation_id=f"add-{key}", result_entry_id=f"entry-{key}", binding=_binding(offer),
                        selected_variant_hash=f"hash-{key}", quantity=quantity,
                    )
                ],
            ),
            deadline_ms=100,
        )

    def _remove(self, cart_item_id: str, cart_version: int, key: str) -> UpdateCartResult:
        return self.adapter.update_cart(
            UpdateCartRequest(
                session_id=self.session_id, expected_state_version=0, expected_cart_version=cart_version,
                idempotency_key=key,
                operations=[RemoveItemOperation(operation_id=f"rm-{key}", cart_item_id=cart_item_id)],
            ),
            deadline_ms=100,
        )

    def test_undo_restores_the_removed_item_with_same_id_and_quantity(self) -> None:
        offer = _real_offers(1)[0]
        added = self._add(offer, quantity=6, cart_version=0, key="a")
        cart_item_id = added.cart.items[0].cart_item_id
        removed = self._remove(cart_item_id, cart_version=1, key="b")
        self.assertEqual(removed.cart.items, [])

        result = self.adapter.undo_last_removal(
            session_id=self.session_id, expected_state_version=0, expected_cart_version=2,
            idempotency_key="undo-key", operation_id="undo-op-1", deadline_ms=100,
        )

        self.assertEqual(result.status, "UPDATED")
        self.assertEqual(len(result.cart.items), 1)
        restored = result.cart.items[0]
        self.assertEqual(restored.cart_item_id, cart_item_id)
        self.assertEqual(restored.quantity, 6)
        self.assertEqual(restored.unit_price.amount_paise, offer["price_paise"])
        self.assertEqual(result.cart.cart_version, 3)
        self.assertEqual(result.applied_operation_ids, ["undo-op-1"])

    def test_undo_with_nothing_removed_is_rejected(self) -> None:
        result = self.adapter.undo_last_removal(
            session_id=self.session_id, expected_state_version=0, expected_cart_version=0,
            idempotency_key="undo-key", operation_id="undo-op-1", deadline_ms=100,
        )
        self.assertEqual(result.status, "REJECTED")
        self.assertEqual(result.warnings, ["NO_REMOVAL_TO_UNDO"])

    def test_undo_twice_with_only_one_removal_rejects_the_second_time(self) -> None:
        offer = _real_offers(1)[0]
        added = self._add(offer, quantity=1, cart_version=0, key="a")
        cart_item_id = added.cart.items[0].cart_item_id
        self._remove(cart_item_id, cart_version=1, key="b")

        first = self.adapter.undo_last_removal(
            session_id=self.session_id, expected_state_version=0, expected_cart_version=2,
            idempotency_key="undo-key-1", operation_id="undo-op-1", deadline_ms=100,
        )
        self.assertEqual(first.status, "UPDATED")

        second = self.adapter.undo_last_removal(
            session_id=self.session_id, expected_state_version=0, expected_cart_version=3,
            idempotency_key="undo-key-2", operation_id="undo-op-2", deadline_ms=100,
        )
        self.assertEqual(second.status, "REJECTED")
        self.assertEqual(second.warnings, ["NO_REMOVAL_TO_UNDO"])

    def test_undo_targets_the_most_recently_removed_item(self) -> None:
        offers = _real_offers(2)
        added_a = self._add(offers[0], quantity=1, cart_version=0, key="a")
        item_a = added_a.cart.items[0].cart_item_id
        added_b = self._add(offers[1], quantity=1, cart_version=1, key="b")
        item_b = next(i.cart_item_id for i in added_b.cart.items if i.cart_item_id != item_a)

        self._remove(item_a, cart_version=2, key="rm-a")
        self._remove(item_b, cart_version=3, key="rm-b")

        result = self.adapter.undo_last_removal(
            session_id=self.session_id, expected_state_version=0, expected_cart_version=4,
            idempotency_key="undo-key", operation_id="undo-op-1", deadline_ms=100,
        )

        self.assertEqual(result.status, "UPDATED")
        restored_ids = {i.cart_item_id for i in result.cart.items}
        # B was removed last, so undo restores B -- A is still removed.
        self.assertEqual(restored_ids, {item_b})

    def test_repeated_undo_walks_back_through_removal_history(self) -> None:
        offers = _real_offers(2)
        added_a = self._add(offers[0], quantity=1, cart_version=0, key="a")
        item_a = added_a.cart.items[0].cart_item_id
        added_b = self._add(offers[1], quantity=1, cart_version=1, key="b")
        item_b = next(i.cart_item_id for i in added_b.cart.items if i.cart_item_id != item_a)

        self._remove(item_a, cart_version=2, key="rm-a")
        self._remove(item_b, cart_version=3, key="rm-b")

        first_undo = self.adapter.undo_last_removal(
            session_id=self.session_id, expected_state_version=0, expected_cart_version=4,
            idempotency_key="undo-key-1", operation_id="undo-op-1", deadline_ms=100,
        )
        self.assertEqual({i.cart_item_id for i in first_undo.cart.items}, {item_b})

        second_undo = self.adapter.undo_last_removal(
            session_id=self.session_id, expected_state_version=0, expected_cart_version=5,
            idempotency_key="undo-key-2", operation_id="undo-op-2", deadline_ms=100,
        )
        self.assertEqual({i.cart_item_id for i in second_undo.cart.items}, {item_a, item_b})

    def test_undo_conflicts_with_a_fresh_re_add_of_the_same_binding(self) -> None:
        offer = _real_offers(1)[0]
        added = self._add(offer, quantity=1, cart_version=0, key="a")
        original_item_id = added.cart.items[0].cart_item_id
        self._remove(original_item_id, cart_version=1, key="rm")
        # Ordinary re-add: a brand new row for the same binding.
        readded = self._add(offer, quantity=1, cart_version=2, key="readd")
        self.assertEqual(readded.status, "UPDATED")
        new_item_id = readded.cart.items[0].cart_item_id
        self.assertNotEqual(new_item_id, original_item_id)

        result = self.adapter.undo_last_removal(
            session_id=self.session_id, expected_state_version=0, expected_cart_version=3,
            idempotency_key="undo-key", operation_id="undo-op-1", deadline_ms=100,
        )

        self.assertEqual(result.status, "REJECTED")
        self.assertEqual(result.warnings, ["UNDO_CONFLICTS_WITH_EXISTING_LINE"])
        # Untouched: still just the re-added line.
        self.assertEqual(len(result.cart.items), 1)
        self.assertEqual(result.cart.items[0].cart_item_id, new_item_id)

    def test_undo_rejects_when_offer_no_longer_active(self) -> None:
        offer = _real_offers(1)[0]
        added = self._add(offer, quantity=1, cart_version=0, key="a")
        cart_item_id = added.cart.items[0].cart_item_id
        self._remove(cart_item_id, cart_version=1, key="rm")

        conn = _connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE offers SET status = 'RETIRED' WHERE catalog_version = %s AND offer_id = %s",
                    (offer["catalog_version"], offer["offer_id"]),
                )
            conn.commit()

            result = self.adapter.undo_last_removal(
                session_id=self.session_id, expected_state_version=0, expected_cart_version=2,
                idempotency_key="undo-key", operation_id="undo-op-1", deadline_ms=100,
            )

            self.assertEqual(result.status, "REJECTED")
            self.assertEqual(result.warnings, ["OFFER_UNAVAILABLE"])
        finally:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE offers SET status = 'ACTIVE' WHERE catalog_version = %s AND offer_id = %s",
                    (offer["catalog_version"], offer["offer_id"]),
                )
            conn.commit()
            conn.close()

    def test_undo_state_and_cart_version_conflicts(self) -> None:
        offer = _real_offers(1)[0]
        added = self._add(offer, quantity=1, cart_version=0, key="a")
        cart_item_id = added.cart.items[0].cart_item_id
        self._remove(cart_item_id, cart_version=1, key="rm")

        stale_state = self.adapter.undo_last_removal(
            session_id=self.session_id, expected_state_version=99, expected_cart_version=2,
            idempotency_key="undo-key-1", operation_id="undo-op-1", deadline_ms=100,
        )
        self.assertEqual(stale_state.status, "CONFLICT")
        self.assertEqual(stale_state.warnings, ["STATE_VERSION_CONFLICT"])

        stale_cart = self.adapter.undo_last_removal(
            session_id=self.session_id, expected_state_version=0, expected_cart_version=99,
            idempotency_key="undo-key-2", operation_id="undo-op-2", deadline_ms=100,
        )
        self.assertEqual(stale_cart.status, "CONFLICT")
        self.assertEqual(stale_cart.warnings, ["CART_VERSION_CONFLICT"])

        # Neither conflict mutated anything -- the item is still removed.
        snapshot = self.adapter.show_cart(self.session_id, known_cart_version=0, deadline_ms=100)
        self.assertEqual(snapshot.items, [])


class DatabaseCartAdapterClearCartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session_id = f"session_{uuid.uuid4().hex}"
        self.cart_id = f"cart_{self.session_id}"
        self.adapter = DatabaseCartAdapter(session_snapshot_provider=_provider(self.session_id, state_version=0))

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

    def _add(self, offer: dict, quantity: int, cart_version: int, key: str) -> UpdateCartResult:
        return self.adapter.update_cart(
            UpdateCartRequest(
                session_id=self.session_id, expected_state_version=0, expected_cart_version=cart_version,
                idempotency_key=key,
                operations=[
                    AddItemOperation(
                        operation_id=f"add-{key}", result_entry_id=f"entry-{key}", binding=_binding(offer),
                        selected_variant_hash=f"hash-{key}", quantity=quantity,
                    )
                ],
            ),
            deadline_ms=100,
        )

    def test_clear_cart_soft_deletes_all_active_lines(self) -> None:
        offers = _real_offers(2)
        self._add(offers[0], quantity=1, cart_version=0, key="a")
        self._add(offers[1], quantity=1, cart_version=1, key="b")

        result = self.adapter.update_cart(
            UpdateCartRequest(
                session_id=self.session_id, expected_state_version=0, expected_cart_version=2,
                idempotency_key="clear-key",
                operations=[ClearCartOperation(operation_id="op-clear", confirmation_token="confirm")],
            ),
            deadline_ms=100,
        )

        self.assertEqual(result.status, "UPDATED")
        self.assertEqual(result.cart.items, [])
        self.assertEqual(result.cart.item_count, 0)
        self.assertEqual(result.cart.subtotal.amount_paise, 0)
        self.assertEqual(result.cart.cart_version, 3)

    def test_clear_cart_without_confirmation_token_rejected(self) -> None:
        offer = _real_offers(1)[0]
        self._add(offer, quantity=1, cart_version=0, key="a")

        result = self.adapter.update_cart(
            UpdateCartRequest(
                session_id=self.session_id, expected_state_version=0, expected_cart_version=1,
                idempotency_key="clear-key",
                operations=[ClearCartOperation(operation_id="op-clear", confirmation_token=None)],
            ),
            deadline_ms=100,
        )

        self.assertEqual(result.status, "REJECTED")
        self.assertEqual(result.warnings, ["CLEAR_CONFIRMATION_REQUIRED"])
        # Untouched -- still one item.
        self.assertEqual(result.cart.item_count, 1)
        self.assertEqual(result.cart.cart_version, 1)

    def test_clear_already_empty_cart_still_succeeds(self) -> None:
        result = self.adapter.update_cart(
            UpdateCartRequest(
                session_id=self.session_id, expected_state_version=0, expected_cart_version=0,
                idempotency_key="clear-key",
                operations=[ClearCartOperation(operation_id="op-clear", confirmation_token="confirm")],
            ),
            deadline_ms=100,
        )

        self.assertEqual(result.status, "UPDATED")
        self.assertEqual(result.cart.items, [])
        self.assertEqual(result.cart.cart_version, 1)

    def test_cleared_items_are_not_undoable(self) -> None:
        offer = _real_offers(1)[0]
        self._add(offer, quantity=1, cart_version=0, key="a")
        cleared = self.adapter.update_cart(
            UpdateCartRequest(
                session_id=self.session_id, expected_state_version=0, expected_cart_version=1,
                idempotency_key="clear-key",
                operations=[ClearCartOperation(operation_id="op-clear", confirmation_token="confirm")],
            ),
            deadline_ms=100,
        )
        self.assertEqual(cleared.status, "UPDATED")

        undo_result = self.adapter.undo_last_removal(
            session_id=self.session_id, expected_state_version=0, expected_cart_version=2,
            idempotency_key="undo-key", operation_id="undo-op-1", deadline_ms=100,
        )

        self.assertEqual(undo_result.status, "REJECTED")
        self.assertEqual(undo_result.warnings, ["NO_REMOVAL_TO_UNDO"])

    def test_individual_removal_before_a_later_clear_remains_undoable(self) -> None:
        offers = _real_offers(2)
        added_a = self._add(offers[0], quantity=1, cart_version=0, key="a")
        item_a = added_a.cart.items[0].cart_item_id
        self._add(offers[1], quantity=1, cart_version=1, key="b")

        removed = self.adapter.update_cart(
            UpdateCartRequest(
                session_id=self.session_id, expected_state_version=0, expected_cart_version=2,
                idempotency_key="remove-key",
                operations=[RemoveItemOperation(operation_id="op-rm", cart_item_id=item_a)],
            ),
            deadline_ms=100,
        )
        self.assertEqual(removed.status, "UPDATED")

        cleared = self.adapter.update_cart(
            UpdateCartRequest(
                session_id=self.session_id, expected_state_version=0, expected_cart_version=3,
                idempotency_key="clear-key",
                operations=[ClearCartOperation(operation_id="op-clear", confirmation_token="confirm")],
            ),
            deadline_ms=100,
        )
        self.assertEqual(cleared.status, "UPDATED")
        self.assertEqual(cleared.cart.items, [])

        # A was individually removed before the clear touched B -- still
        # undoable, since removed_via distinguishes it from the clear.
        undo_result = self.adapter.undo_last_removal(
            session_id=self.session_id, expected_state_version=0, expected_cart_version=4,
            idempotency_key="undo-key", operation_id="undo-op-1", deadline_ms=100,
        )
        self.assertEqual(undo_result.status, "UPDATED")
        self.assertEqual([i.cart_item_id for i in undo_result.cart.items], [item_a])


class DatabaseCartAdapterIdempotencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session_id = f"session_{uuid.uuid4().hex}"
        self.cart_id = f"cart_{self.session_id}"
        self.adapter = DatabaseCartAdapter(session_snapshot_provider=_provider(self.session_id, state_version=0))

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

    def _row_counts(self) -> tuple[int, int]:
        conn = _connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) c FROM cart_items WHERE cart_id = %s", (self.cart_id,))
                items = cursor.fetchone()["c"]
                cursor.execute("SELECT COUNT(*) c FROM cart_operation_events WHERE cart_id = %s", (self.cart_id,))
                events = cursor.fetchone()["c"]
            return items, events
        finally:
            conn.close()

    def test_retrying_add_item_with_same_key_replays_without_re_executing(self) -> None:
        offer = _real_offers(1)[0]
        request = UpdateCartRequest(
            session_id=self.session_id, expected_state_version=0, expected_cart_version=0,
            idempotency_key="retry-key",
            operations=[
                AddItemOperation(
                    operation_id="op-1", result_entry_id="entry-1", binding=_binding(offer),
                    selected_variant_hash="hash-1", quantity=2,
                )
            ],
        )

        first = self.adapter.update_cart(request, deadline_ms=100)
        self.assertEqual(first.status, "UPDATED")
        self.assertEqual(first.cart.cart_version, 1)

        # Exact same request object, same key -- a real client retry.
        second = self.adapter.update_cart(request, deadline_ms=100)

        self.assertEqual(second.status, "UPDATED")
        self.assertEqual(second.cart.cart_version, 1)  # not bumped to 2
        self.assertEqual(second.cart.items[0].cart_item_id, first.cart.items[0].cart_item_id)

        items, events = self._row_counts()
        self.assertEqual(items, 1)   # not duplicated
        self.assertEqual(events, 1)  # not duplicated

    def test_same_key_with_different_request_is_rejected_not_replayed(self) -> None:
        offer = _real_offers(1)[0]
        first_request = UpdateCartRequest(
            session_id=self.session_id, expected_state_version=0, expected_cart_version=0,
            idempotency_key="reused-key",
            operations=[
                AddItemOperation(
                    operation_id="op-1", result_entry_id="entry-1", binding=_binding(offer),
                    selected_variant_hash="hash-1", quantity=1,
                )
            ],
        )
        first = self.adapter.update_cart(first_request, deadline_ms=100)
        self.assertEqual(first.status, "UPDATED")

        # Same key, genuinely different operation (different quantity).
        second_request = UpdateCartRequest(
            session_id=self.session_id, expected_state_version=0, expected_cart_version=1,
            idempotency_key="reused-key",
            operations=[
                AddItemOperation(
                    operation_id="op-2", result_entry_id="entry-2", binding=_binding(offer),
                    selected_variant_hash="hash-2", quantity=99,
                )
            ],
        )
        second = self.adapter.update_cart(second_request, deadline_ms=100)

        self.assertEqual(second.status, "REJECTED")
        self.assertEqual(second.warnings, ["IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST"])
        # Nothing from the second (rejected) request applied.
        self.assertEqual(second.cart.items[0].quantity, 1)

    def test_rejected_outcome_is_not_replayed_and_retry_can_still_succeed(self) -> None:
        # First attempt targets a bogus offer -- REJECTED, no event row
        # written at all (see the module docstring).
        request = UpdateCartRequest(
            session_id=self.session_id, expected_state_version=0, expected_cart_version=0,
            idempotency_key="retry-after-reject-key",
            operations=[
                AddItemOperation(
                    operation_id="op-1", result_entry_id="entry-1",
                    binding=ProductBinding(
                        product_id="not_real", sku_id="not_real", offer_id="not_real",
                        catalog_version="flipkart_v1",
                    ),
                    selected_variant_hash="hash-1", quantity=1,
                )
            ],
        )
        first = self.adapter.update_cart(request, deadline_ms=100)
        self.assertEqual(first.status, "REJECTED")
        self.assertEqual(first.warnings, ["OFFER_UNAVAILABLE"])

        _, events_after_reject = self._row_counts()
        self.assertEqual(events_after_reject, 0)

        # Same key, but now a genuinely valid request -- not blocked by the
        # earlier rejection, since nothing was ever persisted for that key.
        offer = _real_offers(1)[0]
        retry = UpdateCartRequest(
            session_id=self.session_id, expected_state_version=0, expected_cart_version=0,
            idempotency_key="retry-after-reject-key",
            operations=[
                AddItemOperation(
                    operation_id="op-2", result_entry_id="entry-2", binding=_binding(offer),
                    selected_variant_hash="hash-2", quantity=1,
                )
            ],
        )
        second = self.adapter.update_cart(retry, deadline_ms=100)
        self.assertEqual(second.status, "UPDATED")

    def test_undo_last_removal_replays_on_retry(self) -> None:
        offer = _real_offers(1)[0]
        added = self.adapter.update_cart(
            UpdateCartRequest(
                session_id=self.session_id, expected_state_version=0, expected_cart_version=0,
                idempotency_key="add-key",
                operations=[
                    AddItemOperation(
                        operation_id="op-add", result_entry_id="entry-add", binding=_binding(offer),
                        selected_variant_hash="hash-add", quantity=1,
                    )
                ],
            ),
            deadline_ms=100,
        )
        cart_item_id = added.cart.items[0].cart_item_id
        self.adapter.update_cart(
            UpdateCartRequest(
                session_id=self.session_id, expected_state_version=0, expected_cart_version=1,
                idempotency_key="remove-key",
                operations=[RemoveItemOperation(operation_id="op-rm", cart_item_id=cart_item_id)],
            ),
            deadline_ms=100,
        )

        first_undo = self.adapter.undo_last_removal(
            session_id=self.session_id, expected_state_version=0, expected_cart_version=2,
            idempotency_key="undo-retry-key", operation_id="undo-op-1", deadline_ms=100,
        )
        self.assertEqual(first_undo.status, "UPDATED")
        self.assertEqual(first_undo.cart.cart_version, 3)

        second_undo = self.adapter.undo_last_removal(
            session_id=self.session_id, expected_state_version=0, expected_cart_version=2,
            idempotency_key="undo-retry-key", operation_id="undo-op-1", deadline_ms=100,
        )
        self.assertEqual(second_undo.status, "UPDATED")
        self.assertEqual(second_undo.cart.cart_version, 3)  # replayed, not re-applied
        self.assertEqual(second_undo.cart.items[0].cart_item_id, cart_item_id)


if __name__ == "__main__":
    unittest.main()
