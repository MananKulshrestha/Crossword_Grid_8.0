from __future__ import annotations

from fastapi.testclient import TestClient

from fkgrid.api.container import create_demo_container
from fkgrid.api.main import app, create_app

client = TestClient(create_app(create_demo_container()))


def workflow_payload() -> dict[str, object]:
    return {
        "run_id": "api-test-run-001",
        "evidence_window": {
            "window_start": "2026-07-01T00:00:00Z",
            "window_end": "2026-08-01T00:00:00Z",
        },
        "compatibility": {
            "catalog_version": "cat-demo-1",
            "taxonomy_version": "tax-demo-1",
            "category_schema_version": "schema-demo-1",
            "lexicon_version": "lex-demo-1",
            "normalizer_version": "normalizer-v1",
            "mapping_schema_version": "mapping-v1",
            "rank_policy_version": "rank-v1",
        },
        "active_lexicon_version": "lex-demo-1",
        "regression_policy_version": "regression-v1",
        "shadow_policy_version": "shadow-v1",
    }


def test_health_ready_and_openapi_are_available() -> None:
    assert app.state.catalog_language.mode == "gemma"
    assert client.get("/health").json()["status"] == "ok"
    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["mode"] == "demo"

    openapi = client.get("/openapi.json")
    assert openapi.status_code == 200
    paths = openapi.json()["paths"]
    assert "/api/v1/catalog-language/tier2/runs" in paths
    assert "/api/v1/catalog-language/lookup" in paths


def test_capabilities_identify_tier_two_and_forbidden_runtime_actions() -> None:
    payload = client.get("/api/v1/catalog-language/capabilities").json()

    assert payload["tier"] == "TIER_2"
    assert "propose_canonical_mapping" in payload["capabilities"]
    assert "update_cart" in payload["forbidden_capabilities"]


def test_lookup_endpoint_uses_existing_deterministic_runtime_lookup() -> None:
    response = client.post(
        "/api/v1/catalog-language/lookup",
        json={
            "term": "trainers",
            "locale": "en-IN",
            "taxonomy_node_id": "footwear",
            "lexicon_version": "lex-demo-1",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["compatibility_ok"] is True
    assert body["mappings"][0]["target_id"] == "athletic-shoes"


def test_tier_two_endpoint_executes_existing_workflow() -> None:
    response = client.post("/api/v1/catalog-language/tier2/runs", json=workflow_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "COMPLETED"
    assert body["candidate"]["parent_lexicon_version"] == "lex-demo-1"
    assert body["activation"]["activated"] is True


def test_invalid_strict_body_is_rejected_before_workflow_execution() -> None:
    payload = workflow_payload()
    payload["unexpected"] = True

    response = client.post("/api/v1/catalog-language/tier2/runs", json=payload)

    assert response.status_code == 422
