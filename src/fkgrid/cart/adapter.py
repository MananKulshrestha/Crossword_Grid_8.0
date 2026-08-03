"""Real, MySQL-backed CartPort implementation (Track 6).

Milestone 2: show_cart.
Milestone 3: update_cart, ADD_ITEM happy path -- single or multiple ADD_ITEM
operations in one batch, all-or-nothing. Every other operation type is
rejected cleanly (OPERATION_TYPE_NOT_YET_SUPPORTED) rather than crashing.
Milestone 4: ADD_ITEM against an existing active line for the same binding
merges quantity into it (matching FakeCartPort's own ADD_ITEM semantics)
instead of being rejected, and a merge that would push quantity past 99 is
REJECTED with QUANTITY_LIMIT.
Milestone 5: SET_QUANTITY / INCREMENT_ITEM / DECREMENT_ITEM, sharing one
lookup-then-bounds-check path -- unknown/inactive/wrong-cart cart_item_id is
REJECTED CART_ITEM_NOT_FOUND, an out-of-[1,99]-bounds result is REJECTED
QUANTITY_LIMIT, matching FakeCartPort exactly including that these three
types never re-fetch or re-validate the offer (only ADD_ITEM touches offer
data).
Milestone 6: REMOVE_ITEM (soft delete via status='REMOVED', removed_via=
'REMOVE_ITEM'), plus the complete UNDO_LAST_REMOVAL logic (built ahead of
schedule per an explicit agreement with Manan -- see undo_last_removal and
_perform_undo_last_removal below for the full design, including why it's a
second public method rather than an UpdateCartRequest operation type).
Milestone 7: CLEAR_CART -- rejects without a confirmation_token
(CLEAR_CONFIRMATION_REQUIRED), otherwise soft-deletes every currently-ACTIVE
line in one batch with removed_via='CLEAR_CART' rather than 'REMOVE_ITEM' --
that distinction is what keeps a clear from being reachable through
UNDO_LAST_REMOVAL, per the explicit "clear cart doesn't need to be undoable"
decision, without resorting to a hard DELETE that would lose the line-item
history a clear wiped out.
Milestone 8 (this update): real idempotency-key replay for update_cart and
undo_last_removal. A fast-path lookup (_check_idempotency_replay) runs
before any mutating transaction opens: same key + same request content
(request_hash, a SHA-256 over operations/expected versions) replays the
stored result without re-executing; same key + different content is
REJECTED IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST rather than either
silently replaying the wrong answer or silently re-running. A genuine race
(two concurrent identical-key calls both pass the fast-path check) is
resolved at the final INSERT via cart_operation_events' own unique
constraint -- the loser rolls back its redundant mutation and defers to
whichever committed first (_resolve_idempotency_race), rather than raising
or double-applying. Deliberately scoped to the mutating (UPDATED) outcome
only: CONFLICT/REJECTED paths never write an event row and simply
re-evaluate against current state on retry, since there is no
double-application risk for a call that never mutated anything.

Wiring convention matches fakes.FakeCartPort / api.catalog.FixtureCartPort
exactly, as hardened in manan/final-agentic-chat commit e486242 ("enforce
cart session version preconditions"): one adapter instance per session,
constructed with a `session_snapshot_provider` closure over that session's
own state. state_version is never stored or cached here -- it belongs to
session state, not to the cart subsystem, and is always read fresh through
the provider at call time.

Connects with a fresh connection per call (matches RA/sql_filter.py's
_connect()-per-call pattern) rather than holding a long-lived connection,
since an adapter instance's lifetime is tied to a session that may sit idle
between turns.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Callable
from datetime import datetime, timezone

import pymysql
import pymysql.cursors

from fkgrid.agentic.contracts import (
    CartItem,
    CartSnapshot,
    Money,
    ProductBinding,
    TurnSnapshot,
    UpdateCartRequest,
    UpdateCartResult,
)
from fkgrid.agentic.ports import CartPort

DB_HOST = os.environ.get("FLIPKART_DB_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("FLIPKART_DB_PORT", "3307"))
DB_USER = os.environ.get("FLIPKART_DB_USER", "flipkart_user")
DB_PASSWORD = os.environ.get("FLIPKART_DB_PASSWORD", "flipkart_pass")
DB_NAME = os.environ.get("FLIPKART_DB_NAME", "flipkart")


def _connect() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD,
        database=DB_NAME, cursorclass=pymysql.cursors.DictCursor,
    )


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _cart_item_from_row(row: dict) -> CartItem:
    selected_attributes = json.loads(row["selected_attributes_json"]) if row["selected_attributes_json"] else {}
    return CartItem(
        cart_item_id=row["cart_item_id"],
        binding=ProductBinding(
            product_id=row["product_id"],
            sku_id=row["sku_id"],
            offer_id=row["offer_id"],
            catalog_version=row["catalog_version"],
        ),
        selected_attributes=selected_attributes,
        quantity=row["quantity"],
        unit_price=Money(amount_paise=row["unit_price_paise"], currency=row["currency"]),
        line_subtotal=Money(amount_paise=row["line_subtotal_paise"], currency=row["currency"]),
        availability_status=row["availability_status"],
        price_as_of=row["price_as_of"],
        availability_as_of=row["availability_as_of"],
    )


def _build_snapshot(session_id: str, cart_row: dict | None, item_rows: list[dict], state_version: int) -> CartSnapshot:
    if cart_row is None:
        # Matches fakes.empty_cart()'s shape exactly, except state_version
        # is the real current value rather than a hardcoded 0.
        return CartSnapshot(
            cart_id=f"cart_{session_id}",
            session_id=session_id,
            cart_version=0,
            state_version=state_version,
            items=[],
            item_count=0,
            total_quantity=0,
            subtotal=Money(amount_paise=0),
        )
    return CartSnapshot(
        cart_id=cart_row["cart_id"],
        session_id=cart_row["session_id"],
        cart_version=cart_row["cart_version"],
        state_version=state_version,
        items=[_cart_item_from_row(row) for row in item_rows],
        item_count=cart_row["item_count"],
        total_quantity=cart_row["total_quantity"],
        subtotal=Money(amount_paise=cart_row["subtotal_paise"], currency=cart_row["currency"]),
        mock_status=cart_row["mock_status"],
    )


def _fetch_cart_and_items(cursor, session_id: str) -> tuple[dict | None, list[dict]]:
    cursor.execute("SELECT * FROM carts WHERE session_id = %s", (session_id,))
    cart_row = cursor.fetchone()
    if cart_row is None:
        return None, []
    cursor.execute(
        "SELECT * FROM cart_items WHERE cart_id = %s AND status = 'ACTIVE' ORDER BY created_at",
        (cart_row["cart_id"],),
    )
    return cart_row, cursor.fetchall()


def _compute_request_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _find_prior_event(cursor, cart_id: str, idempotency_key: str) -> dict | None:
    cursor.execute(
        "SELECT request_hash, result_json FROM cart_operation_events "
        "WHERE cart_id = %s AND idempotency_key = %s",
        (cart_id, idempotency_key),
    )
    return cursor.fetchone()


def _fetch_offer(cursor, binding: ProductBinding) -> dict | None:
    cursor.execute(
        "SELECT price_paise, availability_status FROM offers "
        "WHERE catalog_version = %s AND offer_id = %s AND status = 'ACTIVE'",
        (binding.catalog_version, binding.offer_id),
    )
    return cursor.fetchone()


_SUPPORTED_OPERATION_TYPES = {
    "ADD_ITEM", "SET_QUANTITY", "INCREMENT_ITEM", "DECREMENT_ITEM", "REMOVE_ITEM", "CLEAR_CART",
}

# UNDO_LAST_REMOVAL is deliberately not in this set: contracts.py has no
# CartOperation variant for it yet, so a real UpdateCartRequest can never
# carry one -- see DatabaseCartAdapter.undo_last_removal and
# _perform_undo_last_removal for the fully-built, fully-tested logic this
# will route to once that contract type and the orchestrator wiring exist.


def _fetch_active_line_by_id(cursor, cart_id: str, cart_item_id: str) -> dict | None:
    cursor.execute(
        "SELECT * FROM cart_items WHERE cart_id = %s AND cart_item_id = %s AND status = 'ACTIVE'",
        (cart_id, cart_item_id),
    )
    return cursor.fetchone()


def _fetch_existing_active_line(cursor, cart_id: str, binding: ProductBinding) -> dict | None:
    # Binding-only match (product_id/sku_id/offer_id/catalog_version), not
    # selected_attributes -- mirrors FakeCartPort's own ADD_ITEM merge
    # lookup (`item.binding == operation.binding`), which never compares
    # selected_attributes either.
    cursor.execute(
        "SELECT * FROM cart_items WHERE cart_id = %s AND catalog_version = %s "
        "AND product_id = %s AND sku_id = %s AND offer_id = %s AND status = 'ACTIVE'",
        (cart_id, binding.catalog_version, binding.product_id, binding.sku_id, binding.offer_id),
    )
    return cursor.fetchone()


def _fetch_most_recently_removed_line(cursor, cart_id: str) -> dict | None:
    # DESC on removed_at (DATETIME(6), see schema_cart.sql) is the actual
    # "last removal" determination -- cart_item_id DESC only exists as a
    # deterministic tiebreaker for the practically-impossible case of two
    # removals landing in the exact same microsecond. removed_via='REMOVE_ITEM'
    # excludes CLEAR_CART batches -- those are deliberately not undoable.
    cursor.execute(
        "SELECT * FROM cart_items WHERE cart_id = %s AND status = 'REMOVED' "
        "AND removed_via = 'REMOVE_ITEM' "
        "ORDER BY removed_at DESC, cart_item_id DESC LIMIT 1 FOR UPDATE",
        (cart_id,),
    )
    return cursor.fetchone()


def _perform_undo_last_removal(cursor, cart_id: str) -> tuple[str | None, str | None]:
    """Shared, cursor-level undo logic -- called today by
    DatabaseCartAdapter.undo_last_removal (the temporary bridging entry
    point), and intended to be called by update_cart's dispatch loop once
    contracts.py gains an UndoLastRemovalOperation type and the orchestrator
    routes UNDO_LAST_REMOVAL to it -- at that point this function itself
    does not need to change, only update_cart's dispatch needs one new
    elif branch calling it.

    Returns (warning_code, restored_cart_item_id). Exactly one is non-None:
    a warning means REJECTED and nothing was mutated; a restored ID means
    the row at that ID was flipped back to ACTIVE with fresh catalog data.
    """
    removed_row = _fetch_most_recently_removed_line(cursor, cart_id)
    if removed_row is None:
        return "NO_REMOVAL_TO_UNDO", None

    binding = ProductBinding(
        product_id=removed_row["product_id"], sku_id=removed_row["sku_id"],
        offer_id=removed_row["offer_id"], catalog_version=removed_row["catalog_version"],
    )
    offer = _fetch_offer(cursor, binding)
    if offer is None:
        # Per the design discussion: undo restores from the *current*
        # catalog, it does not resurrect the frozen at-removal-time price/
        # availability. If the offer is no longer valid, there is nothing
        # safe to restore.
        return "OFFER_UNAVAILABLE", None

    conflict = _fetch_existing_active_line(cursor, cart_id, binding)
    if conflict is not None:
        # The shopper removed this item, then added it again as an
        # ordinary new line (a different cart_item_id -- see
        # schema_cart.sql's header comment on why re-add and undo are
        # deliberately different paths). Reactivating the original row now
        # would collide with that active line on uq_cart_items_active_line.
        # Rather than silently merge two rows' history or let the
        # constraint crash, reject explicitly.
        return "UNDO_CONFLICTS_WITH_EXISTING_LINE", None

    now = _now()
    cursor.execute(
        "UPDATE cart_items SET status = 'ACTIVE', removed_at = NULL, "
        "unit_price_paise = %s, availability_status = %s, price_as_of = %s, "
        "availability_as_of = %s WHERE cart_item_id = %s",
        (offer["price_paise"], offer["availability_status"], now, now, removed_row["cart_item_id"]),
    )
    return None, removed_row["cart_item_id"]


class DatabaseCartAdapter(CartPort):
    def __init__(
        self,
        *,
        session_snapshot_provider: Callable[[], TurnSnapshot],
    ) -> None:
        # Required, not optional like the fake's testing-convenience default:
        # a real adapter has no independent way to know the current
        # session state_version, so there is no sensible fallback if this
        # is omitted -- constructing without one is a caller bug, not a
        # runtime condition to silently paper over.
        if session_snapshot_provider is None:
            raise ValueError("DatabaseCartAdapter requires session_snapshot_provider")
        self._session_snapshot_provider = session_snapshot_provider
        self.show_calls = 0
        self.update_calls = 0
        self.undo_calls = 0

    def _current_state_version(self) -> int:
        return self._session_snapshot_provider().state_version

    def _read_cart_snapshot(self, session_id: str, state_version: int) -> CartSnapshot:
        conn = _connect()
        try:
            with conn.cursor() as cursor:
                cart_row, item_rows = _fetch_cart_and_items(cursor, session_id)
        finally:
            conn.close()
        return _build_snapshot(session_id, cart_row, item_rows, state_version)

    def _check_idempotency_replay(
        self, session_id: str, cart_id: str, idempotency_key: str, request_hash: str,
    ) -> UpdateCartResult | None:
        """Fast-path replay check, run before opening any mutating
        transaction. Returns a ready UpdateCartResult if this exact request
        already completed (replay) or if the key was reused for a
        genuinely different request (rejection) -- None means proceed
        normally.

        Only guards the mutating (UPDATED) outcome: CONFLICT/REJECTED paths
        never write a cart_operation_events row at all. Re-evaluating a
        non-mutating check against possibly-changed current state is safe
        and arguably more correct than replaying a stale rejection reason
        -- idempotency protection only matters where re-execution risks
        double-applying a real mutation.
        """
        conn = _connect()
        try:
            with conn.cursor() as cursor:
                prior = _find_prior_event(cursor, cart_id, idempotency_key)
        finally:
            conn.close()
        if prior is None:
            return None
        if prior["request_hash"] != request_hash:
            return UpdateCartResult(
                status="REJECTED",
                cart=self._read_cart_snapshot(session_id, self._current_state_version()),
                warnings=["IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST"],
            )
        return UpdateCartResult.model_validate_json(prior["result_json"])

    def show_cart(self, session_id: str, known_cart_version: int, deadline_ms: int) -> CartSnapshot:
        # known_cart_version and deadline_ms are accepted for CartPort
        # signature compatibility but unused, matching FakeCartPort's own
        # show_cart -- a read always returns current truth regardless of
        # what the caller believes the version is; there is no CAS on a
        # pure read.
        del known_cart_version, deadline_ms
        self.show_calls += 1
        state_version = self._current_state_version()
        return self._read_cart_snapshot(session_id, state_version)

    def update_cart(self, request: UpdateCartRequest, deadline_ms: int) -> UpdateCartResult:
        del deadline_ms  # not enforced yet -- the fakes don't enforce it either
        self.update_calls += 1
        session_id = request.session_id
        cart_id = f"cart_{session_id}"
        state_version_now = self._current_state_version()

        # Checked before hashing/idempotency: a structurally-unsupported
        # operation is rejected the same way regardless of retries, no DB
        # lookup needed, and (for a future contract type the fake-object
        # tests exercise via model_construct()) may not even support
        # .model_dump(), which request-hash computation below needs.
        unsupported = [
            operation for operation in request.operations
            if operation.type not in _SUPPORTED_OPERATION_TYPES
        ]
        if unsupported:
            return UpdateCartResult(
                status="REJECTED",
                cart=self._read_cart_snapshot(session_id, state_version_now),
                warnings=[f"OPERATION_TYPE_NOT_YET_SUPPORTED:{unsupported[0].type}"],
            )

        request_hash = _compute_request_hash({
            "operations": [operation.model_dump(mode="json") for operation in request.operations],
            "expected_state_version": request.expected_state_version,
            "expected_cart_version": request.expected_cart_version,
        })
        replay = self._check_idempotency_replay(session_id, cart_id, request.idempotency_key, request_hash)
        if replay is not None:
            return replay

        if request.expected_state_version != state_version_now:
            return UpdateCartResult(
                status="CONFLICT",
                cart=self._read_cart_snapshot(session_id, state_version_now),
                warnings=["STATE_VERSION_CONFLICT"],
            )

        conn = _connect()
        try:
            with conn.cursor() as cursor:
                # Atomically ensure the cart row exists, then lock it -- a
                # no-op INSERT on an already-existing row via the unique
                # session_id key, so a first-ever mutation and a concurrent
                # one both resolve safely to the same locked row.
                cursor.execute(
                    "INSERT INTO carts (cart_id, session_id) VALUES (%s, %s) "
                    "ON DUPLICATE KEY UPDATE cart_id = cart_id",
                    (cart_id, session_id),
                )
                cursor.execute("SELECT * FROM carts WHERE session_id = %s FOR UPDATE", (session_id,))
                cart_row = cursor.fetchone()

                if request.expected_cart_version != cart_row["cart_version"]:
                    conn.rollback()
                    return UpdateCartResult(
                        status="CONFLICT",
                        cart=self._read_cart_snapshot(session_id, state_version_now),
                        warnings=["CART_VERSION_CONFLICT"],
                    )

                applied: list[str] = []
                for operation in request.operations:
                    if operation.type == "ADD_ITEM":
                        offer = _fetch_offer(cursor, operation.binding)
                        if offer is None:
                            conn.rollback()
                            return UpdateCartResult(
                                status="REJECTED",
                                cart=self._read_cart_snapshot(session_id, state_version_now),
                                warnings=["OFFER_UNAVAILABLE"],
                            )

                        existing = _fetch_existing_active_line(cursor, cart_row["cart_id"], operation.binding)
                        if existing is not None:
                            # Merge into the existing line -- matches
                            # FakeCartPort's ADD_ITEM semantics exactly:
                            # quantity accumulates, unit_price is deliberately
                            # NOT refreshed to the just-fetched offer price (the
                            # fake never re-prices an existing line on merge,
                            # only on first insert). Match binds on binding
                            # only, not selected_attributes, same as the fake --
                            # moot in practice since this catalog never sets
                            # selected_attributes to anything but {}.
                            new_quantity = existing["quantity"] + operation.quantity
                            if new_quantity > 99:
                                conn.rollback()
                                return UpdateCartResult(
                                    status="REJECTED",
                                    cart=self._read_cart_snapshot(session_id, state_version_now),
                                    warnings=["QUANTITY_LIMIT"],
                                )
                            cursor.execute(
                                "UPDATE cart_items SET quantity = %s WHERE cart_item_id = %s",
                                (new_quantity, existing["cart_item_id"]),
                            )
                            # line_subtotal_paise is a generated column
                            # (quantity * unit_price_paise STORED) -- recomputes
                            # itself, nothing else to update here.
                        else:
                            now = _now()
                            # No try/except around this INSERT: two concurrent
                            # update_cart calls for the same cart both must
                            # acquire the SELECT ... FOR UPDATE lock on the
                            # carts row above first, so they're already
                            # serialized -- the existing-line check right above
                            # can't race with another writer for this cart. If
                            # uq_cart_items_active_line ever fires here anyway,
                            # that's a real bug to see loudly, not a case to
                            # silently absorb into a REJECTED response.
                            cursor.execute(
                                "INSERT INTO cart_items (cart_item_id, cart_id, catalog_version, "
                                "product_id, sku_id, offer_id, selected_attributes_json, quantity, "
                                "unit_price_paise, availability_status, price_as_of, availability_as_of) "
                                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                                (
                                    "item_" + uuid.uuid4().hex, cart_row["cart_id"],
                                    operation.binding.catalog_version, operation.binding.product_id,
                                    operation.binding.sku_id, operation.binding.offer_id,
                                    "{}", operation.quantity, offer["price_paise"],
                                    offer["availability_status"], now, now,
                                ),
                            )
                    elif operation.type == "REMOVE_ITEM":
                        existing = _fetch_active_line_by_id(cursor, cart_row["cart_id"], operation.cart_item_id)
                        if existing is None:
                            conn.rollback()
                            return UpdateCartResult(
                                status="REJECTED",
                                cart=self._read_cart_snapshot(session_id, state_version_now),
                                warnings=["CART_ITEM_NOT_FOUND"],
                            )
                        # Soft delete, not DELETE -- this row is exactly what
                        # undo_last_removal restores later.
                        cursor.execute(
                            "UPDATE cart_items SET status = 'REMOVED', removed_at = NOW(6), "
                            "removed_via = 'REMOVE_ITEM' WHERE cart_item_id = %s",
                            (existing["cart_item_id"],),
                        )
                    elif operation.type == "CLEAR_CART":
                        if not operation.confirmation_token:
                            conn.rollback()
                            return UpdateCartResult(
                                status="REJECTED",
                                cart=self._read_cart_snapshot(session_id, state_version_now),
                                warnings=["CLEAR_CONFIRMATION_REQUIRED"],
                            )
                        # One batch soft-delete, not N individual REMOVE_ITEM
                        # rows -- and removed_via='CLEAR_CART' (not
                        # 'REMOVE_ITEM') is what keeps these out of
                        # undo_last_removal's lookup. Matches FakeCartPort:
                        # unconditional, succeeds even on an already-empty
                        # cart (nothing to update, still counts as applied).
                        cursor.execute(
                            "UPDATE cart_items SET status = 'REMOVED', removed_at = NOW(6), "
                            "removed_via = 'CLEAR_CART' WHERE cart_id = %s AND status = 'ACTIVE'",
                            (cart_row["cart_id"],),
                        )
                    else:
                        # SET_QUANTITY / INCREMENT_ITEM / DECREMENT_ITEM.
                        # Deliberately does not re-fetch/re-validate the
                        # offer, matching FakeCartPort's own handling of
                        # these three types exactly -- a quantity change
                        # doesn't re-check availability there either, only
                        # ADD_ITEM (and, by construction, an offer that was
                        # already invalid could never have produced a line
                        # to begin with) touches offer data.
                        existing = _fetch_active_line_by_id(cursor, cart_row["cart_id"], operation.cart_item_id)
                        if existing is None:
                            conn.rollback()
                            return UpdateCartResult(
                                status="REJECTED",
                                cart=self._read_cart_snapshot(session_id, state_version_now),
                                warnings=["CART_ITEM_NOT_FOUND"],
                            )
                        if operation.type == "SET_QUANTITY":
                            new_quantity = operation.quantity
                        elif operation.type == "INCREMENT_ITEM":
                            new_quantity = existing["quantity"] + operation.amount
                        else:
                            new_quantity = existing["quantity"] - operation.amount
                        if not 1 <= new_quantity <= 99:
                            conn.rollback()
                            return UpdateCartResult(
                                status="REJECTED",
                                cart=self._read_cart_snapshot(session_id, state_version_now),
                                warnings=["QUANTITY_LIMIT"],
                            )
                        cursor.execute(
                            "UPDATE cart_items SET quantity = %s WHERE cart_item_id = %s",
                            (new_quantity, existing["cart_item_id"]),
                        )
                    applied.append(operation.operation_id)

                cursor.execute(
                    "SELECT COUNT(*) AS item_count, COALESCE(SUM(quantity),0) AS total_quantity, "
                    "COALESCE(SUM(line_subtotal_paise),0) AS subtotal_paise FROM cart_items "
                    "WHERE cart_id = %s AND status = 'ACTIVE'",
                    (cart_row["cart_id"],),
                )
                totals = cursor.fetchone()
                new_cart_version = cart_row["cart_version"] + 1
                cursor.execute(
                    "UPDATE carts SET cart_version=%s, item_count=%s, total_quantity=%s, "
                    "subtotal_paise=%s WHERE cart_id=%s",
                    (
                        new_cart_version, totals["item_count"], totals["total_quantity"],
                        totals["subtotal_paise"], cart_row["cart_id"],
                    ),
                )

                _, fresh_item_rows = _fetch_cart_and_items(cursor, session_id)
                cursor.execute("SELECT * FROM carts WHERE cart_id = %s", (cart_row["cart_id"],))
                fresh_cart_row = cursor.fetchone()
                result_cart = _build_snapshot(session_id, fresh_cart_row, fresh_item_rows, state_version_now)
                # The full UpdateCartResult, not just the CartSnapshot --
                # this exact object is what a replay must be able to
                # reconstruct byte-for-byte, so it has to be what gets
                # stored, not a fragment of it.
                final_result = UpdateCartResult(status="UPDATED", applied_operation_ids=applied, cart=result_cart)

                try:
                    cursor.execute(
                        "INSERT INTO cart_operation_events (operation_id, cart_id, session_id, "
                        "idempotency_key, request_hash, operations_json, cart_version_before, "
                        "cart_version_after, status, result_json) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            "evt_" + uuid.uuid4().hex, cart_row["cart_id"], session_id,
                            request.idempotency_key, request_hash,
                            json.dumps([operation.model_dump(mode="json") for operation in request.operations]),
                            cart_row["cart_version"], new_cart_version, "UPDATED",
                            final_result.model_dump_json(),
                        ),
                    )
                except pymysql.err.IntegrityError as exc:
                    if "uq_cart_operation_events_idempotency" not in str(exc):
                        raise
                    # Lost a race with a concurrent identical-key request
                    # that already committed while we did the same work --
                    # discard our own (redundant, uncommitted) mutation and
                    # replay theirs, rather than raising or double-applying.
                    conn.rollback()
                    return self._resolve_idempotency_race(
                        session_id, cart_row["cart_id"], request.idempotency_key,
                        request_hash, state_version_now,
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        return final_result

    def _resolve_idempotency_race(
        self, session_id: str, cart_id: str, idempotency_key: str,
        request_hash: str, state_version_now: int,
    ) -> UpdateCartResult:
        conn = _connect()
        try:
            with conn.cursor() as cursor:
                winner = _find_prior_event(cursor, cart_id, idempotency_key)
        finally:
            conn.close()
        if winner is not None and winner["request_hash"] == request_hash:
            return UpdateCartResult.model_validate_json(winner["result_json"])
        return UpdateCartResult(
            status="REJECTED",
            cart=self._read_cart_snapshot(session_id, state_version_now),
            warnings=["IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST"],
        )

    def undo_last_removal(
        self,
        session_id: str,
        expected_state_version: int,
        expected_cart_version: int,
        idempotency_key: str,
        operation_id: str,
        deadline_ms: int,
    ) -> UpdateCartResult:
        """Temporary bridging entry point for UNDO_LAST_REMOVAL -- see the
        module docstring and _perform_undo_last_removal's own docstring for
        why this exists as a direct method instead of routing through
        update_cart(UpdateCartRequest): contracts.py has no CartOperation
        variant for this yet, so there is no valid request object that
        could carry it. Same CAS checks, same transaction shape, same audit
        trail, same UpdateCartResult return type as update_cart -- this is
        not a different contract, just a different (temporary) door in.
        """
        del deadline_ms
        self.undo_calls += 1
        cart_id = f"cart_{session_id}"
        request_hash = _compute_request_hash({
            "type": "UNDO_LAST_REMOVAL",
            "operation_id": operation_id,
            "expected_state_version": expected_state_version,
            "expected_cart_version": expected_cart_version,
        })
        replay = self._check_idempotency_replay(session_id, cart_id, idempotency_key, request_hash)
        if replay is not None:
            return replay

        state_version_now = self._current_state_version()

        if expected_state_version != state_version_now:
            return UpdateCartResult(
                status="CONFLICT",
                cart=self._read_cart_snapshot(session_id, state_version_now),
                warnings=["STATE_VERSION_CONFLICT"],
            )

        conn = _connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO carts (cart_id, session_id) VALUES (%s, %s) "
                    "ON DUPLICATE KEY UPDATE cart_id = cart_id",
                    (cart_id, session_id),
                )
                cursor.execute("SELECT * FROM carts WHERE session_id = %s FOR UPDATE", (session_id,))
                cart_row = cursor.fetchone()

                if expected_cart_version != cart_row["cart_version"]:
                    conn.rollback()
                    return UpdateCartResult(
                        status="CONFLICT",
                        cart=self._read_cart_snapshot(session_id, state_version_now),
                        warnings=["CART_VERSION_CONFLICT"],
                    )

                # The restored cart_item_id itself doesn't need separate
                # handling here -- result_cart (built fresh below) already
                # reflects it as ACTIVE again.
                warning, _ = _perform_undo_last_removal(cursor, cart_row["cart_id"])
                if warning is not None:
                    conn.rollback()
                    return UpdateCartResult(
                        status="REJECTED",
                        cart=self._read_cart_snapshot(session_id, state_version_now),
                        warnings=[warning],
                    )

                cursor.execute(
                    "SELECT COUNT(*) AS item_count, COALESCE(SUM(quantity),0) AS total_quantity, "
                    "COALESCE(SUM(line_subtotal_paise),0) AS subtotal_paise FROM cart_items "
                    "WHERE cart_id = %s AND status = 'ACTIVE'",
                    (cart_row["cart_id"],),
                )
                totals = cursor.fetchone()
                new_cart_version = cart_row["cart_version"] + 1
                cursor.execute(
                    "UPDATE carts SET cart_version=%s, item_count=%s, total_quantity=%s, "
                    "subtotal_paise=%s WHERE cart_id=%s",
                    (
                        new_cart_version, totals["item_count"], totals["total_quantity"],
                        totals["subtotal_paise"], cart_row["cart_id"],
                    ),
                )

                _, fresh_item_rows = _fetch_cart_and_items(cursor, session_id)
                cursor.execute("SELECT * FROM carts WHERE cart_id = %s", (cart_row["cart_id"],))
                fresh_cart_row = cursor.fetchone()
                result_cart = _build_snapshot(session_id, fresh_cart_row, fresh_item_rows, state_version_now)
                # The full UpdateCartResult, not just the CartSnapshot -- see
                # the identical comment in update_cart for why.
                final_result = UpdateCartResult(
                    status="UPDATED", applied_operation_ids=[operation_id], cart=result_cart,
                )

                try:
                    cursor.execute(
                        "INSERT INTO cart_operation_events (operation_id, cart_id, session_id, "
                        "idempotency_key, request_hash, operations_json, cart_version_before, "
                        "cart_version_after, status, result_json) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            "evt_" + uuid.uuid4().hex, cart_row["cart_id"], session_id, idempotency_key,
                            request_hash,
                            # Not a real Pydantic CartOperation -- see the
                            # module docstring. Self-describing plain dict,
                            # forward-compatible with whatever the eventual
                            # contract type serializes to.
                            json.dumps([{"type": "UNDO_LAST_REMOVAL", "operation_id": operation_id}]),
                            cart_row["cart_version"], new_cart_version, "UPDATED",
                            final_result.model_dump_json(),
                        ),
                    )
                except pymysql.err.IntegrityError as exc:
                    if "uq_cart_operation_events_idempotency" not in str(exc):
                        raise
                    conn.rollback()
                    return self._resolve_idempotency_race(
                        session_id, cart_row["cart_id"], idempotency_key, request_hash, state_version_now,
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        return final_result
