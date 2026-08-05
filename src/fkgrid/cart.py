"""MySQL-backed cart against the live database. No fixtures, no in-memory cart.

Retains the correctness-critical pieces from the old adapter: batch
operations applied all-or-nothing in one transaction, optimistic
concurrency via cart_version, and idempotency-key replay guarded by a
unique DB constraint. Everything else (state_version cross-checks tied to
the old orchestrator, undo-last-removal bridging) is dropped.
"""

from __future__ import annotations

import json
import uuid

import pymysql

from . import db
from .contracts import (
    CartItem,
    CartOperationType,
    CartSnapshot,
    UpdateCartRequest,
    UpdateCartResult,
)

_SUPPORTED_OPERATION_TYPES = {
    CartOperationType.ADD_ITEM,
    CartOperationType.SET_QUANTITY,
    CartOperationType.REMOVE_ITEM,
    CartOperationType.CLEAR_CART,
}


def _cart_id(session_id: str) -> str:
    return f"cart_{session_id}"


def _read_cart_snapshot(cursor, cart_id: str) -> CartSnapshot:
    cursor.execute("SELECT * FROM carts WHERE cart_id=%s", (cart_id,))
    cart_row = cursor.fetchone()
    if cart_row is None:
        return CartSnapshot(cart_version=0, item_count=0, total_quantity=0, subtotal_paise=0)

    cursor.execute(
        "SELECT * FROM cart_items WHERE cart_id=%s AND status='ACTIVE' ORDER BY created_at",
        (cart_id,),
    )
    item_rows = cursor.fetchall()
    items = [
        CartItem(
            cart_item_id=row["cart_item_id"],
            sku_id=row["sku_id"],
            product_id=row["product_id"],
            offer_id=row["offer_id"],
            title=row.get("title") or row["sku_id"],
            quantity=row["quantity"],
            unit_price_paise=row["unit_price_paise"],
            line_subtotal_paise=row["line_subtotal_paise"],
            availability_status=row["availability_status"],
        )
        for row in item_rows
    ]
    return CartSnapshot(
        cart_version=cart_row["cart_version"],
        item_count=cart_row["item_count"],
        total_quantity=cart_row["total_quantity"],
        subtotal_paise=cart_row["subtotal_paise"],
        items=items,
    )


def show_cart(session_id: str) -> CartSnapshot:
    with db.connection() as conn:
        with conn.cursor() as cursor:
            return _read_cart_snapshot(cursor, _cart_id(session_id))


def _fetch_offer(cursor, offer_id: str) -> dict | None:
    cursor.execute(
        "SELECT price_paise, availability_status FROM offers "
        "WHERE offer_id=%s AND status='ACTIVE'",
        (offer_id,),
    )
    return cursor.fetchone()


def _fetch_active_line_by_id(cursor, cart_id: str, cart_item_id: str) -> dict | None:
    cursor.execute(
        "SELECT * FROM cart_items WHERE cart_id=%s AND cart_item_id=%s AND status='ACTIVE'",
        (cart_id, cart_item_id),
    )
    return cursor.fetchone()


def _fetch_existing_active_line(cursor, cart_id: str, sku_id: str, offer_id: str) -> dict | None:
    cursor.execute(
        "SELECT * FROM cart_items WHERE cart_id=%s AND sku_id=%s AND offer_id=%s "
        "AND status='ACTIVE'",
        (cart_id, sku_id, offer_id),
    )
    return cursor.fetchone()


def _apply_operations(cursor, cart_id: str, request: UpdateCartRequest) -> list[str] | UpdateCartResult:
    applied: list[str] = []
    for index, operation in enumerate(request.operations):
        operation_id = f"op_{index}"

        if operation.type == CartOperationType.ADD_ITEM:
            if not operation.offer_id or not operation.sku_id or not operation.product_id:
                return UpdateCartResult(status="REJECTED", reason="REFERENCE_UNRESOLVED")
            offer = _fetch_offer(cursor, operation.offer_id)
            if offer is None:
                return UpdateCartResult(status="REJECTED", reason="OFFER_UNAVAILABLE")
            quantity = operation.quantity or 1
            existing = _fetch_existing_active_line(cursor, cart_id, operation.sku_id, operation.offer_id)
            if existing is not None:
                new_quantity = existing["quantity"] + quantity
                if new_quantity > 99:
                    return UpdateCartResult(status="REJECTED", reason="QUANTITY_LIMIT")
                cursor.execute(
                    "UPDATE cart_items SET quantity=%s WHERE cart_item_id=%s",
                    (new_quantity, existing["cart_item_id"]),
                )
            else:
                cursor.execute(
                    "INSERT INTO cart_items "
                    "(cart_item_id, cart_id, product_id, sku_id, offer_id, quantity, "
                    "unit_price_paise, availability_status, status, price_as_of, availability_as_of) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'ACTIVE',NOW(6),NOW(6))",
                    (
                        f"item_{uuid.uuid4().hex}",
                        cart_id,
                        operation.product_id,
                        operation.sku_id,
                        operation.offer_id,
                        quantity,
                        offer["price_paise"],
                        offer["availability_status"],
                    ),
                )
            applied.append(operation_id)

        elif operation.type == CartOperationType.REMOVE_ITEM:
            if not operation.cart_item_id:
                return UpdateCartResult(status="REJECTED", reason="CART_ITEM_ID_REQUIRED")
            existing = _fetch_active_line_by_id(cursor, cart_id, operation.cart_item_id)
            if existing is None:
                return UpdateCartResult(status="REJECTED", reason="CART_ITEM_NOT_FOUND")
            cursor.execute(
                "UPDATE cart_items SET status='REMOVED', removed_at=NOW(6), "
                "removed_via='REMOVE_ITEM' WHERE cart_item_id=%s",
                (operation.cart_item_id,),
            )
            applied.append(operation_id)

        elif operation.type == CartOperationType.SET_QUANTITY:
            if not operation.cart_item_id or operation.quantity is None:
                return UpdateCartResult(status="REJECTED", reason="SET_QUANTITY_ARGS_REQUIRED")
            existing = _fetch_active_line_by_id(cursor, cart_id, operation.cart_item_id)
            if existing is None:
                return UpdateCartResult(status="REJECTED", reason="CART_ITEM_NOT_FOUND")
            if not (1 <= operation.quantity <= 99):
                return UpdateCartResult(status="REJECTED", reason="QUANTITY_LIMIT")
            cursor.execute(
                "UPDATE cart_items SET quantity=%s WHERE cart_item_id=%s",
                (operation.quantity, operation.cart_item_id),
            )
            applied.append(operation_id)

        elif operation.type == CartOperationType.CLEAR_CART:
            if not operation.confirmation:
                return UpdateCartResult(status="REJECTED", reason="CLEAR_CONFIRMATION_REQUIRED")
            cursor.execute(
                "UPDATE cart_items SET status='REMOVED', removed_at=NOW(6), "
                "removed_via='CLEAR_CART' WHERE cart_id=%s AND status='ACTIVE'",
                (cart_id,),
            )
            applied.append(operation_id)

    return applied


def _recompute_totals(cursor, cart_id: str) -> None:
    cursor.execute(
        "SELECT COUNT(*) item_count, COALESCE(SUM(quantity),0) total_quantity, "
        "COALESCE(SUM(line_subtotal_paise),0) subtotal_paise "
        "FROM cart_items WHERE cart_id=%s AND status='ACTIVE'",
        (cart_id,),
    )
    totals = cursor.fetchone()
    cursor.execute(
        "UPDATE carts SET cart_version=cart_version+1, item_count=%s, "
        "total_quantity=%s, subtotal_paise=%s WHERE cart_id=%s",
        (totals["item_count"], totals["total_quantity"], totals["subtotal_paise"], cart_id),
    )


def update_cart(request: UpdateCartRequest) -> UpdateCartResult:
    for operation in request.operations:
        if operation.type not in _SUPPORTED_OPERATION_TYPES:
            return UpdateCartResult(status="REJECTED", reason=f"OPERATION_TYPE_NOT_SUPPORTED:{operation.type}")

    cart_id = _cart_id(request.session_id)
    request_hash = json.dumps(
        {
            "operations": [op.model_dump(mode="json") for op in request.operations],
            "expected_cart_version": request.expected_cart_version,
        },
        sort_keys=True,
    )

    with db.connection() as conn:
        conn.begin()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT request_hash, result_json FROM cart_operation_events "
                    "WHERE cart_id=%s AND idempotency_key=%s",
                    (cart_id, request.idempotency_key),
                )
                prior = cursor.fetchone()
                if prior is not None:
                    if prior["request_hash"] != request_hash:
                        conn.rollback()
                        return UpdateCartResult(
                            status="REJECTED", reason="IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST"
                        )
                    conn.rollback()
                    return UpdateCartResult.model_validate_json(prior["result_json"])

                cursor.execute(
                    "INSERT INTO carts (cart_id, session_id) VALUES (%s,%s) "
                    "ON DUPLICATE KEY UPDATE cart_id=cart_id",
                    (cart_id, request.session_id),
                )
                cursor.execute("SELECT * FROM carts WHERE cart_id=%s FOR UPDATE", (cart_id,))
                cart_row = cursor.fetchone()
                if cart_row["cart_version"] != request.expected_cart_version:
                    conn.rollback()
                    return UpdateCartResult(status="CONFLICT", reason="CART_VERSION_CONFLICT")

                outcome = _apply_operations(cursor, cart_id, request)
                if isinstance(outcome, UpdateCartResult):
                    conn.rollback()
                    return outcome
                applied_operation_ids = outcome

                _recompute_totals(cursor, cart_id)
                result_cart = _read_cart_snapshot(cursor, cart_id)
                final_result = UpdateCartResult(
                    status="UPDATED", applied_operation_ids=applied_operation_ids, cart=result_cart
                )

                cursor.execute(
                    "INSERT INTO cart_operation_events "
                    "(operation_id, cart_id, session_id, idempotency_key, request_hash, "
                    "operations_json, cart_version_before, cart_version_after, status, result_json) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        f"evt_{uuid.uuid4().hex}",
                        cart_id,
                        request.session_id,
                        request.idempotency_key,
                        request_hash,
                        json.dumps([op.model_dump(mode="json") for op in request.operations]),
                        cart_row["cart_version"],
                        result_cart.cart_version,
                        "UPDATED",
                        final_result.model_dump_json(),
                    ),
                )
            conn.commit()
            return final_result
        except pymysql.err.IntegrityError as exc:
            conn.rollback()
            if "uq_cart_operation_events_idempotency" not in str(exc):
                raise
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT request_hash, result_json FROM cart_operation_events "
                    "WHERE cart_id=%s AND idempotency_key=%s",
                    (cart_id, request.idempotency_key),
                )
                winner = cursor.fetchone()
            if winner is not None and winner["request_hash"] == request_hash:
                return UpdateCartResult.model_validate_json(winner["result_json"])
            return UpdateCartResult(status="REJECTED", reason="IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST")
        except Exception:
            conn.rollback()
            raise
