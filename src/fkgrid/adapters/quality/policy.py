"""File-backed policy adapter; activation/approval belongs to operations."""

from __future__ import annotations

import json
from pathlib import Path

from fkgrid.application.quality_service import default_quality_policy
from fkgrid.domain.quality import IssueClass, QualityPolicy, RoutePriority, SignalType


class JsonQualityPolicyProvider:
    """Load a checked-in candidate policy without mutating the active policy."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def active_policy(self) -> QualityPolicy:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data["urgent_signal_types"] = {
            SignalType(value) for value in data.get("urgent_signal_types", [])
        }
        data["routes"] = [
            {
                **route,
                "issue_class": IssueClass(route["issue_class"]),
                "priority": RoutePriority(route["priority"]),
            }
            for route in data["routes"]
        ]
        return QualityPolicy.model_validate(data)


class PrototypeQualityPolicyProvider:
    def active_policy(self) -> QualityPolicy:
        return default_quality_policy()
