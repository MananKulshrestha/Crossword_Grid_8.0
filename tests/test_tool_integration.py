import unittest

from fkgrid.agentic.contracts import (
    ProductDetails,
    SearchRequest,
    SearchResult,
)
from fkgrid.agentic.gateway import FakeModelGateway, Gemma4ModelAdapter
from fkgrid.api.runtime import ApiRuntime, demo_compatibility


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
        self.assertTrue(result.evidence_refs)
        self.assertTrue(any(fact.label == "Title" for fact in result.entries[0].facts))
        self.assertTrue(any(fact.label == "Prototype price" for fact in result.entries[0].facts))

        details = self.runtime.tooling.shopper_catalog.get_details(
            result.entries[0].binding, compatibility, deadline_ms=100
        )
        self.assertIsInstance(details, ProductDetails)
        self.assertEqual(details.status, "OK")
        self.assertEqual(details.binding.offer_id, result.entries[0].binding.offer_id)


if __name__ == "__main__":
    unittest.main()
