"""Static shopper capability inventory used by deterministic routing.

Model output can select only a declared action. It cannot register a tool,
invent a capability, or expand this allowlist at runtime.
"""

from __future__ import annotations

from .contracts import Action


SHOPPER_CAPABILITIES: dict[Action, frozenset[str]] = {
    Action.SEARCH: frozenset({"search_catalog", "assess_retrieval_confidence", "build_follow_up_candidates"}),
    Action.REFINE: frozenset({"search_catalog", "assess_retrieval_confidence", "build_follow_up_candidates"}),
    Action.PRODUCT_DETAILS: frozenset({"resolve_reference", "check_commerce_eligibility", "get_product_details"}),
    Action.COMPARE: frozenset({"resolve_reference", "compare_products"}),
    Action.CHECK_AVAILABILITY: frozenset({"resolve_reference", "check_availability"}),
    Action.SHOW_CART: frozenset({"show_cart"}),
    Action.UPDATE_CART: frozenset({"resolve_reference", "check_commerce_eligibility", "update_cart"}),
    Action.RESEARCH_EXTERNAL: frozenset({"detect_research_need", "online_search", "synthesize_research_answer", "validate_research_claims"}),
    Action.RESET_SEARCH: frozenset(),
    Action.HELP: frozenset(),
}

