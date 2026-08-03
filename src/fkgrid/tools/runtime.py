"""Deterministic pre-intent and clarification tools used by the shopper runtime."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence

from .contracts import (
    ClarificationPacket,
    ClarifyingQuestion,
    EnhancementContext,
    FollowUpNeed,
    IntentDelta,
    QueryEnhancement,
    QueryState,
)
from .determinism import canonical_hash, normalize_text, stable_id, tokenize, unique_sorted


def _as_terms(values: Iterable[str]) -> list[str]:
    return unique_sorted(token for value in values for token in tokenize(str(value)))


def enhance_chat_query(
    current_text: str,
    query_state: QueryState | None = None,
    recent_turns: Sequence[Mapping[str, object]] = (),
    memory_terms: Sequence[str] = (),
    verified_purchase_context: Sequence[str] = (),
    max_chars: int = 2000,
    max_tokens: int = 100,
) -> QueryEnhancement:
    """Merge bounded context while preserving current-message precedence.

    Memory and history are soft context only. They are never allowed to create a
    hard filter or an action, and raw prior text is not copied into the result.
    """

    original = current_text[:max_chars]
    explicit_terms = _as_terms([original])
    memory = _as_terms(memory_terms)[:5]
    purchases = _as_terms(verified_purchase_context)[:5]
    history_terms: list[str] = []
    for turn in list(recent_turns)[-3:]:
        candidate = turn.get("normalized_terms", [])
        if isinstance(candidate, list):
            history_terms.extend(str(item) for item in candidate)
    history = _as_terms(history_terms)[:5]
    soft_context = unique_sorted([*memory, *history, *purchases])
    current_set = set(explicit_terms)
    appended = [term for term in soft_context if term not in current_set]
    enhanced_terms = (explicit_terms + appended)[:max_tokens]
    enhanced = " ".join(enhanced_terms)
    return QueryEnhancement(
        original_text=original,
        enhanced_text=enhanced,
        normalized_terms=explicit_terms[:max_tokens],
        soft_context_terms=appended,
        provenance=[
            *(["CURRENT_MESSAGE"] if explicit_terms else []),
            *(["PROFILE_MEMORY"] if memory else []),
            *(["RECENT_TURN_TERMS"] if history else []),
            *(["VERIFIED_PURCHASE_CONTEXT"] if purchases else []),
        ],
        used_memory=bool(memory),
        used_history=bool(history),
    )


def build_query_enhancement_context(
    *,
    session_id: str,
    client_turn_id: str,
    state_version: int,
    current_message: str,
    query_state: QueryState | None = None,
    recent_turns: Sequence[Mapping[str, object]] = (),
    memory_candidates: Sequence[Mapping[str, object]] = (),
    verified_purchases: Sequence[Mapping[str, object]] = (),
    memory_version: int | None = None,
    ignore_history: bool = False,
) -> EnhancementContext:
    """Build the plan-23 internal context envelope deterministically.

    This is read-only. Persistent items are selected as soft context and are
    never promoted into hard filters or actions by this function.
    """

    if not current_message.strip():
        raise ValueError("CURRENT_MESSAGE_REQUIRED")
    if len(current_message) > 2000 or len(tokenize(current_message)) > 100:
        raise ValueError("MESSAGE_LIMIT_EXCEEDED")
    state = query_state or QueryState(
        text=current_message, normalized_terms=tokenize(current_message)
    )
    included: list[str] = []
    excluded: list[str] = []
    soft_terms: list[str] = []
    if ignore_history:
        excluded.extend(
            str(
                item.get(
                    "memory_item_id", item.get("turn_id", item.get("source_purchase_id", "unknown"))
                )
            )
            for item in [*memory_candidates, *recent_turns, *verified_purchases]
        )
    else:
        ranked_memory = sorted(
            [item for item in memory_candidates if item.get("memory_item_id")],
            key=lambda item: (
                -int(item.get("score", 0)),
                -int(item.get("precedence", 0)),
                str(item.get("memory_item_id")),
            ),
        )[:200]
        for item in ranked_memory:
            item_id = str(item["memory_item_id"])
            included.append(item_id)
            value = item.get("typed_value", item.get("value", ""))
            soft_terms.extend(tokenize(str(value)))
        for item in list(recent_turns)[-6:]:
            if item.get("turn_id"):
                included.append(str(item["turn_id"]))
        for item in list(verified_purchases)[:50]:
            if item.get("source_purchase_id"):
                included.append(str(item["source_purchase_id"]))
    normalized = normalize_text(current_message)
    context_hash = canonical_hash(
        {
            "session_id": session_id,
            "turn_id": client_turn_id,
            "state_version": state_version,
            "memory_version": memory_version,
            "message": current_message,
            "state": state,
            "included": included,
            "excluded": excluded,
        },
        length=64,
    )
    enhancement_id = stable_id(
        "enh",
        {"session_id": session_id, "turn_id": client_turn_id, "context_hash": context_hash},
    )
    return EnhancementContext(
        enhancement_id=enhancement_id,
        session_id=session_id,
        originating_client_turn_id=client_turn_id,
        state_version=state_version,
        memory_version=memory_version,
        current_message_verbatim=current_message,
        normalized_current_message=normalized,
        query_state=state.model_copy(update={"soft_terms": unique_sorted(soft_terms)}),
        included_source_ids=unique_sorted(included),
        excluded_source_ids=unique_sorted(excluded),
        context_truncated=len(memory_candidates) > 200 or len(verified_purchases) > 50,
        token_count=min(1500, len(tokenize(current_message)) + len(soft_terms)),
        fallback_state="CURRENT_SESSION_ONLY" if ignore_history else "NONE",
        context_hash=context_hash,
    )


def clean_request_summary(current_message: str) -> str:
    """Return a deterministic, non-authoritative summary for optional phrasing."""

    return " ".join(tokenize(current_message))[:500]


def _parse_filter_delta(text: str) -> dict[str, object]:
    normalized = normalize_text(text)
    filters: dict[str, object] = {}
    price_match = re.search(
        r"(?:under|below|less than|upto|up to)\s*(?:rs\.?\s*)?([\d,]+)", normalized
    )
    if price_match:
        filters["max_price"] = float(price_match.group(1).replace(",", ""))
    minimum_match = re.search(r"(?:above|over|more than)\s*(?:rs\.?\s*)?([\d,]+)", normalized)
    if minimum_match:
        filters["min_price"] = float(minimum_match.group(1).replace(",", ""))
    return filters


def resolve_intent_and_delta(text: str, query_state: QueryState | None = None) -> IntentDelta:
    """Return one bounded action from explicit text; no IDs or facts are invented."""

    normalized = normalize_text(text)
    hard_delta = _parse_filter_delta(text)
    references = re.findall(r"(?:product|sku|offer)[-_ ]?[a-z0-9]+", normalized)
    explicit_research = any(
        token in normalized
        for token in ("research", "latest", "news", "according to the web", "online")
    )
    if any(token in normalized for token in ("add to cart", "remove from cart", "cart", "basket")):
        action = "SHOW_CART" if "show" in normalized or "what" in normalized else "UPDATE_CART"
    elif explicit_research:
        action = "RESEARCH_EXTERNAL"
    elif any(token in normalized for token in ("compare", "versus", " vs ", "difference")):
        action = "COMPARE"
    elif any(token in normalized for token in ("available", "availability", "deliver", "stock")):
        action = "CHECK_AVAILABILITY"
    elif any(
        token in normalized
        for token in ("details", "specification", "specs", "tell me about", "more about")
    ):
        action = "PRODUCT_DETAILS"
    else:
        action = "REFINE" if query_state and query_state.normalized_terms else "SEARCH"
    return IntentDelta(
        action=action,
        query_text=text[:2000],
        hard_filter_delta=hard_delta,
        soft_terms=unique_sorted(tokenize(text)),
        references=references[:5],
        explicit_research=explicit_research,
        confidence=1.0
        if action
        in {
            "SEARCH",
            "REFINE",
            "COMPARE",
            "CHECK_AVAILABILITY",
            "PRODUCT_DETAILS",
            "SHOW_CART",
            "UPDATE_CART",
            "RESEARCH_EXTERNAL",
        }
        else 0.0,
    )


def detect_follow_up_need(
    intent: IntentDelta, query_state: QueryState | None = None
) -> FollowUpNeed:
    if (
        intent.action in {"COMPARE", "CHECK_AVAILABILITY", "PRODUCT_DETAILS"}
        and not intent.references
        and not (query_state and query_state.result_set_id)
    ):
        return FollowUpNeed(needed=True, reason="EXACT_REFERENCE_REQUIRED", blocking=True)
    if intent.action == "RESEARCH_EXTERNAL" and not intent.explicit_research:
        return FollowUpNeed(needed=True, reason="EXPLICIT_RESEARCH_CONSENT_REQUIRED", blocking=True)
    if intent.action in {"SEARCH", "REFINE"} and not intent.query_text.strip():
        return FollowUpNeed(needed=True, reason="EMPTY_QUERY", blocking=True)
    return FollowUpNeed(needed=False, reason="SUFFICIENT_CONTEXT", blocking=False)


def generate_clarifying_question(query_state: QueryState, reason: str = "") -> ClarifyingQuestion:
    if reason == "EXACT_REFERENCE_REQUIRED":
        return ClarifyingQuestion(
            status="ASK",
            question="Which item from the active results should I use?",
            field="reference",
        )
    if reason == "EXPLICIT_RESEARCH_CONSENT_REQUIRED":
        return ClarifyingQuestion(
            status="ASK",
            question="Would you like me to research this externally?",
            field="research_consent",
            allowed_values=["yes", "no"],
        )
    filters = query_state.hard_filters
    if "category_id" not in filters and query_state.category_id is None:
        return ClarifyingQuestion(
            status="ASK", question="Which product category should I search?", field="category_id"
        )
    return ClarifyingQuestion(status="NO_QUESTION")


def build_clarification(
    reason_code: str,
    target_field: str,
    preserved_state_summary: str = "",
    options: Sequence[Mapping[str, object] | str] = (),
) -> ClarificationPacket:
    choices: list[str] = []
    labels: list[str] = []
    for index, option in enumerate(options, start=1):
        if isinstance(option, Mapping):
            choices.append(str(option.get("choice_id", f"choice_{index}")))
            labels.append(str(option.get("label", option.get("choice_id", f"choice_{index}"))))
        else:
            choices.append(f"choice_{index}")
            labels.append(str(option))
    return ClarificationPacket(
        reason_code=reason_code,
        target_field=target_field,
        preserved_state_summary=preserved_state_summary[:500],
        choice_ids=choices[:4],
        options=labels[:4],
    )


def validate_clarifying_question(
    question: ClarifyingQuestion, query_state: QueryState | None = None
) -> bool:
    if question.status == "NO_QUESTION":
        return question.question is None and question.field is None
    if not question.question or len(question.question) > 240 or "?" not in question.question:
        return False
    if question.field == "category_id" and query_state and query_state.category_id:
        return False
    return True
