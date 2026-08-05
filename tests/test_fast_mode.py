from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

from fkgrid import catalog
from fkgrid.contracts import Action, QueryExtraction, RerankerRequest, SearchEntry, SearchResult, TurnRequest
from fkgrid.orchestrator import handle_turn


def _row(sku_id: str, title: str, *, price: int, rating: float | None = None) -> dict:
    return {
        "sku_id": sku_id,
        "product_id": f"product-{sku_id}",
        "offer_id": f"offer-{sku_id}",
        "title": title,
        "description": f"{title} for everyday use",
        "brand_name": "Acme",
        "category": "Phones",
        "price_paise": price,
        "rating": rating,
        "availability_status": "IN_STOCK",
        "quantity": 5,
        "variant_label": None,
    }


class _Cursor:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.executed: tuple[str, list[object]] | None = None

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, query: str, params: list[object]) -> None:
        self.executed = (query, params)

    def fetchall(self) -> list[dict]:
        return self.rows


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor
        self.commit_called = False

    def __enter__(self) -> "_Connection":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self) -> _Cursor:
        return self._cursor


def test_fast_search_filters_in_sql_and_does_not_write() -> None:
    cursor = _Cursor(
        [
            _row("sku-b", "Budget phone", price=400_000, rating=4.0),
            _row("sku-a", "Budget phone pro", price=300_000, rating=4.0),
        ]
    )
    connection = _Connection(cursor)

    @contextmanager
    def fake_connection():
        yield connection

    with patch.object(catalog.db, "connection", fake_connection):
        result = catalog.fast_search(
            RerankerRequest(
                soft_query_text="phone",
                hard_constraints={"max_price": 500_000, "category": "Phones"},
                top_n=2,
            )
        )

    assert cursor.executed is not None
    query, params = cursor.executed
    assert query.lstrip().upper().startswith("SELECT")
    assert "o.price_paise <= %s" in query
    assert "pm.category = %s" in query
    assert params == [500_000, "Phones"]
    assert not any(
        keyword in query.upper() for keyword in ("INSERT", "UPDATE", "DELETE", "ALTER", "DROP", "CREATE")
    )
    assert connection.commit_called is False
    assert result.search_mode == "fast"
    assert all(entry.rerank_score is None for entry in result.entries)
    assert all(entry.bm25_score is not None for entry in result.entries)


def test_fast_rank_is_deterministic_for_bm25_ties() -> None:
    rows = [
        _row("sku-z", "Phone", price=100, rating=4.0),
        _row("sku-a", "Phone", price=100, rating=4.0),
    ]
    first = catalog._bm25_rank(rows, "phone", 2)
    second = catalog._bm25_rank(list(reversed(rows)), "phone", 2)
    assert [row["sku_id"] for row, _score in first] == ["sku-a", "sku-z"]
    assert [row["sku_id"] for row, _score in second] == ["sku-a", "sku-z"]


def test_fast_mode_branches_after_extraction_and_never_calls_normal_search() -> None:
    order: list[str] = []
    extraction = QueryExtraction(action=Action.SEARCH, query_terms=["phone"])
    search_result = SearchResult(
        search_mode="fast",
        entries=[
            SearchEntry(
                sku_id="sku-1",
                product_id="product-1",
                offer_id="offer-1",
                title="Phone",
                bm25_score=1.0,
            )
        ],
    )

    def extract(*_args: object) -> QueryExtraction:
        order.append("extraction")
        return extraction

    def fast_search(_request: RerankerRequest) -> SearchResult:
        order.append("fast")
        return search_result

    session_id = "fast-mode-orchestrator-test"
    with (
        patch("fkgrid.orchestrator.llm.extract_query", side_effect=extract),
        patch("fkgrid.orchestrator.load_search_mode", return_value="fast"),
        patch("fkgrid.orchestrator.catalog.fast_search", side_effect=fast_search),
        patch("fkgrid.orchestrator.catalog.search", side_effect=AssertionError("normal search called")),
    ):
        result = handle_turn(TurnRequest(session_id=session_id, message="find a phone"))

    assert result.status.value == "OK"
    assert result.search_result is not None
    assert result.search_result.search_mode == "fast"
    assert order == ["extraction", "fast"]
    stages = [step.stage for step in result.trace]
    assert "fast_sql_bm25_search" in stages
    assert "reranker_search" not in stages


def test_normal_catalog_search_still_uses_the_existing_reranker_contract() -> None:
    row = _row("sku-1", "Phone", price=100_000, rating=4.5)
    with (
        patch.object(catalog, "_call_reranker", return_value=[{"sku_id": "sku-1", "rerank_score": 0.9}]),
        patch.object(catalog, "_fetch_rows", return_value={"sku-1": row}),
    ):
        result = catalog.search(RerankerRequest(soft_query_text="phone", top_n=1))

    assert result.search_mode == "normal"
    assert result.entries[0].rerank_score == 0.9
    assert result.entries[0].bm25_score is None
