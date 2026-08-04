#!/usr/bin/env python3
"""
LightRAG Graph Visualizer Web UI
Displays the graph with statistics and allows exploring nodes/edges
"""

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from collections import defaultdict, Counter

try:
    import streamlit as st
    from streamlit_agraph import agraph, Config
except ImportError:
    print("Installing required packages...")
    import subprocess
    subprocess.check_call(["pip", "install", "streamlit", "streamlit-agraph"])
    import streamlit as st
    from streamlit_agraph import agraph, Config

# Configure Streamlit
st.set_page_config(
    page_title="LightRAG Graph Visualizer",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

STORAGE_DIR = Path("/Users/gr/Desktop/Crossword_Grid_8.0/Vatsalya Version/RA/lightrag_storage")
GRAPHML_FILE = STORAGE_DIR / "graph_chunk_entity_relation.graphml"

@st.cache_data
def load_graph_data():
    """Load graph data from GraphML file"""
    tree = ET.parse(str(GRAPHML_FILE))
    root = tree.getroot()
    ns = {'g': 'http://graphml.graphdrawing.org/xmlns'}

    nodes = root.findall('.//g:node', ns)
    edges = root.findall('.//g:edge', ns)

    nodes_data = []
    edges_data = []
    node_map = {}

    for node in nodes:
        node_id = node.get('id')
        entity_type = ""
        entity_id = ""
        description = ""

        for data in node.findall('g:data', ns):
            key = data.get('key')
            if key == 'd1':  # entity_type
                entity_type = data.text or ""
            elif key == 'd0':  # entity_id
                entity_id = data.text or ""
            elif key == 'd2':  # description
                description = data.text or ""

        nodes_data.append({
            'id': node_id,
            'label': node_id[:50],
            'entity_type': entity_type,
            'entity_id': entity_id,
            'description': description[:200] if description else ""
        })
        node_map[node_id] = node_id

    for edge in edges:
        source = edge.get('source')
        target = edge.get('target')

        weight = ""
        description = ""
        for data in edge.findall('g:data', ns):
            key = data.get('key')
            if key == 'd8':  # weight
                weight = data.text or ""
            elif key == 'd9':  # description
                description = data.text or ""

        edges_data.append({
            'source': source,
            'target': target,
            'weight': float(weight) if weight else 1.0,
            'description': description[:100] if description else ""
        })

    return nodes_data, edges_data

@st.cache_data
def load_storage_stats():
    """Load statistics from storage files"""
    doc_status_file = STORAGE_DIR / "kv_store_doc_status.json"
    full_docs_file = STORAGE_DIR / "kv_store_full_docs.json"
    entities_file = STORAGE_DIR / "kv_store_full_entities.json"
    relations_file = STORAGE_DIR / "kv_store_full_relations.json"

    stats = {}

    try:
        with open(doc_status_file) as f:
            doc_status = json.load(f)
            stats['total_docs'] = len(doc_status)
            status_dist = Counter(doc['status'] for doc in doc_status.values())
            stats['doc_status'] = dict(status_dist)
    except Exception as e:
        stats['doc_error'] = str(e)

    try:
        with open(full_docs_file) as f:
            full_docs = json.load(f)
            stats['unique_docs'] = len(full_docs)
    except Exception as e:
        stats['docs_error'] = str(e)

    try:
        with open(entities_file) as f:
            entities = json.load(f)
            stats['entities'] = len(entities)
    except Exception as e:
        stats['entities_error'] = str(e)

    try:
        with open(relations_file) as f:
            relations = json.load(f)
            stats['relations'] = len(relations)
    except Exception as e:
        stats['relations_error'] = str(e)

    return stats

@st.cache_data
def get_node_type_distribution(nodes_data):
    """Get distribution of node types"""
    types = Counter(node['entity_type'] for node in nodes_data)
    return dict(types)

# Main UI
st.title("📊 LightRAG Graph Visualizer")

# Sidebar
with st.sidebar:
    st.header("⚙️ Controls")

    # Load data
    try:
        nodes_data, edges_data = load_graph_data()
        stats = load_storage_stats()
        node_types = get_node_type_distribution(nodes_data)
    except Exception as e:
        st.error(f"Error loading data: {e}")
        st.stop()

    view_mode = st.radio(
        "View Mode",
        ["Overview", "Full Graph", "Search Node", "Statistics", "Node Types"]
    )

    if view_mode == "Full Graph":
        physics_enabled = st.checkbox("Enable Physics", value=True)
        node_size = st.slider("Node Size", 10, 100, 30)
        edge_opacity = st.slider("Edge Opacity", 0.1, 1.0, 0.5)

# Main content
if view_mode == "Overview":
    st.subheader("📈 Quick Statistics")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Nodes", len(nodes_data))
    col2.metric("Total Edges", len(edges_data))
    col3.metric("Documents", stats.get('total_docs', 'N/A'))
    col4.metric("Unique Docs", stats.get('unique_docs', 'N/A'))

    st.divider()

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Document Status")
        if 'doc_status' in stats:
            st.bar_chart(stats['doc_status'])
        else:
            st.info("No document status data available")

    with col2:
        st.subheader("Top 10 Node Types")
        top_types = dict(sorted(node_types.items(), key=lambda x: x[1], reverse=True)[:10])
        st.bar_chart(top_types)

    st.divider()

    st.subheader("📋 Storage Summary")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Entities", stats.get('entities', 'N/A'))
    col2.metric("Relations", stats.get('relations', 'N/A'))
    col3.metric("Unique Docs", stats.get('unique_docs', 'N/A'))
    col4.metric("Total Docs", stats.get('total_docs', 'N/A'))

elif view_mode == "Full Graph":
    st.subheader("🕸️ Full Knowledge Graph")
    st.info(f"Showing {len(nodes_data)} nodes and {len(edges_data)} edges")

    # Convert nodes and edges for agraph
    agraph_nodes = [
        {"id": n['id'], "label": n['id'][:40], "title": f"{n['entity_type']}\n{n['description'][:100]}", "size": node_size}
        for n in nodes_data[:500]  # Limit to first 500 for performance
    ]

    agraph_edges = [
        {"source": e['source'], "target": e['target'], "physics": physics_enabled}
        for e in edges_data
        if e['source'] in {n['id'] for n in agraph_nodes} and e['target'] in {n['id'] for n in agraph_nodes}
    ]

    config = Config(
        directed=False,
        physics=physics_enabled,
        hierarchical=False,
        interaction={"navigationButtons": True, "keyboard": True},
        height="750px"
    )

    try:
        agraph(nodes=agraph_nodes, edges=agraph_edges, config=config)
    except Exception as e:
        st.warning(f"Graph rendering issue: {e}")
        st.info("Try using 'Search Node' mode for better performance")

elif view_mode == "Search Node":
    st.subheader("🔍 Search & Explore Nodes")

    search_query = st.text_input("Search node by ID", placeholder="e.g., 'Red' or 'Synthetic'")

    if search_query:
        matching_nodes = [n for n in nodes_data if search_query.lower() in n['id'].lower()]

        if matching_nodes:
            st.success(f"Found {len(matching_nodes)} matching nodes")

            selected_node = st.selectbox(
                "Select a node",
                matching_nodes,
                format_func=lambda x: f"{x['id']} ({x['entity_type']})"
            )

            if selected_node:
                col1, col2 = st.columns(2)

                with col1:
                    st.write("**Node Details**")
                    st.json({
                        "ID": selected_node['id'],
                        "Type": selected_node['entity_type'],
                        "Entity ID": selected_node['entity_id'],
                    })

                with col2:
                    st.write("**Description**")
                    st.text_area("", value=selected_node['description'], disabled=True, height=150)

                # Find connected edges
                connected_edges = [
                    e for e in edges_data
                    if e['source'] == selected_node['id'] or e['target'] == selected_node['id']
                ]

                if connected_edges:
                    st.subheader(f"Connected Edges ({len(connected_edges)})")
                    for i, edge in enumerate(connected_edges[:20]):
                        other_node = edge['target'] if edge['source'] == selected_node['id'] else edge['source']
                        st.caption(f"→ {other_node} (weight: {edge['weight']:.2f})")
        else:
            st.warning("No nodes found matching the search")

elif view_mode == "Statistics":
    st.subheader("📊 Detailed Statistics")

    tab1, tab2, tab3 = st.tabs(["Graph Stats", "Document Stats", "Storage Stats"])

    with tab1:
        st.write("**Graph Overview**")
        col1, col2 = st.columns(2)
        col1.metric("Total Nodes", len(nodes_data))
        col2.metric("Total Edges", len(edges_data))

        st.write("**Edge Weight Statistics**")
        weights = [e['weight'] for e in edges_data]
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Min Weight", f"{min(weights):.2f}")
        col2.metric("Max Weight", f"{max(weights):.2f}")
        col3.metric("Avg Weight", f"{sum(weights)/len(weights):.2f}")
        col4.metric("Total Weight", f"{sum(weights):.2f}")

    with tab2:
        st.write("**Document Statistics**")
        if 'total_docs' in stats:
            col1, col2, col3 = st.columns(3)
            col1.metric("Total Documents", stats.get('total_docs', 'N/A'))
            col2.metric("Unique Documents", stats.get('unique_docs', 'N/A'))
            col3.metric("Processed", stats.get('doc_status', {}).get('processed', 'N/A'))

        if 'doc_status' in stats:
            st.write("**Document Status Breakdown**")
            for status, count in sorted(stats['doc_status'].items()):
                st.write(f"  {status}: {count}")

    with tab3:
        st.write("**Storage Statistics**")
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Entities", stats.get('entities', 'N/A'))
        col2.metric("Relations", stats.get('relations', 'N/A'))
        col3.metric("Nodes (Graph)", len(nodes_data))
        col4.metric("Edges (Graph)", len(edges_data))

elif view_mode == "Node Types":
    st.subheader("🏷️ Node Type Distribution")

    # Sort by frequency
    sorted_types = dict(sorted(node_types.items(), key=lambda x: x[1], reverse=True))

    col1, col2 = st.columns(2)

    with col1:
        st.bar_chart(sorted_types)

    with col2:
        st.write("**Node Type Summary**")
        st.dataframe(
            {
                "Type": list(sorted_types.keys()),
                "Count": list(sorted_types.values())
            },
            use_container_width=True
        )

# Footer
st.divider()
st.caption(f"LightRAG Storage: {STORAGE_DIR}")
st.caption(f"Last updated: {STORAGE_DIR.stat().st_mtime if STORAGE_DIR.exists() else 'N/A'}")
