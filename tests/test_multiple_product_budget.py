from __future__ import annotations

from unittest.mock import patch

import pytest

from fkgrid import catalog, llm
from fkgrid.contracts import (
    Action,
    MultiProductCandidateGroup,
    QueryExtraction,
    RerankerRequest,
    SearchEntry,
    SearchResult,
    TurnRequest,
)
from fkgrid.orchestrator import handle_turn


def _entry(sku_id: str, title: str, price: int) -> SearchEntry:
    return SearchEntry(
        sku_id=sku_id,
        product_id=f"product-{sku_id}",
        offer_id=f"offer-{sku_id}",
        title=title,
        description=f"{title} specification",
        price_paise=price,
        availability_status="IN_STOCK",
        rating=4.0,
        bm25_score=1.0,
    )


def test_extraction_has_an_explicit_shared_budget_tick() -> None:
    extraction = QueryExtraction.model_validate(
        {
            "action": "SEARCH",
            "query_terms": [],
            "multi_product_budget": {
                "enabled": True,
                "item_queries": ["computer", "mouse", "keyboard"],
                "total_budget_paise": 10_000,
                "budget_amount": None,
                "budget_currency": "INR",
            },
        }
    )

    assert extraction.multi_product_budget.enabled is True
    assert extraction.multi_product_budget.item_queries == ["computer", "mouse", "keyboard"]
    assert extraction.multi_product_budget.total_budget_paise == 10_000


def test_extraction_prompt_exposes_shared_budget_field() -> None:
    payload = {
        "action": "SEARCH",
        "multi_product_budget": {
            "enabled": True,
            "item_queries": ["computer", "mouse", "keyboard"],
            "total_budget_paise": 10_000,
            "budget_amount": None,
            "budget_currency": "INR",
        },
    }
    captured: dict[str, object] = {}

    def fake_completion(system_prompt: str, user_payload: dict, json_schema: dict) -> dict:
        captured["prompt"] = system_prompt
        captured["payload"] = user_payload
        captured["schema"] = json_schema
        return payload

    with patch.object(llm, "_chat_completion", side_effect=fake_completion):
        extraction = llm.extract_query("computer, mouse and keyboard for 10000 rupees total", [])

    assert extraction.multi_product_budget.enabled is True
    assert '"multi_product_budget"' in str(captured["prompt"])
    assert "required product query" in str(captured["prompt"])


def test_multi_product_candidates_use_fast_search_and_cap_each_group() -> None:
    requests: list[RerankerRequest] = []

    def fake_fast_search(request: RerankerRequest) -> SearchResult:
        requests.append(request)
        return SearchResult(
            search_mode="fast",
            entries=[
                _entry(f"{request.soft_query_text}-{i}", request.soft_query_text, 1_000)
                for i in range(25)
            ],
        )

    with patch.object(catalog, "fast_search", side_effect=fake_fast_search):
        groups = catalog.fast_multi_product_candidates(
            ["computer", "mouse", "keyboard"],
            budget_paise=10_000,
            candidate_cap_per_item=25,
        )

    assert [group.item_query for group in groups] == ["computer", "mouse", "keyboard"]
    assert [len(group.candidates) for group in groups] == [25, 25, 25]
    assert [request.top_n for request in requests] == [25, 25, 25]
    assert all(request.hard_constraints["max_price"] == 10_000 for request in requests)


def test_bundle_analysis_rejects_unknown_or_over_budget_model_choices() -> None:
    groups = [
        MultiProductCandidateGroup(
            item_query="computer", candidates=[_entry("c-1", "Computer", 4_000)]
        ),
        MultiProductCandidateGroup(item_query="mouse", candidates=[_entry("m-1", "Mouse", 1_000)]),
    ]
    invalid = {
        "recommendations": [
            {"selections": {"computer": "c-1", "mouse": "missing"}, "rationale": "bad"},
            {"selections": {"computer": "c-1", "mouse": "m-1"}, "rationale": "good"},
        ],
        "summary": "summary",
    }
    with patch.object(llm, "_chat_completion", return_value=invalid):
        with pytest.raises(llm.LLMError, match="BUNDLE_ANALYSIS_CANDIDATE_INVALID"):
            llm.analyze_multi_product_sets(groups, budget_paise=10_000)


def test_shared_budget_route_is_fast_only_and_returns_canonical_sets() -> None:
    extraction = QueryExtraction(
        action=Action.SEARCH,
        multi_product_budget={
            "enabled": True,
            "item_queries": ["computer", "mouse", "keyboard"],
            "total_budget_paise": 10_000,
            "budget_currency": "INR",
        },
    )
    groups = [
        MultiProductCandidateGroup(
            item_query="computer", candidates=[_entry("c-1", "Computer", 4_000)]
        ),
        MultiProductCandidateGroup(item_query="mouse", candidates=[_entry("m-1", "Mouse", 1_000)]),
        MultiProductCandidateGroup(
            item_query="keyboard", candidates=[_entry("k-1", "Keyboard", 2_000)]
        ),
    ]
    draft = llm.BundleAnalysisDraft(
        recommendations=[
            llm.BundleRecommendationDraft(
                selections={"computer": "c-1", "mouse": "m-1", "keyboard": "k-1"},
                rationale="Lowest combined price.",
            ),
            llm.BundleRecommendationDraft(
                selections={"computer": "c-1", "mouse": "m-1", "keyboard": "k-1"},
                rationale="Best available complete set.",
            ),
        ],
        summary="All three product types fit the shared budget.",
    )

    with (
        patch("fkgrid.orchestrator.llm.extract_query", return_value=extraction),
        patch("fkgrid.orchestrator.catalog.fast_multi_product_candidates", return_value=groups),
        patch("fkgrid.orchestrator.llm.analyze_multi_product_sets", return_value=draft),
        patch(
            "fkgrid.orchestrator.load_search_mode",
            side_effect=AssertionError("mode toggle was consulted"),
        ),
        patch(
            "fkgrid.orchestrator.catalog.search",
            side_effect=AssertionError("normal search was called"),
        ),
    ):
        result = handle_turn(
            TurnRequest(
                session_id="multi-product-budget-orchestrator-test",
                message="computer, mouse, keyboard for 10000 rupees total",
            )
        )

    assert result.status.value == "OK"
    assert result.multi_product_result is not None
    assert result.multi_product_result.search_mode == "fast"
    assert len(result.multi_product_result.recommendations) == 2
    assert result.multi_product_result.recommendations[0].total_price_paise == 7_000
    assert [step.stage for step in result.trace].count("multi_product_fast_candidates") == 1
    assert "multi_product_gemma_analysis" in [step.stage for step in result.trace]
