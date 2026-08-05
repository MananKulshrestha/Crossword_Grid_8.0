"""Query enhancer: deterministically converts a QueryExtraction into the
exact reranker request shape. Not an LLM call - it only copies constraints
the extractor actually found, so it can never invent a price/category the
shopper never mentioned.

The reranker endpoint takes exactly three top-level fields:
    {"soft_query_text": str, "hard_constraints": dict, "top_n": int}
"""

from __future__ import annotations

from .contracts import Action, QueryExtraction, RerankerRequest, SessionState

_DEFAULT_TOP_N = 10
_PRICE_FIELDS = {"max_price"}


def _coerce_constraint_value(field: str, value: object) -> int | float | str | bool | None:
    """The reranker expects max_price as a plain rupee number (int/float).
    A misbehaving extractor call could still hand back a string like
    "10,000" - reject rather than send something that 400s the whole turn."""

    if field not in _PRICE_FIELDS:
        return value
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def build_reranker_request(
    message: str, extraction: QueryExtraction, session_state: SessionState
) -> RerankerRequest:
    hard_constraints: dict = {}

    if extraction.action == Action.REFINE and session_state.last_reranker_request is not None:
        hard_constraints.update(session_state.last_reranker_request.hard_constraints)
        for field in extraction.clear_constraints:
            hard_constraints.pop(field, None)

    for constraint in extraction.constraints:
        value = _coerce_constraint_value(constraint.field, constraint.value)
        if value is None and constraint.field in _PRICE_FIELDS:
            continue
        hard_constraints[constraint.field] = value

    new_query_text = " ".join(extraction.query_terms).strip()
    if new_query_text:
        soft_query_text = new_query_text
    elif extraction.action == Action.REFINE and session_state.last_reranker_request is not None:
        # A REFINE turn narrowing an existing search ("price under 5000")
        # carries no new query terms - keep searching for the same product,
        # don't let the refine sentence itself become the soft query.
        soft_query_text = session_state.last_reranker_request.soft_query_text
    else:
        soft_query_text = message

    return RerankerRequest(
        soft_query_text=soft_query_text,
        hard_constraints=hard_constraints,
        top_n=_DEFAULT_TOP_N,
    )
