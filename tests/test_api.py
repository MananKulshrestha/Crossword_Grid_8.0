from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from fkgrid.api.dependencies import build_demo_dependencies
from fkgrid.api.main import create_app


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(create_app(build_demo_dependencies()))

    def test_swagger_and_openapi_are_available(self) -> None:
        docs = self.client.get("/docs")
        self.assertEqual(docs.status_code, 200)
        self.assertIn("swagger-ui", docs.text.lower())

        openapi = self.client.get("/openapi.json")
        self.assertEqual(openapi.status_code, 200)
        document = openapi.json()
        self.assertEqual(document["info"]["title"], "FK GRiD Query Recovery API")
        self.assertIn("/v1/query-recovery/turn", document["paths"])
        self.assertIn("RecoveryRequest", document["components"]["schemas"])
        self.assertIn("RecoveryResponse", document["components"]["schemas"])
        examples = document["paths"]["/v1/query-recovery/turn"]["post"]["requestBody"]["content"]["application/json"]["examples"]
        self.assertIn("demo_clarification", examples)
        self.assertEqual(examples["demo_clarification"]["value"]["gate"]["decision"], "RECOVERY_ELIGIBLE")

    def test_health_readiness_capabilities_and_example(self) -> None:
        health = self.client.get("/healthz")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["status"], "ok")
        self.assertEqual(self.client.get("/readyz").json()["ready"], True)

        capabilities = self.client.get("/v1/query-recovery/capabilities").json()
        self.assertEqual(capabilities["workflow"], "QueryRecoveryWorkflow")
        self.assertIn("POST /v1/query-recovery/turn", capabilities["endpoints"])

        example = self.client.get("/v1/query-recovery/example")
        self.assertEqual(example.status_code, 200)
        self.assertEqual(example.json()["gate"]["decision"], "RECOVERY_ELIGIBLE")

    def test_post_turn_delegates_to_existing_workflow(self) -> None:
        payload = self.client.get("/v1/query-recovery/example").json()
        response = self.client.post("/v1/query-recovery/turn", json=payload)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["outcome"], "RECOVERED_TIER1")
        self.assertFalse(body["event"]["planner_called"])
        self.assertEqual(body["event"]["retrieval_run_count"], 2)
        self.assertEqual([item["label"] for item in body["suggestions"]], ["Shirts", "Blazers", "Pants"])
        self.assertIsNone(body["clarification"])

    def test_extra_fields_are_rejected_at_api_boundary(self) -> None:
        payload = self.client.get("/v1/query-recovery/example").json()
        payload["unexpected"] = "reject-me"
        response = self.client.post("/v1/query-recovery/turn", json=payload)
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
