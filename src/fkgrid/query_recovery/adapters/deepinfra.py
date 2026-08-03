"""Secret-safe DeepInfra OpenAI-compatible gateway for Gemma 4.

DeepInfra's OpenAI-compatible endpoint is intentionally kept behind the
provider-neutral :class:`StructuredModelGateway` contract. The rest of the
recovery workflow therefore sees only sanitized JSON, token counts, latency,
and bounded error codes; it never sees provider SDK objects or credentials.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import SecretStr

from .model_gateway import GatewayReply, SharedGatewayRecoveryPlanner, StructuredModelGateway

DEEPINFRA_GEMMA_4_26B_A4B_IT = "google/gemma-4-26B-A4B-it"
DEEPINFRA_OPENAI_BASE = "https://api.deepinfra.com/v1/openai"


@dataclass(frozen=True)
class DeepInfraGemmaConfig:
    """Runtime configuration for the pinned DeepInfra Gemma model."""

    api_key: SecretStr
    model_alias: str = DEEPINFRA_GEMMA_4_26B_A4B_IT
    base_url: str = DEEPINFRA_OPENAI_BASE
    max_output_tokens: int = 256
    temperature: float = 0.0

    def __post_init__(self) -> None:
        if not self.api_key.get_secret_value().strip():
            raise ValueError("DeepInfra API key must not be empty")
        if self.model_alias != DEEPINFRA_GEMMA_4_26B_A4B_IT:
            raise ValueError(f"This adapter is pinned to {DEEPINFRA_GEMMA_4_26B_A4B_IT}")
        if not self.base_url.startswith("https://"):
            raise ValueError("DeepInfra base URL must use HTTPS")
        if not 32 <= self.max_output_tokens <= 1024:
            raise ValueError("max_output_tokens must be between 32 and 1024")
        if not 0 <= self.temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> DeepInfraGemmaConfig:
        """Load a process credential without persisting or exposing its value."""

        values = os.environ if environ is None else environ
        api_key = (
            values.get("FKGRID_DEEPINFRA_API_KEY")
            or values.get("DEEPINFRA_API_KEY")
            or values.get("DEEPINFRA_TOKEN")
        )
        if not api_key:
            raise ValueError(
                "Set FKGRID_DEEPINFRA_API_KEY, DEEPINFRA_API_KEY, or DEEPINFRA_TOKEN"
            )
        return cls(api_key=SecretStr(api_key))


class DeepInfraGemmaGateway(StructuredModelGateway):
    """One bounded DeepInfra chat-completion call for the recovery planner."""

    def __init__(
        self,
        *,
        config: DeepInfraGemmaConfig,
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
    ) -> DeepInfraGemmaGateway:
        return cls(config=DeepInfraGemmaConfig.from_env(environ), opener=opener)

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
        def latency() -> int:
            return max(0, int((time.monotonic() - started) * 1000))
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
            "model": self.config.model_alias,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_json},
            ],
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_output_tokens,
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        request = Request(
            f"{self.config.base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.config.api_key.get_secret_value()}",
                "Content-Type": "application/json",
                "User-Agent": "fkgrid-query-recovery/0.1",
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
        except TimeoutError:
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
        token_count = _token_count(decoded.get("usage") if isinstance(decoded, dict) else None)
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


def build_deepinfra_gemma4_recovery_planner(
    *,
    config: DeepInfraGemmaConfig | None = None,
    gateway: DeepInfraGemmaGateway | None = None,
    prompt_path: Path | None = None,
) -> SharedGatewayRecoveryPlanner:
    """Build the recovery planner pinned to DeepInfra's Gemma 4 model."""

    selected_config = config or DeepInfraGemmaConfig.from_env()
    selected_gateway = gateway or DeepInfraGemmaGateway(config=selected_config)
    return SharedGatewayRecoveryPlanner(
        gateway=selected_gateway,
        prompt_version="recovery-v2",
        model_alias=DEEPINFRA_GEMMA_4_26B_A4B_IT,
        prompt_path=prompt_path,
    )


def _answer_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    choice = choices[0]
    if not isinstance(choice, dict):
        return ""
    message = choice.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    text_parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") not in {None, "text"}:
            continue
        text = item.get("text")
        if isinstance(text, str) and text:
            text_parts.append(text)
    return "".join(text_parts)


def _token_count(usage: Any) -> int:
    if not isinstance(usage, dict):
        return 0
    for key in ("total_tokens", "completion_tokens"):
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
    "DEEPINFRA_GEMMA_4_26B_A4B_IT",
    "DEEPINFRA_OPENAI_BASE",
    "DeepInfraGemmaConfig",
    "DeepInfraGemmaGateway",
    "build_deepinfra_gemma4_recovery_planner",
]
