"""Tests for sql_filter.eligible_skus against the real Docker MySQL
container (see ../README.md's parent for setup) -- no mocking, since this
branch's entire job is to be the one guardrail nothing else in the merge
step is allowed to bypass, so it must be verified against real data.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sql_filter import eligible_skus  # noqa: E402


def test_no_constraints_returns_full_table():
    rows = eligible_skus({})
    assert len(rows) == 19998


def test_max_price_excludes_everything_above_cap():
    rows = eligible_skus({"max_price": 500})
    assert len(rows) == 9444
    assert all(row["discounted_price"] <= 500 for row in rows)


def test_max_price_excludes_a_known_expensive_sku():
    rows = eligible_skus({"max_price": 500})
    returned_ids = {row["sku_id"] for row in rows}
    assert "ACCEEV5UV5NV4PKZ" not in returned_ids  # discounted_price 116292.00


def test_category_and_stock_status_combined():
    rows = eligible_skus({"category": "Clothing", "stock_status": "in_stock"})
    assert len(rows) == 5285
    assert all(row["category"] == "Clothing" for row in rows)
    assert all(row["stock_status"] == "in_stock" for row in rows)


def test_known_cheap_sku_survives_a_generous_price_cap():
    rows = eligible_skus({"max_price": 1000})
    returned_ids = {row["sku_id"] for row in rows}
    assert "BMBEHPAGGDSSYMUZ" in returned_ids  # discounted_price 35.00


def test_unknown_constraint_key_raises():
    try:
        eligible_skus({"brand": "Nike"})
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unsupported constraint key")
