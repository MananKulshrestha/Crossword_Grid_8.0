"""Tests for merge.py's pure logic -- _union (fusion, no live infra) and
_bm25_candidates (the BM25 adapter, run against the real full-catalog index
the same way test_bm25.py does -- a local build from the committed corpus,
not a live service, so no mocking here either).

fuse_and_rerank() itself needs live MySQL (sql_filter), a built LightRAG/
Qdrant index (semantic_search), and a downloaded cross-encoder model --
none available in this environment, so it isn't exercised here. That's an
end-to-end test to add once those dependencies are actually running, not a
gap in this file.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from candidate import BranchHit, Candidate  # noqa: E402
from merge import _bm25_candidates, _union  # noqa: E402

# Same known SKU/query pairing test_bm25.py verifies BM25 against.
KNOWN_SKU_ID = "SRTEH2FF9KEDEFGF"
KNOWN_STYLE_CODE = "ALTHT_3P_21"


def _bm25_hit(sku_id, rank=1, signal=10.0):
    return Candidate(
        sku_id=sku_id,
        hits=[BranchHit(branch="bm25", rank=rank, branch_signal=signal, evidence={"match_type": "lexical"})],
    )


def _semantic_hit(sku_id, rank=1, signal=1.0):
    return Candidate(
        sku_id=sku_id,
        hits=[BranchHit(branch="semantic", rank=rank, branch_signal=signal, evidence={"match_type": "chunk"})],
    )


def test_union_keeps_bm25_only_candidate_untouched():
    bm25 = {"SKU-A": _bm25_hit("SKU-A")}
    union = _union(bm25, [])
    assert list(union.keys()) == ["SKU-A"]
    assert len(union["SKU-A"].hits) == 1
    assert union["SKU-A"].hits[0].branch == "bm25"


def test_union_keeps_semantic_only_candidate_untouched():
    union = _union({}, [_semantic_hit("SKU-B")])
    assert list(union.keys()) == ["SKU-B"]
    assert len(union["SKU-B"].hits) == 1
    assert union["SKU-B"].hits[0].branch == "semantic"


def test_union_merges_hits_for_a_sku_found_by_both_branches_without_combining_scores():
    bm25 = {"SKU-C": _bm25_hit("SKU-C", rank=2, signal=7.5)}
    union = _union(bm25, [_semantic_hit("SKU-C", rank=1, signal=0.9)])

    assert list(union.keys()) == ["SKU-C"]
    candidate = union["SKU-C"]
    assert len(candidate.hits) == 2  # both hits kept, not fused into one number
    assert candidate.branches == {"bm25", "semantic"}
    branch_signals = {hit.branch: hit.branch_signal for hit in candidate.hits}
    assert branch_signals == {"bm25": 7.5, "semantic": 0.9}  # each branch's own number, untouched
    assert candidate.rerank_score is None  # union never sets a combined/fused score


def test_union_is_a_true_set_union_not_an_intersection():
    bm25 = {"SKU-D": _bm25_hit("SKU-D"), "SKU-E": _bm25_hit("SKU-E")}
    union = _union(bm25, [_semantic_hit("SKU-F")])
    assert set(union.keys()) == {"SKU-D", "SKU-E", "SKU-F"}


def test_bm25_candidates_wraps_real_bm25_output_for_a_known_sku():
    candidates = _bm25_candidates(KNOWN_STYLE_CODE, top_n=10)
    assert KNOWN_SKU_ID in candidates

    candidate = candidates[KNOWN_SKU_ID]
    assert isinstance(candidate, Candidate)
    assert len(candidate.hits) == 1
    hit = candidate.hits[0]
    assert hit.branch == "bm25"
    assert hit.rank == 1  # ALTHT_3P_21 ranks the owning SKU first, per test_bm25.py
    assert isinstance(hit.branch_signal, float)
    assert hit.evidence["match_type"] == "lexical"
    # bm25_index.tokenize() splits on non-alphanumerics (including "_"), so
    # "ALTHT_3P_21" tokenizes to three separate terms, not one combined token.
    assert hit.evidence["query_terms"] == ["21", "3p", "altht"]


def test_bm25_candidates_ranks_are_1_indexed_and_ordered():
    candidates = _bm25_candidates("cotton formal shirt", top_n=10)
    ranks = sorted(c.hits[0].rank for c in candidates.values())
    assert ranks == list(range(1, len(candidates) + 1))
