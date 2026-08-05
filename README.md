# FK GRiD shopper-facing agentic chat

This branch contains the shopper-facing agentic orchestration described by
technical plan 24 and its shopper-runtime dependencies, plus a FastAPI testing
surface. It is a contract-first slice that can run against a rich synthetic
catalog fixture while the production database, retrieval, cart,
research-provider, Qdrant, LightRAG, and Graph RAG owners finish their adapters.

## What is implemented

Search supports two backend modes. The easy testing toggle is
`FKGRID_FAST_MODE=false` (the default); set it to `true` to bypass the existing
RunPod reranker flow. Fast mode branches after intent extraction and
deterministic query enhancement, then uses only read-only SQL hard filters,
in-process BM25 over the existing product title and description columns, and
deterministic ranking. It does not call RunPod for retrieval, create
embeddings, rerank, or change database objects/data. The fast response
includes `search_mode: "fast"` and per-entry `bm25_score`.

The branch also supports a shared-budget bundle intent. When extraction sets
`multi_product_budget.enabled` for a request such as “computer, mouse, and
keyboard for 10,000 rupees total”, the orchestrator skips the normal search
mode decision and uses the fast path only. It applies the shared budget as a
hard per-candidate ceiling, retrieves and deterministically ranks up to
`FKGRID_BUNDLE_CANDIDATE_CAP` candidates per item, then sends those candidates
to Gemma for 2 or 3 complete sets. The server recomputes every returned total
from canonical SQL rows and rejects unknown, incomplete, duplicate, or
over-budget model selections. The default cap is 25, so three item types send
at most 75 products to Gemma. Deterministic top-25 selection is used so tests
and repeated requests are reproducible.

The catalog stores INR paise. INR bundle budgets are emitted as
`total_budget_paise`; a non-INR amount remains explicit in the intent and is
rejected unless its conversion is configured. For a USD test amount, set an
explicit local conversion such as `$env:FKGRID_BUNDLE_USD_TO_INR = "85"` before
starting the backend. This conversion is configuration, not a live FX lookup.

For PowerShell testing:

```powershell
$env:FKGRID_FAST_MODE = "true"
$env:PYTHONPATH = "src"
uvicorn fkgrid.api:app --host 127.0.0.1 --port 8000
```

Set `$env:FKGRID_FAST_MODE = "false"` and restart the backend to return to
normal mode. `FKGRID_SEARCH_MODE=normal|fast` remains supported only as a
fallback when `FKGRID_FAST_MODE` is not set.

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
- a deterministic 600-record, multi-category synthetic fixture for local API
  testing; this fixture is catalog data only, not a replacement for Gemma.
- deterministic fake adapters and standard-library contract tests.

## Deliberate boundary

The package does not implement a database, hybrid retrieval, Qdrant, LightRAG,
Graph RAG, catalog/tool internals, frontend, or markdown generation. The
orchestrator hands the final typed `ShopperResponse` plus its safe trace to
`MarkdownPipelinePort.handoff`; another owner converts that typed response to
escaped markdown for rendering.

Gemma is configured by alias and provider. The default live provider is
DeepInfra with model `google/gemma-4-26b-a4b-it`; provide `DEEPINFRA_API_KEY`
through the process environment. DeepInfra uses the OpenAI-compatible endpoint
`https://api.deepinfra.com/v1/openai/chat/completions` and Bearer
authentication. No key is stored in this repository. The older Google-native
transport remains available only when explicitly selecting
`FKGRID_MODEL_PROTOCOL=gemini`.

For live DeepInfra calls, the adapter sends the strict structured-output schema
and revalidates the complete Pydantic contract after the response. The hosted
26B smoke path can exceed the plan’s 1.8-second intent budget, in which case
the orchestrator returns its safe deterministic fallback; do not silently raise
that production limit.

## Run the contract tests

From this worktree:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

The eventual repository bootstrap can replace the standard-library test command
with the plan-02 pytest/Ruff/mypy commands without changing the contracts.

## Test through FastAPI Swagger UI

This branch now includes a FastAPI delivery layer at
`src/fkgrid/api/main.py`. It uses the existing `TurnOrchestrator` and the
existing in-memory testing ports; it does not create a second routing or cart
implementation. The default API runtime uses the real Gemma 4 26B adapter.
Fake model mode is available only when explicitly setting
`FKGRID_MODEL_MODE=fake`; the default is never a fake model.

Inject the key through the process environment and start the API from this
worktree:

```powershell
$env:PYTHONPATH = "src"
$env:FKGRID_MODEL_MODE = "live"
$env:FKGRID_MODEL_PROTOCOL = "deepinfra"
$env:FKGRID_MODEL_ALIAS = "google/gemma-4-26b-a4b-it"
$env:FKGRID_MODEL_ENDPOINT = "https://api.deepinfra.com/v1/openai/chat/completions"
$env:DEEPINFRA_API_KEY = "<your DeepInfra token>"
# Optional fixture controls; defaults are 600 records and seed 20260801.
$env:FKGRID_TEST_CATALOG_SIZE = "600"
$env:FKGRID_TEST_CATALOG_SEED = "20260801"
# Optional diagnostic override; the safe production default remains 1800 ms.
$env:FKGRID_INTENT_BUDGET_MS = "5000"
uvicorn fkgrid.api.main:app --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs). The page now has a
chat panel above the normal Swagger operations: type a message, press Enter or
Send, read the agent response, and continue with a follow-up in the same
session. The panel creates the session and rotates the client/idempotency IDs
automatically, so JSON editing is not required for conversational testing.
The underlying `POST /v1/sessions/{session_id}/turns` operation remains
available for contract-level testing, while `GET /v1/model` exposes safe model
metadata without secrets. `SHOW_CART` remains a typed model-free action.
Hosted Gemma 4 26B can exceed the production 1800 ms budget, so the optional
5000 ms setting is useful for local Swagger diagnosis only.

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

The API testing catalog contains 600 synthetic SKU/offer records across
t-shirts, shirts, jeans, sneakers, backpacks, headphones, laptops,
smartphones, tablets, smartwatches, speakers, and fitness bands. Records have
deterministic prices, brands, colors, materials, variants, ratings,
availability, stock, delivery days, warranty, evidence references, and stable
product/SKU/offer identity tuples. Change `FKGRID_TEST_CATALOG_SIZE` and
`FKGRID_TEST_CATALOG_SEED` for another reproducible fixture.

The default fixture intentionally has no exact red T-shirt SKU; its T-shirt
colors are black, maroon, navy, olive, and white. A normal request such as
`recommend me some red T-shirts` reports that limitation and ranks the closest
available color (maroon) first. An explicit category in a follow-up replaces
the previous scope, so `shoes` searches sneakers instead of reusing T-shirts.

Try messages such as `Find a shirt size m`, `Show details for the first one`,
`Compare the first and second one`, `Is the first one available?`, and `Show
my cart`. The runner also supports `/catalog`, `/state`, `/help`, and `/quit`.

To exercise the live DeepInfra Gemma adapter instead, inject a runtime key
through `DEEPINFRA_API_KEY` and run:

```powershell
python tests/manual_shopper_chat.py --model live --intent-budget-ms 5000
```

Use `--intent-budget-ms 1800` to observe the production safety budget and its
deterministic fallback behavior. The harness records the typed markdown
handoff but intentionally does not render markdown.

## Optional voice input

The Swagger chat panel has an opt-in microphone button. Microphone permission
is requested only after the button is pressed. Pressing it again stops the
recording and sends one audio blob to the isolated
`POST /v1/speech/transcriptions` route; the returned transcript is placed in
the composer for review and is not submitted automatically. Ordinary text
turns never call the speech adapter or add a speech wait to the existing
orchestrator path.

The adapter uses DeepInfra's native
`openai/whisper-large-v3-turbo` endpoint and reads `DEEPINFRA_API_KEY` only
from the process environment. Its fixed versioned prompt preserves shopping
terms such as categories, brands, colors, sizes, quantities, rupees, prices,
comparison, availability, and cart language while asking Whisper to return
only the shopper's spoken words. Configure it with the `FKGRID_SPEECH_*`
values in `.env.example`; set `FKGRID_SPEECH_MODE=disabled` to hide the
provider path while keeping text chat available.
