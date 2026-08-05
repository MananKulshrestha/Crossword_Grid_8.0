"""LLM call for query extraction.

One call. No retry, no repair loop, no regex fallback: on any failure
(timeout, non-OK HTTP, invalid JSON, schema-invalid output) this raises
LLMError, which the orchestrator treats as a hard pause.

Query enhancement is NOT an LLM call - see enhancer.py. It deterministically
copies whatever the extractor actually found into the reranker request
shape, so it can never invent a constraint the shopper didn't mention.
"""

from __future__ import annotations

import functools
import json
import urllib.error
import urllib.request

from pydantic import ValidationError

from .config import LLMConfig, load_llm_config
from .contracts import ChatTurn, QueryExtraction


@functools.lru_cache(maxsize=1)
def _config() -> LLMConfig:
    return load_llm_config()

_EXTRACTION_SYSTEM_PROMPT = """\
You are a shopping query extractor. Read the shopper's latest message and the
recent conversation, then output ONLY a JSON object matching this schema:

{
  "action": "CHITCHAT" | "SEARCH" | "REFINE" | "PRODUCT_DETAILS" | "COMPARE" | "CHECK_AVAILABILITY" | "SHOW_CART" | "UPDATE_CART",
  "query_terms": [string],
  "constraints": [{"field": "max_price"|"min_price"|"brand"|"category"|"stock_status", "value": any}],
  "references": [{"ordinal": int|null, "sku_id": string|null}],
  "cart_operations": [{"type": "ADD_ITEM"|"SET_QUANTITY"|"REMOVE_ITEM"|"CLEAR_CART", "reference": {...}|null, "cart_item_id": string|null, "quantity": int|null, "confirmation": bool}],
  "reply": string|null
}

Use CHITCHAT when the message is not a shopping request at all - greetings
("hi", "hello"), small talk ("how are you"), thanks, or anything else that
doesn't ask to search, inspect, compare, or change the cart. For CHITCHAT,
set "reply" to a short, friendly, direct reply to the shopper (no tool call
will be made - this reply is shown as-is), and leave query_terms,
constraints, references, and cart_operations empty. For every other
action, leave "reply" null.

Only include a constraint if the shopper actually stated it (a price, a
brand, a category, an in-stock requirement). Never invent a price limit,
brand, or category the shopper did not mention - leave constraints empty
rather than guess. "brand" and "category" constraints are EXACT filters -
the catalog only has a small fixed set of literal category names (e.g.
"Watches", "Wearable Smart Devices", "Footwear") and literal brand names
(e.g. "Fastrack", "Maxima") - only set these fields when the shopper names
one of those exactly (or something you are confident is literally the
catalog's name for it), never a descriptive phrase you composed yourself
(e.g. "sports watch", "green watch" is NOT a category or brand - it is a
descriptive query). Anything descriptive - color, style, occasion, material,
a category-ish phrase you are not sure is a literal catalog value - belongs
in query_terms instead, since that is matched softly, not filtered exactly.
When unsure whether a word is a real category/brand or just descriptive,
put it in query_terms, not constraints. For "max_price"/"min_price", the
value MUST be a plain
integer number of paise (never a string, never containing commas or a
currency symbol) - the shopper speaks in rupees, so convert by multiplying
by 100 (e.g. "under 2000 rupees" -> {"field": "max_price", "value": 200000};
"more than 10,000" -> {"field": "min_price", "value": 1000000}). Use REFINE
when the shopper is narrowing an existing
search (e.g. "cheaper ones", "only blue"). Use SEARCH for a fresh product
query. Use references with 1-based ordinals when the shopper refers to a
previous result ("the first one", "the second one"). If the shopper types
an exact product/sku id (an alphanumeric code, e.g. "SHOE58EKXSEYAYX6"),
put it in the reference's sku_id field verbatim instead of an ordinal -
do this even if that sku was never shown in this conversation, it will be
looked up directly. Output strict JSON, no prose.
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
