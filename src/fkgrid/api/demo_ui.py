"""The intentionally small, dependency-free actathon demo page."""

from __future__ import annotations


DEMO_PAGE = r'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>FK GRiD · Query Recovery Demo</title>
  <style>
    :root { color-scheme: dark; --ink:#f5f7fb; --muted:#a9b4c7; --line:#28334a; --panel:#111a2b; --accent:#7c9cff; --accent-2:#56d6b1; --danger:#ff8f9c; }
    * { box-sizing:border-box; }
    body { margin:0; min-height:100vh; background:radial-gradient(circle at 10% 0%,#1d2c50 0,#0b1020 42%,#080c16 100%); color:var(--ink); font:16px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
    main { width:min(1060px,calc(100% - 32px)); margin:0 auto; padding:56px 0 72px; }
    .eyebrow { color:var(--accent-2); font-size:.78rem; font-weight:800; letter-spacing:.16em; text-transform:uppercase; }
    h1 { max-width:700px; margin:12px 0 10px; font-size:clamp(2.2rem,6vw,4.7rem); line-height:1.02; letter-spacing:-.055em; }
    .lede { max-width:670px; color:var(--muted); font-size:1.08rem; }
    .layout { display:grid; grid-template-columns:minmax(0,1fr) minmax(280px,.72fr); gap:18px; margin-top:34px; }
    .card { border:1px solid var(--line); border-radius:22px; background:rgba(17,26,43,.84); box-shadow:0 22px 70px rgba(0,0,0,.2); padding:24px; }
    label, legend { font-weight:750; }
    textarea { width:100%; min-height:148px; resize:vertical; border:1px solid #34415b; border-radius:14px; background:#0b1221; color:var(--ink); padding:15px; margin-top:9px; font:inherit; outline:none; }
    textarea:focus { border-color:var(--accent); box-shadow:0 0 0 3px rgba(124,156,255,.18); }
    fieldset { border:0; padding:0; margin:22px 0 0; }
    legend { margin-bottom:11px; }
    .hint { color:var(--muted); font-size:.88rem; font-weight:500; }
    .checks { display:grid; gap:10px; }
    .check { display:flex; align-items:center; gap:11px; border:1px solid #2f3b55; border-radius:12px; padding:11px 12px; color:#dbe3f2; cursor:pointer; }
    .check:hover { border-color:#52678f; background:#17223a; }
    input[type=checkbox] { width:18px; height:18px; accent-color:var(--accent); }
    button { width:100%; margin-top:24px; border:0; border-radius:12px; background:linear-gradient(135deg,var(--accent),#a77cff); color:white; padding:13px 17px; font:800 1rem inherit; cursor:pointer; }
    button:disabled { cursor:wait; opacity:.65; }
    .status { min-height:28px; margin-top:14px; color:var(--muted); }
    .status.error { color:var(--danger); }
    .result-card { min-height:100%; }
    .result-empty { color:var(--muted); padding:30px 0; }
    .outcome { display:inline-flex; border-radius:999px; background:rgba(86,214,177,.13); color:var(--accent-2); padding:6px 10px; font-size:.76rem; font-weight:850; letter-spacing:.08em; }
    h2 { margin:14px 0 7px; font-size:1.65rem; }
    h3 { margin:25px 0 8px; font-size:1rem; }
    .small { color:var(--muted); font-size:.9rem; }
    .options { display:grid; gap:8px; margin-top:12px; }
    .option { border:1px solid #35435f; border-radius:12px; padding:10px 12px; color:#e5ebf7; }
    .chips { display:flex; flex-wrap:wrap; gap:7px; margin-top:10px; }
    .chip { border:1px solid #35435f; border-radius:999px; padding:5px 9px; color:#d7e1f3; font-size:.82rem; }
    .product-list { display:grid; gap:8px; margin-top:14px; }
    .product-row { display:grid; grid-template-columns:36px 1fr auto; align-items:center; gap:10px; border:1px solid #2f3b55; border-radius:12px; padding:10px 12px; background:#111a2b; }
    .product-rank { color:var(--accent); font-weight:850; }
    details { margin-top:22px; color:var(--muted); }
    summary { cursor:pointer; color:#dbe3f2; font-weight:700; }
    pre { overflow:auto; max-height:300px; border-radius:12px; background:#0a101d; padding:13px; font-size:.74rem; white-space:pre-wrap; word-break:break-word; }
    @media (max-width:780px) { main { padding-top:32px; } .layout { grid-template-columns:1fr; } }
  </style>
</head>
<body>
  <main>
    <div class="eyebrow">FK GRiD · Query Recovery Agent</div>
    <h1>Describe what you want. We’ll recover the query safely.</h1>
    <p class="lede">A lightweight actathon surface over the real confidence-gated workflow. Type a shopping request, add a few mock catalogue filters, and inspect whether approved semantic recovery finds meaning, a genuine ambiguity is surfaced, or recovery safely abstains.</p>
    <div class="layout">
      <section class="card">
        <form id="recovery-form">
          <label for="query">Your shopping query</label>
          <textarea id="query" maxlength="2000" placeholder="e.g. formal wear for an office event" required>formal wear for an office event</textarea>
          <fieldset>
            <legend>Quick filters <span class="hint">mock catalogue filters</span></legend>
            <div class="checks">
              <label class="check"><input id="in-stock-only" type="checkbox"> In stock only</label>
              <label class="check"><input id="cotton-only" type="checkbox"> Cotton</label>
              <label class="check"><input id="under-2000" type="checkbox"> Under ₹2,000</label>
            </div>
          </fieldset>
          <button id="run-button" type="submit">Run query recovery</button>
          <div id="status" class="status" aria-live="polite"></div>
        </form>
      </section>
      <section id="result-card" class="card result-card" aria-live="polite">
        <div id="empty-state" class="result-empty">Your recovery result will appear here.</div>
        <div id="result" hidden>
          <div id="outcome" class="outcome"></div>
          <h2 id="headline"></h2>
          <div id="summary" class="small"></div>
          <div id="semantic" class="small"></div>
          <h3>Recovered products</h3>
          <div id="products" class="product-list" aria-live="polite"></div>
          <div id="filters" class="chips"></div>
          <div id="clarification" hidden>
            <h3>What the agent needs from you</h3>
            <div id="question" class="small"></div>
            <div id="options" class="options"></div>
          </div>
          <details>
            <summary>Technical trace</summary>
            <pre id="trace"></pre>
          </details>
        </div>
      </section>
    </div>
  </main>
  <script>
    const form = document.getElementById('recovery-form');
    const button = document.getElementById('run-button');
    const status = document.getElementById('status');
    const emptyState = document.getElementById('empty-state');
    const result = document.getElementById('result');
    const clarification = document.getElementById('clarification');
    const options = document.getElementById('options');
    const products = document.getElementById('products');

    function text(node, value) { node.textContent = value || ''; }

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      button.disabled = true;
      status.className = 'status';
      text(status, 'Running baseline retrieval, confidence gate, and recovery...');
      clarification.hidden = true;
      try {
        const response = await fetch('/v1/query-recovery/demo-turn', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            query: document.getElementById('query').value,
            in_stock_only: document.getElementById('in-stock-only').checked,
            cotton_only: document.getElementById('cotton-only').checked,
            under_2000: document.getElementById('under-2000').checked
          })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail?.code || data.detail || 'Recovery request failed');
        const recovery = data.result;
        emptyState.hidden = true;
        result.hidden = false;
        text(document.getElementById('outcome'), recovery.outcome);
        text(document.getElementById('headline'), recovery.interpretation_label || recovery.terminal_state.replaceAll('_', ' '));
        text(document.getElementById('summary'), `${data.query} · ${recovery.event.retrieval_run_count} retrieval run(s) · ${recovery.event.added_latency_ms} ms`);
        text(document.getElementById('semantic'), recovery.selected_run?.interpretation_family ? `Semantic family: ${recovery.selected_run.interpretation_family}` : '');
        products.replaceChildren();
        const baselineIds = new Set(recovery.baseline_run?.result_product_ids || []);
        (recovery.selected_run?.result_product_ids || []).forEach((productId, index) => {
          const row = document.createElement('div');
          row.className = 'product-row';
          const rank = document.createElement('span');
          rank.className = 'product-rank';
          rank.textContent = `#${index + 1}`;
          const id = document.createElement('span');
          id.textContent = productId;
          const source = document.createElement('span');
          source.className = 'small';
          source.textContent = baselineIds.has(productId) ? 'Direct match' : 'Recovered meaning';
          row.append(rank, id, source);
          products.appendChild(row);
        });
        const filterHost = document.getElementById('filters');
        filterHost.replaceChildren();
        data.filters.forEach((label) => { const chip = document.createElement('span'); chip.className = 'chip'; chip.textContent = label; filterHost.appendChild(chip); });
        if (recovery.clarification) {
          clarification.hidden = false;
          text(document.getElementById('question'), recovery.clarification.question);
          options.replaceChildren();
          recovery.clarification.options.forEach((option) => { const item = document.createElement('div'); item.className = 'option'; item.textContent = option.label; options.appendChild(item); });
        }
        text(document.getElementById('trace'), JSON.stringify({outcome: recovery.outcome, gate: recovery.event.trigger_reasons, planner_called: recovery.event.planner_called, validation_codes: recovery.event.planner_validation_codes, hard_filter_mutations: recovery.event.hard_filter_mutation_count, hard_filter_hash: data.hard_filter_hash}, null, 2));
        text(status, 'Recovery complete.');
      } catch (error) {
        status.className = 'status error';
        text(status, error.message || 'Recovery request failed');
      } finally {
        button.disabled = false;
      }
    });
  </script>
</body>
</html>'''


__all__ = ["DEMO_PAGE"]
