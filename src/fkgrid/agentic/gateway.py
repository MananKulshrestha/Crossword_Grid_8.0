"""Structured model gateway, Gemma 4 adapter, prompts, and deterministic fake."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.parse import quote
from urllib.request import Request, urlopen

from pydantic import TypeAdapter, ValidationError

from .contracts import (
    CompatibilityTuple,
    IntentDeltaV1,
    ClarificationDraft,
    FollowUpSelection,
    RecoveryPlan,
    ModelCallType,
    ModelRequest,
    ModelResponse,
    ModelStatus,
    ResearchSynthesisV1,
    ValidationIssue,
)
from .validation import canonical_hash, canonical_json


class StructuredModelGateway(Protocol):
    def complete(self, request: ModelRequest) -> ModelResponse: ...


class PromptSpec:
    def __init__(
        self,
        call_type: ModelCallType,
        prompt_id: str,
        prompt_version: str,
        input_schema_version: str,
        output_schema_version: str,
        filename: str,
    ) -> None:
        self.call_type = call_type
        self.prompt_id = prompt_id
        self.prompt_version = prompt_version
        self.input_schema_version = input_schema_version
        self.output_schema_version = output_schema_version
        self.filename = filename


class PromptRegistry:
    """Loads immutable prompt resources and binds them to a logical call."""

    _specs = {
        ModelCallType.RESOLVE_INTENT_AND_DELTA: PromptSpec(
            ModelCallType.RESOLVE_INTENT_AND_DELTA,
            "intent_v1",
            "1",
            "IntentContextProjectionV1",
            "IntentDeltaV1",
            "intent_v1.md",
        ),
        ModelCallType.GENERATE_CLARIFYING_QUESTION: PromptSpec(
            ModelCallType.GENERATE_CLARIFYING_QUESTION,
            "clarification_v1",
            "1",
            "ClarificationPacketV1",
            "ClarificationDraftV1",
            "clarification_v1.md",
        ),
        ModelCallType.PLAN_CONSTRAINED_REPAIR: PromptSpec(
            ModelCallType.PLAN_CONSTRAINED_REPAIR,
            "recovery_v1",
            "1",
            "RecoveryContextV1",
            "RecoveryPlanV1",
            "recovery_v1.md",
        ),
        ModelCallType.SYNTHESIZE_RESEARCH_ANSWER: PromptSpec(
            ModelCallType.SYNTHESIZE_RESEARCH_ANSWER,
            "research_synthesis_v1",
            "1",
            "ResearchContextV1",
            "ResearchSynthesisV1",
            "research_synthesis_v1.md",
        ),
        ModelCallType.GENERATE_FOLLOW_UP_SUGGESTIONS: PromptSpec(
            ModelCallType.GENERATE_FOLLOW_UP_SUGGESTIONS,
            "follow_up_phrasing_v1",
            "1",
            "FollowUpCandidateContextV1",
            "FollowUpSelectionV1",
            "follow_up_phrasing_v1.md",
        ),
    }

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(__file__).with_name("prompts")

    def spec(self, call_type: ModelCallType) -> PromptSpec:
        try:
            return self._specs[call_type]
        except KeyError as exc:
            raise ValueError(f"no shopper prompt registered for {call_type.value}") from exc

    def text(self, call_type: ModelCallType) -> str:
        spec = self.spec(call_type)
        return (self.root / spec.filename).read_text(encoding="utf-8")

    def checksum(self, call_type: ModelCallType) -> str:
        return hashlib.sha256(self.text(call_type).encode("utf-8")).hexdigest()

    def manifest_entry(self, call_type: ModelCallType) -> dict[str, Any]:
        spec = self.spec(call_type)
        return {
            "prompt_id": spec.prompt_id,
            "prompt_version": spec.prompt_version,
            "file": spec.filename,
            "input_schema": spec.input_schema_version,
            "output_schema": spec.output_schema_version,
            "file_checksum": self.checksum(call_type),
        }

    def output_schema(self, call_type: ModelCallType) -> dict[str, Any]:
        if call_type is ModelCallType.RESOLVE_INTENT_AND_DELTA:
            return IntentDeltaV1.model_json_schema()
        if call_type is ModelCallType.GENERATE_CLARIFYING_QUESTION:
            return ClarificationDraft.model_json_schema()
        if call_type is ModelCallType.PLAN_CONSTRAINED_REPAIR:
            # Pydantic's JSON schema for a discriminated union is strict and
            # keeps the recovery planner limited to the three declared actions.
            from pydantic import TypeAdapter

            return TypeAdapter(RecoveryPlan).json_schema()
        if call_type is ModelCallType.SYNTHESIZE_RESEARCH_ANSWER:
            return ResearchSynthesisV1.model_json_schema()
        if call_type is ModelCallType.GENERATE_FOLLOW_UP_SUGGESTIONS:
            return FollowUpSelection.model_json_schema()
        # The remaining schemas are intentionally small and are validated by
        # their owner before use. They remain strict JSON objects at the gateway.
        return {"type": "object", "additionalProperties": False}

    def build_request(
        self,
        call_type: ModelCallType,
        payload: dict[str, Any],
        compatibility: CompatibilityTuple,
        call_id: str,
        deadline_ms: int,
        model_alias: str,
    ) -> ModelRequest:
        spec = self.spec(call_type)
        max_output_tokens = {
            ModelCallType.RESOLVE_INTENT_AND_DELTA: 500,
            ModelCallType.GENERATE_CLARIFYING_QUESTION: 200,
            ModelCallType.PLAN_CONSTRAINED_REPAIR: 300,
            ModelCallType.SYNTHESIZE_RESEARCH_ANSWER: 800,
            ModelCallType.GENERATE_FOLLOW_UP_SUGGESTIONS: 300,
        }.get(call_type, 300)
        return ModelRequest(
            call_id=call_id,
            logical_call_type=call_type,
            model_alias=model_alias,
            prompt_id=spec.prompt_id,
            prompt_version=spec.prompt_version,
            input_schema_version=spec.input_schema_version,
            output_schema_version=spec.output_schema_version,
            input_payload=payload,
            output_schema=self.output_schema(call_type),
            deadline_ms=deadline_ms,
            temperature=0.0,
            max_output_tokens=max_output_tokens,
            compatibility_tuple=compatibility,
        )


def _validate_provider_output(request: ModelRequest, output: dict[str, Any]) -> list[ValidationIssue]:
    """Revalidate provider JSON before exposing it as a usable model response."""

    try:
        encoded = canonical_json(output)
        if request.logical_call_type is ModelCallType.RESOLVE_INTENT_AND_DELTA:
            IntentDeltaV1.model_validate_json(encoded)
        elif request.logical_call_type is ModelCallType.GENERATE_CLARIFYING_QUESTION:
            ClarificationDraft.model_validate_json(encoded)
        elif request.logical_call_type is ModelCallType.PLAN_CONSTRAINED_REPAIR:
            TypeAdapter(RecoveryPlan).validate_json(encoded)
        elif request.logical_call_type is ModelCallType.SYNTHESIZE_RESEARCH_ANSWER:
            ResearchSynthesisV1.model_validate_json(encoded)
        elif request.logical_call_type is ModelCallType.GENERATE_FOLLOW_UP_SUGGESTIONS:
            FollowUpSelection.model_validate_json(encoded)
    except ValidationError:
        return [
            ValidationIssue(
                code="OUTPUT_SCHEMA_INVALID",
                path="output_payload",
                safe_message="Provider output failed the declared contract.",
            )
        ]
    return []


def _response(
    request: ModelRequest,
    status: ModelStatus,
    output: dict[str, Any] | None,
    provider_name: str,
    latency_ms: int,
    issues: list[ValidationIssue] | None = None,
    retryable: bool = False,
) -> ModelResponse:
    output_text = canonical_json(output) if output is not None else ""
    return ModelResponse(
        call_id=request.call_id,
        status=status,
        output_payload=output,
        raw_output_hash=hashlib.sha256(output_text.encode("utf-8")).hexdigest() if output is not None else None,
        input_hash=canonical_hash(request.input_payload),
        output_hash=canonical_hash(output) if output is not None else None,
        provider_name=provider_name,
        model_alias=request.model_alias,
        prompt_id=request.prompt_id,
        prompt_version=request.prompt_version,
        latency_ms=max(0, latency_ms),
        validation_issues=issues or [],
        retryable=retryable,
    )


class FakeModelGateway:
    """Deterministic model fake with injectable failures for contract tests."""

    provider_name = "fake"

    def __init__(self) -> None:
        self.calls: list[ModelRequest] = []
        self.script: dict[ModelCallType, list[ModelResponse]] = {}

    def queue_response(self, call_type: ModelCallType, response: ModelResponse) -> None:
        self.script.setdefault(call_type, []).append(response)

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        queued = self.script.get(request.logical_call_type, [])
        if queued:
            return queued.pop(0)
        if request.deadline_ms <= 0:
            return _response(request, ModelStatus.TIMEOUT, None, self.provider_name, 0, retryable=True)
        started = time.perf_counter()
        output: dict[str, Any]
        if request.logical_call_type is ModelCallType.RESOLVE_INTENT_AND_DELTA:
            output = self._intent(request.input_payload)
        elif request.logical_call_type is ModelCallType.GENERATE_CLARIFYING_QUESTION:
            options = request.input_payload.get("options", [])
            output = {
                "question": "Which option would you like me to use?",
                "choice_ids": [option.get("choice_id") for option in options],
                "question_count": 1,
                "mentioned_values": [],
            }
        elif request.logical_call_type is ModelCallType.SYNTHESIZE_RESEARCH_ANSWER:
            sources = request.input_payload.get("sources", [])
            ids = [source.get("source_id") for source in sources[:2]]
            output = {
                "schema_version": "ResearchSynthesisV1",
                "answer_summary": "Here is the bounded external context available for this question.",
                "claims": [
                    {
                        "claim_id": "claim_1",
                        "text": "The supplied sources provide general guidance, with uncertainty where they disagree.",
                        "support_source_ids": ids[:1] or ["missing"],
                        "conflict_source_ids": [],
                        "confidence": "MEDIUM",
                    }
                ],
                "conflicts": [],
                "unanswered_points": [],
            }
        elif request.logical_call_type is ModelCallType.GENERATE_FOLLOW_UP_SUGGESTIONS:
            candidates = request.input_payload.get("candidates", [])
            output = {
                "selected_candidate_ids": [item.get("candidate_id") for item in candidates[:3]],
                "labels": [
                    {"candidate_id": item.get("candidate_id"), "label": item.get("safe_default_label")}
                    for item in candidates[:3]
                ],
            }
        else:
            output = {}
        latency = int((time.perf_counter() - started) * 1000)
        return _response(request, ModelStatus.OK, output, self.provider_name, latency)

    @staticmethod
    def _intent(payload: dict[str, Any]) -> dict[str, Any]:
        text = str(payload.get("current_message_verbatim", ""))
        lower = text.casefold()
        action = "SEARCH"
        if "show" in lower and "cart" in lower:
            action = "SHOW_CART"
        elif "compare" in lower:
            action = "COMPARE"
        elif "availability" in lower or "available" in lower or "come in" in lower:
            action = "CHECK_AVAILABILITY"
        elif "detail" in lower or "tell me more" in lower:
            action = "PRODUCT_DETAILS"
        elif "research" in lower or "current" in lower or "latest" in lower:
            action = "RESEARCH_EXTERNAL"
        elif "add" in lower and "cart" in lower:
            action = "UPDATE_CART"
        elif "cheaper" in lower or "instead" in lower or "refine" in lower:
            action = "REFINE"

        operations: list[dict[str, Any]] = []
        if "cheaper" in lower:
            operations.append(
                {
                    "op": "SET_COMPARATIVE",
                    "kind": "CHEAPER",
                    "anchor_reference": None,
                }
            )
        size = next((candidate for candidate in ("xs", "s", "m", "l", "xl") if f"size {candidate}" in lower), None)
        if size:
            start = lower.find(f"size {size}")
            operations.append(
                {
                    "op": "SET_HARD",
                    "field_id": "size",
                    "operator": "EQ",
                    "typed_values": [f"size_{size}"],
                    "strength": "HARD",
                    "evidence_span": [start, start + len(f"size {size}")],
                    "context_ref": None,
                }
            )
        references: list[dict[str, str]] = []
        for word, ordinal in (("first", "1"), ("second", "2"), ("third", "3"), ("fourth", "4")):
            if word in lower:
                references.append({"kind": "ORDINAL", "value": ordinal})
        return {
            "schema_version": "IntentDeltaV1",
            "primary_action": action,
            "delta_operations": operations,
            "references": references,
            "action_parameters": {},
            "unknown_terms": [],
            "candidate_interpretations": [],
            "clarification_candidate": None,
        }


class ModelTransport(Protocol):
    def post_json(self, payload: dict[str, Any], timeout_ms: int) -> dict[str, Any]: ...


class UrllibJsonTransport:
    """Small OpenAI-compatible transport with no provider SDK dependency."""

    def __init__(self, endpoint: str, api_key: str | None = None) -> None:
        self.endpoint = endpoint
        self.api_key = api_key

    def post_json(self, payload: dict[str, Any], timeout_ms: int) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(self.endpoint, data=body, headers=headers, method="POST")
        with urlopen(request, timeout=max(timeout_ms / 1000, 0.05)) as response:  # noqa: S310 - endpoint is injected
            return json.loads(response.read().decode("utf-8"))


class GeminiGenerateContentTransport:
    """Translate the provider-neutral request into Google's REST contract."""

    def __init__(self, endpoint: str, api_key: str) -> None:
        self.endpoint = endpoint
        self.api_key = api_key

    def post_json(self, payload: dict[str, Any], timeout_ms: int) -> dict[str, Any]:
        model_alias = str(payload.get("model", "")).split("/")[-1].lower()
        endpoint = self.endpoint.replace("{model}", quote(model_alias, safe="-_."))
        messages = payload.get("messages", [])
        system_parts = [
            {"text": message["content"]}
            for message in messages
            if message.get("role") == "system" and isinstance(message.get("content"), str)
        ]
        contents = [
            {
                "role": "model" if message.get("role") == "assistant" else "user",
                "parts": [{"text": message["content"]}],
            }
            for message in messages
            if message.get("role") != "system" and isinstance(message.get("content"), str)
        ]
        generation_config: dict[str, Any] = {
            "temperature": payload.get("temperature", 0.0),
            "maxOutputTokens": payload.get("max_tokens", 800),
            "responseMimeType": "application/json",
        }
        # The application remains the authorization boundary. Google exposes a
        # deliberately smaller JSON-schema subset than the full discriminated
        # Pydantic contracts, so native JSON mode is used here and the complete
        # contract is revalidated after the response returns.
        google_payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": generation_config,
        }
        if system_parts:
            google_payload["systemInstruction"] = {"parts": system_parts}
        body = json.dumps(google_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request = Request(
            endpoint,
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self.api_key,
            },
            method="POST",
        )
        with urlopen(request, timeout=max(timeout_ms / 1000, 0.05)) as response:  # noqa: S310 - endpoint is injected
            provider_response = json.loads(response.read().decode("utf-8"))
        parts = provider_response["candidates"][0]["content"]["parts"]
        content = "".join(part["text"] for part in parts if isinstance(part.get("text"), str))
        if not content:
            raise ValueError("provider returned no text content")
        # Return the gateway's provider-neutral shape; the adapter performs the
        # same JSON and semantic validation for both transports.
        return {"choices": [{"message": {"content": content}}]}


class Gemma4ModelAdapter:
    """Gemma 4 adapter through an injected provider transport."""

    provider_name = "gemma-openai-compatible"
    default_model_alias = "gemma-4-26b-a4b-it"

    def __init__(
        self,
        transport: ModelTransport,
        prompts: PromptRegistry | None = None,
        model_alias: str = default_model_alias,
        provider_name: str = "gemma-openai-compatible",
    ) -> None:
        self.transport = transport
        self.prompts = prompts or PromptRegistry()
        self.model_alias = model_alias
        self.provider_name = provider_name

    @classmethod
    def from_environment(
        cls,
        *,
        prompts: PromptRegistry | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> "Gemma4ModelAdapter":
        """Build the live adapter from environment-only provider settings.

        The key is intentionally never read from a repository file, serialized,
        or included in trace metadata. The endpoint remains explicit because
        Gemma may be served by different OpenAI-compatible providers.
        """

        values = os.environ if environment is None else environment
        protocol = values.get("FKGRID_MODEL_PROTOCOL", "openai").casefold()
        endpoint = values.get("FKGRID_MODEL_ENDPOINT")
        api_key = values.get("FKGRID_MODEL_API_KEY") or values.get("GEMINI_API_KEY")
        if protocol == "gemini" and not endpoint:
            endpoint = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        if not endpoint:
            raise ValueError("FKGRID_MODEL_ENDPOINT_REQUIRED")
        if not api_key:
            raise ValueError("FKGRID_MODEL_API_KEY_REQUIRED")
        alias = values.get("FKGRID_MODEL_ALIAS", cls.default_model_alias)
        if protocol == "gemini":
            transport: ModelTransport = GeminiGenerateContentTransport(endpoint, api_key)
            provider_name = "google-gemini-api"
        else:
            transport = UrllibJsonTransport(endpoint, api_key)
            provider_name = "gemma-openai-compatible"
        return cls(
            transport,
            prompts=prompts,
            model_alias=alias,
            provider_name=provider_name,
        )

    def complete(self, request: ModelRequest) -> ModelResponse:
        started = time.perf_counter()
        try:
            system_prompt = self.prompts.text(request.logical_call_type)
            payload = {
                "model": request.model_alias or self.model_alias,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": canonical_json(request.input_payload)},
                ],
                "temperature": request.temperature,
                "max_tokens": request.max_output_tokens,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": request.output_schema_version,
                        "strict": True,
                        "schema": request.output_schema,
                    },
                },
                "tools": [],
            }
            provider_response = self.transport.post_json(payload, request.deadline_ms)
            content = provider_response["choices"][0]["message"]["content"]
            output = json.loads(content) if isinstance(content, str) else content
            if not isinstance(output, dict):
                raise ValueError("provider output is not a JSON object")
            validation_issues = _validate_provider_output(request, output)
            if validation_issues:
                return _response(
                    request,
                    ModelStatus.INVALID_OUTPUT,
                    None,
                    self.provider_name,
                    int((time.perf_counter() - started) * 1000),
                    issues=validation_issues,
                )
            return _response(
                request,
                ModelStatus.OK,
                output,
                self.provider_name,
                int((time.perf_counter() - started) * 1000),
            )
        except TimeoutError:
            return _response(
                request,
                ModelStatus.TIMEOUT,
                None,
                self.provider_name,
                int((time.perf_counter() - started) * 1000),
                retryable=True,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            issue = ValidationIssue(
                code="PROVIDER_RESPONSE_INVALID",
                path="response",
                safe_message="Provider response could not be parsed.",
            )
            return _response(
                request,
                ModelStatus.INVALID_OUTPUT,
                None,
                self.provider_name,
                int((time.perf_counter() - started) * 1000),
                issues=[issue],
            )
        except Exception:
            return _response(
                request,
                ModelStatus.UNAVAILABLE,
                None,
                self.provider_name,
                int((time.perf_counter() - started) * 1000),
                retryable=True,
            )
