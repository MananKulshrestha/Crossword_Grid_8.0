"""BM25 lexical retrieval branch (retrieval-architecture.md, "The three
retrieval branches" #2): exact-token recall over the same description-only
corpus LightRAG indexes, via load_documents.build_documents() -- no second
parse of flipkart_lightrag_corpus.md, no second definition of what counts
as "the text corpus" for a SKU.

Unlike the semantic/graph branch, BM25 has no per-item LLM cost, so it
indexes every product with a usable description, not just the LightRAG
test batch -- the doc calls this out explicitly: BM25 "costs nothing extra
infrastructure-wise... it's in scope at every tier."
"""

import os
import pickle
import re

from rank_bm25 import BM25Okapi

from config import SOURCE_MD
from load_documents import build_documents

INDEX_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bm25_storage", "index.pkl")

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase, strip punctuation, whitespace split -- no stemming, per
    retrieval-architecture.md ("no stemming needed at this catalog
    size")."""
    return _TOKEN_RE.findall(text.lower())


def _build_index():
    documents, _skipped = build_documents(limit=None)
    sku_ids = [sku_id for sku_id, _text in documents]
    tokenized_corpus = [tokenize(text) for _sku_id, text in documents]
    bm25 = BM25Okapi(tokenized_corpus)
    return sku_ids, bm25


def _load_or_build_index():
    """Rebuilds only when the corpus file is newer than the persisted
    index (or no index exists yet) -- matches the doc's stated local-
    persistence pattern ("the same pattern as Chroma's local
    persistence")."""
    corpus_mtime = os.path.getmtime(SOURCE_MD)
    if os.path.exists(INDEX_PATH):
        with open(INDEX_PATH, "rb") as f:
            stored_mtime, sku_ids, bm25 = pickle.load(f)
        if stored_mtime == corpus_mtime:
            return sku_ids, bm25

    sku_ids, bm25 = _build_index()
    os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
    with open(INDEX_PATH, "wb") as f:
        pickle.dump((corpus_mtime, sku_ids, bm25), f)
    return sku_ids, bm25


_sku_ids = None
_bm25 = None


def search(query_text: str, top_n: int = 50) -> list[tuple[str, float]]:
    """Returns [(sku_id, bm25_score), ...] sorted descending by score.

    Matches retrieval-architecture.md's exact query-time shape:
        scores = bm25_index.get_scores(tokenize(soft_query_text))
        top_bm25_candidates = top_n(scores, n=50)
    """
    global _sku_ids, _bm25
    if _bm25 is None:
        _sku_ids, _bm25 = _load_or_build_index()

    scores = _bm25.get_scores(tokenize(query_text))
    ranked = sorted(zip(_sku_ids, scores), key=lambda pair: pair[1], reverse=True)
    return ranked[:top_n]


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} \"your query\"")
        sys.exit(1)
    for sku_id, score in search(" ".join(sys.argv[1:])):
        print(f"{score:.4f}  {sku_id}")
