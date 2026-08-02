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
        self.assertEqual(body["result"]["outcome"], "CLARIFICATION_REQUIRED")
        self.assertTrue(body["result"]["event"]["planner_called"])
        self.assertEqual(body["result"]["event"]["hard_filter_mutation_count"], 0)

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
