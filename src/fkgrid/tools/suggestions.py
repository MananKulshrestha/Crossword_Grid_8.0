"""Deterministic signed post-response suggestions, excluding cart actions."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping, Sequence

from .contracts import (
    CompatibilityTuple,
    FollowUpPhrasing,
    ProductBinding,
    SuggestionAction,
    SuggestionItem,
    SuggestionSelection,
    SuggestionSet,
)
from .determinism import canonical_json, stable_id

NON_CART_ACTIONS = {
    "REFINE_RESULTS",
    "VIEW_DETAILS",
    "COMPARE_RESULTS",
    "CHECK_AVAILABILITY",
    "RESEARCH_EXTERNAL",
    "NEW_SEARCH",
}


def generate_follow_up_suggestions(
    candidates: Sequence[Mapping[str, object]],
    locale: str = "en-IN",
    tone: str = "clear, neutral, optional",
) -> FollowUpPhrasing:
    """Deterministic stand-in for the optional post-commit phrasing call.

    Only supplied non-cart candidate IDs and safe labels can be returned. The
    locale and tone are accepted as contract inputs but never cause a new
    action, target, or fact to be invented.
    """

    del locale, tone
    selected: list[str] = []
    labels: dict[str, str] = {}
    for candidate in candidates[:3]:
        candidate_id = str(candidate.get("candidate_id", ""))
        action_type = str(candidate.get("action_type", ""))
        label = str(candidate.get("safe_default_label", ""))[:60].strip()
        if not candidate_id or action_type not in NON_CART_ACTIONS or not label:
            continue
        selected.append(candidate_id)
        labels[candidate_id] = label
    return FollowUpPhrasing(selected_candidate_ids=selected, labels=labels)


def build_follow_up_candidates(
    primary_action: str,
    bindings: Sequence[ProductBinding] = (),
    blocked: bool = False,
) -> list[SuggestionItem]:
    if blocked:
        return []
    candidates: list[tuple[str, str, str, dict[str, object]]] = []
    if primary_action in {"SEARCH", "REFINE"}:
        candidates.append(("REFINE_RESULTS", "Refine these results", "refine", {}))
        if bindings:
            candidates.append(
                (
                    "COMPARE_RESULTS",
                    "Compare the top results",
                    "compare",
                    {"offer_ids": [item.offer_id for item in bindings[:5]]},
                )
            )
            candidates.append(
                (
                    "CHECK_AVAILABILITY",
                    "Check prototype availability",
                    "availability",
                    {"offer_id": bindings[0].offer_id},
                )
            )
    elif primary_action == "PRODUCT_DETAILS" and bindings:
        candidates.append(
            (
                "COMPARE_RESULTS",
                "Compare this with another result",
                "compare",
                {"offer_id": bindings[0].offer_id},
            )
        )
        candidates.append(
            (
                "CHECK_AVAILABILITY",
                "Check prototype availability",
                "availability",
                {"offer_id": bindings[0].offer_id},
            )
        )
    elif primary_action == "COMPARE":
        candidates.append(
            (
                "CHECK_AVAILABILITY",
                "Check prototype availability",
                "availability",
                {"offer_ids": [item.offer_id for item in bindings[:5]]},
            )
        )
    candidates.append(("NEW_SEARCH", "Start a new search", "new-search", {}))
    selected: list[SuggestionItem] = []
    for action_type, label, suffix, payload in candidates:
        if action_type not in NON_CART_ACTIONS:
            continue
        action = SuggestionAction(action_id=suffix, action_type=action_type, payload=payload)
        selected.append(
            SuggestionItem(
                suggestion_id=stable_id("suggestion", action), label=label, action=action
            )
        )
        if len(selected) == 3:
            break
    return selected


def _signature(suggestion_set: SuggestionSet, secret: bytes) -> str:
    unsigned = suggestion_set.model_copy(update={"signature": ""})
    return hmac.new(secret, canonical_json(unsigned).encode("utf-8"), hashlib.sha256).hexdigest()


def build_suggestion_set(
    session_id: str,
    turn_id: str,
    primary_action: str,
    bindings: Sequence[ProductBinding],
    compatibility: CompatibilityTuple,
    now_ms: int,
    secret: bytes,
    ttl_ms: int = 300_000,
    blocked: bool = False,
    state_version: int = 0,
    cart_version: int = 0,
    active_result_set_id: str | None = None,
) -> SuggestionSet | None:
    suggestions = build_follow_up_candidates(primary_action, bindings, blocked)
    if not suggestions:
        return None
    unsigned = SuggestionSet(
        suggestion_set_id=stable_id(
            "sset", {"session_id": session_id, "turn_id": turn_id, "suggestions": suggestions}
        ),
        session_id=session_id,
        turn_id=turn_id,
        expires_at_ms=now_ms + ttl_ms,
        suggestions=suggestions,
        signature="",
        compatibility=compatibility,
        state_version=state_version,
        cart_version=cart_version,
        active_result_set_id=active_result_set_id,
    )
    return unsigned.model_copy(update={"signature": _signature(unsigned, secret)})


def validate_suggestion_set(
    suggestion_set: SuggestionSet,
    session_id: str,
    now_ms: int,
    secret: bytes,
) -> bool:
    if suggestion_set.session_id != session_id or now_ms >= suggestion_set.expires_at_ms:
        return False
    if len(suggestion_set.suggestions) > 3:
        return False
    if any(item.action.action_type not in NON_CART_ACTIONS for item in suggestion_set.suggestions):
        return False
    expected = _signature(suggestion_set, secret)
    return hmac.compare_digest(expected, suggestion_set.signature)


class SuggestionStore:
    def __init__(self, secret: bytes) -> None:
        self.secret = secret
        self._sets: dict[str, SuggestionSet] = {}

    def build_and_store(
        self,
        session_id: str,
        turn_id: str,
        primary_action: str,
        bindings: Sequence[ProductBinding],
        compatibility: CompatibilityTuple,
        now_ms: int,
        blocked: bool = False,
        state_version: int = 0,
        cart_version: int = 0,
        active_result_set_id: str | None = None,
    ) -> SuggestionSet | None:
        suggestion_set = build_suggestion_set(
            session_id,
            turn_id,
            primary_action,
            bindings,
            compatibility,
            now_ms,
            self.secret,
            blocked=blocked,
            state_version=state_version,
            cart_version=cart_version,
            active_result_set_id=active_result_set_id,
        )
        if suggestion_set:
            self._sets[suggestion_set.suggestion_set_id] = suggestion_set
        return suggestion_set

    def select(
        self,
        session_id: str,
        suggestion_set_id: str,
        suggestion_id: str,
        now_ms: int,
        expected_state_version: int | None = None,
        expected_cart_version: int | None = None,
    ) -> SuggestionSelection:
        suggestion_set = self._sets.get(suggestion_set_id)
        if suggestion_set is None:
            return SuggestionSelection(status="NOT_FOUND", suggestion_set_id=suggestion_set_id)
        if not validate_suggestion_set(suggestion_set, session_id, now_ms, self.secret):
            return SuggestionSelection(
                status="SUGGESTION_STALE", suggestion_set_id=suggestion_set_id
            )
        if (
            expected_state_version is not None
            and expected_state_version != suggestion_set.state_version
        ):
            return SuggestionSelection(
                status="SUGGESTION_STALE", suggestion_set_id=suggestion_set_id
            )
        if (
            expected_cart_version is not None
            and expected_cart_version != suggestion_set.cart_version
        ):
            return SuggestionSelection(
                status="SUGGESTION_STALE", suggestion_set_id=suggestion_set_id
            )
        for suggestion in suggestion_set.suggestions:
            if suggestion.suggestion_id == suggestion_id:
                return SuggestionSelection(
                    status="SELECTED", action=suggestion.action, suggestion_set_id=suggestion_set_id
                )
        return SuggestionSelection(status="NOT_FOUND", suggestion_set_id=suggestion_set_id)


def select_suggestion(
    store: SuggestionStore, session_id: str, suggestion_set_id: str, suggestion_id: str, now_ms: int
) -> SuggestionSelection:
    return store.select(session_id, suggestion_set_id, suggestion_id, now_ms)
