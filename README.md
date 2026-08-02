# FK GRiD Catalog Quality Sentinel — Tier 1

This worktree implements the Tier 1 **Qualified Case Builder** from
`technical plans/12-CATALOG-QUALITY-SENTINEL.md`.

The workflow is deliberately bounded and review-only:

```text
ingest -> redact -> exact identity/reason/time grouping -> deterministic qualification
       -> event-time catalog snapshots -> bounded evidence packet
       -> one strict classifier port -> citation validation -> deterministic route
       -> human reviewer decision / close / reopen
```

The model sees only a bounded redacted evidence packet and an allowlist of eight
issue classes. It cannot choose tools, queue names, priorities, IDs, or actions.
The code has no catalog-publication, suppression, ranking, refund, seller-action,
or enforcement port.

## What is included

- Strict Pydantic domain contracts for signals, policies, groups, snapshots,
  evidence, assessments, routes, cases, decisions, lifecycle events, and traces.
- Deterministic PII minimization, identity-safe grouping, recurrence/source
  thresholds, correlated-group caps, evidence bounds, citation validation, and
  route mapping.
- An explicit `QualitySentinelWorkflow` with a static capability inventory and
  the eight Tier 1 tools from the plan.
- Ports for persistence, event-time catalog snapshots, classifier, review queue,
  clock, IDs, and audit. In-memory/fake adapters exist only for tests/local
  contract demos.
- `migrations/quality_sentinel_v1.sql` as a database-owner handoff. It is not
  executed here and is not a substitute for the repository's Alembic migration.
- `resources/quality_policy_v2.json` with labelled low-threshold prototype policy/routes.
- `src/fkgrid/ui/app.py`, a simple Streamlit tester with separate report and review forms,
  four-level risk display, case inspection, and human decision controls.

The local prototype opens a case after two independent signals in the same
verified product/reason window. Reports and low-star reviews are both accepted;
subjective reviews can qualify as low-risk aggregate evidence after two
independent submissions. This is intentionally easy to exercise and is not a
production moderation threshold.

Risk is displayed as `CRITICAL`, `HIGH`, `MEDIUM`, or `LOW`. Gemma proposes an
issue class and risk under a versioned ground-truth prompt, then deterministic
policy derives the final risk from structured signal type and severity. That
policy-owned step makes identical evidence replay to the same risk even if a
provider varies its proposed label.

## Integration work left for other owners

The database owner must implement the port contracts with SQLAlchemy/Alembic,
short transactions, unique idempotency constraints, append-only case events,
and the repository's shared canonical IDs/version tuple. The catalog owner must
provide immutable event-time snapshots. A provider-neutral `QualityClassifier`
port is implemented by the offline fake and by the optional
`Gemma4QualityClassifier` adapter. The default live transport uses DeepInfra's
OpenAI-compatible API with the exact `google/gemma-4-26B-A4B-it` model ID,
sends one redacted packet with no tools, requests strict JSON Schema output, and
strictly revalidates citations and fields. An explicit Gemini compatibility
transport is retained behind `FKGRID_GEMMA_PROVIDER=gemini`. Operations must
approve/activate policy and queue configuration. API/UI owners should expose
typed inputs and redacted traces only.

No database or production queue is started by this package. The local API and
Streamlit tester use in-memory adapters; live Gemma calls are opt-in.

## FastAPI and Swagger UI

The thin API layer is available at `fkgrid.api.main:app` and keeps the existing
workflow as the only owner of qualification, evidence, assessment, routing, and
case-transition policy. It provides:

- `GET /healthz` and `GET /readyz` for local readiness.
- `POST /api/v1/quality/signals` for the complete signal-to-review workflow.
- `GET /api/v1/quality/cases/{case_id}` and `/events` for review inspection.
- `POST /api/v1/quality/cases/{case_id}/decision` for reviewer decisions.
- `POST /api/v1/quality/cases/{case_id}/lifecycle` for close/reopen.
- `GET /api/v1/quality/tools`, `/policy`, and `/demo-state` for safe inspection.

Run the no-database local demo with:

```powershell
uv run uvicorn fkgrid.api.main:app --host 127.0.0.1 --port 8000
```

Then open http://127.0.0.1:8000/docs. The default composition uses
deterministic in-memory adapters. Database owners can pass their ports to
`create_app(QualityApiState(...))`; no migration or database startup occurs.

Run the simple Quality Sentinel tester against the API with:

```powershell
uv run streamlit run src/fkgrid/ui/app.py --server.port 8501
```

Open http://127.0.0.1:8501. The sidebar shows whether the API is using the
deterministic fake or `google/gemma-4-26B-A4B-it`. Use the Report and Review
tabs to submit two distinct signals, then use Case review for the assessment,
risk, evidence, route, human decision, and close/reopen flow.

## Local verification

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

The project uses the bundled fake/in-memory adapters for deterministic tests. To
compose the live classifier, set the runtime-only variables in `.env.example`
(or the process environment) and call
`Gemma4QualityClassifier.from_environment()`. No key is read from repository
files, persisted, logged, or included in traces. The database owner still
supplies persistence and migration adapters.

The FastAPI demo uses the deterministic classifier by default. To opt into the
DeepInfra-backed Gemma adapter explicitly, set `FKGRID_USE_GEMMA=true`,
`FKGRID_GEMMA_PROVIDER=deepinfra`, and the runtime-only
`FKGRID_DEEPINFRA_API_KEY` before starting Uvicorn. The model defaults to
`google/gemma-4-26B-A4B-it`; override it only when the operator has approved a
different compatible model.

For an authorized local smoke test, inject `FKGRID_DEEPINFRA_API_KEY` from the
operator's secret manager for the process only, instantiate the classifier, and
run one representative `EvidencePacket`. Do not put the key in `.env`, shell
history, fixtures, traces, or command arguments; clear the process variable
after the smoke test.
