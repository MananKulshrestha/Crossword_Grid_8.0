"""Tests for candidate.candidate_text against the real corpus (no mocking,
same convention as test_bm25.py) -- the whole point of candidate_text is
that it returns identical text for a SKU regardless of which branch(es)
produced the Candidate, so that's what these tests actually verify, not
just "it returns non-empty text."
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from candidate import BranchHit, Candidate, candidate_text  # noqa: E402

# Reuses the same known SKU/text pairing test_bm25.py verifies BM25 against,
# so both test files agree on what "real" looks like for this SKU.
KNOWN_SKU_ID = "SRTEH2FF9KEDEFGF"
KNOWN_STYLE_CODE = "ALTHT_3P_21"


def test_returns_real_corpus_text_for_a_known_sku():
    candidate = Candidate(sku_id=KNOWN_SKU_ID)
    text = candidate_text(candidate)
    assert KNOWN_STYLE_CODE in text


def test_text_is_identical_regardless_of_which_branch_found_the_candidate():
    bm25_only = Candidate(
        sku_id=KNOWN_SKU_ID,
        hits=[BranchHit(branch="bm25", rank=1, branch_signal=12.3, evidence={"match_type": "lexical"})],
    )
    semantic_only = Candidate(
        sku_id=KNOWN_SKU_ID,
        hits=[BranchHit(branch="semantic", rank=1, branch_signal=1.0, evidence={"match_type": "chunk"})],
    )
    hybrid = Candidate(
        sku_id=KNOWN_SKU_ID,
        hits=[
            BranchHit(branch="bm25", rank=1, branch_signal=12.3, evidence={"match_type": "lexical"}),
            BranchHit(branch="semantic", rank=3, branch_signal=0.33, evidence={"match_type": "entity"}),
        ],
    )
    no_hits = Candidate(sku_id=KNOWN_SKU_ID)  # e.g. attached post-eligibility-filter, before rerank

    texts = {candidate_text(c) for c in (bm25_only, semantic_only, hybrid, no_hits)}
    assert len(texts) == 1  # candidate_text never looks at .hits/.evidence


def test_unknown_sku_id_raises_instead_of_substituting_empty_text():
    candidate = Candidate(sku_id="NOT-A-REAL-SKU-ID")
    with pytest.raises(KeyError):
        candidate_text(candidate)


def test_candidate_branches_property_reflects_hit_branches():
    hybrid = Candidate(
        sku_id=KNOWN_SKU_ID,
        hits=[
            BranchHit(branch="bm25", rank=1, branch_signal=12.3, evidence={}),
            BranchHit(branch="semantic", rank=1, branch_signal=1.0, evidence={}),
        ],
    )
    assert hybrid.branches == {"bm25", "semantic"}
