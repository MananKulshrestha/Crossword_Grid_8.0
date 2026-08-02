from __future__ import annotations

import json

import httpx
from pydantic import SecretStr

from fkgrid.adapters.model.gemma import GemmaCatalogLanguageModel, GemmaModelSettings
from fkgrid.domain.catalog_language import LexiconCompatibility, ModelCallRequest, ModelStatus


def compatibility() -> LexiconCompatibility:
    return LexiconCompatibility(
        catalog_version="cat-1",
        taxonomy_version="tax-1",
        category_schema_version="schema-1",
        lexicon_version="lex-1",
        normalizer_version="normalizer-v1",
        mapping_schema_version="mapping-v1",
        rank_policy_version="rank-v1",
    )


def proposer_request() -> ModelCallRequest:
    return ModelCallRequest(
        call_id="model-1",
        logical_call="propose_canonical_mapping",
        prompt_id="catalog_language_proposer_v1",
        prompt_version="1",
        input_schema_version="CatalogLanguageProposerInputV1",
        output_schema_version="MappingDraftV1",
        input_payload={
            "normalized_form": "trainers",
            "surface_forms": ["trainers"],
            "locale": "en-IN",
            "scope": {"locale": "en-IN", "taxonomy_node_id": "footwear"},
            "allowed_targets": [
                {
                    "target_type": "TAXONOMY_NODE",
                    "target_id": "athletic-shoes",
                    "canonical_name": "Athletic trainers",
                }
            ],
            "evidence_ids": ["group-1"],
            "evidence_summary": {"support_count": 8},
        },
        allowed_target_ids=["athletic-shoes"],
        deadline_ms=1_800,
        temperature=0.0,
        compatibility=compatibility(),
    )


def model(settings: GemmaModelSettings, handler: httpx.MockTransport) -> GemmaCatalogLanguageModel:
    return GemmaCatalogLanguageModel(settings, transport=handler)


def test_ollama_adapter_sends_structured_prompt_and_parses_gemma_json() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": json.dumps(
                        {
                            "decision": "SELECT",
                            "source_form": "trainers",
                            "normalized_form": "trainers",
                            "mapping_kind": "SYNONYM",
                            "target_type": "TAXONOMY_NODE",
                            "target_id": "athletic-shoes",
                            "scope": {"locale": "en-IN", "taxonomy_node_id": "footwear"},
                            "direction": "QUERY_TO_CANONICAL",
                            "expansion_action": "CANONICAL_SYNONYM",
                            "evidence_band": "MEDIUM",
                            "interpretation_label": "Athletic trainers",
                            "evidence_ids": ["group-1"],
                        }
                    )
                }
            },
        )

    adapter = model(
        GemmaModelSettings(
            provider="ollama",
            base_url="http://gemma.test",
            model_name="gemma3:27b",
        ),
        httpx.MockTransport(handler),
    )

    result = adapter.propose_canonical_mapping(proposer_request())

    assert result.status == ModelStatus.OK
    assert result.payload is not None and result.payload.target_id == "athletic-shoes"
    assert len(requests) == 1
    body = json.loads(requests[0].content)
    assert str(requests[0].url) == "http://gemma.test/api/chat"
    assert body["model"] == "gemma3:27b"
    assert body["options"]["temperature"] == 0
    assert body["format"]["properties"]["decision"]
    assert "trainers" in body["messages"][0]["content"]
    assert "{{canonical_proposer_input_json}}" not in body["messages"][0]["content"]


def test_gemma_readiness_requires_the_configured_model_alias() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": "gemma3:27b"}]})

    adapter = model(
        GemmaModelSettings(
            provider="ollama",
            base_url="http://gemma.test",
            model_name="gemma3:27b",
        ),
        httpx.MockTransport(handler),
    )

    assert adapter.readiness() == (True, "Gemma model 'gemma3:27b' is ready")


def test_deepinfra_adapter_uses_openai_compatible_endpoint_and_bearer_auth() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "decision": "ABSTAIN",
                                }
                            )
                        }
                    }
                ]
            },
        )

    adapter = model(
        GemmaModelSettings(
            provider="deepinfra",
            base_url="https://api.deepinfra.com/v1/openai",
            model_name="google/gemma-4-26B-A4B-it",
            api_key=SecretStr("test-deepinfra-token"),
        ),
        httpx.MockTransport(handler),
    )

    result = adapter.propose_canonical_mapping(proposer_request())

    assert result.status == ModelStatus.OK
    assert result.payload is not None and result.payload.decision == "ABSTAIN"
    assert str(requests[0].url) == "https://api.deepinfra.com/v1/openai/chat/completions"
    assert requests[0].headers["authorization"] == "Bearer test-deepinfra-token"


def test_deepinfra_readiness_accepts_openai_models_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        return httpx.Response(
            200,
            json={"data": [{"id": "google/gemma-4-26B-A4B-it"}]},
        )

    adapter = model(
        GemmaModelSettings(
            provider="deepinfra",
            base_url="https://api.deepinfra.com/v1/openai",
            model_name="google/gemma-4-26B-A4B-it",
            api_key=SecretStr("test-deepinfra-token"),
        ),
        httpx.MockTransport(handler),
    )

    assert adapter.readiness() == (
        True,
        "Gemma model 'google/gemma-4-26B-A4B-it' is ready",
    )


def test_invalid_gemma_json_is_abstained_as_invalid_output() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": "not-json"}})

    adapter = model(
        GemmaModelSettings(base_url="http://gemma.test"),
        httpx.MockTransport(handler),
    )

    result = adapter.propose_canonical_mapping(proposer_request())

    assert result.status == ModelStatus.INVALID_OUTPUT
    assert result.payload is None
    assert result.validation_codes == ["OUTPUT_JSON_INVALID"]


def test_gemma_json_after_reasoning_prefix_is_still_strictly_validated() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                'I checked the supplied candidates.\n{"decision":"ABSTAIN"}\n'
                            )
                        }
                    }
                ]
            },
        )

    adapter = model(
        GemmaModelSettings(provider="deepinfra", base_url="http://gemma.test"),
        httpx.MockTransport(handler),
    )

    result = adapter.propose_canonical_mapping(proposer_request())

    assert result.status == ModelStatus.OK
    assert result.payload is not None and result.payload.decision == "ABSTAIN"
