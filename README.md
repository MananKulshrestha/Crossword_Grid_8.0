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
  provider, and an OpenAI-compatible Qwen3.6 27B adapter;
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

Qwen is configured by alias only. The default alias is
`Qwen/Qwen3.6-27B-Instruct`; supply an injected OpenAI-compatible transport and
API key later through `Qwen36ModelAdapter`. No key is stored in this repository.

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
