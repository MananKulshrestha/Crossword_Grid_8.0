import unittest

from fkgrid.agentic.contracts import (
    Availability,
    CommerceEligibility,
    Comparison,
    Constraint,
    ConstraintOperator,
    OnlineSearchResult,
    ProductDetails,
    SearchRequest,
    SearchResult,
    ValidatedResearch,
)
from fkgrid.agentic.gateway import FakeModelGateway, Gemma4ModelAdapter
from fkgrid.agentic.validation import hard_filter_hash as chat_hard_filter_hash
from fkgrid.api.runtime import ApiRuntime, demo_compatibility
from fkgrid.tools.integration import build_runtime_tooling


class RuntimeToolingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = ApiRuntime(
            gateway=FakeModelGateway(),
            model_mode="fake",
            model_alias=Gemma4ModelAdapter.default_model_alias,
            protocol="test",
        )
        self.runtime.create_session("tool-integration-session")
        self.snapshot = self.runtime.snapshot("tool-integration-session")

    def test_runtime_exposes_the_complete_non_cart_registry(self) -> None:
        names = self.runtime.tooling.tool_names

        self.assertEqual(len(names), 73)
        self.assertEqual(len(set(names)), 73)
        self.assertIn("search_catalog", names)
        self.assertIn("check_availability", names)
        self.assertNotIn("update_cart", names)
        self.assertNotIn("show_cart", names)
        self.assertIsNotNone(self.runtime.tooling.query_recovery)

    def test_catalog_adapter_returns_chat_contracts_and_grounded_facts(self) -> None:
        compatibility = demo_compatibility(self.runtime.model_alias)
        request = SearchRequest(
            session_id="tool-integration-session",
            query_state=self.snapshot.state,
            top_k=3,
            compatibility_tuple=compatibility,
        )
        result = self.runtime.tooling.shopper_catalog.search(request, deadline_ms=100)

        self.assertIsInstance(result, SearchResult)
        self.assertEqual(result.status, "OK")
        self.assertEqual(len(result.entries), 3)
        self.assertEqual(result.hard_filter_hash, chat_hard_filter_hash(self.snapshot.state))
        self.assertTrue(result.evidence_refs)
        self.assertTrue(any(fact.label == "Title" for fact in result.entries[0].facts))
        self.assertTrue(any(fact.label == "Prototype price" for fact in result.entries[0].facts))

        details = self.runtime.tooling.shopper_catalog.get_details(
            result.entries[0].binding, compatibility, deadline_ms=100
        )
        self.assertIsInstance(details, ProductDetails)
        self.assertEqual(details.status, "OK")
        self.assertEqual(details.binding.offer_id, result.entries[0].binding.offer_id)
        self.assertTrue(any(fact.evidence_refs for fact in details.facts))

        comparison = self.runtime.tooling.shopper_catalog.compare(
            [entry.binding for entry in result.entries[:2]], compatibility, deadline_ms=100
        )
        availability = self.runtime.tooling.shopper_catalog.check_availability(
            result.entries[0].binding, compatibility, deadline_ms=100
        )
        eligibility = self.runtime.tooling.shopper_catalog.check_eligibility(
            result.entries[0].binding, "PRODUCT_DETAILS", deadline_ms=100
        )
        self.assertIsInstance(comparison, Comparison)
        self.assertEqual(comparison.status, "OK")
        self.assertTrue(any(row.cells[0].evidence_refs for row in comparison.rows))
        self.assertIsInstance(availability, Availability)
        self.assertEqual(availability.status, "OK")
        self.assertIsInstance(eligibility, CommerceEligibility)
        self.assertTrue(eligibility.eligible)

        ranged_state = self.snapshot.state.model_copy(
            update={
                "hard_constraints": [
                    Constraint(
                        field_id="price",
                        operator=ConstraintOperator.RANGE,
                        values=[10_000, 30_000],
                        provenance_turn_id="range-turn",
                        explicit=True,
                    )
                ]
            }
        )
        ranged = self.runtime.tooling.shopper_catalog.search(
            SearchRequest(
                session_id="tool-integration-session",
                query_state=ranged_state,
                top_k=10,
                compatibility_tuple=compatibility,
            ),
            deadline_ms=100,
        )
        self.assertTrue(ranged.entries)
        for entry in ranged.entries:
            price_fact = next(fact for fact in entry.facts if fact.label == "Prototype price")
            price = price_fact.typed_value["amount_paise"] / 100
            self.assertGreaterEqual(price, 10_000)
            self.assertLessEqual(price, 30_000)

    def test_enabled_research_adapter_preserves_cited_sources(self) -> None:
        compatibility = demo_compatibility(self.runtime.model_alias)
        tooling = build_runtime_tooling(
            self.runtime.catalog_entries,
            compatibility,
            suggestion_secret=b"integration-test-secret",
            research_enabled=True,
            research_fixtures=[
                {
                    "url": "https://example.com/guide",
                    "domain": "example.com",
                    "title": "Example guide",
                    "extract": "A bounded, cited research extract.",
                }
            ],
        )

        decision = tooling.research.detect_need(
            {"explicit_research": True},
            "research current guidance",
            ["product_0001"],
        )
        search_result = tooling.research.search(decision, deadline_ms=100)

        self.assertIsInstance(search_result, OnlineSearchResult)
        self.assertEqual(search_result.status, "SUCCESS")
        source_id = search_result.sources[0].source_id
        validated = tooling.research.validate(
            decision,
            search_result,
            {
                "answer_summary": "The cited guidance is available.",
                "claims": [
                    {
                        "claim_id": "claim-1",
                        "text": "The guidance is cited.",
                        "support_source_ids": [source_id],
                        "conflict_source_ids": [],
                        "confidence": "MEDIUM",
                    }
                ],
            },
        )

        self.assertIsInstance(validated, ValidatedResearch)
        self.assertEqual(validated.status, "VALID")
        self.assertEqual(validated.claims[0].support_source_ids, [source_id])

    def test_suggestion_adapter_requires_the_signed_action_token(self) -> None:
        suggestion_set = self.runtime.tooling.suggestions.build_and_store(
            {
                "action": "SEARCH",
                "search_entries": [
                    entry.model_dump(mode="json") for entry in self.runtime.catalog_entries[:2]
                ],
            },
            self.snapshot,
            "trace-suggestion-1",
            deadline_ms=100,
        )

        assert suggestion_set is not None
        suggestion = suggestion_set.suggestions[0]
        request = {
            "session_id": self.snapshot.session_id,
            "suggestion_set_id": suggestion_set.suggestion_set_id,
            "suggestion_id": suggestion.suggestion_id,
            "signed_action_token": suggestion.signed_action_token,
            "expected_state_version": suggestion_set.state_version,
            "expected_cart_version": suggestion_set.cart_version,
        }
        selected = self.runtime.tooling.suggestions.select(request)
        self.assertEqual(selected.status, "VALID")

        stale = self.runtime.tooling.suggestions.select(
            {**request, "signed_action_token": "tampered-token"}
        )
        self.assertEqual(stale.status, "SUGGESTION_STALE")


if __name__ == "__main__":
    unittest.main()
