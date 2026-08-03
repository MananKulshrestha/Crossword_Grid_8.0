-- Cart subsystem schema (Track 6). Adds exactly three tables on top of the
-- existing Tier 1 catalog schema (catalog_versions/products/skus/offers/...,
-- see schema_tier1.sql) -- that schema is a fixed dependency here, not
-- redesigned. cart_items FKs directly into products/skus/offers so the DB
-- itself rejects a cart line that doesn't point at a real catalog entity.
--
-- Design decisions from the architecture discussion this schema encodes:
--   - cart_items rows are soft-deleted (status='REMOVED'), never hard
--     DELETEd. This is what makes UNDO_LAST_REMOVAL cheap: undo is just
--     "find the most recently REMOVED row for this cart, flip it back to
--     ACTIVE" -- no event-log reconstruction needed, same cart_item_id
--     reappears.
--   - cart_operation_events is one row per update_cart CALL (a whole
--     batch), not per operation inside it -- matches the atomic-batch
--     model (UpdateCartRequest.operations is a single all-or-nothing unit).
--     Its job is idempotency-key replay and audit, not undo.
--   - No state_version column anywhere here. state_version belongs to
--     session state (owned by the orchestrator/session store, confirmed by
--     manan/final-agentic-chat's FakeCartPort now reading it live through
--     an injected session_snapshot_provider rather than storing its own
--     copy) -- the cart subsystem has no business persisting a value it
--     doesn't own and can't independently keep correct.

-- ---------------------------------------------------------------------
-- carts
-- ---------------------------------------------------------------------
CREATE TABLE carts (
    cart_id           VARCHAR(64) NOT NULL,
    session_id        VARCHAR(128) NOT NULL,
    cart_version      INT NOT NULL DEFAULT 0,
    currency          CHAR(3) NOT NULL DEFAULT 'INR',
    item_count        INT NOT NULL DEFAULT 0,
    total_quantity    INT NOT NULL DEFAULT 0,
    subtotal_paise    BIGINT NOT NULL DEFAULT 0,
    mock_status       VARCHAR(32) NOT NULL DEFAULT 'PROTOTYPE_MOCK_CATALOG',
    created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (cart_id),
    -- One cart per session, matching CartSnapshot/empty_cart's own
    -- one-cart-per-session semantics.
    UNIQUE KEY uq_carts_session (session_id),
    CONSTRAINT chk_carts_version_non_negative CHECK (cart_version >= 0),
    CONSTRAINT chk_carts_counts_non_negative CHECK (
        item_count >= 0 AND total_quantity >= 0 AND subtotal_paise >= 0
    )
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- cart_items
-- ---------------------------------------------------------------------
CREATE TABLE cart_items (
    cart_item_id            VARCHAR(64) NOT NULL,
    cart_id                 VARCHAR(64) NOT NULL,
    -- ProductBinding fields, verbatim -- real FKs into the existing catalog
    -- schema, not just matching shapes.
    catalog_version         VARCHAR(64) NOT NULL,
    product_id              VARCHAR(64) NOT NULL,
    sku_id                  VARCHAR(64) NOT NULL,
    offer_id                VARCHAR(64) NOT NULL,
    selected_attributes_json JSON NULL,
    quantity                 INT NOT NULL,
    unit_price_paise         BIGINT NOT NULL,
    currency                 CHAR(3) NOT NULL DEFAULT 'INR',
    -- Generated, not application-maintained -- removes an entire class of
    -- "forgot to recompute the line subtotal" bugs.
    line_subtotal_paise      BIGINT AS (quantity * unit_price_paise) STORED,
    availability_status      VARCHAR(32) NOT NULL,
    price_as_of               DATETIME NOT NULL,
    availability_as_of        DATETIME NOT NULL,
    -- Soft-delete for undo, see header.
    status                    ENUM('ACTIVE', 'REMOVED') NOT NULL DEFAULT 'ACTIVE',
    -- Microsecond precision, not plain DATETIME: UNDO_LAST_REMOVAL orders by
    -- "most recent removed_at" to find the item to restore, and two
    -- REMOVE_ITEM calls landing in the same second would otherwise tie with
    -- no reliable way to tell which was actually last.
    removed_at                 DATETIME(6) NULL,
    -- Distinguishes an individual REMOVE_ITEM (undoable, see
    -- UNDO_LAST_REMOVAL) from a CLEAR_CART batch (deliberately not
    -- undoable). Both soft-delete via the same status column for a
    -- uniform, queryable audit history; this is what lets the undo lookup
    -- correctly skip lines that were cleared rather than individually
    -- removed, without resorting to a hard DELETE that would lose the
    -- line-item history a clear wiped out.
    removed_via                ENUM('REMOVE_ITEM', 'CLEAR_CART') NULL,
    created_at                 DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                 DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (cart_item_id),
    CONSTRAINT fk_cart_items_cart FOREIGN KEY (cart_id)
        REFERENCES carts (cart_id),
    CONSTRAINT fk_cart_items_product FOREIGN KEY (catalog_version, product_id)
        REFERENCES products (catalog_version, product_id),
    CONSTRAINT fk_cart_items_sku FOREIGN KEY (catalog_version, sku_id)
        REFERENCES skus (catalog_version, sku_id),
    CONSTRAINT fk_cart_items_offer FOREIGN KEY (catalog_version, offer_id)
        REFERENCES offers (catalog_version, offer_id),
    CONSTRAINT chk_cart_items_quantity_bounds CHECK (quantity BETWEEN 1 AND 99),
    CONSTRAINT chk_cart_items_price_non_negative CHECK (unit_price_paise >= 0),
    -- One ACTIVE line per (cart, binding, variant) -- but a REMOVED line
    -- must NOT occupy this slot, or re-adding the same SKU after removal
    -- (a different, ordinary path than undo) would be blocked. Same
    -- generated-column-plus-unique-index trick schema_tier1.sql uses for
    -- catalog_versions' single-ACTIVE-row constraint: NULL out the key
    -- whenever status isn't ACTIVE, since MySQL unique indexes ignore NULLs.
    active_line_key VARCHAR(400) AS (
        CASE WHEN status = 'ACTIVE'
            THEN CONCAT(cart_id, '|', sku_id, '|', offer_id, '|',
                        CAST(selected_attributes_json AS CHAR(255)))
            ELSE NULL
        END
    ) STORED,
    UNIQUE KEY uq_cart_items_active_line (active_line_key),
    -- Supports "find the most recently REMOVED row for this cart" for undo.
    KEY ix_cart_items_cart_status_removed (cart_id, status, removed_at)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- cart_operation_events -- one row per update_cart CALL (a whole batch),
-- not per operation inside it. Backs idempotency-key replay and audit.
-- ---------------------------------------------------------------------
CREATE TABLE cart_operation_events (
    operation_id       VARCHAR(64) NOT NULL,
    cart_id            VARCHAR(64) NOT NULL,
    session_id         VARCHAR(128) NOT NULL,
    idempotency_key     VARCHAR(128) NOT NULL,
    -- SHA-256 of the semantically-meaningful request content (operations +
    -- expected versions). Lets a genuine retry (same key, same content) be
    -- distinguished from an idempotency_key accidentally reused for a
    -- different request -- the latter is a caller bug to reject, not
    -- something safe to silently replay the old result for.
    request_hash          CHAR(64) NOT NULL,
    operations_json      JSON NOT NULL,
    cart_version_before   INT NOT NULL,
    cart_version_after    INT NULL,
    -- Same literal vocabulary as UpdateCartResult.status -- no separate
    -- naming to keep in sync with the contract.
    status                 ENUM('UPDATED', 'REVALIDATION_REQUIRED', 'CONFLICT', 'REJECTED') NOT NULL,
    result_json             JSON NOT NULL,
    warnings_json            JSON NULL,
    created_at                DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (operation_id),
    CONSTRAINT fk_cart_operation_events_cart FOREIGN KEY (cart_id)
        REFERENCES carts (cart_id),
    -- The actual idempotency lookup: same key replayed twice for the same
    -- cart returns the stored result instead of re-executing.
    UNIQUE KEY uq_cart_operation_events_idempotency (cart_id, idempotency_key)
) ENGINE=InnoDB;
