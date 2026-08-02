"""Bounded Google Gemma 4 adapter for the Quality Sentinel classifier port.

The adapter sends one redacted evidence projection, registers no tools, requests
JSON, and revalidates the response before returning the domain proposal.
Provider credentials are accepted only at runtime and are held as ``SecretStr``.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from pydantic import SecretStr, ValidationError

from fkgrid.domain.quality import (
    CatalogFact,
    EvidenceItem,
    EvidencePacket,
    IssueClass,
    QualityAssessmentProposal,
)
from fkgrid.ports.quality import QualityClassifier

DEFAULT_MODEL_ALIAS = "gemma-4-26b-a4b-it"
DEFAULT_PROMPT_VERSION = "quality_v1"
DEFAULT_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_TIMEOUT_MS = 1_800
MAX_OUTPUT_TOKENS = 600


class GemmaQualityModelError(RuntimeError):
    """Safe provider/validation error; provider response text is never exposed."""


class QualityModelTransport(Protocol):
    def generate(
        self,
        *,
        model_alias: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
        timeout_ms: int,
    ) -> str: ...


class GeminiGenerateContentTransport:
    """Google REST transport for Gemma 4's ``generateContent`` method."""

    def __init__(self, endpoint: str, api_key: SecretStr) -> None:
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("GEMMA_ENDPOINT_MUST_USE_HTTPS")
        self.endpoint = endpoint
        self._api_key = api_key

    def generate(
        self,
        *,
        model_alias: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
        timeout_ms: int,
    ) -> str:
        endpoint = self.endpoint.replace("{model}", quote(model_alias, safe="-_."))
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": MAX_OUTPUT_TOKENS,
                "responseFormat": {
                    "text": {"mimeType": "application/json", "schema": response_schema}
                },
            },
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request = Request(
            endpoint,
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self._api_key.get_secret_value(),
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=max(timeout_ms / 1000, 0.05)) as response:  # nosec B310
                provider_response = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise GemmaQualityModelError("GEMMA_PROVIDER_UNAVAILABLE") from exc
        return _extract_text(provider_response)


class QualityPrompt:
    """Loads the immutable prompt resource instead of embedding prompt text in code."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = (
            Path(path)
            if path is not None
            else Path(__file__).with_name("prompts") / ("quality_classification_v1.md")
        )

    def text(self) -> str:
        try:
            prompt = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise GemmaQualityModelError("QUALITY_PROMPT_UNAVAILABLE") from exc
        if not prompt.strip():
            raise GemmaQualityModelError("QUALITY_PROMPT_EMPTY")
        return prompt


def _output_model() -> type[Any]:
    from pydantic import BaseModel, ConfigDict, Field

    class GemmaQualityOutput(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)

        issue_class: str
        confidence: float = Field(ge=0, le=1)
        supporting_evidence_ids: list[str] = Field(default_factory=list, max_length=20)
        contradicting_evidence_ids: list[str] = Field(default_factory=list, max_length=20)
        missing_information: list[str] = Field(default_factory=list, max_length=20)
        bounded_summary: str = Field(min_length=1, max_length=500)

    return GemmaQualityOutput


def _extract_text(provider_response: Any) -> str:
    try:
        candidates = provider_response["candidates"]
        parts = candidates[0]["content"]["parts"]
        text = "".join(part["text"] for part in parts if isinstance(part.get("text"), str))
    except (KeyError, IndexError, TypeError) as exc:
        raise GemmaQualityModelError("GEMMA_RESPONSE_INVALID") from exc
    if not text.strip():
        raise GemmaQualityModelError("GEMMA_RESPONSE_EMPTY")
    return text


def _safe_catalog_value(value: Any) -> Any:
    """Keep arbitrary catalog data bounded and JSON-shaped before prompting."""

    try:
        encoded = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
        if len(encoded) > 600:
            return encoded[:597] + "..."
        return json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError):
        return "[UNAVAILABLE]"


def _item_payload(item: EvidenceItem) -> dict[str, Any]:
    # Identity, source references, reporter/correlation groups, and asset
    # references are intentionally omitted. The opaque evidence ID is the only
    # identifier needed for a citation and downstream validation.
    return {
        "evidence_id": item.evidence_id,
        "kind": item.kind.value,
        "source_type": item.source_type.value,
        "source_class": item.source_class,
        "severity": item.severity.value,
        "occurred_at": item.occurred_at.isoformat(),
        "normalized_issue_type": item.normalized_issue_type.value,
        "redacted_excerpt": item.redacted_excerpt,
        "catalog_fact_ids": item.catalog_fact_ids,
        "occurrence_count": item.occurrence_count,
        "source_count": item.source_count,
    }


def _fact_payload(fact: CatalogFact) -> dict[str, Any]:
    return {
        "evidence_id": fact.evidence_id,
        "field_path": fact.field_path,
        "value": _safe_catalog_value(fact.value),
        "truth_status": fact.truth_status,
    }


def _packet_payload(packet: EvidencePacket) -> dict[str, Any]:
    return {
        "case_id": packet.case_id,
        "policy_version": packet.policy_version,
        "group": {
            "scope_kind": packet.group_key.scope_kind,
            "signal_type": packet.group_key.signal_type.value,
            "window_start": packet.group_key.window_start.isoformat(),
            "window_end": packet.group_key.window_end.isoformat(),
        },
        "allowed_issue_classes": [item.value for item in packet.allowed_issue_classes],
        "items": [_item_payload(item) for item in packet.items],
        "catalog_facts": [
            _fact_payload(fact) for snapshot in packet.snapshots for fact in snapshot.facts
        ],
        "missing_information": packet.missing_information,
        "untrusted_content_notice": packet.untrusted_content_notice,
    }


def _response_schema(packet: EvidencePacket) -> dict[str, Any]:
    """Build the smallest Gemini-compatible schema for this packet's allowlist."""

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "issue_class": {
                "type": "string",
                "enum": [item.value for item in packet.allowed_issue_classes],
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "supporting_evidence_ids": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 20,
            },
            "contradicting_evidence_ids": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 20,
            },
            "missing_information": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 20,
            },
            "bounded_summary": {"type": "string"},
        },
        "required": [
            "issue_class",
            "confidence",
            "supporting_evidence_ids",
            "contradicting_evidence_ids",
            "missing_information",
            "bounded_summary",
        ],
    }


class Gemma4QualityClassifier(QualityClassifier):
    """One bounded Gemma 4 classification call implementing ``QualityClassifier``."""

    def __init__(
        self,
        transport: QualityModelTransport,
        *,
        prompt: QualityPrompt | None = None,
        model_alias: str = DEFAULT_MODEL_ALIAS,
        prompt_version: str = DEFAULT_PROMPT_VERSION,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ) -> None:
        if not model_alias.strip() or len(model_alias) > 128:
            raise ValueError("GEMMA_MODEL_ALIAS_INVALID")
        if not prompt_version.strip() or len(prompt_version) > 128:
            raise ValueError("QUALITY_PROMPT_VERSION_INVALID")
        if timeout_ms < 100 or timeout_ms > DEFAULT_TIMEOUT_MS:
            raise ValueError("QUALITY_CLASSIFIER_TIMEOUT_INVALID")
        self.transport = transport
        self.prompt = prompt or QualityPrompt()
        self.model_alias = model_alias
        self.prompt_version = prompt_version
        self.timeout_ms = timeout_ms

    @classmethod
    def from_environment(
        cls,
        *,
        environment: Mapping[str, str] | None = None,
        prompt: QualityPrompt | None = None,
    ) -> Gemma4QualityClassifier:
        values = os.environ if environment is None else environment
        api_key = values.get("FKGRID_GEMMA_API_KEY") or values.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMMA_API_KEY_REQUIRED")
        endpoint = values.get("FKGRID_GEMMA_ENDPOINT", DEFAULT_ENDPOINT)
        model_alias = values.get("FKGRID_GEMMA_MODEL_ALIAS", DEFAULT_MODEL_ALIAS)
        prompt_version = values.get("FKGRID_GEMMA_PROMPT_VERSION", DEFAULT_PROMPT_VERSION)
        timeout_text = values.get("FKGRID_GEMMA_TIMEOUT_MS", str(DEFAULT_TIMEOUT_MS))
        try:
            timeout_ms = int(timeout_text)
        except ValueError as exc:
            raise ValueError("QUALITY_CLASSIFIER_TIMEOUT_INVALID") from exc
        return cls(
            GeminiGenerateContentTransport(endpoint, SecretStr(api_key)),
            prompt=prompt,
            model_alias=model_alias,
            prompt_version=prompt_version,
            timeout_ms=timeout_ms,
        )

    def classify(self, packet: EvidencePacket) -> QualityAssessmentProposal:
        user_prompt = json.dumps(
            _packet_payload(packet), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        try:
            response_text = self.transport.generate(
                model_alias=self.model_alias,
                system_prompt=self.prompt.text(),
                user_prompt=user_prompt,
                response_schema=_response_schema(packet),
                timeout_ms=self.timeout_ms,
            )
            raw = json.loads(response_text)
            output = _output_model().model_validate(raw)
        except (GemmaQualityModelError, json.JSONDecodeError, ValidationError, TypeError) as exc:
            raise GemmaQualityModelError("GEMMA_OUTPUT_INVALID") from exc

        try:
            issue_class = IssueClass(output.issue_class)
        except ValueError as exc:
            raise GemmaQualityModelError("GEMMA_ISSUE_CLASS_INVALID") from exc
        if issue_class not in packet.allowed_issue_classes:
            raise GemmaQualityModelError("GEMMA_ISSUE_CLASS_NOT_ALLOWED")
        available_ids = {item.evidence_id for item in packet.items} | {
            fact.evidence_id for snapshot in packet.snapshots for fact in snapshot.facts
        }
        supporting = set(output.supporting_evidence_ids)
        contradicting = set(output.contradicting_evidence_ids)
        if not supporting.issubset(available_ids) or not contradicting.issubset(available_ids):
            raise GemmaQualityModelError("GEMMA_CITATION_NOT_RESOLVED")
        if supporting & contradicting:
            raise GemmaQualityModelError("GEMMA_CITATION_ROLES_CONFLICT")

        missing = list(dict.fromkeys([*packet.missing_information, *output.missing_information]))
        return QualityAssessmentProposal(
            issue_class=issue_class,
            confidence=output.confidence,
            supporting_evidence_ids=output.supporting_evidence_ids,
            contradicting_evidence_ids=output.contradicting_evidence_ids,
            missing_information=missing[:20],
            bounded_summary=output.bounded_summary,
            model_alias=self.model_alias,
            prompt_version=self.prompt_version,
        )
