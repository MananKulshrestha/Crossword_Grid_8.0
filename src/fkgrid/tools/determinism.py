"""Small deterministic primitives shared by every tool."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import asdict, is_dataclass
from typing import Any


def _to_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _to_jsonable(value.model_dump(mode="json"))
    if is_dataclass(value):
        return _to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {
            str(k): _to_jsonable(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_to_jsonable(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        _to_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def canonical_hash(value: Any, length: int = 24) -> str:
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return digest[:length]


def stable_id(prefix: str, value: Any, length: int = 16) -> str:
    return f"{prefix}_{canonical_hash(value, length)}"


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = normalized.replace("–", "-").replace("—", "-")
    normalized = re.sub(r"[\-_]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def tokenize(value: str) -> list[str]:
    return [
        token for token in re.findall(r"[\w]+", normalize_text(value), flags=re.UNICODE) if token
    ]


def unique_sorted(values: Iterable[str]) -> list[str]:
    return sorted(
        {value for value in values if value}, key=lambda item: (normalize_text(item), item)
    )


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def hard_filter_hash(query_state: Any) -> str:
    filters = getattr(query_state, "hard_filters", query_state)
    return canonical_hash(filters)


def remaining_budget(started_ms: int, now_ms: int, deadline_ms: int) -> int:
    return max(0, deadline_ms - max(0, now_ms - started_ms))
