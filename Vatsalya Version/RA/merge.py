"""Candidate fusion & rerank -- retrieval-architecture.md's "Candidate
fusion & rerank" stage, plan.md Step 7.

Treats sql_filter, bm25_index, and semantic_search purely as candidate
generators/eligibility gates: this file calls their existing public
functions unmodified (sql_filter.eligible_skus, bm25_index.search,
semantic_search.search) and never reimplements or duplicates their
retrieval logic. Each of those three stays independently runnable exactly
as it is today; nothing here changes their behavior.

Deliberately not a score-fusion step -- no RRF, no weighted combination of
branch_signal values across BM25 and semantic. Branch-native scores are
never comparable (see candidate.BranchHit's docstring: different scale,
different meaning per branch) and are kept only as evidence. The only
number that ever orders the final output is rerank_score, produced by a
single cross-encoder pass over the merged, eligibility-filtered candidate
set, scored against one canonical text per candidate
(candidate.candidate_text) -- so the reranker is blind to which branch(es)
produced a given candidate, per retrieval-architecture.md's exact step
order: union -> intersect with eligible -> cross-encoder rerank.
"""

import bm25_index
import sql_filter
from candidate import BranchHit, Candidate, candidate_text
from config import RERANK_MODEL

# semantic_search is imported lazily inside fuse_and_rerank(), not here --
# it pulls in ingest.py's full LightRAG/Ollama/Qdrant chain, which this
# module's pure union/eligibility-adapter logic (_union, _bm25_candidates)
# has no need for. Keeps merge.py importable and its pure logic testable
# without that heavier chain installed/running.

_cross_encoder = None


def _get_cross_encoder():
    """Lazily loaded, process-wide singleton -- avoids reloading the model
    weights on every fuse_and_rerank() call. The import itself is deferred
    here too (not at module level), so this module -- and its pure
    union/eligibility logic -- stays importable and testable without
    sentence-transformers installed/loaded unless a rerank is actually
    requested."""
    global _cross_encoder
    if _cross_encoder is None:
        from sentence_transformers import CrossEncoder

        _cross_encoder = CrossEncoder(RERANK_MODEL)
    return _cross_encoder


def _bm25_candidates(soft_query_text: str, top_n: int) -> dict[str, Candidate]:
    """Adapts bm25_index.search()'s raw [(sku_id, score), ...] into the
    shared Candidate currency. bm25_index.py itself is untouched -- this is
    the one place its thin native output gets wrapped, so its own contract
    (list[tuple[str, float]]) never has to change for other callers."""
    matched_terms = sorted(set(bm25_index.tokenize(soft_query_text)))
    candidates: dict[str, Candidate] = {}
    for rank, (sku_id, score) in enumerate(bm25_index.search(soft_query_text, top_n=top_n), start=1):
        candidates[sku_id] = Candidate(
            sku_id=sku_id,
            hits=[
                BranchHit(
                    branch="bm25",
                    rank=rank,
                    branch_signal=score,
                    evidence={"match_type": "lexical", "query_terms": matched_terms},
                )
            ],
        )
    return candidates


def _union(bm25_candidates: dict[str, Candidate], semantic_candidates: list[Candidate]) -> dict[str, Candidate]:
    """Union by sku_id, per retrieval-architecture.md: "a SKU only needs to
    be found by one of the two text-search branches to be considered." A
    SKU found by both gets both branches' BranchHits appended onto one
    Candidate -- never a combined score."""
    union = dict(bm25_candidates)
    for candidate in semantic_candidates:
        if candidate.sku_id in union:
            union[candidate.sku_id].hits.extend(candidate.hits)
        else:
            union[candidate.sku_id] = candidate
    return union


def fuse_and_rerank(
    soft_query_text: str,
    full_query_text: str,
    hard_constraints: dict,
    top_n: int = 20,
    branch_top_n: int = 50,
) -> list[Candidate]:
    """Implements retrieval-architecture.md's merge/rerank step, in order:

    1. eligible = sql_filter.eligible_skus(hard_constraints) -- the
       mandatory, never-relaxed gate.
    2. bm25 = bm25_index.search(soft_query_text), wrapped into Candidates.
    3. semantic = semantic_search.search(soft_query_text) (already Candidates).
    4. union = bm25 ∪ semantic, deduplicated by sku_id.
    5. surviving = union ∩ eligible -- unconditional drop of anything not
       SQL-eligible, regardless of branch score.
    6. cross-encoder rerank: surviving candidates scored against
       full_query_text (not soft_query_text), so hard-constraint phrasing
       still shapes the final ranking, per the doc.

    soft_query_text drives BM25/semantic search (never the hard-constraint
    fields, which are SQL-only). full_query_text is what the cross-encoder
    scores against.
    """
    import semantic_search

    eligible_rows = {row["sku_id"]: row for row in sql_filter.eligible_skus(hard_constraints)}

    bm25_hits = _bm25_candidates(soft_query_text, branch_top_n)
    semantic_hits = semantic_search.search(soft_query_text, top_n=branch_top_n)
    union = _union(bm25_hits, semantic_hits)

    surviving = [c for c in union.values() if c.sku_id in eligible_rows]
    if not surviving:
        return []
    for candidate in surviving:
        candidate.metadata = eligible_rows[candidate.sku_id]

    cross_encoder = _get_cross_encoder()
    pairs = [(full_query_text, candidate_text(c)) for c in surviving]
    scores = cross_encoder.predict(pairs)
    for candidate, score in zip(surviving, scores):
        candidate.rerank_score = float(score)

    surviving.sort(key=lambda c: c.rerank_score, reverse=True)
    return surviving[:top_n]
