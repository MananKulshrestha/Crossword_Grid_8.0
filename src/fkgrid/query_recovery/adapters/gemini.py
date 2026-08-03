"""Secret-safe Gemini REST gateway configured for Gemma 4 26B A4B."""

from __future__ import annotations

import json
import os
import socket
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from pydantic import SecretStr

from .model_gateway import GatewayReply, SharedGatewayRecoveryPlanner, StructuredModelGateway


GEMMA_4_26B_A4B_IT = "gemma-4-26b-a4b-it"
GEMINI_GENERATE_CONTENT_BASE = "https://generativelanguage.googleapis.com/v1beta"


@dataclass(frozen=True)
class GeminiGemmaConfig:
    """Runtime configuration; the key is masked by Pydantic's SecretStr."""

    api_key: SecretStr
    model_alias: str = GEMMA_4_26B_A4B_IT
    base_url: str = GEMINI_GENERATE_CONTENT_BASE
    max_output_tokens: int = 256

    def __post_init__(self) -> None:
        if not self.api_key.get_secret_value().strip():
            raise ValueError("Gemini API key must not be empty")
        if self.model_alias != GEMMA_4_26B_A4B_IT:
            raise ValueError(f"This adapter is pinned to {GEMMA_4_26B_A4B_IT}")
        if not self.base_url.startswith("https://"):
            raise ValueError("Gemini base URL must use HTTPS")
        if not 32 <= self.max_output_tokens <= 1024:
            raise ValueError("max_output_tokens must be between 32 and 1024")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> GeminiGemmaConfig:
        """Load a key without writing it to a file or exposing it in repr/logs."""

        values = os.environ if environ is None else environ
        api_key = values.get("FKGRID_GEMINI_API_KEY") or values.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("Set FKGRID_GEMINI_API_KEY or GEMINI_API_KEY")
        return cls(api_key=SecretStr(api_key))


class GeminiGemmaGateway(StructuredModelGateway):
    """One-call Gemini GenerateContent adapter for the constrained planner.

    The adapter intentionally uses only the API's JSON MIME mode. The shared
    planner then parses and validates the returned JSON with the strict local
    discriminated union; provider output never becomes authorization.
    """

    def __init__(
        self,
        *,
        config: GeminiGemmaConfig,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        self.config = config
        self._opener = opener

    @classmethod
    def from_env(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        opener: Callable[..., Any] = urlopen,
    ) -> GeminiGemmaGateway:
        return cls(config=GeminiGemmaConfig.from_env(environ), opener=opener)

    def complete_json(
        self,
        *,
        prompt_version: str,
        model_alias: str,
        system_prompt: str,
        user_json: str,
        schema_name: str,
        timeout_ms: int,
        tools: tuple[str, ...],
    ) -> GatewayReply:
        del prompt_version, schema_name
        started = time.monotonic()
        latency = lambda: max(0, int((time.monotonic() - started) * 1000))
        if tools:
            return GatewayReply(
                status="REJECTED",
                json_text=None,
                latency_ms=latency(),
                error_code="TOOLS_NOT_ALLOWED",
            )
        if model_alias != self.config.model_alias:
            return GatewayReply(
                status="REJECTED",
                json_text=None,
                latency_ms=latency(),
                error_code="MODEL_ALIAS_MISMATCH",
            )

        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_json}]}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": self.config.max_output_tokens,
                "responseMimeType": "application/json",
                "thinkingConfig": {"thinkingLevel": "minimal"},
            },
        }
        url = (
            f"{self.config.base_url.rstrip('/')}/models/"
            f"{quote(self.config.model_alias, safe='-_.')}:generateContent"
        )
        request = Request(
            url,
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "fkgrid-query-recovery/0.1",
                "x-goog-api-key": self.config.api_key.get_secret_value(),
            },
            method="POST",
        )
        try:
            with self._opener(request, timeout=max(0.1, timeout_ms / 1000)) as response:
                body = response.read()
            decoded = json.loads(body.decode("utf-8"))
        except HTTPError as exc:
            return GatewayReply(
                status="ERROR",
                json_text=None,
                latency_ms=latency(),
                error_code=_http_error_code(exc.code),
            )
        except (socket.timeout, TimeoutError):
            return GatewayReply(
                status="TIMEOUT",
                json_text=None,
                latency_ms=latency(),
                error_code="PROVIDER_TIMEOUT",
            )
        except (URLError, OSError):
            return GatewayReply(
                status="ERROR",
                json_text=None,
                latency_ms=latency(),
                error_code="NETWORK_ERROR",
            )
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            return GatewayReply(
                status="ERROR",
                json_text=None,
                latency_ms=latency(),
                error_code="MALFORMED_PROVIDER_RESPONSE",
            )

        text = _answer_text(decoded)
        usage = decoded.get("usageMetadata") if isinstance(decoded, dict) else None
        token_count = _token_count(usage)
        if not text:
            return GatewayReply(
                status="ERROR",
                json_text=None,
                token_count=token_count,
                latency_ms=latency(),
                error_code="MODEL_EMPTY_OUTPUT",
            )
        return GatewayReply(
            status="OK",
            json_text=text,
            token_count=token_count,
            latency_ms=latency(),
        )


def build_gemma4_recovery_planner(
    *,
    config: GeminiGemmaConfig | None = None,
    gateway: GeminiGemmaGateway | None = None,
    prompt_path=None,
) -> SharedGatewayRecoveryPlanner:
    """Build the recovery planner pinned to Gemma 4 26B A4B instruction-tuned."""

    selected_config = config or GeminiGemmaConfig.from_env()
    selected_gateway = gateway or GeminiGemmaGateway(config=selected_config)
    return SharedGatewayRecoveryPlanner(
        gateway=selected_gateway,
        prompt_version="recovery-v2",
        model_alias=GEMMA_4_26B_A4B_IT,
        prompt_path=prompt_path,
    )


def _answer_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return ""
    candidate = candidates[0]
    if not isinstance(candidate, dict):
        return ""
    content = candidate.get("content")
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts")
    if not isinstance(parts, list):
        return ""
    answer_parts: list[str] = []
    for part in parts:
        if not isinstance(part, dict) or part.get("thought") is True:
            continue
        text = part.get("text")
        if isinstance(text, str) and text:
            answer_parts.append(text)
    return "".join(answer_parts)


def _token_count(usage: Any) -> int:
    if not isinstance(usage, dict):
        return 0
    for key in ("totalTokenCount", "candidatesTokenCount"):
        value = usage.get(key)
        if isinstance(value, int) and value >= 0:
            return value
    return 0


def _http_error_code(status: int) -> str:
    if status in {401, 403}:
        return "AUTH_REJECTED"
    if status == 429:
        return "RATE_LIMITED"
    if 500 <= status <= 599:
        return "PROVIDER_UNAVAILABLE"
    return f"HTTP_{status}"


__all__ = [
    "GEMINI_GENERATE_CONTENT_BASE",
    "GEMMA_4_26B_A4B_IT",
    "GeminiGemmaConfig",
    "GeminiGemmaGateway",
    "build_gemma4_recovery_planner",
]
