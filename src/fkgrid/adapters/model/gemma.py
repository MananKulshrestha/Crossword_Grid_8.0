"""Gemma model adapter for DeepInfra, Ollama, and OpenAI-compatible runtimes.

The workflow owns all authority and validation.  This adapter only sends the
versioned proposer/critic prompt, requests structured JSON, and converts the
provider response into the provider-neutral ``CatalogLanguageModelPort``
contract.  It never selects IDs, calls tools, or activates a lexicon.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from time import monotonic, perf_counter
from typing import Literal, cast

import httpx
from pydantic import SecretStr, ValidationError

from fkgrid.adapters.catalog_language.prompts import PromptRegistry
from fkgrid.catalog_language.serialization import canonical_json_bytes, sha256_hex
from fkgrid.domain.catalog_language import (
    CriticDraft,
    MappingDraft,
    ModelCallRequest,
    ModelCallResponse,
    ModelStatus,
)
from fkgrid.ports.catalog_language import CatalogLanguageModelPort

GemmaProvider = Literal["ollama", "openai_compatible", "deepinfra"]


@dataclass(frozen=True, slots=True)
class GemmaModelSettings:
    """Runtime configuration for a hosted or local Gemma model."""

    provider: GemmaProvider = "deepinfra"
    base_url: str = "https://api.deepinfra.com/v1/openai"
    model_name: str = "google/gemma-4-26B-A4B-it"
    api_key: SecretStr | None = None
    readiness_timeout_seconds: float = 2.0
    readiness_cache_seconds: float = 15.0
    max_output_tokens: int = 384

    def __post_init__(self) -> None:
        if not self.base_url.strip():
            raise ValueError("Gemma base_url must not be empty")
        if not self.model_name.strip():
            raise ValueError("Gemma model_name must not be empty")
        if self.readiness_timeout_seconds <= 0:
            raise ValueError("readiness_timeout_seconds must be positive")
        if self.readiness_cache_seconds <= 0:
            raise ValueError("readiness_cache_seconds must be positive")
        if not 32 <= self.max_output_tokens <= 2_048:
            raise ValueError("max_output_tokens must be between 32 and 2048")

    @classmethod
    def from_environment(cls) -> GemmaModelSettings:
        provider = os.getenv("FKGRID_GEMMA_PROVIDER", "deepinfra")
        if provider not in {"ollama", "openai_compatible", "deepinfra"}:
            raise ValueError(
                "FKGRID_GEMMA_PROVIDER must be ollama, openai_compatible, or deepinfra"
            )
        provider_value = cast(GemmaProvider, provider)
        default_base_url = (
            "http://127.0.0.1:11434"
            if provider == "ollama"
            else "https://api.deepinfra.com/v1/openai"
            if provider == "deepinfra"
            else "http://127.0.0.1:1234/v1"
        )
        raw_key = (
            os.getenv("DEEPINFRA_API_KEY")
            or os.getenv("DEEPINFRA_TOKEN")
            or os.getenv("FKGRID_GEMMA_API_KEY")
        )
        return cls(
            provider=provider_value,
            base_url=os.getenv("FKGRID_GEMMA_BASE_URL", default_base_url).rstrip("/"),
            model_name=os.getenv("FKGRID_GEMMA_MODEL", "google/gemma-4-26B-A4B-it"),
            api_key=SecretStr(raw_key) if raw_key else None,
            readiness_timeout_seconds=float(os.getenv("FKGRID_GEMMA_READY_TIMEOUT_S", "2")),
            readiness_cache_seconds=float(os.getenv("FKGRID_GEMMA_READY_CACHE_S", "15")),
            max_output_tokens=int(os.getenv("FKGRID_GEMMA_MAX_OUTPUT_TOKENS", "384")),
        )


class GemmaCatalogLanguageModel(CatalogLanguageModelPort):
    """Call Gemma with strict local parsing and bounded request deadlines."""

    def __init__(
        self,
        settings: GemmaModelSettings,
        *,
        prompt_registry: PromptRegistry | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.prompt_registry = prompt_registry or PromptRegistry()
        self._transport = transport
        self._readiness_cache: tuple[float, tuple[bool, str]] | None = None

    def readiness(self) -> tuple[bool, str]:
        """Return a safe readiness result without exposing provider failures."""

        now = monotonic()
        if self._readiness_cache is not None:
            cached_at, cached_result = self._readiness_cache
            if now - cached_at < self.settings.readiness_cache_seconds:
                return cached_result
        result = self._probe_readiness()
        if result[0]:
            self._readiness_cache = (monotonic(), result)
        return result

    def _probe_readiness(self) -> tuple[bool, str]:
        try:
            with httpx.Client(
                timeout=self.settings.readiness_timeout_seconds,
                transport=self._transport,
            ) as client:
                response = client.get(self._models_url(), headers=self._headers())
            if response.status_code >= 400:
                return False, f"Gemma provider returned HTTP {response.status_code}"
            payload = response.json()
            available = self._available_models(payload)
            if self.settings.model_name not in available:
                names = ", ".join(available[:8]) or "none"
                return (
                    False,
                    f"Gemma model '{self.settings.model_name}' is not available; found: {names}",
                )
            return True, f"Gemma model '{self.settings.model_name}' is ready"
        except httpx.TimeoutException:
            return False, "Gemma provider readiness timed out"
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            return False, "Gemma provider is unavailable"

    def propose_canonical_mapping(self, request: ModelCallRequest) -> ModelCallResponse:
        return self._complete(request, MappingDraft)

    def critique_mapping(self, request: ModelCallRequest) -> ModelCallResponse:
        return self._complete(request, CriticDraft)

    def _complete(
        self,
        request: ModelCallRequest,
        payload_type: type[MappingDraft] | type[CriticDraft],
    ) -> ModelCallResponse:
        started = perf_counter()
        input_hash = sha256_hex(request.input_payload)
        try:
            prompt = self._render_prompt(request)
            response = self._post(request, prompt, payload_type)
            content = self._extract_content(response.json())
            parsed = self._parse_json_content(content)
            payload = payload_type.model_validate_json(canonical_json_bytes(parsed))
            return ModelCallResponse(
                call_id=request.call_id,
                status=ModelStatus.OK,
                payload=payload,
                input_hash=input_hash,
                output_hash=sha256_hex(parsed),
                model_alias=self.settings.model_name,
                latency_ms=max(0, int((perf_counter() - started) * 1000)),
            )
        except httpx.TimeoutException:
            return self._failure(
                request, input_hash, ModelStatus.TIMEOUT, "PROVIDER_TIMEOUT", True, started
            )
        except httpx.HTTPStatusError as exc:
            status, code, retryable = self._http_failure(exc.response.status_code)
            return self._failure(request, input_hash, status, code, retryable, started)
        except httpx.HTTPError:
            return self._failure(
                request,
                input_hash,
                ModelStatus.UNAVAILABLE,
                "PROVIDER_UNAVAILABLE",
                True,
                started,
            )
        except json.JSONDecodeError:
            return self._failure(
                request,
                input_hash,
                ModelStatus.INVALID_OUTPUT,
                "OUTPUT_JSON_INVALID",
                False,
                started,
            )
        except ValidationError as exc:
            return self._failure(
                request,
                input_hash,
                ModelStatus.INVALID_OUTPUT,
                "OUTPUT_SCHEMA_INVALID",
                False,
                started,
                self._validation_codes(exc),
            )
        except (TypeError, ValueError, KeyError, IndexError):
            return self._failure(
                request,
                input_hash,
                ModelStatus.INVALID_OUTPUT,
                "OUTPUT_SCHEMA_INVALID",
                False,
                started,
            )

    def _post(
        self,
        request: ModelCallRequest,
        prompt: str,
        payload_type: type[MappingDraft] | type[CriticDraft],
    ) -> httpx.Response:
        headers = self._headers()
        schema = payload_type.model_json_schema()
        if self.settings.provider == "ollama":
            url = f"{self.settings.base_url}/api/chat"
            body = {
                "model": self.settings.model_name,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "format": schema,
                "options": {"temperature": 0, "num_predict": self.settings.max_output_tokens},
            }
        else:
            url = f"{self.settings.base_url}/chat/completions"
            body = {
                "model": self.settings.model_name,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": self.settings.max_output_tokens,
                "stream": False,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": request.output_schema_version,
                        "strict": True,
                        "schema": schema,
                    },
                },
            }
        with httpx.Client(
            timeout=max(0.001, request.deadline_ms / 1000),
            transport=self._transport,
        ) as client:
            response = client.post(url, headers=headers, json=body)
            response.raise_for_status()
            return response

    def _render_prompt(self, request: ModelCallRequest) -> str:
        prompt = self.prompt_registry.read(request.prompt_id)
        placeholder = (
            "{{canonical_proposer_input_json}}"
            if request.logical_call == "propose_canonical_mapping"
            else "{{canonical_critic_input_json}}"
        )
        if placeholder not in prompt:
            raise ValueError("prompt placeholder is missing")
        return prompt.replace(
            placeholder,
            canonical_json_bytes(request.input_payload).decode("utf-8"),
        )

    def _models_url(self) -> str:
        if self.settings.provider == "ollama":
            return f"{self.settings.base_url}/api/tags"
        if self.settings.provider == "deepinfra":
            return f"{self.settings.base_url.removesuffix('/openai')}/models"
        return f"{self.settings.base_url}/models"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.api_key is not None:
            headers["Authorization"] = f"Bearer {self.settings.api_key.get_secret_value()}"
        return headers

    def _available_models(self, payload: object) -> list[str]:
        if not isinstance(payload, dict):
            raise TypeError("model list response must be an object")
        values = payload.get("models", payload.get("data", []))
        if not isinstance(values, list):
            raise TypeError("model list must be an array")
        names: list[str] = []
        for value in values:
            if isinstance(value, dict):
                name = value.get("name") or value.get("model") or value.get("id")
                if isinstance(name, str):
                    names.append(name)
        return names

    @staticmethod
    def _extract_content(payload: object) -> str:
        if not isinstance(payload, dict):
            raise TypeError("provider response must be an object")
        message: object
        if "message" in payload:
            message = payload["message"]
        else:
            choices = payload.get("choices")
            if not isinstance(choices, list) or not choices:
                raise KeyError("provider response has no choices")
            choice = choices[0]
            if not isinstance(choice, dict):
                raise TypeError("provider choice must be an object")
            message = choice.get("message")
        if not isinstance(message, dict):
            raise TypeError("provider message content must be a string")
        raw_content = message.get("content")
        if isinstance(raw_content, list):
            text_parts: list[str] = []
            for part in raw_content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    text_parts.append(part["text"])
            raw_content = "".join(text_parts)
        if not isinstance(raw_content, str):
            raise TypeError("provider message content must be a string")
        content = raw_content.strip()
        if content.startswith("```"):
            content = content.removeprefix("```").removeprefix("json").removesuffix("```").strip()
        return content

    @staticmethod
    def _parse_json_content(content: str) -> object:
        """Parse strict JSON even when a model adds a short reasoning prefix.

        The returned object is still passed through the strict Pydantic output
        model.  We only locate a JSON object; we never repair fields, invent
        IDs, or accept arbitrary natural-language output.
        """

        try:
            return json.loads(content)
        except json.JSONDecodeError as original_error:
            decoder = json.JSONDecoder()
            for index, character in enumerate(content):
                if character != "{":
                    continue
                try:
                    value, _ = decoder.raw_decode(content[index:])
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    return value
            raise original_error

    @staticmethod
    def _http_failure(code: int) -> tuple[ModelStatus, str, bool]:
        if code == 429:
            return ModelStatus.RATE_LIMITED, "PROVIDER_RATE_LIMITED", True
        if code >= 500:
            return ModelStatus.UNAVAILABLE, "PROVIDER_SERVER_ERROR", True
        return ModelStatus.ERROR, "PROVIDER_REQUEST_REJECTED", False

    @staticmethod
    def _validation_codes(error: ValidationError) -> list[str]:
        codes = ["OUTPUT_SCHEMA_INVALID"]
        for item in error.errors()[:15]:
            location = ".".join(str(part) for part in item.get("loc", ())) or "root"
            error_type = str(item.get("type", "unknown")).upper().replace("-", "_")
            codes.append(f"OUTPUT_FIELD_{error_type}:{location}")
        return codes

    def _failure(
        self,
        request: ModelCallRequest,
        input_hash: str,
        status: ModelStatus,
        code: str,
        retryable: bool,
        started: float,
        validation_codes: list[str] | None = None,
    ) -> ModelCallResponse:
        return ModelCallResponse(
            call_id=request.call_id,
            status=status,
            input_hash=input_hash,
            model_alias=self.settings.model_name,
            latency_ms=max(0, int((perf_counter() - started) * 1000)),
            validation_codes=validation_codes or [code],
            retryable=retryable,
        )
