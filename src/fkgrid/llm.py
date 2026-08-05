"""LLM calls for query extraction and query enhancement.

One call per step. No retry, no repair loop, no regex fallback: on any
failure (timeout, non-OK HTTP, invalid JSON, schema-invalid output) this
raises LLMError, which the orchestrator treats as a hard pause.
"""

from __future__ import annotations

import functools
import json
import urllib.error
import urllib.request

from pydantic import ValidationError

from .config import LLMConfig, load_llm_config
from .contracts import ChatTurn, QueryExtraction, RerankerRequest, SessionState


@functools.lru_cache(maxsize=1)
def _config() -> LLMConfig:
    return load_llm_config()

_EXTRACTION_SYSTEM_PROMPT = """\
You are a shopping query extractor. Read the shopper's latest message and the
recent conversation, then output ONLY a JSON object matching this schema:

{
  "action": "SEARCH" | "REFINE" | "PRODUCT_DETAILS" | "COMPARE" | "CHECK_AVAILABILITY" | "SHOW_CART" | "UPDATE_CART",
  "query_terms": [string],
  "constraints": [{"field": "max_price"|"min_price"|"brand"|"category"|"stock_status", "value": any}],
  "references": [{"ordinal": int|null, "sku_id": string|null}],
  "cart_operations": [{"type": "ADD_ITEM"|"SET_QUANTITY"|"REMOVE_ITEM"|"CLEAR_CART", "reference": {...}|null, "cart_item_id": string|null, "quantity": int|null, "confirmation": bool}]
}

Use REFINE when the shopper is narrowing an existing search (e.g. "cheaper
ones", "only blue"). Use SEARCH for a fresh product query. Use references
with 1-based ordinals when the shopper refers to a previous result
("the first one", "the second one"). Output strict JSON, no prose.
"""

_ENHANCEMENT_SYSTEM_PROMPT = """\
You convert a structured query extraction plus prior search constraints into
the exact request body for a product search API. Output ONLY a JSON object:

{
  "soft_query_text": string,
  "hard_constraints": {"max_price": number, "min_price": number, "brand": string, "category": string, "stock_status": "in_stock"},
  "top_n": int
}

Only include hard_constraints keys that are actually known. soft_query_text
should be a short natural-language description of what the shopper wants.
Default top_n to 10 unless the shopper asked for a specific count.
"""


class LLMError(RuntimeError):
    """Raised on any LLM failure. The orchestrator must not fall back silently."""


def _chat_completion(system_prompt: str, user_payload: dict, json_schema: dict) -> dict:
    body = {
        "model": _config().model_alias,
        "temperature": 0.0,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, sort_keys=True)},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "output", "strict": True, "schema": json_schema},
        },
    }
    config = _config()
    request = urllib.request.Request(
        config.endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key}",
            "User-Agent": "fkgrid-chat/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=config.timeout_s) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise LLMError(f"LLM_HTTP_{exc.code}") from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"LLM_UNAVAILABLE:{exc.reason}") from exc
    except TimeoutError as exc:
        raise LLMError("LLM_TIMEOUT") from exc

    try:
        provider_response = json.loads(raw)
        content = provider_response["choices"][0]["message"]["content"]
        parsed = json.loads(content)
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise LLMError("LLM_MALFORMED_RESPONSE") from exc

    if not isinstance(parsed, dict):
        raise LLMError("LLM_OUTPUT_NOT_OBJECT")
    return parsed


def extract_query(message: str, chat_history: list[ChatTurn]) -> QueryExtraction:
    recent = [{"role": turn.role.value, "content": turn.content} for turn in chat_history[-10:]]
    payload = _chat_completion(
        _EXTRACTION_SYSTEM_PROMPT,
        {"message": message, "history": recent},
        QueryExtraction.model_json_schema(),
    )
    try:
        return QueryExtraction.model_validate(payload)
    except ValidationError as exc:
        raise LLMError("EXTRACTION_SCHEMA_INVALID") from exc


def enhance_query(extraction: QueryExtraction, session_state: SessionState) -> RerankerRequest:
    prior = session_state.last_reranker_request.model_dump() if (
        extraction.action.value == "REFINE" and session_state.last_reranker_request
    ) else None
    payload = _chat_completion(
        _ENHANCEMENT_SYSTEM_PROMPT,
        {
            "extraction": extraction.model_dump(mode="json"),
            "prior_reranker_request": prior,
        },
        RerankerRequest.model_json_schema(),
    )
    try:
        return RerankerRequest.model_validate(payload)
    except ValidationError as exc:
        raise LLMError("ENHANCEMENT_SCHEMA_INVALID") from exc
