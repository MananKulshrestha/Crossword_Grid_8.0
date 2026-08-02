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
- `resources/quality_policy_v1.json` with labelled prototype thresholds/routes.

## Integration work left for other owners

The database owner must implement the port contracts with SQLAlchemy/Alembic,
short transactions, unique idempotency constraints, append-only case events,
and the repository's shared canonical IDs/version tuple. The catalog owner must
provide immutable event-time snapshots. A provider-neutral `QualityClassifier`
port is implemented by the offline fake and by the optional
`Gemma4QualityClassifier` Google REST adapter. The live adapter uses
`gemma-4-26b-a4b-it`, sends one redacted packet with no tools, requests JSON,
and strictly revalidates citations and fields. Operations must approve/activate
policy and queue configuration. API/UI owners should expose typed inputs and
redacted traces only.

No database, provider, network queue, API server, or UI is started by this
package. Tests are offline and deterministic.

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

## Local verification

```powershell
uv run pytest
uv run ruff check .
```

The project uses the bundled fake/in-memory adapters for deterministic tests. To
compose the live classifier, set the runtime-only variables in `.env.example`
(or the process environment) and call
`Gemma4QualityClassifier.from_environment()`. No key is read from repository
files, persisted, logged, or included in traces. The database owner still
supplies persistence and migration adapters.

The FastAPI demo uses the deterministic classifier by default. To opt into the
Gemma adapter explicitly, set `FKGRID_USE_GEMMA=true` together with the
runtime-only `FKGRID_GEMMA_API_KEY` before starting Uvicorn.

For an authorized local smoke test, inject `FKGRID_GEMMA_API_KEY` from the
operator's secret manager for the process only, instantiate the classifier, and
run one representative `EvidencePacket`. Do not put the key in `.env`, shell
history, fixtures, traces, or command arguments; clear the process variable
after the smoke test.
