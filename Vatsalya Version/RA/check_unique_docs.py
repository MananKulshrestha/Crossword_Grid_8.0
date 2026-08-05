#!/usr/bin/env python3
"""Check unique documents actually ingested into the graph."""

import json
from pathlib import Path

STORAGE_DIR = Path("lightrag_storage")

# Get actual unique docs
docs_file = STORAGE_DIR / "kv_store_full_docs.json"
with open(docs_file) as f:
    unique_docs = json.load(f)

# Get status breakdown
status_file = STORAGE_DIR / "kv_store_doc_status.json"
with open(status_file) as f:
    doc_status = json.load(f)

# Count status types
status_counts = {}
for doc_id, status_info in doc_status.items():
    st = status_info.get("status", "unknown")
    status_counts[st] = status_counts.get(st, 0) + 1

# Count duplicates
duplicates = sum(1 for st_data in doc_status.values()
                if st_data.get("metadata", {}).get("is_duplicate"))

print("=" * 70)
print("UNIQUE DOCUMENTS ACTUALLY INGESTED INTO GRAPH")
print("=" * 70)

print(f"\n✅ REAL UNIQUE DOCUMENTS: {len(unique_docs):,}")
print(f"   (These are the actual products in your graph)")

print(f"\n📊 ATTEMPT RECORDS: {len(doc_status):,}")
print(f"   (Tracks all ingestion attempts, including duplicates rejected)")

print(f"\n   Breakdown:")
for st, count in sorted(status_counts.items(), key=lambda x: -x[1]):
    pct = (count / len(doc_status)) * 100
    print(f"      {st:.<25} {count:>7,} ({pct:>5.1f}%)")

print(f"\n   Duplicates caught: {duplicates:,}")
print(f"   (Same content, rejected to avoid redundant extraction)")

print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)
print(f"✓ Unique products in graph:  {len(unique_docs):,}")
print(f"✗ Duplicate attempts:        {duplicates:,}")
print(f"✗ Failed/pending:            {len(doc_status) - len(unique_docs):,}")
print("=" * 70)
