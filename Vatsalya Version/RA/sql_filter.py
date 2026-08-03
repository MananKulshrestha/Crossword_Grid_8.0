"""SQL hard-filter branch: the eligible-SKU gate nothing else in
search_catalog is allowed to bypass (retrieval-architecture.md, "The three
retrieval branches" #1).

Queries product_metadata directly -- no ORM, no query builder library --
because the constraint set is small and fixed (max_price, category, size,
stock_status) and a parameterized WHERE clause is simpler and more
auditable than an abstraction layer over four columns.
"""

import os

import pymysql
import pymysql.cursors

DB_HOST = os.environ.get("FLIPKART_DB_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("FLIPKART_DB_PORT", "3307"))
DB_USER = os.environ.get("FLIPKART_DB_USER", "flipkart_user")
DB_PASSWORD = os.environ.get("FLIPKART_DB_PASSWORD", "flipkart_pass")
DB_NAME = os.environ.get("FLIPKART_DB_NAME", "flipkart")

# hard_constraints key -> (SQL column, comparison)
_CONSTRAINT_CLAUSES = {
    "max_price": "discounted_price <= %s",
    "category": "category = %s",
    "size": "size = %s",
    "stock_status": "stock_status = %s",
}


def _connect():
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        cursorclass=pymysql.cursors.DictCursor,
    )


def eligible_skus(hard_constraints: dict) -> list[dict]:
    """hard_constraints keys (all optional): max_price, category, size,
    stock_status. Returns matching product_metadata rows (as dicts) --
    never returns a row that violates any given constraint.

    Matches retrieval-architecture.md's step 1: "price cap
    (discounted_price <= X), stock_status != 'out_of_stock', category,
    size." Unknown constraint keys are a caller bug, not a silently
    ignored input -- they raise immediately.
    """
    unknown = set(hard_constraints) - set(_CONSTRAINT_CLAUSES)
    if unknown:
        raise ValueError(f"Unknown hard_constraints keys: {sorted(unknown)}")

    where_clauses = []
    params = []
    for key, value in hard_constraints.items():
        where_clauses.append(_CONSTRAINT_CLAUSES[key])
        params.append(value)

    query = "SELECT * FROM product_metadata"
    if where_clauses:
        query += " WHERE " + " AND ".join(where_clauses)

    conn = _connect()
    try:
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchall()
    finally:
        conn.close()
