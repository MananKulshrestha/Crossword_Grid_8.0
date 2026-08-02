from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from fkgrid.query_recovery.adapters.model_gateway import (
    GatewayReply,
    SharedGatewayRecoveryPlanner,
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
            "approved_suggestions": [],
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
        prompt_path = Path(__file__).parents[1] / "src" / "fkgrid" / "query_recovery" / "prompts" / "recovery_v1.md"
        manifest_path = prompt_path.with_name("manifest.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        digest = hashlib.sha256(prompt_path.read_bytes()).hexdigest()
        self.assertEqual(manifest["prompts"]["recovery-v1"]["sha256"], digest)


if __name__ == "__main__":
    unittest.main()
