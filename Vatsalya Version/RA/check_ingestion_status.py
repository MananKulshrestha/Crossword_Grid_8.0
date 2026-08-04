#!/usr/bin/env python3
"""Check ingestion status: graph vs Qdrant."""

import json
import requests
from pathlib import Path

STORAGE_DIR = Path("lightrag_storage")
QDRANT_URL = "http://localhost:6333"

def get_graph_stats():
    """Get stats from local storage files."""
    doc_status_file = STORAGE_DIR / "kv_store_doc_status.json"

    with open(doc_status_file) as f:
        doc_status = json.load(f)

    full_extraction = sum(1 for status in doc_status.values()
                         if status.get("process_options") != "!")
    embedding_only = sum(1 for status in doc_status.values()
                        if status.get("process_options") == "!")

    return {
        "full_extraction_docs": full_extraction,
        "embedding_only_docs": embedding_only,
        "total_doc_records": len(doc_status),
    }

def get_qdrant_stats():
    """Get vector counts from Qdrant."""
    try:
        response = requests.get(f"{QDRANT_URL}/collections", timeout=5)
        collections = response.json()

        stats = {}
        if collections.get("result", {}).get("collections"):
            for coll in collections["result"]["collections"]:
                coll_name = coll["name"]
                info_response = requests.get(f"{QDRANT_URL}/collections/{coll_name}", timeout=5)
                info = info_response.json()
                stats[coll_name] = info["result"]["points_count"]
        return stats
    except Exception as e:
        print(f"⚠️  Error connecting to Qdrant: {e}")
        return None

if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("INGESTION STATUS: GRAPH vs QDRANT")
    print("=" * 70)

    # Graph stats
    graph = get_graph_stats()
    print("\n📊 GRAPH INGESTION (Local Storage):")
    print(f"   Documents with full extraction:  {graph['full_extraction_docs']:>7,}")
    print(f"   Documents with embedding-only:  {graph['embedding_only_docs']:>7,}")
    print(f"   Total doc records in status:     {graph['total_doc_records']:>7,}")

    # Qdrant stats
    print("\n🔷 QDRANT VECTOR STORE:")
    qdrant = get_qdrant_stats()
    if qdrant:
        for coll_name, count in sorted(qdrant.items()):
            # Parse collection name for readability
            if "chunks" in coll_name:
                label = "Text Chunks"
            elif "entities" in coll_name:
                label = "Entities"
            elif "relationships" in coll_name:
                label = "Relationships"
            else:
                label = coll_name.split("_")[-2].capitalize()

            print(f"   {label:.<40} {count:>7,} vectors")
    else:
        print("   ⚠️  Unable to connect to Qdrant")

    print("\n" + "=" * 70)
