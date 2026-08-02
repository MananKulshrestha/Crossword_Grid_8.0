"""Provider-neutral constrained planner adapter.

The shared model gateway owner can implement `StructuredModelGateway` for the
configured provider. Provider SDK request/response objects stop at this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import TypeAdapter, ValidationError

from ..domain import RecoveryContext, RecoveryPlannerOutput
from ..ports import RecoveryPlannerPort
from ..validation import canonical_json


@dataclass(frozen=True)
class GatewayReply:
    status: str
    json_text: str | None
    token_count: int = 0
    latency_ms: int = 0
    error_code: str | None = None


class StructuredModelGateway(Protocol):
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
        """Return provider-neutral structured JSON; `tools` must remain empty."""


class SharedGatewayRecoveryPlanner(RecoveryPlannerPort):
    def __init__(
        self,
        *,
        gateway: StructuredModelGateway,
        prompt_version: str = "recovery-v1",
        model_alias: str = "configured-recovery-model",
        prompt_path: Path | None = None,
    ) -> None:
        self.gateway = gateway
        self.prompt_version = prompt_version
        self.model_alias = model_alias
        self.prompt_path = prompt_path or Path(__file__).resolve().parents[1] / "prompts" / "recovery_v1.md"

    def plan(
        self,
        *,
        context: RecoveryContext,
        timeout_ms: int,
    ) -> tuple[RecoveryPlannerOutput | None, list[str], int, int]:
        prompt = self.prompt_path.read_text(encoding="utf-8")
        reply = self.gateway.complete_json(
            prompt_version=self.prompt_version,
            model_alias=self.model_alias,
            system_prompt=prompt,
            user_json=canonical_json(context),
            schema_name="RecoveryPlannerOutputV1",
            timeout_ms=max(1, timeout_ms),
            tools=(),
        )
        codes: list[str] = []
        if reply.status != "OK":
            codes.append(reply.error_code or f"MODEL_{reply.status}")
            return None, codes, reply.token_count, reply.latency_ms
        if not reply.json_text:
            return None, ["MODEL_EMPTY_OUTPUT"], reply.token_count, reply.latency_ms
        try:
            output = TypeAdapter(RecoveryPlannerOutput).validate_json(reply.json_text)
        except ValidationError as exc:
            codes.extend(
                f"SCHEMA:{error.get('type', 'invalid')}:{error.get('loc', [])}"
                for error in exc.errors()
            )
            return None, codes, reply.token_count, reply.latency_ms
        return output, codes, reply.token_count, reply.latency_ms


__all__ = ["GatewayReply", "SharedGatewayRecoveryPlanner", "StructuredModelGateway"]
