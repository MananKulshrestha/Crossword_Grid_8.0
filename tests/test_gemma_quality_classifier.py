from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from fkgrid.adapters.model import gemma_quality_classifier as adapter
from fkgrid.adapters.model.gemma_quality_classifier import (
    DEFAULT_MODEL_ALIAS,
    DeepInfraChatCompletionsTransport,
    GeminiGenerateContentTransport,
    Gemma4QualityClassifier,
    GemmaQualityModelError,
)
from fkgrid.domain.quality import (
    EvidenceItem,
    EvidenceKind,
    EvidencePacket,
    GroupKey,
    IssueClass,
    QualityEntityBinding,
    Severity,
    SignalType,
    SourceType,
)

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


def packet() -> EvidencePacket:
    binding = QualityEntityBinding(
        product_id="product_secret",
        sku_id="sku_secret",
        listing_id="listing_secret",
        catalog_version="catalog_v1",
    )
    return EvidencePacket(
        case_id="case_1",
        policy_version="quality_policy_v1",
        group_key=GroupKey(
            scope_kind="LISTING",
            scope_id="listing_secret",
            product_id="product_secret",
            sku_id="sku_secret",
            listing_id="listing_secret",
            signal_type=SignalType.OBJECTIVE_INCORRECT,
            window_start=NOW - timedelta(days=7),
            window_end=NOW,
        ),
        items=[
            EvidenceItem(
                evidence_id="evidence_1",
                kind=EvidenceKind.PROSE_EXCERPT,
                signal_id="signal_private",
                binding=binding,
                source_type=SourceType.EXPLICIT_REPORT,
                source_class="REPORT",
                severity=Severity.HIGH,
                occurred_at=NOW - timedelta(days=1),
                normalized_issue_type=SignalType.OBJECTIVE_INCORRECT,
                redacted_excerpt="The material differs from the verified catalog value.",
                evidence_hash="a" * 64,
            )
        ],
        allowed_issue_classes=list(IssueClass),
    )


class FakeTransport:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def generate(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        return json.dumps(self.response)


def valid_response() -> dict[str, Any]:
    return {
        "issue_class": "LISTING_CONTENT_MISMATCH",
        "risk_rating": "HIGH",
        "confidence": 0.91,
        "supporting_evidence_ids": ["evidence_1"],
        "contradicting_evidence_ids": [],
        "missing_information": [],
        "bounded_summary": "The report supports a listing-content mismatch.",
    }


def test_gemma_classifier_returns_strict_proposal_and_redacted_projection() -> None:
    transport = FakeTransport(valid_response())
    classifier = Gemma4QualityClassifier(transport)

    proposal = classifier.classify(packet())

    assert proposal.issue_class is IssueClass.LISTING_CONTENT_MISMATCH
    assert proposal.model_alias == DEFAULT_MODEL_ALIAS
    assert proposal.prompt_version == "quality_v2"
    assert proposal.supporting_evidence_ids == ["evidence_1"]
    sent_packet = json.loads(transport.calls[0]["user_prompt"])
    assert sent_packet["items"][0]["evidence_id"] == "evidence_1"
    assert "product_secret" not in transport.calls[0]["user_prompt"]
    assert "signal_private" not in transport.calls[0]["user_prompt"]
    assert transport.calls[0]["response_schema"]["additionalProperties"] is False
    assert transport.calls[0]["response_schema"]["properties"]["risk_rating"]["enum"] == [
        "CRITICAL",
        "HIGH",
        "MEDIUM",
        "LOW",
    ]
    assert transport.calls[0]["timeout_ms"] == 1800


def test_gemma_classifier_rejects_extra_fields_and_foreign_citations() -> None:
    extra = FakeTransport({**valid_response(), "queue_name": "do-not-accept"})
    with pytest.raises(GemmaQualityModelError, match="GEMMA_OUTPUT_INVALID"):
        Gemma4QualityClassifier(extra).classify(packet())

    foreign = FakeTransport({**valid_response(), "supporting_evidence_ids": ["foreign"]})
    with pytest.raises(GemmaQualityModelError, match="GEMMA_CITATION_NOT_RESOLVED"):
        Gemma4QualityClassifier(foreign).classify(packet())


def test_from_environment_uses_runtime_key_without_persisting_configuration() -> None:
    classifier = Gemma4QualityClassifier.from_environment(
        environment={
            "FKGRID_DEEPINFRA_API_KEY": "runtime-only",
            "FKGRID_GEMMA_TIMEOUT_MS": "1500",
        }
    )

    assert classifier.model_alias == DEFAULT_MODEL_ALIAS
    assert classifier.timeout_ms == 1500
    assert "runtime-only" not in repr(classifier)
    assert isinstance(classifier.transport, DeepInfraChatCompletionsTransport)


def test_from_environment_keeps_gemini_as_explicit_compatibility_provider() -> None:
    classifier = Gemma4QualityClassifier.from_environment(
        environment={
            "FKGRID_GEMMA_PROVIDER": "gemini",
            "FKGRID_GEMMA_API_KEY": "runtime-only",
        }
    )

    assert isinstance(classifier.transport, GeminiGenerateContentTransport)


def test_google_transport_uses_header_auth_and_structured_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(
                {"candidates": [{"content": {"parts": [{"text": json.dumps(valid_response())}]}}]}
            ).encode()

    captured: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: float) -> FakeResponse:
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(adapter, "urlopen", fake_urlopen)
    transport = GeminiGenerateContentTransport(
        "https://example.test/v1beta/models/{model}:generateContent",
        adapter.SecretStr("runtime-only"),
    )
    response = transport.generate(
        model_alias="gemma-4-26b-a4b-it",
        system_prompt="system",
        user_prompt="user",
        response_schema={"type": "object"},
        timeout_ms=1200,
    )

    request = captured["request"]
    body = json.loads(request.data)
    assert request.full_url.endswith("models/gemma-4-26b-a4b-it:generateContent")
    assert request.get_header("X-goog-api-key") == "runtime-only"
    assert body["generationConfig"]["responseFormat"]["text"]["mimeType"] == "application/json"
    assert json.loads(response)["issue_class"] == "LISTING_CONTENT_MISMATCH"


def test_deepinfra_transport_uses_bearer_auth_and_json_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(
                {"choices": [{"message": {"content": json.dumps(valid_response())}}]}
            ).encode()

    captured: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: float) -> FakeResponse:
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(adapter, "urlopen", fake_urlopen)
    transport = DeepInfraChatCompletionsTransport(
        "https://api.deepinfra.com/v1/openai",
        adapter.SecretStr("runtime-only"),
    )
    response = transport.generate(
        model_alias=DEFAULT_MODEL_ALIAS,
        system_prompt="system",
        user_prompt="user",
        response_schema={"type": "object"},
        timeout_ms=1200,
    )

    request = captured["request"]
    body = json.loads(request.data)
    assert request.full_url.endswith("/v1/openai/chat/completions")
    assert request.get_header("Authorization") == "Bearer runtime-only"
    assert body["model"] == DEFAULT_MODEL_ALIAS
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert json.loads(response)["issue_class"] == "LISTING_CONTENT_MISMATCH"
