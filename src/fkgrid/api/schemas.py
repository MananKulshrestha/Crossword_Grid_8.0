"""HTTP wire models that convert JSON strings into strict domain contracts."""

from __future__ import annotations

from pydantic import ConfigDict

from ..query_recovery.domain import RecoveryGate, RecoveryRequest


class ApiRecoveryGate(RecoveryGate):
    """Accept JSON enum strings, then revalidate through the strict domain model."""

    model_config = ConfigDict(
        extra="forbid",
        strict=False,
        validate_assignment=True,
    )


class ApiRecoveryRequest(RecoveryRequest):
    """OpenAPI request shape; the endpoint converts it back to RecoveryRequest."""

    model_config = ConfigDict(
        extra="forbid",
        strict=False,
        validate_assignment=True,
    )
    gate: ApiRecoveryGate


__all__ = ["ApiRecoveryGate", "ApiRecoveryRequest"]
