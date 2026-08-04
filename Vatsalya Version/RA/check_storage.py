#!/usr/bin/env python3
"""Check LightRAG storage statistics."""

import json
import xml.etree.ElementTree as ET
from pathlib import Path

STORAGE_DIR = Path("lightrag_storage")

def count_documents():
    """Count total documents in storage."""
    doc_file = STORAGE_DIR / "kv_store_full_docs.json"
    if not doc_file.exists():
        return 0

    with open(doc_file) as f:
        docs = json.load(f)
    return len(docs)

def count_nodes():
    """Count total entities (nodes) in storage."""
    entities_file = STORAGE_DIR / "kv_store_full_entities.json"
    if not entities_file.exists():
        return 0

    with open(entities_file) as f:
        entities = json.load(f)
    return len(entities)

def count_edges():
    """Count total relations (edges) in storage."""
    relations_file = STORAGE_DIR / "kv_store_full_relations.json"
    if not relations_file.exists():
        return 0

    with open(relations_file) as f:
        relations = json.load(f)
    return len(relations)

def count_graph_stats():
    """Get stats from graphml file."""
    graph_file = STORAGE_DIR / "graph_chunk_entity_relation.graphml"
    if not graph_file.exists():
        return None, None

    try:
        tree = ET.parse(graph_file)
        root = tree.getroot()

        # Extract namespace
        ns = {'g': 'http://graphml.graphdrawing.org/xmlns'}

        # Count nodes and edges
        nodes = root.findall('.//g:node', ns)
        edges = root.findall('.//g:edge', ns)

        return len(nodes), len(edges)
    except Exception as e:
        print(f"Error reading graphml: {e}")
        return None, None

if __name__ == "__main__":
    docs = count_documents()
    nodes = count_nodes()
    edges = count_edges()
    graph_nodes, graph_edges = count_graph_stats()

    print("=" * 50)
    print("LightRAG Storage Statistics")
    print("=" * 50)
    print(f"📄 Documents:      {docs:,}")
    print(f"🔵 Nodes (Entities): {nodes:,}")
    print(f"🔗 Edges (Relations): {edges:,}")
    print()
    if graph_nodes is not None:
        print(f"Graph Stats (from GraphML):")
        print(f"  Nodes: {graph_nodes:,}")
        print(f"  Edges: {graph_edges:,}")
    print("=" * 50)
