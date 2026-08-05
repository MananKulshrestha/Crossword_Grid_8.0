"""search_catalog(query_state) -- the external contract retrieval-architecture.md
and plan.md (Step 8) specify: the single entrypoint an outer caller (the
Agentic Chat subsystem) invokes. Splits query_state into hard constraints
and query text, delegates to merge.fuse_and_rerank, and shapes its
Candidate objects into the plain-dict output shape the doc's step 7 calls
for: sku_id, rank/score, matched-attribute evidence, source pass.

This is the only file in the orchestration layer meant to be imported by
code outside RA/ -- sql_filter, bm25_index, semantic_search, and merge
remain internal building blocks, each independently runnable/testable, per
retrieval-architecture.md's scope boundary ("search_catalog's external
contract... stays exactly as already defined").
"""

import merge


def search_catalog(query_state: dict, top_n: int = 20) -> list[dict]:
    """query_state keys:
      hard_constraints: dict, optional (max_price, category, size,
        stock_status -- passed straight through to sql_filter.eligible_skus;
        see that function's docstring for valid keys).
      soft_query_text: str, required -- the semantic/lexical query text
        driving BM25 and semantic search (never hard-constraint fields).
      full_query_text: str, optional -- the full original query text scored
        by the cross-encoder; defaults to soft_query_text if not given.

    Returns a ranked list of dicts, one per surviving candidate, ordered by
    rerank_score descending:
      sku_id, rerank_score, metadata (the product_metadata row), branches
      (which retrieval branch(es) found it: ["bm25"], ["semantic"], or
      both), evidence (one entry per branch hit, with that branch's rank/
      branch_signal/match-specific provenance -- see candidate.BranchHit).
    """
    candidates = merge.fuse_and_rerank(
        soft_query_text=query_state["soft_query_text"],
        full_query_text=query_state.get("full_query_text", query_state["soft_query_text"]),
        hard_constraints=query_state.get("hard_constraints", {}),
        top_n=top_n,
    )
    return [
        {
            "sku_id": candidate.sku_id,
            "rerank_score": candidate.rerank_score,
            "metadata": candidate.metadata,
            "branches": sorted(candidate.branches),
            "evidence": [
                {
                    "branch": hit.branch,
                    "rank": hit.rank,
                    "branch_signal": hit.branch_signal,
                    **hit.evidence,
                }
                for hit in candidate.hits
            ],
        }
        for candidate in candidates
    ]


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} \"your query\"")
        sys.exit(1)
    query_text = " ".join(sys.argv[1:])
    results = search_catalog(
        {"soft_query_text": query_text, "full_query_text": query_text, "hard_constraints": {}}
    )
    print(json.dumps(results, indent=2, default=str))
