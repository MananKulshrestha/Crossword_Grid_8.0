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

from pydantic import BaseModel, ValidationError

from .config import LLMConfig, load_llm_config
from .contracts import ChatTurn, Comparison, MultiProductCandidateGroup, QueryExtraction


@functools.lru_cache(maxsize=1)
def _config() -> LLMConfig:
    return load_llm_config()

_EXTRACTION_SYSTEM_PROMPT = """\
You are a shopping query extractor. Read the shopper's latest message and the
recent conversation, then output ONLY a JSON object matching this schema:

{
  "action": "CHITCHAT" | "SEARCH" | "REFINE" | "PRODUCT_DETAILS" | "COMPARE" | "CHECK_AVAILABILITY" | "SHOW_CART" | "UPDATE_CART",
  "query_terms": [string],
  "constraints": [{"field": "max_price"|"category"|"size"|"stock_status", "value": any}],
  "clear_constraints": [string],
  "references": [{"ordinal": int|null, "sku_id": string|null, "all": bool, "count": int|null}],
  "cart_operations": [{"type": "ADD_ITEM"|"SET_QUANTITY"|"REMOVE_ITEM"|"CLEAR_CART", "reference": {...}|null, "cart_item_id": string|null, "quantity": int|null, "confirmation": bool}],
  "multi_product_budget": {"enabled": bool, "item_queries": [string], "total_budget_paise": int|null, "budget_amount": number|null, "budget_currency": string|null},
  "reply": string|null
}

RULES

- CHITCHAT: message isn't a shopping request (greeting, small talk, thanks).
  Set "reply" to a short direct reply; leave every other field empty/null.
- SEARCH: a fresh product request. REFINE: narrowing the CURRENT search
  (cheaper, a color, a brand, a size, dropping a filter). query_terms for a
  REFINE are ADDED words, not a replacement - the caller appends them to the
  previous search text, so only put the NEW words in query_terms, never
  repeat old ones.
- constraints: only "max_price" (number, shopper's own rupee units, never
  paise, never a string/comma), "category" (ONLY a literal catalog name like
  "Footwear" - never a descriptive phrase), "size", "stock_status". No
  "brand" field exists and no minimum-price field exists - a brand name
  always goes in query_terms; a lower price bound is dropped silently. If
  unsure whether a word is a real category, put it in query_terms instead.
- clear_constraints: on a REFINE, name any field the shopper explicitly
  lifted ("any size", "no price limit") so it stops applying. Don't also
  add a new constraint for that same field in the same turn.
- references: 1-based "ordinal" for "the second one"; "sku_id" verbatim for
  an exact product code even if never shown before; "all": true for
  "all of them"/"everything" (one reference covers every result); "count": N
  for "the first N" (one reference covers the first N, don't enumerate).

- multi_product_budget: set enabled=true only when the shopper asks for two
  or more different product types with one shared total budget. Put one
  concise required product query per type in item_queries. For INR, put the
  amount in total_budget_paise. For another currency such as USD, preserve
  the amount and currency in budget_amount/budget_currency and leave
  total_budget_paise null; never invent an exchange rate. Keep generic
  constraints empty unless the shopper also states a separate exact filter.

EXAMPLES (previous soft_query_text in parens where relevant)

1. ("running shoes") "only adidas" -> REFINE, query_terms=["adidas"]
   (caller merges to "running shoes adidas" - never emit query_terms that
   drop the original product type).
2. ("clothes", hard_constraints has size=M) "any size is fine" -> REFINE,
   query_terms=[], clear_constraints=["size"].
3. "add the first 3 to my cart" -> UPDATE_CART, cart_operations=[{"type":
   "ADD_ITEM", "reference": {"count": 3}, "confirmation": true}].

Output strict JSON, no prose.
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
    recent = [
        {
            "role": turn.role.value,
            "content": turn.canonical_content or turn.content,
        }
        for turn in chat_history[-10:]
    ]
    payload = _chat_completion(
        _EXTRACTION_SYSTEM_PROMPT,
        {"message": message, "history": recent},
        QueryExtraction.model_json_schema(),
    )
    try:
        return QueryExtraction.model_validate(payload)
    except ValidationError as exc:
        raise LLMError("EXTRACTION_SCHEMA_INVALID") from exc


class BundleRecommendationDraft(BaseModel):
    selections: dict[str, str]
    rationale: str


class BundleAnalysisDraft(BaseModel):
    recommendations: list[BundleRecommendationDraft]
    summary: str


_BUNDLE_ANALYSIS_SYSTEM_PROMPT = """\
You are a grounded shopping bundle analyst. Choose the best 2 or 3 complete
sets from the supplied candidate products. Each set must select exactly one
candidate for every required item query, must stay within the shared budget,
and must use only the supplied sku_id values. Compare the supplied prices,
ratings, availability, titles, descriptions, and variant labels. The
catalogue rows are authoritative: do not invent specifications or IDs.
Return only this JSON shape:
{
  "recommendations": [
    {"selections": {"item query": "sku_id"}, "rationale": "short explanation"}
  ],
  "summary": "short overall explanation"
}
Return exactly 2 or 3 recommendations. Do not include totals; the server
computes totals from the canonical catalogue rows.
"""


def analyze_multi_product_sets(
    candidate_groups: list[MultiProductCandidateGroup], budget_paise: int
) -> BundleAnalysisDraft:
    payload = {
        "budget_paise": budget_paise,
        "candidate_groups": [group.model_dump(mode="json") for group in candidate_groups],
    }
    parsed = _chat_completion(
        _BUNDLE_ANALYSIS_SYSTEM_PROMPT,
        payload,
        BundleAnalysisDraft.model_json_schema(),
    )
    try:
        draft = BundleAnalysisDraft.model_validate(parsed)
    except ValidationError as exc:
        raise LLMError("BUNDLE_ANALYSIS_SCHEMA_INVALID") from exc

    if len(draft.recommendations) not in (2, 3):
        raise LLMError("BUNDLE_ANALYSIS_COUNT_INVALID")

    expected_queries = [group.item_query for group in candidate_groups]
    candidate_by_query = {
        group.item_query: {entry.sku_id: entry for entry in group.candidates}
        for group in candidate_groups
    }
    for recommendation in draft.recommendations:
        if set(recommendation.selections) != set(expected_queries):
            raise LLMError("BUNDLE_ANALYSIS_ITEMS_INVALID")
        selected_skus = list(recommendation.selections.values())
        if len(set(selected_skus)) != len(selected_skus):
            raise LLMError("BUNDLE_ANALYSIS_DUPLICATE_SKU")
        total = 0
        for item_query in expected_queries:
            entry = candidate_by_query[item_query].get(recommendation.selections[item_query])
            if entry is None or entry.price_paise is None:
                raise LLMError("BUNDLE_ANALYSIS_CANDIDATE_INVALID")
            total += entry.price_paise
        if total > budget_paise:
            raise LLMError("BUNDLE_ANALYSIS_OVER_BUDGET")
    return draft


_SUMMARY_SYSTEM_PROMPT = """\
You are a shopping assistant. You are given a product comparison as rows of
{field, cells: [{sku_id, value}]}. Write a short (2-4 sentence) plain-text
summary highlighting the key differences (price, rating, availability,
brand) and, if one option is clearly better value, say so. Do not invent
any fact not present in the rows. Output ONLY a JSON object:
{"summary": string}
"""

_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}


_RELEVANCE_SYSTEM_PROMPT = """\
You are a shopping search result filter. The reranker already applied the
exact hard filters (price/category/size/stock) and did a soft semantic
match on the soft_query_text, but it sometimes returns clearly wrong items
anyway. Given the soft_query_text and a list of candidate products (each
with sku_id, title, brand, category), decide which ones are genuinely
plausible matches for what the shopper asked for and which are obviously
wrong (e.g. wrong product type entirely, or a described attribute like a
color/material the title contradicts).

Be conservative: only drop an item if it is clearly, obviously wrong - not
merely a weaker or less-ideal match. If you are unsure whether it matches,
keep it. Do not drop items just because a soft attribute (like color) isn't
mentioned in the title at all and can't be confirmed either way - only drop
when the title/category actively contradicts the request. Never re-check
the hard constraints (price/category/size/stock) yourself - those were
already applied exactly; judge only the soft/semantic match.

Output ONLY a JSON object: {"keep_sku_ids": [string]} listing the sku_ids
to keep, in the same order given.
"""

_RELEVANCE_SCHEMA = {
    "type": "object",
    "properties": {"keep_sku_ids": {"type": "array", "items": {"type": "string"}}},
    "required": ["keep_sku_ids"],
    "additionalProperties": False,
}


def filter_relevant_sku_ids(
    soft_query_text: str, hard_constraints: dict, candidates: list[dict]
) -> list[str]:
    """Best-effort semantic relevance filter over reranker results. Callers
    should treat LLMError as non-fatal and keep the unfiltered candidates."""

    payload = _chat_completion(
        _RELEVANCE_SYSTEM_PROMPT,
        {
            "soft_query_text": soft_query_text,
            "hard_constraints": hard_constraints,
            "candidates": candidates,
        },
        _RELEVANCE_SCHEMA,
    )
    keep = payload.get("keep_sku_ids")
    if not isinstance(keep, list) or not all(isinstance(sku_id, str) for sku_id in keep):
        raise LLMError("RELEVANCE_SCHEMA_INVALID")
    return keep


def summarize_comparison(comparison: Comparison) -> str:
    payload = _chat_completion(
        _SUMMARY_SYSTEM_PROMPT,
        {"rows": [row.model_dump(mode="json") for row in comparison.rows]},
        _SUMMARY_SCHEMA,
    )
    summary = payload.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise LLMError("SUMMARY_SCHEMA_INVALID")
    return summary
