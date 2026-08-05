"""Tests for bm25_index.search against the real persisted index built over
the full flipkart_lightrag_corpus.md -- no mocking, since this branch's
whole job is exact-token recall, so it must be verified against real
product text, not synthetic fixtures.

Each query below uses a distinctive term (a style code, a brand+product
combo, a rare descriptive phrase) pulled straight from a real product's
own description, matching retrieval-architecture.md's stated purpose for
BM25: catching exact tokens semantic search can smooth over.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bm25_index import search  # noqa: E402
from load_documents import build_documents  # noqa: E402


def test_index_covers_every_product_with_a_usable_description():
    documents, skipped_empty_description = build_documents(limit=None)
    assert len(documents) == 19998
    assert skipped_empty_description == 2


def test_exact_style_code_ranks_the_owning_sku_first():
    # "ALTHT_3P_21" is Alisha Solid Womens Cycling Shorts' unique style
    # code (appears exactly once in the whole corpus) -- the clearest case
    # BM25 exists for: an exact token embeddings could smooth over.
    results = search("ALTHT_3P_21", top_n=10)
    top_sku_id, _score = results[0]
    assert top_sku_id == "SRTEH2FF9KEDEFGF"


def test_brand_plus_product_terms_rank_the_owning_sku_first():
    results = search("AW Bellies Wedges", top_n=10)
    top_sku_id, _score = results[0]
    assert top_sku_id == "SHOEH4GRSUBJGZXE"


def test_distinctive_phrase_surfaces_owning_sku_in_top_handful():
    # These terms come straight out of FabHomeDecor Fabric Double Sofa
    # Bed's own description; several sibling products from the same
    # FabHomeDecor sofa-bed line share close variants of this text, so this
    # SKU isn't guaranteed rank 1 -- but it must land in the top handful.
    results = search("chrome legs mango wood click clack", top_n=10)
    returned_ids = [sku_id for sku_id, _score in results]
    assert "SBEEH3QGU7MFYJFY" in returned_ids


def test_scores_are_sorted_descending():
    results = search("cotton formal shirt", top_n=25)
    scores = [score for _sku_id, score in results]
    assert scores == sorted(scores, reverse=True)


def test_top_n_is_respected():
    results = search("cotton", top_n=5)
    assert len(results) == 5
