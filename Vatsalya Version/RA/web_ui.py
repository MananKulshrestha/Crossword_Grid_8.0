#!/usr/bin/env python3
"""
LightRAG Graph Web UI - Flask app with query interface
"""

import json
import xml.etree.ElementTree as ET
import asyncio
import threading
from pathlib import Path
from collections import defaultdict, Counter
from flask import Flask, jsonify, request
from lightrag import QueryParam
from config import WORKING_DIR
from query import build_query_rag

STORAGE_DIR = Path(WORKING_DIR)
GRAPHML_FILE = STORAGE_DIR / "graph_chunk_entity_relation.graphml"

app = Flask(__name__)

# Global RAG instance
_rag_instance = None
_rag_lock = threading.Lock()

async def get_rag():
    """Get or create RAG instance"""
    global _rag_instance
    if _rag_instance is None:
        _rag_instance = await build_query_rag()
    return _rag_instance

_bg_loop = None
_bg_loop_thread = None
_bg_loop_ready = threading.Event()


def _run_background_loop():
    global _bg_loop
    _bg_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_bg_loop)
    _bg_loop_ready.set()
    _bg_loop.run_forever()


def run_async_in_thread(coro):
    """Run a coroutine on a single persistent background event loop and
    block until it completes.

    _rag_instance (get_rag()) is cached process-wide, and LightRAG binds
    its internal LLM/embedding worker pools to whichever event loop was
    running the first time it was built. A previous version of this
    function created and closed a brand-new loop per request -- the first
    query worked (it built those worker pools on its own throwaway loop),
    but every query after that failed with "Event loop is closed" as soon
    as it touched those pools, since the loop that any given request will
    close is not the loop already reused by build_query_rag(). Keeping one
    loop alive for the process lifetime, on its own thread, means every
    request that ever mints or reuses those worker pools does so against
    the same non-closed loop.
    """
    global _bg_loop_thread
    if _bg_loop_thread is None:
        _bg_loop_thread = threading.Thread(target=_run_background_loop, daemon=True)
        _bg_loop_thread.start()
        _bg_loop_ready.wait()

    future = asyncio.run_coroutine_threadsafe(coro, _bg_loop)
    return future.result()

# Load data once
def load_graph_data():
    """Load graph from GraphML"""
    tree = ET.parse(str(GRAPHML_FILE))
    root = tree.getroot()
    ns = {'g': 'http://graphml.graphdrawing.org/xmlns'}

    nodes = []
    edges = []

    for node in root.findall('.//g:node', ns):
        node_id = node.get('id')
        node_type = ""
        description = ""

        for data in node.findall('g:data', ns):
            key = data.get('key')
            if key == 'd1':
                node_type = data.text or ""
            elif key == 'd2':
                description = data.text or ""

        nodes.append({
            'id': node_id,
            'label': node_id,
            'type': node_type,
            'description': description[:300]
        })

    for edge in root.findall('.//g:edge', ns):
        source = edge.get('source')
        target = edge.get('target')
        weight = 1.0

        for data in edge.findall('g:data', ns):
            if data.get('key') == 'd8':
                try:
                    weight = float(data.text or 1.0)
                except:
                    pass

        edges.append({
            'source': source,
            'target': target,
            'weight': weight
        })

    return nodes, edges

def load_stats():
    """Load storage statistics"""
    stats = {}

    # Load document stats
    doc_file = STORAGE_DIR / "kv_store_doc_status.json"
    if doc_file.exists():
        with open(doc_file) as f:
            docs = json.load(f)
            stats['total_docs'] = len(docs)
            status_count = Counter(d.get('status') for d in docs.values())
            stats['doc_status'] = dict(status_count)

    # Load full docs
    docs_file = STORAGE_DIR / "kv_store_full_docs.json"
    if docs_file.exists():
        with open(docs_file) as f:
            stats['unique_docs'] = len(json.load(f))

    # Load entities
    ent_file = STORAGE_DIR / "kv_store_full_entities.json"
    if ent_file.exists():
        with open(ent_file) as f:
            stats['entities'] = len(json.load(f))

    # Load relations
    rel_file = STORAGE_DIR / "kv_store_full_relations.json"
    if rel_file.exists():
        with open(rel_file) as f:
            stats['relations'] = len(json.load(f))

    return stats

# Load data
GRAPH_NODES, GRAPH_EDGES = load_graph_data()
STATS = load_stats()

# Count node types
NODE_TYPES = Counter(n['type'] for n in GRAPH_NODES)

@app.route('/')
def index():
    # Prepare JSON for visualization (first 500 nodes for performance)
    limited_nodes = GRAPH_NODES[:500]
    limited_node_ids = {n['id'] for n in limited_nodes}

    nodes_json = json.dumps([
        {'id': n['id'], 'label': n['label'][:30], 'type': n['type'], 'description': n['description'][:100],
         'color': '#4a9eff', 'title': f"{n['type']} - {n['description'][:100]}"}
        for n in limited_nodes
    ])

    edges_json = json.dumps([
        {'from': e['source'], 'to': e['target'], 'weight': e['weight']}
        for e in GRAPH_EDGES
        if e['source'] in limited_node_ids and e['target'] in limited_node_ids
    ])

    total_nodes = len(GRAPH_NODES)
    total_edges = len(GRAPH_EDGES)
    storage_dir = str(STORAGE_DIR)

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>LightRAG Graph Visualizer & Query</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <script src="https://cdnjs.cloudflare.com/ajax/libs/vis/4.21.0/vis.min.js"></script>
        <link href="https://cdnjs.cloudflare.com/ajax/libs/vis/4.21.0/vis.min.css" rel="stylesheet" type="text/css" />
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif; background: #0f1419; color: #e0e0e0; }}
            .header {{ background: #1a1f2e; padding: 20px; border-bottom: 1px solid #333; }}
            .header h1 {{ margin-bottom: 10px; color: #fff; font-size: 24px; }}
            .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; margin-bottom: 20px; }}
            .stat-card {{ background: #1a1f2e; padding: 12px; border-radius: 6px; border-left: 3px solid #4a9eff; text-align: center; }}
            .stat-label {{ font-size: 11px; color: #888; text-transform: uppercase; }}
            .stat-value {{ font-size: 20px; font-weight: bold; color: #4a9eff; margin-top: 5px; }}

            .query-section {{ background: #1a1f2e; padding: 20px; border-bottom: 1px solid #333; }}
            .query-container {{ display: flex; gap: 10px; align-items: flex-start; }}
            .query-input-wrapper {{ flex: 1; }}
            .query-input {{ width: 100%; padding: 12px; background: #0f1419; border: 1px solid #444; border-radius: 4px; color: #e0e0e0; font-size: 14px; }}
            .query-input:focus {{ outline: none; border-color: #4a9eff; }}
            .query-btn {{ padding: 12px 24px; background: #4a9eff; border: none; border-radius: 4px; color: #0f1419; font-weight: bold; cursor: pointer; transition: all 0.2s; }}
            .query-btn:hover {{ background: #6bb3ff; transform: translateY(-2px); }}
            .query-btn:active {{ transform: translateY(0); }}
            .query-btn:disabled {{ background: #333; color: #666; cursor: not-allowed; }}

            .query-results {{ background: #1a1f2e; padding: 20px; border-bottom: 1px solid #333; max-height: 400px; overflow-y: auto; }}
            .result-title {{ color: #4a9eff; font-weight: bold; margin-bottom: 15px; }}
            .result-item {{ background: #0f1419; padding: 12px; margin-bottom: 10px; border-radius: 4px; border-left: 3px solid #4a9eff; }}
            .result-item-title {{ font-weight: bold; color: #fff; margin-bottom: 5px; }}
            .result-item-text {{ font-size: 12px; color: #aaa; line-height: 1.4; }}
            .result-item-type {{ display: inline-block; background: #4a9eff; color: #0f1419; padding: 2px 8px; border-radius: 3px; font-size: 10px; font-weight: bold; margin-top: 5px; }}

            .loading {{ color: #4a9eff; font-style: italic; }}
            .error {{ color: #ff6b6b; background: #2a1a1a; padding: 12px; border-radius: 4px; }}

            .container {{ display: flex; height: calc(100vh - 400px); position: relative; }}
            .sidebar {{ width: 350px; background: #1a1f2e; border-right: 1px solid #333; overflow-y: auto; padding: 20px; }}
            .main {{ flex: 1; display: flex; flex-direction: column; position: relative; }}
            #network {{ flex: 1; background: #0f1419; border: 1px solid #333; }}
            
            .graph-controls {{ position: absolute; top: 10px; right: 10px; z-index: 1000; display: flex; gap: 8px; }}
            .fullscreen-btn {{ padding: 8px 12px; background: #4a9eff; border: none; border-radius: 4px; color: #0f1419; font-weight: bold; cursor: pointer; transition: all 0.2s; font-size: 12px; }}
            .fullscreen-btn:hover {{ background: #6bb3ff; transform: translateY(-2px); }}
            .fullscreen-btn:active {{ transform: translateY(0); }}

            .fullscreen-mode {{ position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; z-index: 2000; background: #0f1419; }}
            .fullscreen-mode .main {{ height: 100vh; width: 100vw; }}
            .fullscreen-mode .sidebar {{ display: none; }}
            .fullscreen-mode .header {{ display: none; }}
            .fullscreen-mode .query-section {{ display: none; }}
            .fullscreen-mode .query-results {{ display: none; }}
            .fullscreen-mode .footer {{ display: none; }}
            .fullscreen-close {{ position: fixed; top: 20px; right: 20px; z-index: 2001; padding: 10px 16px; background: #4a9eff; border: none; border-radius: 4px; color: #0f1419; font-weight: bold; cursor: pointer; }}
            .fullscreen-close:hover {{ background: #6bb3ff; }}

            .search-box {{ width: 100%; padding: 10px; background: #0f1419; border: 1px solid #333; border-radius: 4px; color: #e0e0e0; margin-bottom: 15px; }}
            .node-list {{ max-height: 250px; overflow-y: auto; margin-bottom: 15px; }}
            .node-item {{ padding: 8px; background: #0f1419; margin-bottom: 5px; border-radius: 4px; cursor: pointer; border-left: 3px solid #333; transition: all 0.2s; }}
            .node-item:hover {{ border-left-color: #4a9eff; background: #151a26; }}
            .node-details {{ background: #0f1419; padding: 12px; border-radius: 6px; margin-top: 10px; font-size: 12px; }}
            .footer {{ background: #1a1f2e; padding: 12px; border-top: 1px solid #333; font-size: 11px; color: #888; }}
            h3 {{ margin-top: 12px; margin-bottom: 8px; font-size: 12px; text-transform: uppercase; color: #4a9eff; }}
            .type-item {{ display: flex; justify-content: space-between; padding: 4px 0; font-size: 11px; }}
        </style>
    </head>
    <body id="body">
        <div class="header">
            <h1>📊 LightRAG Graph Visualizer & Query Engine</h1>
            <div class="stats">
                <div class="stat-card"><div class="stat-label">Nodes</div><div class="stat-value">{total_nodes}</div></div>
                <div class="stat-card"><div class="stat-label">Edges</div><div class="stat-value">{total_edges}</div></div>
                <div class="stat-card"><div class="stat-label">Documents</div><div class="stat-value">{STATS.get('total_docs', 'N/A')}</div></div>
                <div class="stat-card"><div class="stat-label">Entities</div><div class="stat-value">{STATS.get('entities', 'N/A')}</div></div>
            </div>
        </div>

        <div class="query-section">
            <div class="query-container">
                <div class="query-input-wrapper">
                    <label for="query-input" style="display: block; margin-bottom: 8px; font-size: 12px; color: #4a9eff; text-transform: uppercase;">🔍 Query Product Listings (Mixed Mode - Qdrant + Knowledge Graph)</label>
                    <input type="text" id="query-input" class="query-input" placeholder="e.g., What cotton shirts are available? Find me blue products with round neck...">
                </div>
                <button class="query-btn" id="query-btn" onclick="executeQuery()">Search</button>
            </div>
        </div>

        <div class="query-results" id="query-results" style="display: none;">
            <div class="result-title">📦 Query Results:</div>
            <div id="results-container"></div>
        </div>

        <div class="container">
            <div class="sidebar">
                <input type="text" class="search-box" id="search-box" placeholder="Search nodes...">
                <div id="node-list-container"></div>

                <h3>Document Status</h3>
                <div id="doc-status"></div>

                <h3>Top Node Types</h3>
                <div id="node-types"></div>

                <div class="node-details" id="node-details" style="display:none;">
                    <h3>Node Details</h3>
                    <div id="node-info"></div>
                </div>
            </div>

            <div class="main">
                <div class="graph-controls">
                    <button class="fullscreen-btn" onclick="toggleFullscreen()">⛶ Fullscreen</button>
                </div>
                <div id="network"></div>
            </div>
        </div>

        <div class="footer">
            <p>💾 Storage: {storage_dir}</p>
            <p>Showing first 500 nodes of {total_nodes} total | {total_edges} edges</p>
        </div>

        <script type="text/javascript">
            const nodesData = {nodes_json};
            const edgesData = {edges_json};

            const nodes = new vis.DataSet(nodesData);
            const edges = new vis.DataSet(edgesData);

            const container = document.getElementById('network');
            const data = {{ nodes: nodes, edges: edges }};
            const options = {{
                physics: {{ enabled: true, barnesHut: {{ gravitationalConstant: -26000, centralGravity: 0.3, springLength: 200 }} }},
                interaction: {{ navigationButtons: true, keyboard: true }},
                nodes: {{ shape: 'dot', font: {{ size: 12, color: '#e0e0e0' }}, borderWidth: 2 }},
                edges: {{ color: {{ color: '#444', highlight: '#4a9eff' }}, smooth: {{ type: 'continuous' }} }}
            }};

            const network = new vis.Network(container, data, options);

            // Fullscreen functionality
            let isFullscreen = false;

            function toggleFullscreen() {{
                isFullscreen = !isFullscreen;
                const body = document.getElementById('body');
                
                if (isFullscreen) {{
                    body.classList.add('fullscreen-mode');
                    const closeBtn = document.createElement('button');
                    closeBtn.className = 'fullscreen-close';
                    closeBtn.textContent = '✕ Exit Fullscreen (Esc)';
                    closeBtn.onclick = toggleFullscreen;
                    body.appendChild(closeBtn);
                    network.fit();
                }} else {{
                    body.classList.remove('fullscreen-mode');
                    const closeBtn = document.querySelector('.fullscreen-close');
                    if (closeBtn) closeBtn.remove();
                }}
            }}

            // Exit fullscreen with Escape key
            document.addEventListener('keydown', (e) => {{
                if (e.key === 'Escape' && isFullscreen) {{
                    toggleFullscreen();
                }}
            }});

            // Allow Enter key to submit query
            document.getElementById('query-input').addEventListener('keypress', (e) => {{
                if (e.key === 'Enter') {{
                    executeQuery();
                }}
            }});

            // Query execution
            async function executeQuery() {{
                const queryText = document.getElementById('query-input').value.trim();
                if (!queryText) {{
                    alert('Please enter a query');
                    return;
                }}

                const queryBtn = document.getElementById('query-btn');
                const resultsDiv = document.getElementById('query-results');
                const resultsContainer = document.getElementById('results-container');

                queryBtn.disabled = true;
                queryBtn.textContent = '🔄 Searching...';
                resultsContainer.innerHTML = '<p class="loading">Querying in mixed mode (Qdrant + Knowledge Graph)...</p>';
                resultsDiv.style.display = 'block';

                try {{
                    const response = await fetch('/api/query', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{ query: queryText }})
                    }});

                    if (!response.ok) {{
                        throw new Error(`HTTP ${{response.status}}: ${{response.statusText}}`);
                    }}

                    const data = await response.json();

                    if (data.error) {{
                        resultsContainer.innerHTML = `<div class="error">Error: ${{data.error}}</div>`;
                    }} else {{
                        displayResults(data);
                    }}
                }} catch (error) {{
                    resultsContainer.innerHTML = `<div class="error">Error: ${{error.message}}</div>`;
                }} finally {{
                    queryBtn.disabled = false;
                    queryBtn.textContent = 'Search';
                }}
            }}

            function displayResults(data) {{
                const resultsContainer = document.getElementById('results-container');

                if (!data.context || data.context.length === 0) {{
                    resultsContainer.innerHTML = '<p class="loading">No results found for your query.</p>';
                    return;
                }}

                let html = '';
                const results = typeof data.context === 'string' ? [data.context] : [data.context];

                results.forEach((item, index) => {{
                    const text = typeof item === 'string' ? item : JSON.stringify(item);
                    const lines = text.split('\\n').filter(l => l.trim());

                    html += `
                        <div class="result-item">
                            <div class="result-item-title">Result {{index + 1}}</div>
                            <div class="result-item-text">
                                ${{lines.slice(0, 5).join('<br>')}}
                                ${{lines.length > 5 ? '<br>...' : ''}}
                            </div>
                            <span class="result-item-type">📦 PRODUCT DATA</span>
                        </div>
                    `;
                }});

                resultsContainer.innerHTML = html;
            }}

            // Load stats
            fetch('/api/stats')
                .then(r => r.json())
                .then(stats => {{
                    // Document status
                    let docHtml = '';
                    for (let [status, count] of Object.entries(stats.doc_status || {{}})) {{
                        docHtml += `<div class="type-item"><span>${{status}}</span><span>${{count}}</span></div>`;
                    }}
                    document.getElementById('doc-status').innerHTML = docHtml;

                    // Node types
                    fetch('/api/node-types')
                        .then(r => r.json())
                        .then(types => {{
                            let typeHtml = '';
                            Object.entries(types).slice(0, 10).forEach(([type, count]) => {{
                                typeHtml += `<div class="type-item"><span>${{type || 'unknown'}}</span><span>${{count}}</span></div>`;
                            }});
                            document.getElementById('node-types').innerHTML = typeHtml;
                        }});
                }});

            // Search
            document.getElementById('search-box').addEventListener('input', (e) => {{
                const query = e.target.value.toLowerCase();
                let results = query ? nodesData.filter(n => n.label.toLowerCase().includes(query)).slice(0, 20) : [];

                let html = results.length ? results.map(n => `
                    <div class="node-item" onclick="selectNode('${{n.id}}', '${{n.label}}', '${{n.type}}', '${{n.description}}')">
                        <strong>${{n.label}}</strong><br>
                        <small>${{n.type || 'unknown'}}</small>
                    </div>
                `).join('') : '<p style="color:#666; font-size:11px;">No results</p>';

                document.getElementById('node-list-container').innerHTML = html;
            }});

            function selectNode(id, label, type, description) {{
                network.selectNodes([id]);
                const connected = edgesData.filter(e => e.from === id || e.to === id);
                document.getElementById('node-details').style.display = 'block';
                document.getElementById('node-info').innerHTML = `
                    <p><strong>ID:</strong> ${{label}}</p>
                    <p><strong>Type:</strong> ${{type || 'unknown'}}</p>
                    <p><strong>Description:</strong> ${{description.substring(0, 120)}}...</p>
                    <p><strong>Connected:</strong> ${{connected.length}} edges</p>
                `;
            }}
        </script>
    </body>
    </html>
    """
    return html

@app.route('/api/stats')
def api_stats():
    return jsonify(STATS)

@app.route('/api/node-types')
def api_node_types():
    return jsonify(dict(sorted(NODE_TYPES.items(), key=lambda x: x[1], reverse=True)[:15]))

@app.route('/api/query', methods=['POST'])
def api_query():
    try:
        data = request.get_json()
        query_text = data.get('query', '').strip()

        if not query_text:
            return jsonify({'error': 'Query text is required'}), 400

        # Run async query in thread
        async def run_query():
            rag = await get_rag()
            context = await rag.aquery(
                query_text, param=QueryParam(mode="mix", only_need_context=True)
            )
            return context

        context = run_async_in_thread(run_query())
        return jsonify({'context': context, 'query': query_text})

    except Exception as e:
        print(f"Query error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    print(f"\n{'='*60}")
    print("🚀 LightRAG Graph Visualizer & Query Engine")
    print(f"{'='*60}")
    print(f"\n📊 Loaded {len(GRAPH_NODES)} nodes and {len(GRAPH_EDGES)} edges")
    print(f"\n🌐 Starting server on http://localhost:8000")
    print(f"   Total documents: {STATS.get('total_docs', 'N/A')}")
    print(f"   Entities: {STATS.get('entities', 'N/A')}")
    print(f"   Relations: {STATS.get('relations', 'N/A')}")
    print(f"\n   ➜ Open your browser and go to: http://localhost:8000")
    print(f"   ➜ Query mode: MIXED (Qdrant Vector Search + Knowledge Graph Traversal)")
    print(f"\n   Features:")
    print(f"     • Query product listings with natural language")
    print(f"     • Fullscreen graph visualization (⛶ button)")
    print(f"     • Visualize the knowledge graph")
    print(f"     • Search nodes in the graph sidebar")
    print(f"\n{'='*60}\n")

    # Builds _rag_instance (and, via build_query_rag()'s warmup() call,
    # loads the local embedding model) now, before Flask starts accepting
    # connections -- torch+sentence_transformers import alone measured
    # ~90-100s on this host (site-packages on a FUSE-mounted volume), so
    # without this the first real user request pays that cost instead of
    # server startup.
    print("Warming up query engine (first-time model import/load can take a minute or two)...")
    run_async_in_thread(get_rag())
    print("Query engine ready.\n")

    app.run(host='0.0.0.0', port=8000, debug=False)
