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
provide immutable event-time snapshots. The model owner must provide a strict
structured gateway adapter with no tools and bounded timeout. Operations must
approve/activate policy and queue configuration. API/UI owners should expose
typed inputs and redacted traces only.

No database, provider, network queue, API server, or UI is started by this
package. Tests are offline and deterministic.

## Local verification

```powershell
uv run pytest
uv run ruff check .
```

The project currently uses the bundled fake/in-memory adapters to prove the
workflow while the shared application and database layers are assembled.
