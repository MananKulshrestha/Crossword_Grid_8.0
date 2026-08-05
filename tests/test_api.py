import unittest

from fastapi.testclient import TestClient

from fkgrid.agentic.contracts import (
    Action,
    Constraint,
    ConstraintOperator,
    ModelCallType,
    ModelResponse,
    ModelStatus,
    QueryState,
    SearchRequest,
    ValidationIssue,
)
from fkgrid.agentic.gateway import (
    FakeModelGateway,
    Gemma4ModelAdapter,
    UrllibJsonTransport,
)
from fkgrid.api.catalog import FixtureCatalogPort
from fkgrid.api.main import create_app
from fkgrid.api.runtime import ApiRuntime, UnavailableModelGateway, demo_compatibility


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
        self.assertIn("Cart disabled", docs.text)
        self.assertIn("/v1/sessions/{session_id}/turns", schema["paths"])
        self.assertIn("/v1/sessions/{session_id}/trace", schema["paths"])
        self.assertIn("/v1/catalog/facets", schema["paths"])
        self.assertIn("examples", schema["components"]["schemas"]["ApiTurnRequest"])
        self.assertEqual(schema["info"]["title"], "FK GRiD Shopper Agentic API")

    def test_natural_language_controls_bypass_model_intent_extraction(self) -> None:
        gateway = FakeModelGateway()
        runtime = ApiRuntime(
            gateway=gateway,
            model_mode="fake",
            model_alias=Gemma4ModelAdapter.default_model_alias,
            protocol="test",
        )
        client = TestClient(create_app(runtime))
        self.assertEqual(
            client.post("/v1/sessions", json={"session_id": "control-session"}).status_code,
            201,
        )

        controls = (
            ("show-cart", "Yeah so show me the cart. What all is there in it?", Action.SHOW_CART),
            ("help", "What else can you do?", Action.HELP),
            ("reset", "Start a new search", Action.RESET_SEARCH),
        )
        for turn_id, message, expected_action in controls:
            response = client.post(
                "/v1/sessions/control-session/turns",
                json={
                    "client_turn_id": turn_id,
                    "idempotency_key": f"{turn_id}-key",
                    "message": message,
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["response"]["action"], expected_action.value)

        self.assertEqual(gateway.calls, [])

    def test_fixture_runtime_reports_cart_readiness_without_a_database_probe(self) -> None:
        fixture_status = self.client.get("/v1/model")
        self.assertEqual(fixture_status.status_code, 200)
        self.assertEqual(fixture_status.json()["cart_mode"], "fixture")
        self.assertTrue(fixture_status.json()["cart_ready"])
        self.assertIsNone(fixture_status.json()["cart_error"])

        database_runtime = ApiRuntime.from_environment(
            {
                "FKGRID_MODEL_MODE": "fake",
                "FKGRID_CART_MODE": "database",
                "FKGRID_SPEECH_MODE": "disabled",
            }
        )
        database_client = TestClient(create_app(database_runtime))
        database_status = database_client.get("/v1/model")
        self.assertEqual(database_status.status_code, 200)
        self.assertEqual(database_status.json()["cart_mode"], "database")
        self.assertFalse(database_status.json()["cart_ready"])
        self.assertEqual(
            database_status.json()["cart_error"],
            "CART_CATALOG_VERSION_MISMATCH",
        )
        self.assertFalse(database_client.get("/readyz").json()["ready"])

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
        detail_events = details.json()["trace"]["events"]
        eligibility_event = next(
            event
            for event in detail_events
            if event["logical_name"] == "check_commerce_eligibility"
        )
        self.assertEqual(eligibility_event["status"], "ALLOWED")
        self.assertEqual(eligibility_event["safe_metadata"]["tool_output"]["eligible"], True)
        self.assertIn("route_primary_action", [event["logical_name"] for event in detail_events])

    def test_active_results_rehydrate_full_entries_with_display_positions(self) -> None:
        created = self.client.post("/v1/sessions", json={"session_id": "active-results-session"})
        self.assertEqual(created.status_code, 201)

        search = self.client.post(
            "/v1/sessions/active-results-session/turns",
            json={
                "client_turn_id": "active-results-turn-1",
                "idempotency_key": "active-results-key-1",
                "message": "Find a laptop under 80000",
            },
        )
        self.assertEqual(search.status_code, 200)
        search_response = search.json()["response"]

        active = self.client.get("/v1/sessions/active-results-session/active-results")
        self.assertEqual(active.status_code, 200)
        payload = active.json()
        self.assertEqual(payload["result_set_id"], search_response["result_set_id"])
        self.assertEqual(len(payload["items"]), 5)
        first = payload["items"][0]
        self.assertEqual(first["display_position"], 1)
        self.assertTrue(first["title"])
        labels = {fact["label"] for fact in first["facts"]}
        self.assertIn("brand", labels)
        self.assertIn("price", labels)

    def test_trace_exposes_structured_model_tool_output_and_recent_memory(self) -> None:
        created = self.client.post("/v1/sessions", json={"session_id": "trace-session"})
        self.assertEqual(created.status_code, 201)

        first = self.client.post(
            "/v1/sessions/trace-session/turns",
            json={
                "client_turn_id": "trace-turn-1",
                "message": "Find a shirt size m",
                "idempotency_key": "trace-key-1",
            },
        )
        self.assertEqual(first.status_code, 200)
        first_trace = first.json()["trace"]
        intent_event = next(
            event
            for event in first_trace["events"]
            if event["logical_name"] == "resolve_intent_and_delta"
        )
        self.assertEqual(
            intent_event["safe_metadata"]["structured_output"]["primary_action"],
            "SEARCH",
        )
        self.assertTrue(intent_event["safe_metadata"]["call_id"])
        self.assertEqual(intent_event["safe_metadata"]["status"], "OK")
        search_event = next(
            event
            for event in first_trace["events"]
            if event["logical_name"] == "search_catalog"
        )
        self.assertEqual(search_event["safe_metadata"]["tool_output"]["status"], "OK")
        self.assertTrue(search_event["safe_metadata"]["tool_run_id"])
        self.assertTrue(search_event["safe_metadata"]["trace_span_id"])

        second = self.client.post(
            "/v1/sessions/trace-session/turns",
            json={
                "client_turn_id": "trace-turn-2",
                "message": "Find another shirt",
                "idempotency_key": "trace-key-2",
            },
        )
        self.assertEqual(second.status_code, 200)
        enhancement_event = next(
            event
            for event in second.json()["trace"]["events"]
            if event["logical_name"] == "enhance_chat_query"
        )
        self.assertEqual(enhancement_event["safe_metadata"]["recent_turn_count"], 1)
        self.assertEqual(
            enhancement_event["safe_metadata"]["recent_turn_ids"],
            ["trace-turn-1"],
        )
        self.assertTrue(enhancement_event["safe_metadata"]["recent_memory_used"])
        self.assertNotIn("recent_turn_context", enhancement_event["safe_metadata"])

        session = self.client.get("/v1/sessions/trace-session")
        self.assertEqual(len(session.json()["recent_turns"]), 2)
        self.assertEqual(session.json()["recent_turns"][0]["user_query"], "Find a shirt size m")
        self.assertEqual(
            session.json()["recent_turns"][0]["assistant_summary"],
            first.json()["response"]["summary"],
        )
        history = self.client.get("/v1/sessions/trace-session/trace")
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()["trace_count"], 2)
        self.assertEqual(len(history.json()["traces"]), 2)
        history_intent = next(
            event
            for event in history.json()["traces"][0]["events"]
            if event["logical_name"] == "resolve_intent_and_delta"
        )
        self.assertEqual(
            history_intent["safe_metadata"]["structured_output"]["primary_action"],
            "SEARCH",
        )

    def test_trace_exposes_safe_invalid_model_diagnostics_without_raw_output(self) -> None:
        runtime = ApiRuntime(
            gateway=FakeModelGateway(),
            model_mode="live",
            model_alias=Gemma4ModelAdapter.default_model_alias,
            protocol="test",
        )
        runtime.gateway.queue_response(
            ModelCallType.RESOLVE_INTENT_AND_DELTA,
            ModelResponse(
                call_id="invalid-model-call",
                status=ModelStatus.INVALID_OUTPUT,
                output_payload={"primary_action": "NOT_A_REAL_ACTION", "unsafe field": "secret"},
                raw_output_hash="raw-hash",
                input_hash="input-hash",
                output_hash="output-hash",
                provider_name="test-provider",
                model_alias=Gemma4ModelAdapter.default_model_alias,
                prompt_id="intent_v3",
                prompt_version="3",
                latency_ms=12,
                validation_issues=[
                    ValidationIssue(
                        code="OUTPUT_SCHEMA_INVALID",
                        path="output_payload.primary_action",
                        safe_message="Provider output failed the declared contract.",
                    )
                ],
            ),
        )
        runtime.gateway.queue_response(
            ModelCallType.RESOLVE_INTENT_AND_DELTA,
            ModelResponse(
                call_id="invalid-model-repair-call",
                status=ModelStatus.INVALID_OUTPUT,
                output_payload=None,
                input_hash="repair-input-hash",
                provider_name="test-provider",
                model_alias=Gemma4ModelAdapter.default_model_alias,
                prompt_id="intent_v3",
                prompt_version="3",
                latency_ms=8,
            ),
        )
        client = TestClient(create_app(runtime))
        client.post("/v1/sessions", json={"session_id": "invalid-trace-session"})

        response = client.post(
            "/v1/sessions/invalid-trace-session/turns",
            json={
                "message": "find something unusual",
                "idempotency_key": "invalid-trace-key",
            },
        )
        self.assertEqual(response.status_code, 200)
        intent_event = next(
            event
            for event in response.json()["trace"]["events"]
            if event["logical_name"] == "resolve_intent_and_delta"
        )
        metadata = intent_event["safe_metadata"]
        self.assertEqual(metadata["status"], "INVALID_OUTPUT")
        self.assertEqual(metadata["output_summary"]["output_hash"], "output-hash")
        self.assertIn("<redacted>", metadata["output_summary"]["keys"])
        self.assertEqual(metadata["output_summary"]["shape"]["type"], "object")
        self.assertEqual(metadata["structured_output"], None)
        self.assertEqual(metadata["validation_issues"][0]["path"], "output_payload.primary_action")
        self.assertNotIn("unsafe field", str(metadata))

    def test_failed_interpretation_is_traced_but_excluded_from_recent_memory(self) -> None:
        runtime = ApiRuntime(
            gateway=UnavailableModelGateway(
                Gemma4ModelAdapter.default_model_alias,
                "MODEL_TIMEOUT",
            ),
            model_mode="live",
            model_alias=Gemma4ModelAdapter.default_model_alias,
            protocol="test",
        )
        client = TestClient(create_app(runtime))
        client.post("/v1/sessions", json={"session_id": "failed-memory-session"})

        failed = client.post(
            "/v1/sessions/failed-memory-session/turns",
            json={
                "client_turn_id": "failed-memory-turn",
                "idempotency_key": "failed-memory-key",
                "message": "blorf qzx",
            },
        )
        self.assertEqual(failed.json()["response"]["terminal_state"], "INTERPRETATION_UNAVAILABLE")
        failed_commit = next(
            event
            for event in failed.json()["trace"]["events"]
            if event["logical_name"] == "commit_turn"
        )
        self.assertFalse(failed_commit["safe_metadata"]["recent_memory_recorded"])
        self.assertEqual(
            failed_commit["safe_metadata"]["recent_memory_exclusion"],
            "TERMINAL_STATE_NOT_MEMORY_ELIGIBLE",
        )
        self.assertEqual(
            client.get("/v1/sessions/failed-memory-session").json()["recent_turns"],
            [],
        )
        trace_history = client.get("/v1/sessions/failed-memory-session/trace").json()
        self.assertEqual(trace_history["trace_count"], 1)

        recovered = client.post(
            "/v1/sessions/failed-memory-session/turns",
            json={
                "client_turn_id": "failed-memory-recovery-turn",
                "idempotency_key": "failed-memory-recovery-key",
                "message": "red tshirt size L",
            },
        )
        self.assertEqual(
            recovered.json()["response"]["terminal_state"],
            "ANSWERED_WITH_GROUNDED_RESULTS",
        )
        enhancement = next(
            event
            for event in recovered.json()["trace"]["events"]
            if event["logical_name"] == "enhance_chat_query"
        )
        self.assertEqual(enhancement["safe_metadata"]["recent_turn_count"], 0)
        self.assertEqual(enhancement["safe_metadata"]["recent_turn_ids"], [])
        session = client.get("/v1/sessions/failed-memory-session").json()
        self.assertEqual(
            [item["user_query"] for item in session["recent_turns"]],
            ["red tshirt size L"],
        )

    def test_clarification_is_authoritative_but_not_added_to_recent_memory(self) -> None:
        runtime = ApiRuntime(
            gateway=FakeModelGateway(),
            model_mode="fake",
            model_alias=Gemma4ModelAdapter.default_model_alias,
            protocol="test",
        )
        client = TestClient(create_app(runtime))
        client.post("/v1/sessions", json={"session_id": "clarification-memory-session"})

        clarification = client.post(
            "/v1/sessions/clarification-memory-session/turns",
            json={
                "client_turn_id": "clarification-memory-turn",
                "idempotency_key": "clarification-memory-key",
                "message": "show product details",
            },
        )
        self.assertEqual(
            clarification.json()["response"]["terminal_state"],
            "CLARIFICATION_REQUIRED",
        )
        session = client.get("/v1/sessions/clarification-memory-session").json()
        self.assertEqual(session["recent_turns"], [])
        self.assertIsNotNone(session["query_state"]["pending_clarification"])

        ambiguous = client.post(
            "/v1/sessions/clarification-memory-session/turns",
            json={
                "client_turn_id": "clarification-memory-ambiguous-turn",
                "idempotency_key": "clarification-memory-ambiguous-key",
                "message": "what about it",
            },
        )
        self.assertEqual(
            ambiguous.json()["response"]["terminal_state"],
            "CLARIFICATION_REQUIRED",
        )
        self.assertEqual(
            client.get("/v1/sessions/clarification-memory-session").json()["recent_turns"],
            [],
        )

        explicit = client.post(
            "/v1/sessions/clarification-memory-session/turns",
            json={
                "client_turn_id": "clarification-memory-explicit-turn",
                "idempotency_key": "clarification-memory-explicit-key",
                "message": "blue shoes",
            },
        )
        self.assertEqual(
            explicit.json()["response"]["terminal_state"],
            "ANSWERED_WITH_GROUNDED_RESULTS",
        )
        final_session = client.get("/v1/sessions/clarification-memory-session").json()
        self.assertIsNone(final_session["query_state"]["pending_clarification"])
        self.assertEqual(
            [item["user_query"] for item in final_session["recent_turns"]],
            ["blue shoes"],
        )

    def test_no_match_filters_remain_state_but_do_not_consume_recent_memory(self) -> None:
        runtime = ApiRuntime(
            gateway=UnavailableModelGateway(
                Gemma4ModelAdapter.default_model_alias,
                "MODEL_TIMEOUT",
            ),
            model_mode="live",
            model_alias=Gemma4ModelAdapter.default_model_alias,
            protocol="test",
        )
        client = TestClient(create_app(runtime))
        client.post("/v1/sessions", json={"session_id": "no-match-memory-session"})

        no_match = client.post(
            "/v1/sessions/no-match-memory-session/turns",
            json={
                "client_turn_id": "no-match-memory-turn",
                "idempotency_key": "no-match-memory-key",
                "message": "Blue shoes size UK 9",
            },
        )
        self.assertEqual(no_match.json()["response"]["terminal_state"], "NO_ELIGIBLE_MATCH")
        session = client.get("/v1/sessions/no-match-memory-session").json()
        self.assertEqual(session["recent_turns"], [])
        constraints = {
            item["field_id"]: item["values"]
            for item in session["query_state"]["hard_constraints"]
        }
        self.assertEqual(constraints["size"], ["size_9"])

        recovered = client.post(
            "/v1/sessions/no-match-memory-session/turns",
            json={
                "client_turn_id": "no-match-memory-recovery-turn",
                "idempotency_key": "no-match-memory-recovery-key",
                "message": "Blue shoes size UK 7",
            },
        )
        self.assertEqual(
            recovered.json()["response"]["terminal_state"],
            "ANSWERED_WITH_GROUNDED_RESULTS",
        )
        enhancement = next(
            event
            for event in recovered.json()["trace"]["events"]
            if event["logical_name"] == "enhance_chat_query"
        )
        self.assertEqual(enhancement["safe_metadata"]["recent_turn_ids"], [])

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

    def test_remembered_size_does_not_create_reference_clarification(self) -> None:
        runtime = ApiRuntime(
            gateway=FakeModelGateway(),
            model_mode="fake",
            model_alias=Gemma4ModelAdapter.default_model_alias,
            protocol="test",
        )
        client = TestClient(create_app(runtime))
        created = client.post("/v1/sessions", json={"session_id": "remember-size-session"})
        self.assertEqual(created.status_code, 201)

        messages = (
            "my tshirt size is L remember that",
            "green tshirt",
            "show me some green tshirts",
        )
        state_version = 0
        cart_version = 0
        responses = []
        for index, message in enumerate(messages, start=1):
            turn = client.post(
                "/v1/sessions/remember-size-session/turns",
                json={
                    "client_turn_id": f"remember-size-turn-{index}",
                    "idempotency_key": f"remember-size-key-{index}",
                    "expected_state_version": state_version,
                    "expected_cart_version": cart_version,
                    "message": message,
                },
            )
            self.assertEqual(turn.status_code, 200)
            body = turn.json()["response"]
            responses.append(body)
            session = client.get("/v1/sessions/remember-size-session").json()
            state_version = session["state_version"]
            cart_version = session["cart_version"]

        for response in responses:
            self.assertEqual(response["terminal_state"], "ANSWERED_WITH_GROUNDED_RESULTS")
            self.assertNotEqual(response["clarification_reason_code"], "REFERENCE_REQUIRED")
            self.assertEqual(len(response["search_entries"]), 5)
        for response in responses[1:]:
            for entry in response["search_entries"]:
                category = next(
                    fact["typed_value"]
                    for fact in entry["facts"]
                    if fact["label"] == "category"
                )
                size = next(
                    fact["typed_value"]
                    for fact in entry["facts"]
                    if fact["label"] == "size"
                )
                self.assertEqual(category, "tshirts")
                self.assertEqual(size, "size_l")

    def test_natural_language_add_first_result_to_cart(self) -> None:
        runtime = ApiRuntime(
            gateway=FakeModelGateway(),
            model_mode="fake",
            model_alias=Gemma4ModelAdapter.default_model_alias,
            protocol="test",
        )
        client = TestClient(create_app(runtime))
        created = client.post("/v1/sessions", json={"session_id": "cart-language-session"})
        self.assertEqual(created.status_code, 201)
        search = client.post(
            "/v1/sessions/cart-language-session/turns",
            json={
                "client_turn_id": "cart-language-search",
                "idempotency_key": "cart-language-search-key",
                "message": "Find a shirt size m",
            },
        )
        self.assertEqual(search.status_code, 200)
        search_response = search.json()["response"]
        first_binding = search_response["search_entries"][0]["binding"]
        state = client.get("/v1/sessions/cart-language-session").json()

        cart_turn = client.post(
            "/v1/sessions/cart-language-session/turns",
            json={
                "client_turn_id": "cart-language-add",
                "idempotency_key": "cart-language-add-key",
                "expected_state_version": state["state_version"],
                "expected_cart_version": state["cart_version"],
                "message": "put the first option in my cart",
            },
        )
        self.assertEqual(cart_turn.status_code, 200)
        response = cart_turn.json()["response"]
        self.assertEqual(response["terminal_state"], "CART_UPDATED")
        self.assertIsNotNone(response["cart"])
        self.assertEqual(response["cart"]["item_count"], 1)
        self.assertEqual(response["cart"]["items"][0]["binding"], first_binding)

    def test_numeric_result_shorthand_adds_the_third_acknowledged_result(self) -> None:
        runtime = ApiRuntime(
            gateway=FakeModelGateway(),
            model_mode="fake",
            model_alias=Gemma4ModelAdapter.default_model_alias,
            protocol="test",
        )
        client = TestClient(create_app(runtime))
        created = client.post("/v1/sessions", json={"session_id": "cart-numeric-session"})
        self.assertEqual(created.status_code, 201)
        search = client.post(
            "/v1/sessions/cart-numeric-session/turns",
            json={
                "client_turn_id": "cart-numeric-search",
                "idempotency_key": "cart-numeric-search-key",
                "message": "Find a shirt size m",
            },
        )
        self.assertEqual(search.status_code, 200)
        entries = search.json()["response"]["search_entries"]
        third_binding = entries[2]["binding"]
        state = client.get("/v1/sessions/cart-numeric-session").json()

        cart_turn = client.post(
            "/v1/sessions/cart-numeric-session/turns",
            json={
                "client_turn_id": "cart-numeric-add",
                "idempotency_key": "cart-numeric-add-key",
                "expected_state_version": state["state_version"],
                "expected_cart_version": state["cart_version"],
                "message": "add 3 to the cart",
            },
        )
        self.assertEqual(cart_turn.status_code, 200)
        response = cart_turn.json()["response"]
        self.assertEqual(response["terminal_state"], "CART_UPDATED")
        self.assertEqual(response["cart"]["item_count"], 1)
        self.assertEqual(response["cart"]["items"][0]["binding"], third_binding)

    def test_catalog_fallback_answers_recognized_query_when_live_model_is_unavailable(self) -> None:
        runtime = ApiRuntime(
            gateway=UnavailableModelGateway(
                Gemma4ModelAdapter.default_model_alias,
                "MODEL_TIMEOUT",
            ),
            model_mode="live",
            model_alias=Gemma4ModelAdapter.default_model_alias,
            protocol="test",
        )
        client = TestClient(create_app(runtime))
        created = client.post("/v1/sessions", json={"session_id": "catalog-fallback-session"})
        self.assertEqual(created.status_code, 201)

        turn = client.post(
            "/v1/sessions/catalog-fallback-session/turns",
            json={
                "client_turn_id": "catalog-fallback-turn",
                "idempotency_key": "catalog-fallback-key",
                "message": "red tshirt size L",
            },
        )
        self.assertEqual(turn.status_code, 200)
        response = turn.json()["response"]
        self.assertEqual(response["action"], "SEARCH")
        self.assertEqual(response["terminal_state"], "ANSWERED_WITH_GROUNDED_RESULTS")
        self.assertEqual(len(response["search_entries"]), 5)
        self.assertIn("REQUESTED_COLOR_NOT_IN_DATASET", response["warnings"])

    def test_catalog_fallback_supports_uk_shoe_size(self) -> None:
        runtime = ApiRuntime(
            gateway=UnavailableModelGateway(
                Gemma4ModelAdapter.default_model_alias,
                "MODEL_TIMEOUT",
            ),
            model_mode="live",
            model_alias=Gemma4ModelAdapter.default_model_alias,
            protocol="test",
        )
        client = TestClient(create_app(runtime))
        client.post("/v1/sessions", json={"session_id": "uk-shoe-size-session"})

        turn = client.post(
            "/v1/sessions/uk-shoe-size-session/turns",
            json={
                "client_turn_id": "uk-shoe-size-turn",
                "idempotency_key": "uk-shoe-size-key",
                "message": "Blue shoes size UK 9",
            },
        )
        self.assertEqual(turn.status_code, 200)
        body = turn.json()
        response = body["response"]
        self.assertEqual(response["action"], "SEARCH")
        self.assertEqual(response["terminal_state"], "NO_ELIGIBLE_MATCH")
        self.assertIn("NO_ELIGIBLE_MATCH", response["warnings"])
        state = client.get("/v1/sessions/uk-shoe-size-session").json()
        hard_constraints = {
            constraint["field_id"]: constraint["values"]
            for constraint in state["query_state"]["hard_constraints"]
        }
        self.assertEqual(hard_constraints["taxonomy_node_id"], ["sneakers"])
        self.assertEqual(hard_constraints["size"], ["size_9"])
        self.assertEqual(
            state["query_state"]["soft_preferences"][0]["values"],
            ["blue"],
        )
        self.assertIn(
            "deterministic_catalog_grammar",
            [event["logical_name"] for event in body["trace"]["events"]],
        )

        supported = client.post(
            "/v1/sessions/uk-shoe-size-session/turns",
            json={
                "client_turn_id": "uk-shoe-size-supported-turn",
                "idempotency_key": "uk-shoe-size-supported-key",
                "message": "Blue shoes size UK 7",
            },
        )
        self.assertEqual(supported.json()["response"]["terminal_state"], "ANSWERED_WITH_GROUNDED_RESULTS")

    def test_catalog_fallback_does_not_drop_unsupported_constraints(self) -> None:
        runtime = ApiRuntime(
            gateway=UnavailableModelGateway(
                Gemma4ModelAdapter.default_model_alias,
                "MODEL_TIMEOUT",
            ),
            model_mode="live",
            model_alias=Gemma4ModelAdapter.default_model_alias,
            protocol="test",
        )
        client = TestClient(create_app(runtime))
        client.post("/v1/sessions", json={"session_id": "catalog-fallback-unsafe-session"})

        turn = client.post(
            "/v1/sessions/catalog-fallback-unsafe-session/turns",
            json={
                "client_turn_id": "catalog-fallback-unsafe-turn",
                "idempotency_key": "catalog-fallback-unsafe-key",
                "message": "red cotton tshirt under 1500",
            },
        )
        self.assertEqual(turn.status_code, 200)
        self.assertEqual(
            turn.json()["response"]["terminal_state"],
            "INTERPRETATION_UNAVAILABLE",
        )

    def test_cart_fallback_adds_numeric_acknowledged_result_when_model_unavailable(self) -> None:
        runtime = ApiRuntime(
            gateway=UnavailableModelGateway(
                Gemma4ModelAdapter.default_model_alias,
                "MODEL_TIMEOUT",
            ),
            model_mode="live",
            model_alias=Gemma4ModelAdapter.default_model_alias,
            protocol="test",
        )
        client = TestClient(create_app(runtime))
        client.post("/v1/sessions", json={"session_id": "cart-fallback-session"})

        search = client.post(
            "/v1/sessions/cart-fallback-session/turns",
            json={
                "message": "red tshirt size L",
                "idempotency_key": "cart-fallback-search-key",
            },
        )
        self.assertEqual(search.json()["response"]["terminal_state"], "ANSWERED_WITH_GROUNDED_RESULTS")
        expected_binding = search.json()["response"]["search_entries"][2]["binding"]
        available_binding = search.json()["response"]["search_entries"][0]["binding"]

        cart = client.post(
            "/v1/sessions/cart-fallback-session/turns",
            json={
                "message": "add 3 to the cart",
                "idempotency_key": "cart-fallback-add-key",
            },
        )
        self.assertEqual(cart.status_code, 200)
        body = cart.json()
        self.assertEqual(body["response"]["terminal_state"], "ACTION_FAILED_WITH_REASON")
        self.assertIn("COMMERCE_POLICY_BLOCKED", body["response"]["warnings"])
        self.assertIn("unavailable", body["response"]["summary"])
        self.assertNotIn("INTERPRETATION_UNAVAILABLE", body["response"]["terminal_state"])
        self.assertIn(
            "deterministic_cart_grammar",
            [event["logical_name"] for event in body["trace"]["events"]],
        )
        eligibility_event = next(
            event
            for event in body["trace"]["events"]
            if event["logical_name"] == "check_cart_eligibility"
        )
        self.assertEqual(eligibility_event["status"], "UNAVAILABLE")
        self.assertEqual(
            [item["user_query"] for item in client.get("/v1/sessions/cart-fallback-session").json()["recent_turns"]],
            ["red tshirt size L"],
        )

        available_cart = client.post(
            "/v1/sessions/cart-fallback-session/turns",
            json={
                "message": "add 1 to the cart",
                "idempotency_key": "cart-fallback-add-available-key",
            },
        )
        self.assertEqual(available_cart.json()["response"]["terminal_state"], "CART_UPDATED")
        self.assertEqual(
            available_cart.json()["response"]["cart"]["items"][0]["binding"],
            available_binding,
        )

    def test_catalog_fallback_covers_reviewed_fixture_colors_and_categories(self) -> None:
        runtime = ApiRuntime(
            gateway=UnavailableModelGateway(
                Gemma4ModelAdapter.default_model_alias,
                "MODEL_TIMEOUT",
            ),
            model_mode="live",
            model_alias=Gemma4ModelAdapter.default_model_alias,
            protocol="test",
        )
        client = TestClient(create_app(runtime))
        client.post("/v1/sessions", json={"session_id": "catalog-fallback-vocabulary-session"})

        green = client.post(
            "/v1/sessions/catalog-fallback-vocabulary-session/turns",
            json={
                "client_turn_id": "catalog-fallback-green-turn",
                "idempotency_key": "catalog-fallback-green-key",
                "message": "show me some green tshirts",
            },
        )
        self.assertEqual(green.json()["response"]["terminal_state"], "ANSWERED_WITH_GROUNDED_RESULTS")
        self.assertTrue(
            all(
                any(fact["typed_value"] == "tshirts" for fact in entry["facts"] if fact["label"] == "category")
                for entry in green.json()["response"]["search_entries"]
            )
        )

        shoes = client.post(
            "/v1/sessions/catalog-fallback-vocabulary-session/turns",
            json={
                "client_turn_id": "catalog-fallback-shoes-turn",
                "idempotency_key": "catalog-fallback-shoes-key",
                "message": "shoes",
            },
        )
        self.assertEqual(shoes.json()["response"]["terminal_state"], "ANSWERED_WITH_GROUNDED_RESULTS")
        self.assertTrue(
            all(
                any(fact["typed_value"] == "sneakers" for fact in entry["facts"] if fact["label"] == "category")
                for entry in shoes.json()["response"]["search_entries"]
            )
        )

    def test_category_switch_drops_incompatible_apparel_size_before_cart_follow_up(self) -> None:
        runtime = ApiRuntime(
            gateway=UnavailableModelGateway(
                Gemma4ModelAdapter.default_model_alias,
                "MODEL_TIMEOUT",
            ),
            model_mode="live",
            model_alias=Gemma4ModelAdapter.default_model_alias,
            protocol="test",
        )
        client = TestClient(create_app(runtime))
        client.post("/v1/sessions", json={"session_id": "category-switch-session"})

        first = client.post(
            "/v1/sessions/category-switch-session/turns",
            json={
                "message": "red tshirt size L",
                "idempotency_key": "category-switch-search-key",
            },
        )
        self.assertEqual(
            first.json()["response"]["terminal_state"],
            "ANSWERED_WITH_GROUNDED_RESULTS",
        )

        shoes = client.post(
            "/v1/sessions/category-switch-session/turns",
            json={
                "message": "shoes",
                "idempotency_key": "category-switch-shoes-key",
            },
        )
        self.assertEqual(
            shoes.json()["response"]["terminal_state"],
            "ANSWERED_WITH_GROUNDED_RESULTS",
        )
        self.assertEqual(
            {
                next(fact["typed_value"] for fact in entry["facts"] if fact["label"] == "category")
                for entry in shoes.json()["response"]["search_entries"]
            },
            {"sneakers"},
        )

        cart = client.post(
            "/v1/sessions/category-switch-session/turns",
            json={
                "message": "add 1 to the cart",
                "idempotency_key": "category-switch-cart-key",
            },
        )
        self.assertNotEqual(cart.json()["response"]["terminal_state"], "INTERPRETATION_UNAVAILABLE")
        self.assertIn(
            "deterministic_cart_grammar",
            [event["logical_name"] for event in cart.json()["trace"]["events"]],
        )

    def test_explicit_quantity_ordinal_cart_phrase_is_deterministic(self) -> None:
        runtime = ApiRuntime(
            gateway=UnavailableModelGateway(
                Gemma4ModelAdapter.default_model_alias,
                "MODEL_TIMEOUT",
            ),
            model_mode="live",
            model_alias=Gemma4ModelAdapter.default_model_alias,
            protocol="test",
        )
        client = TestClient(create_app(runtime))
        client.post("/v1/sessions", json={"session_id": "quantity-cart-session"})
        search = client.post(
            "/v1/sessions/quantity-cart-session/turns",
            json={
                "message": "red tshirt size L",
                "idempotency_key": "quantity-search-key",
            },
        )
        self.assertEqual(search.json()["response"]["terminal_state"], "ANSWERED_WITH_GROUNDED_RESULTS")

        cart = client.post(
            "/v1/sessions/quantity-cart-session/turns",
            json={
                "message": "add 2 units of the first one",
                "idempotency_key": "quantity-cart-key",
            },
        )
        response = cart.json()["response"]
        self.assertEqual(response["terminal_state"], "CART_UPDATED")
        self.assertEqual(response["cart"]["items"][0]["quantity"], 2)
        self.assertIn(
            "deterministic_cart_grammar",
            [event["logical_name"] for event in cart.json()["trace"]["events"]],
        )

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
