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
python -m uvicorn fkgrid.api.main:app --app-dir src --host 127.0.0.1 --port 8000
```

The default provider is Ollama at `http://127.0.0.1:11434` with model
`gemma3:27b`. Set these variables before starting the server if your Gemma
runtime uses another alias or an OpenAI-compatible endpoint:

```powershell
$env:FKGRID_GEMMA_PROVIDER = "ollama"
$env:FKGRID_GEMMA_BASE_URL = "http://127.0.0.1:11434"
$env:FKGRID_GEMMA_MODEL = "gemma3:27b"
```

If that model is not installed in Ollama, install it explicitly before starting
the API (the download is large):

```powershell
ollama pull gemma3:27b
```

For an OpenAI-compatible local server, use `FKGRID_GEMMA_PROVIDER=\"openai_compatible\"`,
set `FKGRID_GEMMA_BASE_URL` to its `/v1` URL, and set `FKGRID_GEMMA_MODEL` to the
exact loaded Gemma alias. The app reports the configured model at `/ready` and
returns `503` for workflow calls when that model is not available; it never
silently falls back to the fake model.

Then open [Swagger UI](http://127.0.0.1:8000/docs). The useful routes are:

- `GET /health` and `GET /ready` — process/readiness state.
- `GET /api/v1/catalog-language/capabilities` — Tier 2 capability and forbidden-action contract.
- `POST /api/v1/catalog-language/tier2/runs` — runs the existing bounded proposer/critic,
  validation, regression, shadow, review, and activation workflow.
- `POST /api/v1/catalog-language/lookup` — runs the existing model-free deterministic
  active-lexicon lookup.

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
