import unittest

from fastapi.testclient import TestClient

from fkgrid.agentic.contracts import (
    Constraint,
    ConstraintOperator,
    QueryState,
    SearchRequest,
)
from fkgrid.agentic.gateway import (
    FakeModelGateway,
    Gemma4ModelAdapter,
    UrllibJsonTransport,
)
from fkgrid.api.catalog import FixtureCatalogPort
from fkgrid.api.main import create_app
from fkgrid.api.runtime import ApiRuntime, demo_compatibility


class FastApiWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime = ApiRuntime(
            gateway=FakeModelGateway(),
            model_mode="fake",
            model_alias=Gemma4ModelAdapter.default_model_alias,
            protocol="test",
        )
        self.client = TestClient(create_app(runtime))

    def test_openapi_and_swagger_routes_are_available(self) -> None:
        openapi = self.client.get("/openapi.json")
        self.assertEqual(openapi.status_code, 200)
        schema = openapi.json()
        docs = self.client.get("/docs")
        self.assertEqual(docs.status_code, 200)
        self.assertIn("Chat with the shopper agent", docs.text)
        self.assertIn("/v1/sessions/", docs.text)
        self.assertIn("fkgrid-chat-input", docs.text)
        self.assertIn("/v1/sessions/{session_id}/turns", schema["paths"])
        self.assertIn("/v1/catalog/facets", schema["paths"])
        self.assertIn("examples", schema["components"]["schemas"]["ApiTurnRequest"])
        self.assertEqual(schema["info"]["title"], "FK GRiD Shopper Agentic API")

    def test_swagger_turns_use_the_existing_orchestrator_and_versions(self) -> None:
        created = self.client.post("/v1/sessions", json={"session_id": "swagger-session"})
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["state_version"], 0)

        search = self.client.post(
            "/v1/sessions/swagger-session/turns",
            json={
                "client_turn_id": "swagger-turn-1",
                "idempotency_key": "swagger-key-1",
                "message": "Find a shirt size m",
            },
        )
        self.assertEqual(search.status_code, 200)
        self.assertEqual(search.json()["http_status"], 200)
        self.assertEqual(len(search.json()["response"]["search_entries"]), 5)

        state = self.client.get("/v1/sessions/swagger-session").json()
        details = self.client.post(
            "/v1/sessions/swagger-session/turns",
            json={
                "client_turn_id": "swagger-turn-2",
                "idempotency_key": "swagger-key-2",
                "expected_state_version": state["state_version"],
                "expected_cart_version": state["cart_version"],
                "ui_action": {
                    "action": "PRODUCT_DETAILS",
                    "payload": {"references": [{"kind": "ORDINAL", "value": "1"}]},
                },
            },
        )
        self.assertEqual(details.status_code, 200)
        self.assertEqual(
            details.json()["response"]["terminal_state"],
            "ANSWERED_WITH_PRODUCT_DETAILS",
        )

    def test_swagger_exposes_rich_fixture_catalog_and_facets(self) -> None:
        page = self.client.get("/v1/catalog", params={"limit": 20})
        self.assertEqual(page.status_code, 200)
        body = page.json()
        self.assertEqual(body["profile"], "synthetic-shopping-fixture-v1")
        self.assertEqual(body["total"], 600)
        self.assertEqual(len(body["items"]), 20)
        self.assertGreaterEqual(len(body["items"][0]["facts"]), 10)

        facets = self.client.get("/v1/catalog/facets")
        self.assertEqual(facets.status_code, 200)
        self.assertGreaterEqual(len(facets.json()["facets"]["category"]), 10)
        self.assertGreaterEqual(len(facets.json()["facets"]["brand"]), 6)

    def test_fixture_catalog_applies_hard_size_constraint_and_keeps_identity_tuple(self) -> None:
        runtime = ApiRuntime(
            gateway=FakeModelGateway(),
            model_mode="fake",
            model_alias=Gemma4ModelAdapter.default_model_alias,
            protocol="test",
        )
        catalog = FixtureCatalogPort(runtime.catalog())
        compatibility = demo_compatibility(runtime.model_alias)
        query_state = QueryState(
            state_version=0,
            catalog_version=compatibility.catalog_version,
            index_version=compatibility.index_version,
            lexicon_version=compatibility.lexicon_version,
            hard_constraints=[
                Constraint(
                    field_id="size",
                    operator=ConstraintOperator.EQ,
                    values=["size_m"],
                    provenance_turn_id="test",
                    explicit=True,
                )
            ],
        )
        result = catalog.search(
            SearchRequest(
                session_id="catalog-test",
                query_state=query_state,
                top_k=10,
                compatibility_tuple=compatibility,
            ),
            300,
        )
        self.assertEqual(result.status.value, "OK")
        self.assertGreater(result.eligible_count, 0)
        for entry in result.entries:
            size_fact = next(fact for fact in entry.facts if fact.label == "size")
            self.assertEqual(size_fact.typed_value, "size_m")
            self.assertTrue(entry.binding.product_id)
            self.assertTrue(entry.binding.sku_id)
            self.assertTrue(entry.binding.offer_id)

        price_state = query_state.model_copy(
            update={
                "hard_constraints": [
                    Constraint(
                        field_id="price",
                        operator=ConstraintOperator.LTE,
                        values=[150000],
                        provenance_turn_id="test-price",
                        explicit=True,
                    )
                ]
            }
        )
        price_result = catalog.search(
            SearchRequest(
                session_id="catalog-test",
                query_state=price_state,
                top_k=10,
                compatibility_tuple=compatibility,
            ),
            300,
        )
        self.assertGreater(price_result.eligible_count, 0)
        for entry in price_result.entries:
            price_fact = next(fact for fact in entry.facts if fact.label == "price")
            self.assertLessEqual(price_fact.typed_value.amount_paise, 150000)

    def test_chat_uses_explicit_category_and_ranks_closest_available_color(self) -> None:
        runtime = ApiRuntime(
            gateway=FakeModelGateway(),
            model_mode="fake",
            model_alias=Gemma4ModelAdapter.default_model_alias,
            protocol="test",
        )
        tshirt_colors = {
            fact.typed_value
            for entry in runtime.catalog()
            if any(
                fact.label == "category" and fact.typed_value == "tshirts"
                for fact in entry.facts
            )
            for fact in entry.facts
            if fact.label == "color"
        }
        self.assertNotIn("red", tshirt_colors)
        self.assertIn("maroon", tshirt_colors)

        client = TestClient(create_app(runtime))
        created = client.post("/v1/sessions", json={"session_id": "color-session"})
        self.assertEqual(created.status_code, 201)
        first = client.post(
            "/v1/sessions/color-session/turns",
            json={
                "client_turn_id": "color-turn-1",
                "idempotency_key": "color-key-1",
                "message": "recommend me some red t-shirts",
            },
        )
        self.assertEqual(first.status_code, 200)
        first_body = first.json()["response"]
        self.assertIn("REQUESTED_COLOR_NOT_IN_DATASET", first_body["warnings"])
        self.assertIn("closest available colors", first_body["summary"])
        first_colors = [
            next(fact["typed_value"] for fact in entry["facts"] if fact["label"] == "color")
            for entry in first_body["search_entries"]
        ]
        first_categories = [
            next(fact["typed_value"] for fact in entry["facts"] if fact["label"] == "category")
            for entry in first_body["search_entries"]
        ]
        self.assertEqual(first_colors[0], "maroon")
        self.assertEqual(set(first_categories), {"tshirts"})

        state = client.get("/v1/sessions/color-session").json()
        second = client.post(
            "/v1/sessions/color-session/turns",
            json={
                "client_turn_id": "color-turn-2",
                "idempotency_key": "color-key-2",
                "expected_state_version": state["state_version"],
                "expected_cart_version": state["cart_version"],
                "message": "shoes",
            },
        )
        self.assertEqual(second.status_code, 200)
        second_entries = second.json()["response"]["search_entries"]
        second_categories = [
            next(fact["typed_value"] for fact in entry["facts"] if fact["label"] == "category")
            for entry in second_entries
        ]
        self.assertEqual(set(second_categories), {"sneakers"})
        self.assertTrue(all("T-Shirt" not in entry["title"] for entry in second_entries))

    def test_sessions_are_isolated(self) -> None:
        first = self.client.post("/v1/sessions", json={"session_id": "session-one"})
        second = self.client.post("/v1/sessions", json={"session_id": "session-two"})
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        search = self.client.post(
            "/v1/sessions/session-one/turns",
            json={"message": "Find a laptop", "idempotency_key": "isolation-key"},
        )
        self.assertEqual(search.status_code, 200)
        untouched = self.client.get("/v1/sessions/session-two").json()
        self.assertEqual(untouched["state_version"], 0)
        self.assertIsNone(untouched["acknowledged_result_set_id"])

    def test_live_environment_defaults_to_deepinfra_transport_and_gemma_alias(self) -> None:
        environment = {
            "DEEPINFRA_API_KEY": "test-only",
            "FKGRID_MODEL_MODE": "live",
        }
        runtime = ApiRuntime.from_environment(environment=environment)
        self.assertEqual(runtime.model_mode, "live")
        self.assertEqual(runtime.protocol, "deepinfra")
        self.assertEqual(runtime.model_alias, "google/gemma-4-26b-a4b-it")
        self.assertEqual(runtime.intent_budget_ms, 1800)
        self.assertIsInstance(runtime.gateway, Gemma4ModelAdapter)
        self.assertIsInstance(runtime.gateway.transport, UrllibJsonTransport)
        self.assertEqual(
            runtime.gateway.transport.endpoint,
            "https://api.deepinfra.com/v1/openai/chat/completions",
        )
        self.assertEqual(runtime.gateway.provider_name, "deepinfra")
        self.assertNotEqual(runtime.model_mode, "fake")

    def test_intent_budget_can_be_overridden_for_diagnostic_swagger_runs(self) -> None:
        runtime = ApiRuntime.from_environment(
            environment={
                "DEEPINFRA_API_KEY": "test-only",
                "FKGRID_MODEL_MODE": "live",
                "FKGRID_INTENT_BUDGET_MS": "5000",
            }
        )
        self.assertEqual(runtime.intent_budget_ms, 5000)

    def test_missing_live_configuration_does_not_silently_switch_to_fake(self) -> None:
        runtime = ApiRuntime.from_environment(
            environment={"FKGRID_MODEL_MODE": "live", "FKGRID_MODEL_PROTOCOL": "deepinfra"}
        )
        self.assertEqual(runtime.model_mode, "unavailable")
        self.assertEqual(runtime.gateway.provider_name, "unconfigured")
        self.assertNotEqual(runtime.model_mode, "fake")


if __name__ == "__main__":
    unittest.main()
