# FK GRiD Query Recovery — Tier 2

This branch implements the confidence-gated Query Recovery Agent through Tier 2:

`baseline retrieval -> deterministic gate -> Tier 1 approved-lexicon recovery -> deterministic comparison -> one constrained Tier 2 planner call -> optional one rerun -> grounded recovery/suggestions/no-safe terminal`

The package is deliberately independent of the unfinished catalog, retrieval, session, and database implementations. It provides strict Pydantic contracts, ports, deterministic tools, a bounded workflow, in-memory fakes, a parameterized SQLite adapter, and a migration fragment. A later DB owner can back the ports with SQLAlchemy/PostgreSQL without changing the recovery workflow.

## Scope and safety boundary

- Normal retrieval is mandatory and is supplied by `CatalogRetrievalPort`; recovery never owns ranking or product truth.
- Hard constraints are represented canonically and hashed before/after every plan. A changed hash rejects the plan.
- Approved mappings and recovery concepts are read-only runtime inputs. Recovery cannot publish, mutate catalog data, alter lexicon versions, change cart state, browse the web, or register tools.
- Tier 1 direct mappings are attempted before Tier 2. Tier 2 makes at most one structured planner call and at most one generative rewrite rerun.
- Planner output is untrusted JSON. IDs must be a subset of the context allowlist; ambiguous recovery is rendered as bounded alternatives rather than a chat question.
- Retrieval comparison is deterministic and uses safety/coverage/quality rules before eligible count.
- Every path records a recovery event, including confident-query skips and provider failures.

## Integration points for other owners

Implement these ports in the owning services:

- `CatalogRetrievalPort.search`: normal deterministic retrieval with exact product/SKU/offer bindings and evidence.
- `ApprovedExpansionPort.lookup`: read approved, compatible lexicon mappings.
- `RecoveryConstraintPort.get_constraints`: return active taxonomy/attribute/value concepts allowed for the current tuple.
- `RecoveryPlannerPort.plan`: call the shared structured model gateway with the versioned recovery prompt; it must not expose tools.
- `RecoveryEventPort.record`: persist the sanitized event in the shared `recovery_events` table/outbox.
- `RecoveryCircuitPort`: optional Tier 2 circuit state/metrics.

The default provider-backed adapter is `DeepInfraGemmaGateway`, pinned to
DeepInfra's OpenAI-compatible `google/gemma-4-26B-A4B-it` model. Configure
`FKGRID_DEEPINFRA_API_KEY` (or `DEEPINFRA_API_KEY`/`DEEPINFRA_TOKEN`) outside
Git and run `python scripts/smoke_gemma4.py` for a redacted diagnostic workflow
check. Set `FKGRID_GEMMA_SMOKE_MODE=production` to verify the binding 1,800 ms
timeout/fallback. See `docs/deepinfra-gemma4-integration.md`.

The query layer expects the catalog-language owner’s `lexicon_mappings` table and the catalog/taxonomy owner’s versioned vocabulary. The included migration creates the recovery-owned event and materialized allowed-concept contracts; it does not provision or migrate the rest of the application database.

## FastAPI and Swagger UI

The API layer is a thin delivery adapter over the existing `QueryRecoveryWorkflow`. It exposes typed health/readiness checks, a ready-to-submit example, capabilities, and `POST /v1/query-recovery/turn` without moving recovery policy into FastAPI.

Run it from the repository root:

```text
uv run uvicorn fkgrid.api.main:app --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs). `GET /v1/query-recovery/example` returns a complete request body that can be pasted into the POST operation. If `FKGRID_DEEPINFRA_API_KEY`, `DEEPINFRA_API_KEY`, or `DEEPINFRA_TOKEN` is configured, the default app uses the DeepInfra Gemma 4 26B planner with demo in-memory catalog seams; otherwise it uses a deterministic demo planner. Real DB/retrieval adapters can be injected through `create_app(RecoveryApiDependencies(...))` later.

For an actathon-friendly experience, open `/demo` on the same host. It provides one query box and three intentionally small mock filters (`In stock only`, `Cotton`, and `Under ₹2,000`). Submitting the form calls `POST /v1/query-recovery/demo-turn`, which translates those values into the canonical request and runs the same recovery workflow without requiring anyone to edit the full schema by hand.

The Swagger demo includes approved semantic examples so changing the query changes the recovery result: `shoes` recovers to the footwear family, `trainers` expands to the sports-shoes family (`trainers / sports shoes / athletic shoes`), and `formal wear` or approved typos such as `formalware`/`formalwaer` expand into Shirts, Blazers, and Pants. The UI keeps the same compact form and shows bounded `Did you mean?` alternatives without asking a chat-style clarification. Each approved interpretation is passed through the catalog retrieval port; the demo records the three formalwear branches as `query_branches`. These are deterministic demo lexicon/retrieval seams; production catalog, lexicon, retrieval, session, memory, cart, and purchase-context adapters plug into the existing ports.

## Local contract checks

The package has no required database or provider. With Python 3.12 and Pydantic installed:

```text
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

The eventual full repository should run the standard project commands from the binding plans (`uv sync --all-groups`, Alembic migrations, Ruff, mypy, and pytest). Those commands are not claimed to work in this design-only base repository until the other application slices land.
