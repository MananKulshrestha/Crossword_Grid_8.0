"""Query enhancer: deterministically converts a QueryExtraction into the
exact reranker request shape. Not an LLM call - it only copies constraints
the extractor actually found, so it can never invent a price/brand/category
the shopper never mentioned.

The reranker endpoint takes exactly three top-level fields:
    {"soft_query_text": str, "hard_constraints": dict, "top_n": int}
"""

from __future__ import annotations

from .contracts import Action, QueryExtraction, RerankerRequest, SessionState

_DEFAULT_TOP_N = 10


def build_reranker_request(
    message: str, extraction: QueryExtraction, session_state: SessionState
) -> RerankerRequest:
    hard_constraints: dict = {}

    if extraction.action == Action.REFINE and session_state.last_reranker_request is not None:
        hard_constraints.update(session_state.last_reranker_request.hard_constraints)

    for constraint in extraction.constraints:
        hard_constraints[constraint.field] = constraint.value

    soft_query_text = " ".join(extraction.query_terms).strip() or message

    return RerankerRequest(
        soft_query_text=soft_query_text,
        hard_constraints=hard_constraints,
        top_n=_DEFAULT_TOP_N,
    )
