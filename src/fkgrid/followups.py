"""Deterministic follow-up suggestions. No LLM call, no signing/tokens."""

from __future__ import annotations

from .contracts import Action, FollowUpSuggestion, SessionState, TurnResult


def build(result: TurnResult, session_state: SessionState) -> list[FollowUpSuggestion]:
    suggestions: list[FollowUpSuggestion] = []

    if result.action in (Action.SEARCH, Action.REFINE) and result.search_result and result.search_result.entries:
        suggestions.append(
            FollowUpSuggestion(label="See details of the first result", action=Action.PRODUCT_DETAILS,
                                params={"ordinal": 1})
        )
        if len(result.search_result.entries) >= 2:
            suggestions.append(
                FollowUpSuggestion(label="Compare the top two results", action=Action.COMPARE,
                                    params={"ordinals": [1, 2]})
            )
        suggestions.append(
            FollowUpSuggestion(label="Add the first result to cart", action=Action.UPDATE_CART,
                                params={"ordinal": 1})
        )

    if result.action == Action.PRODUCT_DETAILS and result.product_details and result.product_details.found:
        suggestions.append(
            FollowUpSuggestion(label="Add this to cart", action=Action.UPDATE_CART, params={})
        )
        suggestions.append(
            FollowUpSuggestion(label="Check availability", action=Action.CHECK_AVAILABILITY, params={})
        )

    if result.action == Action.UPDATE_CART and result.cart is not None:
        suggestions.append(FollowUpSuggestion(label="Show cart", action=Action.SHOW_CART, params={}))

    if result.action == Action.SHOW_CART and result.cart and result.cart.items:
        suggestions.append(
            FollowUpSuggestion(label="Continue shopping", action=Action.SEARCH, params={})
        )

    return suggestions[:4]
