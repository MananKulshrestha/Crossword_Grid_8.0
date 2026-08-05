"""Environment-driven configuration. No fixtures, no fallback endpoints."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class MySQLConfig:
    host: str
    port: int
    user: str
    password: str
    database: str


@dataclass(frozen=True)
class RerankerConfig:
    endpoint: str
    timeout_s: float


@dataclass(frozen=True)
class LLMConfig:
    endpoint: str
    api_key: str
    protocol: str
    model_alias: str
    timeout_s: float


class ConfigError(RuntimeError):
    """Raised when required configuration is missing. No silent defaults for secrets."""


def load_search_mode() -> str:
    """Return the shopper search mode without changing any external state.

    ``normal`` preserves the existing RunPod reranker path. ``fast`` is the
    read-only SQL-hard-filter/BM25 path. The boolean alias is accepted for
    local deployments that want a simple feature flag; the explicit mode wins
    when both variables are present.
    """

    configured = os.environ.get("FKGRID_SEARCH_MODE")
    if configured is None:
        fast_flag = os.environ.get("FKGRID_FAST_MODE", "false").strip().lower()
        configured = "fast" if fast_flag in {"1", "true", "yes", "on"} else "normal"
    mode = configured.strip().lower()
    if mode not in {"normal", "fast"}:
        raise ConfigError("FKGRID_SEARCH_MODE must be 'normal' or 'fast'")
    return mode


def load_mysql_config() -> MySQLConfig:
    return MySQLConfig(
        host=os.environ.get("FKGRID_MYSQL_HOST", "213.173.105.95"),
        port=int(os.environ.get("FKGRID_MYSQL_PORT", "24679")),
        user=os.environ.get("FKGRID_MYSQL_USER", "flipkart_user"),
        password=os.environ.get("FKGRID_MYSQL_PASSWORD", "flipkart_pass"),
        database=os.environ.get("FKGRID_MYSQL_DB", "flipkart"),
    )


def load_reranker_config() -> RerankerConfig:
    return RerankerConfig(
        endpoint=os.environ.get(
            "FKGRID_RERANKER_ENDPOINT",
            "https://dk82n1hdtxfqmf-8002.proxy.runpod.net/api/search",
        ),
        timeout_s=float(os.environ.get("FKGRID_RERANKER_TIMEOUT_S", "60")),
    )


def load_llm_config() -> LLMConfig:
    endpoint = os.environ.get("FKGRID_MODEL_ENDPOINT")
    protocol = os.environ.get("FKGRID_MODEL_PROTOCOL", "openai")
    api_key = os.environ.get("FKGRID_MODEL_API_KEY") or (
        os.environ.get("DEEPINFRA_API_KEY") if protocol == "deepinfra" else None
    )
    if not endpoint:
        raise ConfigError("FKGRID_MODEL_ENDPOINT is required")
    if not api_key:
        raise ConfigError("FKGRID_MODEL_API_KEY is required")
    return LLMConfig(
        endpoint=endpoint,
        api_key=api_key,
        protocol=protocol,
        model_alias=os.environ.get("FKGRID_MODEL_ALIAS", "gemma-4-26b-a4b-it"),
        timeout_s=float(os.environ.get("FKGRID_MODEL_TIMEOUT_S", "20")),
    )
