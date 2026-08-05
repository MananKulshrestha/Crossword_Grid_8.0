"""Shared data contracts for the retrieval orchestration layer (merge.py,
semantic_search.py) -- the common currency every candidate generator's
output gets normalized into before fusion/rerank.

Kept in its own module, separate from merge.py, so semantic_search.py can
construct Candidate/BranchHit objects directly without importing the
merge/rerank logic (merge.py imports semantic_search.py, so the reverse
can't also be true without a cycle).
"""

from dataclasses import dataclass, field

from load_documents import build_documents


@dataclass(frozen=True)
class BranchHit:
    """One retrieval branch's evidence for including a SKU.

    `branch_signal` is deliberately not named `score`: it's a branch-native
    relevance number (BM25's Okapi score, or the semantic branch's
    rank-derived value -- see semantic_search.py's module docstring for why
    no real similarity number is available there) with a different scale
    and meaning per branch. It is never compared or combined across
    branches -- used only as provenance/evidence and for each branch's own
    top-N cutoff, never as a merge/rerank sort key. `evidence`'s shape
    depends on `branch`/`evidence["match_type"]` (see semantic_search.py and
    merge.py for the concrete shapes each branch produces)."""

    branch: str            # "bm25" | "semantic"
    rank: int              # 1-based position within that branch's own top-N result list
    branch_signal: float   # branch-native relevance number; meaningless outside its own branch
    evidence: dict         # structured provenance -- shape depends on branch/match_type


@dataclass
class Candidate:
    """One SKU surviving to (or through) the merge stage.

    `hits` accumulates one BranchHit per branch that surfaced this SKU -- a
    SKU found by both BM25 and the semantic branch carries two hits, never
    a combined score (no RRF/weighted fusion in this pipeline; see
    merge.py). `rerank_score` is unset until the cross-encoder step; it is
    the only field the final ordering is ever sorted by."""

    sku_id: str
    hits: list[BranchHit] = field(default_factory=list)
    metadata: dict | None = None
    rerank_score: float | None = None

    @property
    def branches(self) -> set[str]:
        return {hit.branch for hit in self.hits}


_sku_text_cache: dict[str, str] | None = None


def _load_sku_text_lookup() -> dict[str, str]:
    """Lazily built, process-wide cache of sku_id -> corpus text, sourced
    from load_documents.build_documents() -- the exact same parse both
    bm25_index.py and ingest.py already use, so this never risks disagreeing
    with either branch about what a SKU's text is. Built once per process
    (~20k entries, same cost bm25_index.py's own index build already pays)."""
    global _sku_text_cache
    if _sku_text_cache is None:
        documents, _skipped_counts = build_documents(limit=None)
        _sku_text_cache = dict(documents)
    return _sku_text_cache


def candidate_text(candidate: Candidate) -> str:
    """The single canonical text reranked against the query -- identical
    regardless of which branch(es) produced the candidate (BM25-only,
    semantic-only, or both), so the cross-encoder is never handed
    branch-specific evidence text and stays independent of which retrieval
    branch found a given SKU. This is exactly the same product_name +
    description text BM25 and LightRAG were both built from (per
    retrieval-architecture.md's "one text corpus, two search engines" rule)
    -- not a BM25-matched-term snippet, not a LightRAG chunk excerpt.

    Raises KeyError if the SKU isn't in the corpus. This should never
    happen -- every candidate reaching this function was itself sourced
    from this same corpus by BM25 or LightRAG -- so a KeyError here means
    the corpus and an index built from it have drifted apart: a real bug to
    surface loudly, not paper over with a fallback string."""
    text_lookup = _load_sku_text_lookup()
    return text_lookup[candidate.sku_id]
