"""Applies schema_cart.sql (carts/cart_items/cart_operation_events) against
the live MySQL database that already holds the Tier 1 catalog schema
(schema_tier1.sql -- catalog_versions/products/skus/offers/...). That schema
is a fixed dependency here: this script only creates the three cart tables,
FKing directly into the existing catalog tables, and never touches them.

Idempotent: drops the three cart tables (in FK-safe child-first order) if
they already exist, then recreates them from schema_cart.sql. Safe to re-run
during development; cart data does not survive a re-run, which is expected
at this stage since Milestone 1 is schema-only, no adapter writes yet.
"""

import os

import pymysql
import pymysql.cursors

DB_HOST = os.environ.get("FLIPKART_DB_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("FLIPKART_DB_PORT", "3307"))
DB_USER = os.environ.get("FLIPKART_DB_USER", "flipkart_user")
DB_PASSWORD = os.environ.get("FLIPKART_DB_PASSWORD", "flipkart_pass")
DB_NAME = os.environ.get("FLIPKART_DB_NAME", "flipkart")

SCHEMA_SQL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema_cart.sql")

REQUIRED_CATALOG_TABLES = ("catalog_versions", "products", "skus", "offers")
CART_TABLES_CHILD_FIRST = ("cart_operation_events", "cart_items", "carts")


def _connect():
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD,
        database=DB_NAME, cursorclass=pymysql.cursors.DictCursor, autocommit=False,
    )


def _check_catalog_dependency(cursor):
    cursor.execute(
        "SELECT TABLE_NAME FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = %s AND TABLE_NAME IN %s",
        (DB_NAME, REQUIRED_CATALOG_TABLES),
    )
    present = {row["TABLE_NAME"] for row in cursor.fetchall()}
    missing = set(REQUIRED_CATALOG_TABLES) - present
    if missing:
        raise RuntimeError(
            f"Missing catalog tables {sorted(missing)} -- run build_tier1.py "
            "first. The cart schema FKs directly into products/skus/offers "
            "and cannot be created without them."
        )


def _drop_cart_tables(cursor):
    for table in CART_TABLES_CHILD_FIRST:
        cursor.execute(f"DROP TABLE IF EXISTS {table}")


def _run_schema_ddl(cursor):
    with open(SCHEMA_SQL_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()
    sql_text = "\n".join(line for line in lines if not line.strip().startswith("--"))
    for statement in sql_text.split(";"):
        statement = statement.strip()
        if statement:
            cursor.execute(statement)


def main():
    conn = _connect()
    try:
        with conn.cursor() as cursor:
            _check_catalog_dependency(cursor)
            _drop_cart_tables(cursor)
            _run_schema_ddl(cursor)
        conn.commit()
        print("Cart schema created: carts, cart_items, cart_operation_events")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
