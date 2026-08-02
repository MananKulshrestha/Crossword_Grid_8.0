# FK GRiD shopper-facing agentic chat

This branch contains only the shopper-facing agentic orchestration described by
technical plan 24 and its shopper-runtime dependencies. It is a contract-first
thin slice that can run with deterministic fakes before the database, catalog,
retrieval, cart, research-provider, Qdrant, LightRAG, or Graph RAG owners finish
their adapters.

## What is implemented

- strict Pydantic contracts for the turn state machine, compatibility tuple,
  model requests/responses, intent/delta output, evidence, exact product
  bindings, cart operations, research claims, suggestions, traces, and terminal
  states;
- deterministic free-text query enhancement with current-message precedence,
  optional same-profile memory/history ports, bounded projection, token limit,
  provenance-safe context refs, and current-session fallback;
- tool-less structured model gateway with versioned prompts, one sanitized repair
  attempt, semantic validation, deterministic exact-grammar fallback, a fake
  provider, and a provider-neutral Gemma 4 adapter;
- explicit routing for search/refine, details, compare, availability, show
  cart, atomic typed cart drafts, explicit cited research, reset, help, bounded
  recovery, clarification, suggestions, idempotency, optimistic versions, and
  safe terminal states;
- integration ports for database snapshots/commit, catalog/search/retrieval,
  Qdrant-compatible exact result semantics, optional LightRAG/Graph RAG advisory
  context, memory/history, cart, research, suggestions, traces, and the future
  markdown pipeline;
- deterministic fake adapters and standard-library contract tests.

## Deliberate boundary

The package does not implement a database, hybrid retrieval, Qdrant, LightRAG,
Graph RAG, catalog/tool internals, frontend, or markdown generation. The
orchestrator hands the final typed `ShopperResponse` plus its safe trace to
`MarkdownPipelinePort.handoff`; another owner converts that typed response to
escaped markdown for rendering.

Gemma is configured by alias only. The default hosted alias is
`gemma-4-26b-a4b-it`; supply `FKGRID_MODEL_ENDPOINT` and
`FKGRID_MODEL_API_KEY` through the environment, or inject an
OpenAI-compatible transport into `Gemma4ModelAdapter`. Set
`FKGRID_MODEL_PROTOCOL=gemini` for Google’s native `generateContent` endpoint;
the adapter sends the key via `x-goog-api-key`. No key is stored in this
repository. The endpoint is provider-specific because Gemma is open-weight and
may be served locally or by a compatible hosted provider.

For the native Gemini path, the adapter disables Gemma thinking for the
latency-sensitive shopper call, sends a provider-safe `responseJsonSchema`
projection, and revalidates the complete Pydantic contract after the response.
The hosted 26B smoke path can exceed the plan’s 1.8-second intent budget, in
which case the orchestrator returns its safe deterministic fallback; do not
silently raise that production limit.

## Run the contract tests

From this worktree:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

The eventual repository bootstrap can replace the standard-library test command
with the plan-02 pytest/Ruff/mypy commands without changing the contracts.

See [possible_future_issues.md](possible_future_issues.md) for fragile
integration points, latency risks, accuracy risks, and release gates.

## Manually test the shopper chat

This branch includes an interactive console harness at
`tests/manual_shopper_chat.py`. It uses the fake state, catalog, cart, and
markdown ports, so it does not need a database or frontend and does not make
network calls by default:

```powershell
$env:PYTHONPATH = "src;tests"
python tests/manual_shopper_chat.py --model fake
```

The testing catalog contains exactly three mock products:

- `Prototype shirt 1` — `entry_1` / `product_1` / `sku_1` / `offer_1` — INR 1,000.01
- `Prototype shirt 2` — `entry_2` / `product_2` / `sku_2` / `offer_2` — INR 1,000.02
- `Prototype shirt 3` — `entry_3` / `product_3` / `sku_3` / `offer_3` — INR 1,000.03

Try messages such as `Find a shirt size m`, `Show details for the first one`,
`Compare the first and second one`, `Is the first one available?`, and `Show
my cart`. The runner also supports `/catalog`, `/state`, `/help`, and `/quit`.

To exercise the live Gemma adapter instead, inject a runtime key through
`GEMINI_API_KEY` or `FKGRID_MODEL_API_KEY` and run:

```powershell
python tests/manual_shopper_chat.py --model live --intent-budget-ms 5000
```

Use `--intent-budget-ms 1800` to observe the production safety budget and its
deterministic fallback behavior. The harness records the typed markdown
handoff but intentionally does not render markdown.
