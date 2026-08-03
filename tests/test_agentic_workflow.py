from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fkgrid.agentic.contracts import (
    Action,
    ActiveResultBinding,
    Availability,
    CartSnapshot,
    CompatibilityTuple,
    Constraint,
    ConstraintOperator,
    Fact,
    FactScope,
    MemoryCandidate,
    PurchaseContext,
    Money,
    ProductBinding,
    QueryState,
    SearchEntry,
    TerminalState,
    TruthStatus,
    TurnRequest,
    TurnSnapshot,
    UiAction,
)
from fkgrid.agentic.fakes import (
    DeterministicEnhancer,
    FakeCatalogPort,
    FakeCartPort,
    FakeClock,
    FakeCommerceHistoryPort,
    FakeMemorySnapshotPort,
    FakeMarkdownPipeline,
    FakeRecoveryPort,
    FakeReferenceResolver,
    FakeResearchPort,
    FakeSuggestionPort,
    FakeTraceSink,
    InMemorySessionState,
    SequentialIds,
    empty_cart,
)
from fkgrid.agentic.gateway import FakeModelGateway
from fkgrid.agentic.gateway import (
    GeminiGenerateContentTransport,
    Gemma4ModelAdapter,
    PromptRegistry,
    UrllibJsonTransport,
)
from fkgrid.agentic.orchestrator import TurnOrchestrator
from fkgrid.agentic.validation import canonical_hash, merge_query_state


def compatibility() -> CompatibilityTuple:
    return CompatibilityTuple(
        contract_schema_version="agentic-contracts-v1",
        catalog_version="catalog-demo-v1",
        index_version="index-demo-v1",
        taxonomy_version="taxonomy-demo-v1",
        category_schema_version="schema-demo-v1",
        lexicon_version="lexicon-demo-v1",
        rank_policy_version="rank-v1",
        gate_policy_version="gate-v1",
        intent_prompt_version="3",
        intent_model_alias="gemma-4-26b-a4b-it",
        response_template_version="response-v1",
        commerce_policy_version="commerce-v1",
        research_policy_version="research-v1",
        suggestion_policy_version="suggestions-v1",
        memory_schema_version="memory-v1",
        query_enhancement_policy_version="enhancement-v1",
        clarification_prompt_version="2",
        recovery_prompt_version="2",
        research_prompt_version="2",
        research_model_alias="gemma-4-26b-a4b-it",
        suggestion_prompt_version="2",
    )


def fixture() -> tuple[TurnSnapshot, list[SearchEntry]]:
    compat = compatibility()
    bindings = [
        ProductBinding(product_id="product_1", sku_id="sku_1", offer_id="offer_1", catalog_version=compat.catalog_version),
        ProductBinding(product_id="product_2", sku_id="sku_2", offer_id="offer_2", catalog_version=compat.catalog_version),
        ProductBinding(product_id="product_3", sku_id="sku_3", offer_id="offer_3", catalog_version=compat.catalog_version),
    ]
    entries = [
        SearchEntry(
            result_entry_id=f"entry_{index}",
            display_position=index,
            binding=binding,
            title=f"Prototype shirt {index}",
            facts=[
                Fact(
                    fact_id=f"fact_{index}",
                    label="price",
                    typed_value=Money(amount_paise=100_000 + index),
                    status=TruthStatus.VERIFIED,
                    scope=FactScope.CATALOG,
                    provenance_type="MOCK_CATALOG",
                )
            ],
        )
        for index, binding in enumerate(bindings, start=1)
    ]
    state = QueryState(
        state_version=0,
        hard_constraints=[],
        soft_preferences=[],
        query_terms=[],
        selected_result_entry_ids=[],
        catalog_version=compat.catalog_version,
        index_version=compat.index_version,
        lexicon_version=compat.lexicon_version,
        compact_goal_summary="prototype shirts",
    )
    cart = empty_cart("session_1", compat)
    snapshot = TurnSnapshot(
        session_id="session_1",
        state=state,
        state_version=0,
        cart_version=0,
        cart=cart,
        acknowledged_result_set_id="result_set_1",
        acknowledged_entries=[
            ActiveResultBinding(
                result_entry_id=entry.result_entry_id,
                display_position=entry.display_position,
                binding=entry.binding,
            )
            for entry in entries
        ],
        compatibility_tuple=compat,
    )
    return snapshot, entries


def orchestrator(snapshot: TurnSnapshot, entries: list[SearchEntry]) -> tuple[TurnOrchestrator, dict[str, object]]:
    clock = FakeClock()
    ids = SequentialIds()
    state = InMemorySessionState(snapshot)
    gateway = FakeModelGateway()
    markdown = FakeMarkdownPipeline()
    catalog = FakeCatalogPort(entries)
    dependencies: dict[str, object] = {
        "state": state,
        "gateway": gateway,
        "markdown": markdown,
        "catalog": catalog,
    }
    app = TurnOrchestrator(
        state=state,
        enhancer=DeterministicEnhancer(clock, ids),
        gateway=gateway,
        catalog=catalog,
        recovery=FakeRecoveryPort(),
        references=FakeReferenceResolver(),
        cart=FakeCartPort(snapshot.cart),
        research=FakeResearchPort(clock),
        suggestions=FakeSuggestionPort(),
        markdown=markdown,
        clock=clock,
        ids=ids,
        trace_sink=FakeTraceSink(),
    )
    return app, dependencies


class AgenticWorkflowTests(unittest.TestCase):
    def test_free_text_runs_enhancement_intent_search_commit_and_markdown_handoff(self) -> None:
        snapshot, entries = fixture()
        app, dependencies = orchestrator(snapshot, entries)
        result = app.handle(
            TurnRequest(
                session_id="session_1",
                client_turn_id="turn_1",
                idempotency_key="key_1",
                expected_state_version=0,
                expected_cart_version=0,
                message="Find a shirt size m",
            )
        )
        self.assertEqual(result.status, "COMPLETED")
        assert result.response is not None
        self.assertEqual(result.response.terminal_state, TerminalState.ANSWERED_WITH_GROUNDED_RESULTS)
        self.assertEqual(len(result.response.search_entries), 3)
        self.assertEqual(len(dependencies["gateway"].calls), 1)  # type: ignore[attr-defined]
        self.assertEqual(len(dependencies["markdown"].handoffs), 1)  # type: ignore[attr-defined]
        self.assertTrue(result.trace.events)

    def test_greeting_is_help_and_bypasses_external_intent_model(self) -> None:
        snapshot, entries = fixture()
        app, dependencies = orchestrator(snapshot, entries)
        result = app.handle(
            TurnRequest(
                session_id="session_1",
                client_turn_id="turn_greeting",
                idempotency_key="key_greeting",
                expected_state_version=0,
                expected_cart_version=0,
                message="hey",
            )
        )
        self.assertEqual(result.status, "COMPLETED")
        assert result.response is not None
        self.assertEqual(result.response.action, Action.HELP)
        self.assertEqual(
            result.response.terminal_state,
            TerminalState.ANSWERED_WITH_GROUNDED_RESULTS,
        )
        self.assertIn("search", result.response.summary.casefold())
        self.assertEqual(len(dependencies["gateway"].calls), 0)  # type: ignore[attr-defined]
        self.assertIn("INTENT_FALLBACK", [event.stage for event in result.trace.events])

    def test_typed_show_cart_bypasses_enhancement_and_model(self) -> None:
        snapshot, entries = fixture()
        app, dependencies = orchestrator(snapshot, entries)
        result = app.handle(
            TurnRequest(
                session_id="session_1",
                client_turn_id="turn_2",
                idempotency_key="key_2",
                expected_state_version=0,
                expected_cart_version=0,
                ui_action=UiAction(action=Action.SHOW_CART),
            )
        )
        self.assertEqual(result.status, "COMPLETED")
        assert result.response is not None
        self.assertEqual(result.response.terminal_state, TerminalState.CART_SHOWN)
        self.assertEqual(len(dependencies["gateway"].calls), 0)  # type: ignore[attr-defined]
        self.assertEqual(len(dependencies["markdown"].handoffs), 1)  # type: ignore[attr-defined]

    def test_search_commit_pins_result_set_and_typed_reference_resolution_uses_it(self) -> None:
        snapshot, entries = fixture()
        app, dependencies = orchestrator(snapshot, entries)
        search = app.handle(
            TurnRequest(
                session_id="session_1",
                client_turn_id="turn_result_set",
                idempotency_key="key_result_set",
                expected_state_version=0,
                expected_cart_version=0,
                message="Find a shirt",
            )
        )
        assert search.response is not None
        self.assertEqual(search.response.result_set_id, "fake-result-set-1")
        state = dependencies["state"]  # type: ignore[assignment]
        self.assertEqual(state.snapshot.acknowledged_result_set_id, "fake-result-set-1")
        self.assertEqual(
            [entry.display_position for entry in state.snapshot.acknowledged_entries],
            [1, 2, 3],
        )

        details = app.handle(
            TurnRequest(
                session_id="session_1",
                client_turn_id="turn_result_set_details",
                idempotency_key="key_result_set_details",
                expected_state_version=1,
                expected_cart_version=0,
                ui_action=UiAction(
                    action=Action.PRODUCT_DETAILS,
                    payload={"references": [{"kind": "ORDINAL", "value": "2"}]},
                ),
            )
        )
        assert details.response is not None
        self.assertEqual(details.response.terminal_state, TerminalState.ANSWERED_WITH_PRODUCT_DETAILS)

    def test_completed_replay_returns_the_stored_response_without_reloading_old_state(self) -> None:
        snapshot, entries = fixture()
        app, _ = orchestrator(snapshot, entries)
        request = TurnRequest(
            session_id="session_1",
            client_turn_id="turn_replay",
            idempotency_key="key_replay",
            expected_state_version=0,
            expected_cart_version=0,
            message="Find a shirt",
        )
        first = app.handle(request)
        second = app.handle(request)
        self.assertEqual(first.status, "COMPLETED")
        self.assertEqual(second.status, "COMPLETED")
        assert first.response is not None and second.response is not None
        self.assertEqual(first.response.response_id, second.response.response_id)
        self.assertEqual(first.response.suggestions, second.response.suggestions)

    def test_persistent_memory_is_projected_without_stable_source_ids(self) -> None:
        snapshot, entries = fixture()
        snapshot = snapshot.model_copy(update={"memory_profile_id": "profile_1", "memory_version": 3})
        memory = FakeMemorySnapshotPort(
            [
                MemoryCandidate(
                    memory_item_id="memory_item_1",
                    kind="EXPLICIT_PREFERENCE",
                    field_id="colour",
                    typed_value="blue",
                    confidence="EXPLICIT",
                    age_seconds=3600,
                )
            ]
        )
        history = FakeCommerceHistoryPort(
            [
                PurchaseContext(
                    source_purchase_id="purchase_1",
                    public_product_name="Demo shirt",
                    selected_attributes={"size": "M"},
                    provenance_type="VERIFIED_DEMO",
                )
            ]
        )
        clock = FakeClock()
        ids = SequentialIds()
        enhancer = DeterministicEnhancer(clock, ids, memory=memory, history=history)
        output = enhancer.enhance(
            TurnRequest(
                session_id="session_1",
                client_turn_id="turn_memory",
                idempotency_key="key_memory",
                expected_state_version=0,
                expected_cart_version=0,
                message="same as usual",
            ),
            snapshot,
            75,
        )
        self.assertEqual(output.envelope.persistent_memory_candidates[0].memory_item_id, "memory_item_1")
        projected = output.projection.model_dump(mode="json")
        self.assertNotIn("memory_item_id", projected["persistent_memory_candidates"][0])
        self.assertNotIn("source_purchase_id", projected["verified_purchase_context"][0])
        self.assertNotEqual(
            output.projection.persistent_memory_candidates[0].context_ref,
            "memory_item_1",
        )

        ignored = enhancer.enhance(
            TurnRequest(
                session_id="session_1",
                client_turn_id="turn_memory_ignore",
                idempotency_key="key_memory_ignore",
                expected_state_version=0,
                expected_cart_version=0,
                message="same as usual",
                ignore_history=True,
            ),
            snapshot,
            75,
        )
        self.assertEqual(ignored.projection.persistent_memory_candidates, [])
        self.assertIn("HISTORY_IGNORED_FOR_TURN", ignored.warnings)

    def test_cart_update_accepts_only_acknowledged_exact_entry_and_skips_model(self) -> None:
        snapshot, entries = fixture()
        app, dependencies = orchestrator(snapshot, entries)
        result = app.handle(
            TurnRequest(
                session_id="session_1",
                client_turn_id="turn_cart",
                idempotency_key="key_cart",
                expected_state_version=0,
                expected_cart_version=0,
                ui_action=UiAction(
                    action=Action.UPDATE_CART,
                    payload={
                        "operations": [
                            {
                                "type": "ADD_ITEM",
                                "operation_id": "op_1",
                                "result_entry_id": "entry_1",
                                "quantity": 2,
                            }
                        ]
                    },
                ),
            )
        )
        self.assertEqual(result.status, "COMPLETED")
        assert result.response is not None
        self.assertEqual(result.response.terminal_state, TerminalState.CART_UPDATED)
        self.assertEqual(len(dependencies["gateway"].calls), 0)  # type: ignore[attr-defined]
        self.assertEqual(len(result.response.cart.items), 1)

    def test_prompt_registry_has_versioned_checksums_and_gemma_adapter_has_no_tools(self) -> None:
        prompts = PromptRegistry()
        spec = prompts.manifest_entry(__import__("fkgrid.agentic.contracts", fromlist=["ModelCallType"]).ModelCallType.RESOLVE_INTENT_AND_DELTA)
        self.assertEqual(len(spec["file_checksum"]), 64)
        self.assertEqual(spec["prompt_id"], "intent_v3")
        self.assertEqual(spec["prompt_version"], "3")
        self.assertIn('"primary_action":"SEARCH"', prompts.text(__import__("fkgrid.agentic.contracts", fromlist=["ModelCallType"]).ModelCallType.RESOLVE_INTENT_AND_DELTA))

        class Transport:
            def __init__(self) -> None:
                self.payload = None

            def post_json(self, payload, timeout_ms):
                self.payload = payload
                return {
                    "choices": [
                        {
                            "message": {
                                "content": '{"schema_version":"IntentDeltaV1","primary_action":"SHOW_CART","delta_operations":[],"references":[],"action_parameters":{},"unknown_terms":[],"candidate_interpretations":[],"clarification_candidate":null}'
                            }
                        }
                    ]
                }

        transport = Transport()
        adapter = Gemma4ModelAdapter(transport)
        request = prompts.build_request(
            __import__("fkgrid.agentic.contracts", fromlist=["ModelCallType"]).ModelCallType.RESOLVE_INTENT_AND_DELTA,
            {"current_message_verbatim": "show cart"},
            compatibility(),
            "call_1",
            1800,
            compatibility().intent_model_alias,
        )
        response = adapter.complete(request)
        self.assertEqual(response.status.value, "OK")
        self.assertEqual(transport.payload["tools"], [])
        self.assertEqual(transport.payload["model"], compatibility().intent_model_alias)

    def test_gemma_environment_factory_requires_endpoint_and_never_uses_repository_secrets(self) -> None:
        with self.assertRaisesRegex(ValueError, "FKGRID_MODEL_ENDPOINT_REQUIRED"):
            Gemma4ModelAdapter.from_environment(environment={"FKGRID_MODEL_API_KEY": "test-only"})
        with self.assertRaisesRegex(ValueError, "FKGRID_MODEL_API_KEY_REQUIRED"):
            Gemma4ModelAdapter.from_environment(environment={"FKGRID_MODEL_ENDPOINT": "https://provider.test"})

        adapter = Gemma4ModelAdapter.from_environment(
            environment={
                "FKGRID_MODEL_ENDPOINT": "https://provider.test/v1/chat/completions",
                "FKGRID_MODEL_API_KEY": "test-only",
            }
        )
        self.assertEqual(adapter.model_alias, "gemma-4-26b-a4b-it")

        google_adapter = Gemma4ModelAdapter.from_environment(
            environment={
                "FKGRID_MODEL_PROTOCOL": "gemini",
                "FKGRID_MODEL_API_KEY": "test-only",
            }
        )
        self.assertIsInstance(google_adapter.transport, GeminiGenerateContentTransport)
        self.assertEqual(google_adapter.provider_name, "google-gemini-api")

    def test_google_transport_uses_native_header_and_json_mode_without_network(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(
                    {"candidates": [{"content": {"parts": [{"text": '{"ok":true}'}]}}]}
                ).encode()

        transport = GeminiGenerateContentTransport(
            "https://provider.test/v1beta/models/{model}:generateContent",
            "test-only-key",
        )
        with patch("fkgrid.agentic.gateway.urlopen", return_value=Response()) as opener:
            output = transport.post_json(
                {
                    "model": "gemma-4-26b-a4b-it",
                    "messages": [
                        {"role": "system", "content": "system"},
                        {"role": "user", "content": "user"},
                    ],
                    "temperature": 0.0,
                    "max_tokens": 50,
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "schema": {
                                "$defs": {"Action": {"type": "string", "enum": ["SEARCH"]}},
                                "type": "object",
                                "properties": {
                                    "ok": {"type": "boolean", "minLength": 1},
                                    "primary_action": {"$ref": "#/$defs/Action"},
                                    "schema_version": {
                                        "type": "string",
                                        "const": "IntentDeltaV1",
                                    },
                                },
                                "required": ["ok", "primary_action", "schema_version"],
                            }
                        },
                    },
                },
                1000,
            )
        request = opener.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.get_header("X-goog-api-key"), "test-only-key")
        self.assertEqual(body["generationConfig"]["responseMimeType"], "application/json")
        self.assertEqual(body["generationConfig"]["thinkingConfig"], {"thinkingLevel": "minimal"})
        self.assertEqual(body["generationConfig"]["responseJsonSchema"]["type"], "object")
        self.assertNotIn(
            "minLength", body["generationConfig"]["responseJsonSchema"]["properties"]["ok"]
        )
        self.assertEqual(
            body["generationConfig"]["responseJsonSchema"]["properties"]["primary_action"]["enum"],
            ["SEARCH"],
        )
        self.assertEqual(
            body["generationConfig"]["responseJsonSchema"]["properties"]["schema_version"]["enum"],
            ["IntentDeltaV1"],
        )
        self.assertNotIn("$defs", body["generationConfig"]["responseJsonSchema"])
        self.assertEqual(output["choices"][0]["message"]["content"], '{"ok":true}')

    def test_openai_compatible_transport_omits_empty_tools_for_deepinfra(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"choices":[{"message":{"content":"{}"}}]}'

        transport = UrllibJsonTransport(
            "https://api.deepinfra.com/v1/openai/chat/completions",
            "test-only-key",
        )
        with patch("fkgrid.agentic.gateway.urlopen", return_value=Response()) as opener:
            transport.post_json(
                {
                    "model": "google/gemma-4-26b-a4b-it",
                    "messages": [],
                    "tools": [],
                    "tool_choice": "none",
                },
                1800,
            )
        request = opener.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertNotIn("tools", payload)
        self.assertNotIn("tool_choice", payload)
        self.assertEqual(request.get_header("Authorization"), "Bearer test-only-key")

    def test_ambiguous_details_clarifies_and_never_calls_catalog_details(self) -> None:
        snapshot, entries = fixture()
        app, dependencies = orchestrator(snapshot, entries)
        result = app.handle(
            TurnRequest(
                session_id="session_1",
                client_turn_id="turn_3",
                idempotency_key="key_3",
                expected_state_version=0,
                expected_cart_version=0,
                message="Show product details",
            )
        )
        self.assertEqual(result.status, "COMPLETED")
        assert result.response is not None
        self.assertEqual(result.response.terminal_state, TerminalState.CLARIFICATION_REQUIRED)
        self.assertEqual(dependencies["gateway"].calls[0].logical_call_type.value, "resolve_intent_and_delta")  # type: ignore[attr-defined]
        state = dependencies["state"]  # type: ignore[assignment]
        self.assertIsNotNone(state.snapshot.pending_clarification)
        self.assertEqual(state.snapshot.pending_clarification.reason_code, "REFERENCE_REQUIRED")

    def test_arbitrary_model_reference_is_rejected_without_catalog_action(self) -> None:
        snapshot, entries = fixture()
        app, dependencies = orchestrator(snapshot, entries)
        gateway = dependencies["gateway"]
        # The fake's scripted response is schema-valid but references a foreign ID.
        from fkgrid.agentic.gateway import _response
        from fkgrid.agentic.contracts import ModelCallType, ModelRequest, ModelStatus

        request = ModelRequest(
            call_id="scripted",
            logical_call_type=ModelCallType.RESOLVE_INTENT_AND_DELTA,
            model_alias=compatibility().intent_model_alias,
            prompt_id="intent_v3",
            prompt_version="3",
            input_schema_version="IntentContextProjectionV1",
            output_schema_version="IntentDeltaV1",
            input_payload={},
            output_schema={},
            deadline_ms=1800,
            temperature=0.0,
            max_output_tokens=500,
            compatibility_tuple=compatibility(),
        )
        gateway.queue_response(
            ModelCallType.RESOLVE_INTENT_AND_DELTA,
            _response(
                request,
                ModelStatus.OK,
                {
                    "schema_version": "IntentDeltaV1",
                    "primary_action": "PRODUCT_DETAILS",
                    "delta_operations": [],
                    "references": [{"kind": "OWNED_ID", "value": "foreign_entry"}],
                    "action_parameters": {},
                    "unknown_terms": [],
                    "candidate_interpretations": [],
                    "clarification_candidate": None,
                },
                "fake",
                0,
            ),
        )
        result = app.handle(
            TurnRequest(
                session_id="session_1",
                client_turn_id="turn_4",
                idempotency_key="key_4",
                expected_state_version=0,
                expected_cart_version=0,
                message="show details of that",
            )
        )
        self.assertEqual(result.status, "COMPLETED")
        assert result.response is not None
        self.assertEqual(result.response.terminal_state, TerminalState.CLARIFICATION_REQUIRED)
        self.assertEqual(dependencies["catalog"].details_calls, 0)  # type: ignore[attr-defined]

    def test_research_is_cited_and_cannot_use_external_price_as_catalog_authority(self) -> None:
        snapshot, entries = fixture()
        app, dependencies = orchestrator(snapshot, entries)
        from fkgrid.agentic.fakes import FakeResearchPort
        source = {
            "source_id": "source_1",
            "canonical_url": "https://example.test/guide",
            "domain": "example.test",
            "title": "General guide",
            "extract": "General guidance from a bounded fake source.",
            "extract_hash": "hash_1",
            "retrieved_at": datetime.now(timezone.utc),
            "trust_tier": "EDITORIAL",
        }
        # Replace only the research fake's source list; this keeps the test focused
        # on the orchestration boundary rather than a provider implementation.
        research = FakeResearchPort(FakeClock(), [__import__("fkgrid.agentic.contracts", fromlist=["ResearchSource"]).ResearchSource(**source)])
        app.research = research
        result = app.handle(
            TurnRequest(
                session_id="session_1",
                client_turn_id="turn_5",
                idempotency_key="key_5",
                expected_state_version=0,
                expected_cart_version=0,
                message="research current guidance for this product",
            )
        )
        self.assertEqual(result.status, "COMPLETED")
        assert result.response is not None
        self.assertIn(result.response.terminal_state, {TerminalState.ANSWERED_WITH_EXTERNAL_RESEARCH, TerminalState.RESEARCH_UNAVAILABLE})
        if result.response.research:
            for claim in result.response.research.claims:
                self.assertTrue(set(claim.support_source_ids).issubset({"source_1"}))

    def test_canonical_hash_is_stable_for_nested_contracts(self) -> None:
        snapshot, _ = fixture()
        first = canonical_hash(snapshot)
        second = canonical_hash(snapshot.model_copy(deep=True))
        self.assertEqual(first, second)

    def test_merge_preserves_unspecified_hard_constraints(self) -> None:
        snapshot, _ = fixture()
        state = snapshot.state.model_copy(
            update={
                "hard_constraints": [
                    Constraint(
                        field_id="material",
                        operator=ConstraintOperator.EQ,
                        values=["cotton"],
                        provenance_turn_id="old",
                        explicit=True,
                    )
                ]
            }
        )
        from fkgrid.agentic.contracts import IntentDeltaV1, SetHardOperation

        updated, summary = merge_query_state(
            state,
            IntentDeltaV1(
                primary_action=Action.REFINE,
                delta_operations=[
                    SetHardOperation(
                        field_id="size",
                        operator=ConstraintOperator.EQ,
                        typed_values=["size_m"],
                        evidence_span=(0, 1),
                    )
                ],
            ),
            "new",
        )
        self.assertEqual({item.field_id for item in updated.hard_constraints}, {"material", "size"})
        self.assertIn("material", summary.preserved)


if __name__ == "__main__":
    unittest.main()
