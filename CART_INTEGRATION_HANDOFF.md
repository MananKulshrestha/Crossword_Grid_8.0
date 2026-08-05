# Cart + Retrieval integration — handoff notes

Written by Vatsalya. Current as of a **local, unpushed** merge of
`vatsalya/cart-subsystem` into `manan/final-agentic-chat` on this machine.
Follows the same Confirmed / Recommendation / Open decision labeling
`RA/AGENTIC_INTEGRATION.md` already uses, for consistency.

---

## 1. What happened (context you need before reading the rest)

`vatsalya/cart-subsystem` and `manan/final-agentic-chat` both forked from
`e486242` and independently implemented `DatabaseCartAdapter`. That got
reconciled and PR #1 (`vatsalya/cart-subsystem` → `manan/final-agentic-chat`)
is **already merged on GitHub** (`e578041`).

Since then, **two more independent things landed on top of that same base**,
built in parallel without either side knowing about the other:

- On `manan/final-agentic-chat` directly (`ea557bd`, pushed under Vatsalya's
  own account/a separate Claude session): a `RetrievalCatalogAdapter`
  (`src/fkgrid/catalog/adapter.py`) implementing `CatalogSearchPort` against
  RA + live MySQL. Verified against a **live** Retrieval API and live DB
  (confirmed: `offer_id == 'off_' + sku_id` for all 19,998 rows, exactly one
  ACTIVE offer per sku_id, `catalog_version` uniformly `flipkart_v1`), and
  matches the real React frontend's exact fact-label conventions
  (`"price"`/`"brand"`/`"category"`/etc.). Not wired into `ApiRuntime` yet.
- On `vatsalya/cart-subsystem` (this session): the *same idea*, a different
  `RetrievalCatalogAdapter` implementation, plus the actual `FKGRID_CATALOG_MODE`
  wiring into `ApiRuntime`/`api/runtime.py` — tested against the real remote
  MySQL, never against a live RA (couldn't reach one from this laptop).

Merging the two produced an add/add conflict on exactly those two files.
Resolved by **keeping `ea557bd`'s adapter** (more thoroughly verified — live
RA, live DB, frontend-compatible) and **re-applying only this session's
`FKGRID_CATALOG_MODE` wiring on top of it** — their adapter's constructor and
method signatures matched closely enough (`RetrievalCatalogAdapter()`,
same `FKGRID_RETRIEVAL_API_URL` env var, same `CatalogSearchPort` methods)
that the wiring needed no changes. Verified after resolving: full test suite
green, and a live turn through the merged branch correctly reaches their
adapter and degrades gracefully when RA is unreachable.

**Not pushed** — this merge exists only in this local checkout so far.

---

## 2. Cart subsystem — Confirmed, and actually tested live

- Real, MySQL-backed, transactional (`src/fkgrid/cart/adapter.py`,
  `DatabaseCartAdapter`). Off by default; `FKGRID_CART_MODE=database` turns
  it on. `database_cart_readiness()` refuses `cart_ready` unless
  `catalog_version == "flipkart_v1"` *and* MySQL/the three cart tables/an
  ACTIVE offer for that version are all actually reachable.
- **The real DB is directly reachable from this machine — no SSH tunnel,
  no localhost/Docker instance.** Confirmed live this session against:
  `FLIPKART_DB_HOST=213.173.105.95`, `FLIPKART_DB_PORT=24679` (credentials
  held separately, not repeated here). `/readyz` reported `cart_ready: true,
  cart_error: null`, and a real `SHOW_CART` turn round-tripped correctly
  through the full orchestrator against that database.

## 3. Catalog search — Confirmed, partially tested live

- `FKGRID_CATALOG_MODE` (default `fixture`; `retrieval` swaps the
  orchestrator's `catalog=` port to `RetrievalCatalogAdapter`). Mirrors
  `FKGRID_CART_MODE`. See `ApiRuntime.__init__`/`from_environment` in
  `api/runtime.py`.
- **catalog_version fix confirmed working**: `catalog_mode=retrieval` pins
  `ApiRuntime.catalog_version` (and every session's `CompatibilityTuple`) to
  `DATABASE_CART_CATALOG_VERSION` — imported from `cart/adapter.py` by both
  independent adapter implementations, so it can't drift between them.
  Verified live: `FKGRID_CART_MODE=database FKGRID_CATALOG_MODE=retrieval`
  against the real DB above → `cart_ready: true`.
- **Not yet tested end-to-end against a live RA from this machine** — this
  session's default `FKGRID_RETRIEVAL_API_URL` (`http://127.0.0.1:8002`)
  isn't reachable here, so search calls degrade gracefully
  (`RETRIEVAL_API_UNAVAILABLE`) rather than actually completing. `ea557bd`'s
  own docstring says it *was* verified against a live RA + a full
  search → binding → `check_eligibility` → `DatabaseCartAdapter.update_cart`
  round trip — presumably from wherever that session had RA reachable.
  **Open question for whoever ran that: where was RA reachable from, and is
  it still up?** That's the one thing this handoff can't confirm itself.
- `recovery`/`references` stay on the fixture `DeterministicCatalog`
  regardless of `catalog_mode` — both are pure functions of the previous
  turn's own acknowledged entries, not the fixture's stored records, so they
  work unchanged against real search results too.

## 4. Deployment — still nothing built/running anywhere but laptops

No Dockerfile, no CI/CD anywhere in this repo. Nothing has been started on
RunPod as a persistent service. Standing this up still needs a manual
decision on port, a tmux session, deps installed, and env vars pointed at
the real DB/RA. Not attempted.

## 5. Suggested next steps

1. Confirm where RA is actually reachable from (see §3's open question), and
   test a real search → real ADD_ITEM round trip through the merged branch
   from a machine that can reach it.
2. Push this merge once reviewed.
3. Decide who owns actually starting this on RunPod, and on which port.
