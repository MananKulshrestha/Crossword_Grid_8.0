"""Builds the text documents fed into LightRAG, by joining:
  - the free-text description from flipkart_lightrag_corpus.md
  - a short structured "facts" line from flipkart_catalog_structured.jsonl

The facts line exists purely so LightRAG's entity/relation extraction has
concrete entities (brand, category) to anchor the knowledge graph on --
descriptions alone tend to under-produce graph structure. Keep it short;
the full structured record already lives in SQL/JSONL and shouldn't be
duplicated here.
"""

import json
import re

from config import SOURCE_JSONL, SOURCE_MD


def load_structured_records():
    records = {}
    with open(SOURCE_JSONL, "r", encoding="utf-8") as f:
        for line in f:
            record = json.loads(line)
            if record.get("sku_id"):
                records[record["sku_id"]] = record
    return records


def load_md_blocks():
    with open(SOURCE_MD, "r", encoding="utf-8") as f:
        content = f.read()
    blocks = content.split("\n\n---\n\n")
    parsed = {}
    for block in blocks:
        id_match = re.search(r"^Product ID:\s*(.+)$", block, re.MULTILINE)
        desc_match = re.search(r"## Description\n(.*)", block, re.DOTALL)
        if not id_match:
            continue
        sku_id = id_match.group(1).strip()
        description = desc_match.group(1).strip() if desc_match else ""
        parsed[sku_id] = description
    return parsed


def build_facts_line(record):
    parts = [f"Product: {record.get('product_name') or 'Unknown'}"]
    if record.get("brand"):
        parts.append(f"Brand: {record['brand']}")
    if record.get("category"):
        parts.append(f"Category: {record['category']}")
    if record.get("subcategory_path"):
        parts.append(f"Subcategory: {record['subcategory_path']}")
    if record.get("material"):
        parts.append(f"Material: {record['material']}")
    if record.get("retail_price") is not None:
        parts.append(f"Retail Price: {record['retail_price']}")
    if record.get("discounted_price") is not None:
        parts.append(f"Discounted Price: {record['discounted_price']}")
    return " | ".join(parts)


def build_documents(limit=None):
    """Returns a list of (sku_id, document_text) tuples, ready for
    LightRAG.insert(). Skips products with no description -- nothing for
    the graph/vector store to usefully index."""
    structured = load_structured_records()
    descriptions = load_md_blocks()

    documents = []
    for sku_id, record in structured.items():
        description = descriptions.get(sku_id, "")
        if not description or description == "(No description available)":
            continue
        facts = build_facts_line(record)
        text = f"{facts}\n\n{description}"
        documents.append((sku_id, text))
        if limit and len(documents) >= limit:
            break
    return documents
