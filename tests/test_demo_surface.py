from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from fkgrid.api.demo import DemoRecoveryInput, build_demo_recovery_request
from fkgrid.api.dependencies import build_demo_dependencies
from fkgrid.api.main import create_app
from fkgrid.query_recovery.validation import hard_filter_hash


class DemoSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(create_app(build_demo_dependencies()))

    def test_demo_page_is_simple_and_has_limited_filters(self) -> None:
        response = self.client.get("/demo")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Run query recovery", response.text)
        self.assertIn('id="query"', response.text)
        self.assertIn("Did you mean?", response.text)
        self.assertNotIn("What the agent needs from you", response.text)
        self.assertNotIn('id="question"', response.text)
        self.assertEqual(response.text.count('type="checkbox"'), 3)
        self.assertNotIn("RecoveryRequest", response.text)

    def test_simple_demo_input_runs_existing_workflow_with_filters(self) -> None:
        response = self.client.post(
            "/v1/query-recovery/demo-turn",
            json={
                "query": "formal wear for an office event",
                "in_stock_only": True,
                "cotton_only": True,
                "under_2000": False,
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["query"], "formal wear for an office event")
        self.assertEqual(body["filters"], ["In stock only", "Cotton"])
        self.assertEqual(body["result"]["outcome"], "RECOVERED_TIER1")
        self.assertFalse(body["result"]["event"]["planner_called"])
        self.assertEqual(body["result"]["plan"]["added_query_terms"], ["shirts", "blazers", "pants"])
        self.assertEqual(body["result"]["selected_run"]["query_branches"], ["shirts", "blazers", "pants"])
        self.assertEqual(
            [suggestion["label"] for suggestion in body["result"]["suggestions"]],
            ["Shirts", "Blazers", "Pants"],
        )
        self.assertEqual(body["result"]["event"]["hard_filter_mutation_count"], 0)

    def test_shoes_uses_current_query_and_recovers_semantically(self) -> None:
        response = self.client.post(
            "/v1/query-recovery/demo-turn",
            json={
                "query": "shoes",
                "in_stock_only": True,
                "cotton_only": True,
                "under_2000": False,
            },
        )
        self.assertEqual(response.status_code, 200)
        recovery = response.json()["result"]
        self.assertEqual(recovery["outcome"], "RECOVERED_TIER1")
        self.assertFalse(recovery["event"]["planner_called"])
        self.assertEqual(recovery["plan"]["added_query_terms"], ["footwear"])
        self.assertEqual(recovery["selected_run"]["interpretation_family"], "shoes / footwear")
        self.assertEqual(recovery["event"]["hard_filter_mutation_count"], 0)
        self.assertEqual(
            recovery["event"]["hard_filter_hash_before"],
            recovery["event"]["hard_filter_hash_after"],
        )

    def test_trainers_expands_to_the_sports_shoe_meaning_family(self) -> None:
        response = self.client.post(
            "/v1/query-recovery/demo-turn",
            json={"query": "trainers", "under_2000": True},
        )
        self.assertEqual(response.status_code, 200)
        recovery = response.json()["result"]
        self.assertEqual(recovery["outcome"], "RECOVERED_TIER1")
        self.assertEqual(recovery["plan"]["added_query_terms"], ["sports shoes"])
        self.assertIn("sports shoes", recovery["selected_run"]["interpretation_family"])
        self.assertIn("athletic shoes", recovery["selected_run"]["interpretation_family"])
        self.assertFalse(recovery["event"]["planner_called"])
        self.assertEqual(recovery["event"]["hard_filter_mutation_count"], 0)

    def test_spelling_variants_show_suggestions_without_questions(self) -> None:
        expected = {
            "formalwaer": {"Shirts", "Blazers", "Pants"},
            "shoees": {"Footwear"},
            "traiers": {"Sports Shoes"},
        }
        for query, labels in expected.items():
            with self.subTest(query=query):
                response = self.client.post(
                    "/v1/query-recovery/demo-turn",
                    json={"query": query},
                )
                self.assertEqual(response.status_code, 200)
                recovery = response.json()["result"]
                self.assertEqual(recovery["outcome"], "RECOVERED_TIER1")
                self.assertEqual(
                    {suggestion["label"] for suggestion in recovery["suggestions"]},
                    labels,
                )
                self.assertIsNone(recovery["clarification"])

    def test_changing_query_does_not_replay_the_previous_result(self) -> None:
        shoes = self.client.post(
            "/v1/query-recovery/demo-turn",
            json={"query": "shoes"},
        ).json()["result"]
        formal = self.client.post(
            "/v1/query-recovery/demo-turn",
            json={"query": "formalware"},
        ).json()["result"]
        self.assertEqual(shoes["outcome"], "RECOVERED_TIER1")
        self.assertEqual(formal["outcome"], "RECOVERED_TIER1")
        self.assertNotEqual(shoes["event"]["original_terms"], formal["event"]["original_terms"])
        self.assertEqual(
            {suggestion["label"] for suggestion in formal["suggestions"]},
            {"Shirts", "Blazers", "Pants"},
        )
        self.assertIsNone(formal["clarification"])

    def test_unknown_query_does_not_get_a_spurious_category_question(self) -> None:
        response = self.client.post(
            "/v1/query-recovery/demo-turn",
            json={"query": "moon boots"},
        )
        self.assertEqual(response.status_code, 200)
        recovery = response.json()["result"]
        self.assertEqual(recovery["outcome"], "NO_SAFE_RECOVERY")
        self.assertIsNone(recovery["clarification"])
        self.assertEqual(recovery["suggestions"], [])

    def test_builder_places_visible_filters_in_hard_constraints(self) -> None:
        payload = DemoRecoveryInput(
            query="formal wear",
            in_stock_only=True,
            cotton_only=False,
            under_2000=True,
        )
        request = build_demo_recovery_request(payload)
        self.assertEqual(
            [constraint.field_id for constraint in request.query_state.hard_constraints],
            ["availability", "price"],
        )
        self.assertEqual(
            request.baseline_run.hard_filter_hash,
            hard_filter_hash(request.query_state),
        )
        self.assertEqual(request.gate.hard_filter_hash, request.baseline_run.hard_filter_hash)

    def test_demo_input_rejects_blank_query_and_unknown_fields(self) -> None:
        blank = self.client.post("/v1/query-recovery/demo-turn", json={"query": "   "})
        extra = self.client.post(
            "/v1/query-recovery/demo-turn",
            json={"query": "shirts", "unexpected": True},
        )
        self.assertEqual(blank.status_code, 422)
        self.assertEqual(extra.status_code, 422)


if __name__ == "__main__":
    unittest.main()
