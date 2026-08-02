"""Small, typed-enough HTTP client used by the replaceable Streamlit UI."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class QualityApiError(RuntimeError):
    """Safe UI-facing API error without provider or secret details."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class QualityApiClient:
    """Call only the existing Quality Sentinel HTTP contracts."""

    def __init__(self, base_url: str, *, timeout_seconds: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _request(
        self, method: str, path: str, payload: Mapping[str, Any] | None = None
    ) -> dict[str, Any] | list[Any]:
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(f"{self.base_url}{path}", data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # nosec B310
                decoded = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8"))
            except (OSError, ValueError):
                detail = {"detail": "The Quality Sentinel API rejected the request."}
            message = detail.get("detail", "The Quality Sentinel API rejected the request.")
            if isinstance(message, dict):
                message = message.get("message", "The Quality Sentinel API rejected the request.")
            raise QualityApiError(str(message), status_code=exc.code) from exc
        except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise QualityApiError(
                "The Quality Sentinel API is unavailable. Check the API base URL and server."
            ) from exc
        if not isinstance(decoded, (dict, list)):
            raise QualityApiError("The Quality Sentinel API returned an invalid response.")
        return decoded

    def demo_state(self) -> dict[str, Any]:
        result = self._request("GET", "/api/v1/quality/demo-state")
        return result if isinstance(result, dict) else {}

    def submit_signal(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        result = self._request("POST", "/api/v1/quality/signals", payload)
        return result if isinstance(result, dict) else {}

    def get_case(self, case_id: str) -> dict[str, Any]:
        result = self._request("GET", f"/api/v1/quality/cases/{case_id}")
        return result if isinstance(result, dict) else {}

    def get_events(self, case_id: str) -> list[dict[str, Any]]:
        result = self._request("GET", f"/api/v1/quality/cases/{case_id}/events")
        return result if isinstance(result, list) else []

    def record_decision(
        self, case_id: str, *, decision: str, reviewer_id: str, reason: str
    ) -> dict[str, Any]:
        result = self._request(
            "POST",
            f"/api/v1/quality/cases/{case_id}/decision",
            {
                "decision": decision,
                "reviewer_role": "QUALITY_REVIEWER",
                "reviewer_id": reviewer_id,
                "reason": reason,
            },
        )
        return result if isinstance(result, dict) else {}

    def lifecycle(self, case_id: str, *, action: str, actor_id: str, reason: str) -> dict[str, Any]:
        result = self._request(
            "POST",
            f"/api/v1/quality/cases/{case_id}/lifecycle",
            {"action": action, "actor_id": actor_id, "reason": reason},
        )
        return result if isinstance(result, dict) else {}
