"""Builds the text documents fed into LightRAG.

Per the retrieval-architecture doc, the text corpus is description-only,
by design, and is shared identically across every text-search branch
(BM25 and LightRAG alike) -- structured facts (brand, category, material,
price, stock, rating) live in `product_metadata` / the JSONL and are never
duplicated into this corpus. LightRAG's own extraction step is responsible
for pulling brand/category/material entities out of the description text
itself (see ENTITY_TYPES / addon_params in ingest.py) -- we don't help it
by pre-seeding structured fields into the input text.

This reads flipkart_lightrag_corpus.md directly and parses out exactly the
same per-SKU (product_name, description) pairs that build_lightrag_block
in flipkart_to_lightrag.py produced -- no second source of truth for what
"the text corpus" contains.
"""

import re

from config import SOURCE_MD


def load_md_blocks():
    """Returns a list of (sku_id, product_name, description) tuples, in
    file order, parsed from flipkart_lightrag_corpus.md."""
    with open(SOURCE_MD, "r", encoding="utf-8") as f:
        content = f.read()
    blocks = content.split("\n\n---\n\n")
    parsed = []
    for block in blocks:
        name_match = re.search(r"^# Product:\s*(.+)$", block, re.MULTILINE)
        id_match = re.search(r"^Product ID:\s*(.+)$", block, re.MULTILINE)
        desc_match = re.search(r"## Description\n(.*)", block, re.DOTALL)
        if not id_match:
            continue
        sku_id = id_match.group(1).strip()
        product_name = name_match.group(1).strip() if name_match else ""
        description = desc_match.group(1).strip() if desc_match else ""
        parsed.append((sku_id, product_name, description))
    return parsed


def build_documents(limit=None):
    """Returns a list of (sku_id, document_text) tuples, ready for
    LightRAG.insert(). document_text is exactly `product_name +
    description`, matching flipkart_lightrag_corpus.md's own per-SKU
    block content -- no structured fields folded in. Skips products with
    no description -- nothing for the graph/vector store to usefully
    index there."""
    documents = []
    for sku_id, product_name, description in load_md_blocks():
        if not description or description == "(No description available)":
            continue
        text = f"{product_name}\n\n{description}" if product_name else description
        documents.append((sku_id, text))
        if limit and len(documents) >= limit:
            break
    return documents
