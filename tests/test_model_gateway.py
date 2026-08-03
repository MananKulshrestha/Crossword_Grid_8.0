from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from fkgrid.query_recovery.adapters.model_gateway import (
    GatewayReply,
    SharedGatewayRecoveryPlanner,
)
from fkgrid.query_recovery.adapters.gemini import (
    GEMMA_4_26B_A4B_IT,
    GeminiGemmaConfig,
    GeminiGemmaGateway,
)
from fkgrid.query_recovery.domain import (
    CompatibilityTuple,
    ConceptType,
    RecoveryConstraint,
    RecoveryContext,
    RecoveryRewritePlan,
    RecoveryTriggerReason,
    BaselineSignals,
    QueryState,
)
from fkgrid.query_recovery.validation import hard_filter_hash, canonical_hash


class RecordingGateway:
    def __init__(self, json_text: str) -> None:
        self.json_text = json_text
        self.calls: list[dict[str, object]] = []

    def complete_json(self, **kwargs):
        self.calls.append(kwargs)
        return GatewayReply(status="OK", json_text=self.json_text, token_count=42, latency_ms=8)


class FakeHttpResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.body


class ModelGatewayTests(unittest.TestCase):
    def make_context(self) -> RecoveryContext:
        compatibility = CompatibilityTuple(
            contract_schema_version="recovery-contract-v1",
            catalog_version="catalog-1",
            index_version="index-1",
            taxonomy_version="taxonomy-1",
            category_schema_version="schema-1",
            lexicon_version="lexicon-1",
            rank_policy_version="rank-1",
            gate_policy_version="gate-1",
            recovery_policy_version="recovery-policy-v1",
        )
        state = QueryState(
            query_terms=["ambiguous"],
            catalog_version="catalog-1",
            index_version="index-1",
            taxonomy_version="taxonomy-1",
            category_schema_version="schema-1",
            lexicon_version="lexicon-1",
        )
        concept = RecoveryConstraint(
            concept_id="concept-1",
            concept_type=ConceptType.TAXONOMY,
            label="Shirts",
            canonical_term="shirts",
            catalog_version="catalog-1",
            taxonomy_version="taxonomy-1",
            category_schema_version="schema-1",
            lexicon_version="lexicon-1",
        )
        base = {
            "unresolved_terms": ["ambiguous"],
            "query_state": state,
            "hard_filter_hash": hard_filter_hash(state),
            "gate_reasons": [RecoveryTriggerReason.UNKNOWN_IMPORTANT_TERM],
            "baseline_run_id": "baseline",
            "baseline_summary": BaselineSignals(eligible_count=0),
            "allowed_concepts": [concept],
            "approved_mappings": [],
            "compatibility": compatibility,
            "locale": "en-IN",
        }
        return RecoveryContext(**base, planner_input_hash=canonical_hash(base))

    def test_gateway_boundary_passes_no_tools_and_revalidates_strict_output(self) -> None:
        context = self.make_context()
        output = RecoveryRewritePlan(
            added_concept_ids=["concept-1"],
            interpretation_label="safe label",
            preserved_hard_filter_hash=context.hard_filter_hash,
        )
        gateway = RecordingGateway(output.model_dump_json())
        planner = SharedGatewayRecoveryPlanner(gateway=gateway)
        parsed, codes, tokens, latency = planner.plan(context=context, timeout_ms=500)
        self.assertEqual(parsed, output)
        self.assertEqual(codes, [])
        self.assertEqual((tokens, latency), (42, 8))
        self.assertEqual(gateway.calls[0]["tools"], ())
        self.assertEqual(gateway.calls[0]["schema_name"], "RecoveryPlannerOutputV1")

    def test_extra_model_field_is_rejected_without_retry(self) -> None:
        context = self.make_context()
        gateway = RecordingGateway(
            '{"action":"NO_SAFE_RECOVERY","reason_code":"x",'
            '"preserved_hard_filter_hash":"' + context.hard_filter_hash + '","tool":"update_cart"}'
        )
        planner = SharedGatewayRecoveryPlanner(gateway=gateway)
        parsed, codes, _tokens, _latency = planner.plan(context=context, timeout_ms=500)
        self.assertIsNone(parsed)
        self.assertTrue(codes)
        self.assertEqual(len(gateway.calls), 1)

    def test_prompt_manifest_checksum_matches_resource(self) -> None:
        prompt_path = Path(__file__).parents[1] / "src" / "fkgrid" / "query_recovery" / "prompts" / "recovery_v2.md"
        manifest_path = prompt_path.with_name("manifest.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        digest = hashlib.sha256(prompt_path.read_bytes()).hexdigest()
        self.assertEqual(manifest["prompts"]["recovery-v2"]["sha256"], digest)

    def test_gemini_adapter_filters_thought_parts_and_keeps_secret_out_of_repr(self) -> None:
        context = self.make_context()
        output = RecoveryRewritePlan(
            added_concept_ids=["concept-1"],
            interpretation_label="safe label",
            preserved_hard_filter_hash=context.hard_filter_hash,
        )
        calls: list[object] = []

        def opener(request, **kwargs):
            calls.append((request, kwargs))
            return FakeHttpResponse(
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {"text": "hidden thought", "thought": True},
                                    {"text": output.model_dump_json()},
                                ]
                            }
                        }
                    ],
                    "usageMetadata": {"totalTokenCount": 17},
                }
            )

        config = GeminiGemmaConfig.from_env({"FKGRID_GEMINI_API_KEY": "test-secret"})
        gateway = GeminiGemmaGateway(config=config, opener=opener)
        reply = gateway.complete_json(
            prompt_version="recovery-v1",
            model_alias=GEMMA_4_26B_A4B_IT,
            system_prompt="system",
            user_json="{}",
            schema_name="RecoveryPlannerOutputV1",
            timeout_ms=500,
            tools=(),
        )
        self.assertEqual(reply.status, "OK")
        self.assertEqual(reply.json_text, output.model_dump_json())
        self.assertEqual(reply.token_count, 17)
        self.assertNotIn("test-secret", repr(config))
        request, _kwargs = calls[0]
        self.assertEqual(request.full_url, f"https://generativelanguage.googleapis.com/v1beta/models/{GEMMA_4_26B_A4B_IT}:generateContent")
        self.assertEqual(request.get_header("X-goog-api-key"), "test-secret")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["generationConfig"]["thinkingConfig"]["thinkingLevel"], "minimal")
        self.assertNotIn("tools", payload)

    def test_gemini_adapter_rejects_tools_without_network_call(self) -> None:
        calls: list[object] = []
        config = GeminiGemmaConfig.from_env({"FKGRID_GEMINI_API_KEY": "test-secret"})
        gateway = GeminiGemmaGateway(config=config, opener=lambda *_args, **_kwargs: calls.append(True))
        reply = gateway.complete_json(
            prompt_version="recovery-v1",
            model_alias=GEMMA_4_26B_A4B_IT,
            system_prompt="system",
            user_json="{}",
            schema_name="RecoveryPlannerOutputV1",
            timeout_ms=500,
            tools=("lookup",),
        )
        self.assertEqual(reply.error_code, "TOOLS_NOT_ALLOWED")
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
