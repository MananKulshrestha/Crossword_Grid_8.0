from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from fkgrid.api.dependencies import build_default_dependencies
from fkgrid.query_recovery.adapters.deepinfra import (
    DEEPINFRA_GEMMA_4_26B_A4B_IT,
    DEEPINFRA_OPENAI_BASE,
    DeepInfraGemmaConfig,
    DeepInfraGemmaGateway,
)


class FakeHttpResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.body


class DeepInfraGatewayTests(unittest.TestCase):
    def test_default_api_composition_selects_deepinfra_without_calling_it(self) -> None:
        with patch.dict(os.environ, {"FKGRID_DEEPINFRA_API_KEY": "test-secret"}):
            dependencies = build_default_dependencies()
        self.assertEqual(dependencies.mode, "deepinfra-gemma4")
        self.assertEqual(dependencies.planner_model, DEEPINFRA_GEMMA_4_26B_A4B_IT)

    def test_openai_compatible_json_request_and_response_are_mapped(self) -> None:
        calls: list[object] = []
        response_json = '{"action":"NO_SAFE_RECOVERY","reason_code":"x"}'

        def opener(request, **kwargs):
            calls.append((request, kwargs))
            return FakeHttpResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": [
                                    {"type": "reasoning", "text": "do not expose"},
                                    {"type": "text", "text": response_json},
                                ],
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 30, "completion_tokens": 17, "total_tokens": 47},
                }
            )

        config = DeepInfraGemmaConfig.from_env({"FKGRID_DEEPINFRA_API_KEY": "test-secret"})
        gateway = DeepInfraGemmaGateway(config=config, opener=opener)
        reply = gateway.complete_json(
            prompt_version="recovery-v1",
            model_alias=DEEPINFRA_GEMMA_4_26B_A4B_IT,
            system_prompt="system",
            user_json="{}",
            schema_name="RecoveryPlannerOutputV1",
            timeout_ms=500,
            tools=(),
        )

        self.assertEqual(reply.status, "OK")
        self.assertEqual(reply.json_text, response_json)
        self.assertEqual(reply.token_count, 47)
        self.assertNotIn("test-secret", repr(config))
        request, kwargs = calls[0]
        self.assertEqual(request.full_url, f"{DEEPINFRA_OPENAI_BASE}/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-secret")
        self.assertEqual(kwargs["timeout"], 0.5)
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["model"], DEEPINFRA_GEMMA_4_26B_A4B_IT)
        self.assertEqual(payload["max_tokens"], 256)
        self.assertEqual(payload["temperature"], 0.0)
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertFalse(payload["stream"])
        self.assertNotIn("tools", payload)

    def test_environment_precedence_and_model_pin(self) -> None:
        config = DeepInfraGemmaConfig.from_env(
            {
                "FKGRID_DEEPINFRA_API_KEY": "project-secret",
                "DEEPINFRA_TOKEN": "official-secret",
            }
        )
        self.assertEqual(config.api_key.get_secret_value(), "project-secret")
        self.assertEqual(config.model_alias, DEEPINFRA_GEMMA_4_26B_A4B_IT)
        with self.assertRaisesRegex(ValueError, "pinned"):
            DeepInfraGemmaConfig(api_key=config.api_key, model_alias="other-model")

    def test_tools_and_wrong_model_are_rejected_before_network(self) -> None:
        calls: list[object] = []
        config = DeepInfraGemmaConfig.from_env({"DEEPINFRA_API_KEY": "test-secret"})
        gateway = DeepInfraGemmaGateway(
            config=config,
            opener=lambda *_args, **_kwargs: calls.append(True),
        )
        common = {
            "prompt_version": "recovery-v1",
            "system_prompt": "system",
            "user_json": "{}",
            "schema_name": "RecoveryPlannerOutputV1",
            "timeout_ms": 500,
        }
        tools_reply = gateway.complete_json(
            **common, model_alias=DEEPINFRA_GEMMA_4_26B_A4B_IT, tools=("lookup",)
        )
        model_reply = gateway.complete_json(
            **common, model_alias="wrong-model", tools=()
        )
        self.assertEqual(tools_reply.error_code, "TOOLS_NOT_ALLOWED")
        self.assertEqual(model_reply.error_code, "MODEL_ALIAS_MISMATCH")
        self.assertEqual(calls, [])

    def test_provider_statuses_are_sanitized(self) -> None:
        config = DeepInfraGemmaConfig.from_env({"DEEPINFRA_TOKEN": "test-secret"})

        def opener(_request, **_kwargs):
            raise HTTPError(
                url="https://api.deepinfra.com/v1/openai/chat/completions",
                code=401,
                msg="invalid secret should not escape",
                hdrs=None,
                fp=None,
            )

        reply = DeepInfraGemmaGateway(config=config, opener=opener).complete_json(
            prompt_version="recovery-v1",
            model_alias=DEEPINFRA_GEMMA_4_26B_A4B_IT,
            system_prompt="system",
            user_json="{}",
            schema_name="RecoveryPlannerOutputV1",
            timeout_ms=500,
            tools=(),
        )
        self.assertEqual(reply.status, "ERROR")
        self.assertEqual(reply.error_code, "AUTH_REJECTED")
        self.assertIsNone(reply.json_text)


if __name__ == "__main__":
    unittest.main()
