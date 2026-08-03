# FK GRiD Catalog Language — Tier 2

This branch implements the Tier 2 evidence-driven lexicon workflow described by:

- `Agent-Plans/catalog-language-query-expansion-agent.md`
- `Agent-Plans/Workflows/catalog-language/tier-2-evidence-driven-lexicon-operations.md`
- `technical plans/09-QUERY-RECOVERY-AGENT.md`
- `technical plans/10-CATALOG-LANGUAGE-AGENT.md`
- `technical plans/24-AGENTIC-PIPELINE-AND-LLM-CONTRACTS.md`

The implementation is intentionally offline and contract-first. It does not create,
migrate, or connect to a database. `db/catalog_language_schema.sql` and
`fkgrid.adapters.catalog_language.sql` define the tables/queries a database owner
must wire to the ports.

## Workflow

```text
privacy-safe aggregates
  -> threshold gate
  -> deterministic spelling/morphology clustering
  -> pinned canonical vocabulary
  -> bounded allowed-target retrieval
  -> proposer model (select/abstain)
  -> independent critic (concern codes only)
  -> deterministic evidence score + mapping validation
  -> regression + shadow evaluation
  -> human review
  -> compare-and-swap activation
```

The runtime lookup is deterministic, immutable-snapshot based, scoped, compatible-
version checked, and capped at three mappings per term. It never reads memory,
purchase history, raw transcripts, or user logs, and it never creates hard filters.

Candidate artifacts are written under a temporary sibling directory and published
atomically only after candidate, mapping, regression, shadow, and manifest checksums
are valid. Activation is a compare-and-swap pointer update; an active pointer stores
the approved mapping-ID allowlist so partial human review cannot delete inherited
mappings or activate rejected proposals.

## Integration handoff

Implementations still required outside this branch are represented by ports:

- DB owner: `EvidenceAggregationPort`, `CanonicalVocabularyPort`, `ActiveLexiconPort`,
  and durable proposal/review/activation persistence behind the SQL query catalog.
- Model owner/provider adapter: `CatalogLanguageModelPort`; the fake gateway and
  versioned prompts are included for contract tests.
- Catalog/retrieval owner: `TargetRetrievalPort` and the regression/shadow fixtures.
- Admin/review owner: `ReviewPort` and `ActivationPort` with role checks and audit.

No active lexicon is changed by a workflow run unless an authorized review decision
and activation CAS both succeed.

## Swagger / FastAPI local test

The FastAPI delivery layer is available at `src/fkgrid/api/main.py`. It is an
additive adapter over the existing workflow and deterministic runtime lookup;
the default app sends proposer and critic calls to the configured Gemma model.
The fake model remains available only when tests explicitly inject the demo
container.

From the repository root, run:

```text
python -m uvicorn fkgrid.api.main:app --app-dir src --host 127.0.0.1 --port 8010
```

The default provider is DeepInfra at
`https://api.deepinfra.com/v1/openai` with model
`google/gemma-4-26B-A4B-it`. Configure it before starting the server:

The Tier 2 proposer and critic use a bounded 10-second default per remote model
call, with a 30-second hard maximum. This is an offline catalog-language budget,
separate from the shopper runtime’s 1.8-second intent budget.

```powershell
$env:FKGRID_GEMMA_PROVIDER = "deepinfra"
$env:FKGRID_GEMMA_BASE_URL = "https://api.deepinfra.com/v1/openai"
$env:FKGRID_GEMMA_MODEL = "google/gemma-4-26B-A4B-it"
$env:DEEPINFRA_API_KEY = "<your-token>"
```

The API reads `DEEPINFRA_API_KEY`, `DEEPINFRA_TOKEN`, or the legacy
`FKGRID_GEMMA_API_KEY` variable. Secrets are read only at process startup and
are never written to the repository or returned by the API.
Successful provider readiness is cached for 15 seconds by default so repeated
guided Swagger submissions do not re-probe the model catalog unnecessarily;
override that with `FKGRID_GEMMA_READY_CACHE_S` when needed.

For a local Ollama runtime instead, set the provider and model explicitly:

```powershell
$env:FKGRID_GEMMA_PROVIDER = "ollama"
$env:FKGRID_GEMMA_BASE_URL = "http://127.0.0.1:11434"
$env:FKGRID_GEMMA_MODEL = "gemma3:27b"
```

For another OpenAI-compatible server, use
`FKGRID_GEMMA_PROVIDER="openai_compatible"`, set `FKGRID_GEMMA_BASE_URL` to its
`/v1` URL, and set `FKGRID_GEMMA_MODEL` to the exact loaded Gemma alias. The
app reports the configured model at `/ready` and returns `503` for workflow
calls when that model is not available; it never silently falls back to the
fake model.

Then open [Swagger UI](http://127.0.0.1:8010/docs). Start with the **Guided
run — enter a query term without editing JSON** operation. Click **Try it out**
and fill in `term`, `locale`, and `category` (the included fixture uses
`footwear`). It generates the run ID, compatibility tuple, evidence window,
and policy versions for you, then sends your term through the same proposer,
critic, validation, regression, review, and activation workflow.
The response is intentionally compact: look under `output.expanded_to` to see
the normalized query, target IDs, mapping type, expansion action, scope, and
evidence band. The `model` and `gates` sections show whether Gemma ran and
which deterministic gates passed.

## Human-friendly demo UI

For a cleaner presentation than Swagger, run the same FastAPI app on the demo
port:

```text
python -m uvicorn fkgrid.api.main:app --app-dir src --host 127.0.0.1 --port 8520
```

Then open [Query Expansion Lab](http://127.0.0.1:8520/demo). The page uses the
same live `POST /api/v1/catalog-language/tier2/guided-run` endpoint, so Gemma
proposer and critic calls remain active. It adds a field-based input, starter
queries, progress states, a readable expansion card, a complete query list
(initial phrase, normalized form, and every returned expansion), model/gate
status, a decision timeline, recent runs, and a copyable one-line summary. It
does not introduce a second workflow or replace the advanced JSON/API routes.

The useful routes are:

- `GET /health` and `GET /ready` — process/readiness state.
- `GET /api/v1/catalog-language/capabilities` — Tier 2 capability and forbidden-action contract.
- `POST /api/v1/catalog-language/tier2/guided-run` — clean field-based input for one term;
  this is the recommended Gemma test path.
- `POST /api/v1/catalog-language/tier2/runs` — runs the existing bounded proposer/critic,
  validation, regression, shadow, review, and activation workflow using the advanced JSON
  contract.
- `POST /api/v1/catalog-language/lookup` — runs the existing model-free deterministic
  active-lexicon lookup; it does not call Gemma.

The Swagger examples use local fixture versions `cat-demo-1` / `tax-demo-1` /
`lex-demo-1`, but proposer and critic decisions are made by the configured
Gemma model. They do not create a database, persist a proposal, or represent
live catalogue truth. For production, construct `CatalogLanguageApiContainer`
with the database, retrieval, review, activation, and artifact adapters; the
HTTP contracts do not need to change.

## Local checks

```text
python -m pytest
python -m ruff check .
python -m mypy src
```

The repository environment may use `uv`; no database or provider secret is needed
for the included deterministic fakes/tests.
