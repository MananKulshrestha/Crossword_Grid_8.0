# Cart + Retrieval integration — handoff notes for Manan

Written by Vatsalya, current as of local commit `b2b710f` on
`vatsalya/cart-subsystem` (**not pushed yet** — this file describes work
still under review). Follows the same Confirmed / Recommendation / Open
decision labeling `RA/AGENTIC_INTEGRATION.md` already uses, for consistency.

---

## 1. What just happened (context you need before reviewing the PR)

`vatsalya/cart-subsystem` and `manan/final-agentic-chat` both forked from
`e486242` and **independently implemented the same DatabaseCartAdapter cart
subsystem** — my branch stopped at one commit; yours moved three commits
further (an intent-parsing fix, a cart-readiness gate + `/readyz` reporting,
and Saloni's React frontend). I merged `origin/manan/final-agentic-chat`
into `vatsalya/cart-subsystem` locally. All four conflicts (`cart/adapter.py`,
`api/runtime.py`, both cart test files) turned out to be strict additive
supersets on your side — nothing on my side was unique — so I resolved by
keeping your side throughout. 30 fixture-based tests still pass; all 43
DB-backed cart tests now skip cleanly (via the `DATABASE_AVAILABLE` guard
your branch already had) instead of erroring without MySQL access.

**Not pushed yet** — reviewing the diff before I do.

---

## 2. Cart subsystem — Confirmed (code today)

- Real, MySQL-backed, transactional (`src/fkgrid/cart/adapter.py`,
  `DatabaseCartAdapter`) — not a fake. Full ADD_ITEM/SET_QUANTITY/
  INCREMENT/DECREMENT/REMOVE_ITEM/CLEAR_CART/UNDO_LAST_REMOVAL, idempotency-
  key replay, optimistic concurrency.
- Off by default. Switch: `FKGRID_CART_MODE=database` (default `fixture`,
  i.e. `FakeCartPort`). Wired in `api/runtime.py`'s `ApiRuntime.create_session`.
- Your `654f56b` commit added `database_cart_readiness()` — refuses
  `cart_ready` on `/readyz` unless `catalog_version == "flipkart_v1"`
  *and* MySQL/the three cart tables/an ACTIVE offer for that version are
  all actually reachable. Good safety rail, kept as-is.
- Required tables (`carts`, `cart_items`, `cart_operation_events`,
  `db/schema_cart.sql`) — **already confirmed present on the RunPod MySQL**
  (I queried it directly: `SHOW TABLES` includes all three, `offers` has
  19,998 rows, `carts`/`cart_items` are empty as expected, nobody's used it
  yet).
- Required env vars for turning this on against the real DB:
  `FKGRID_CART_MODE=database`, `FLIPKART_DB_HOST/PORT/USER/PASSWORD/NAME`
  pointed at the real RunPod MySQL (currently default to a local Docker
  instance, `127.0.0.1:3307`).

## 3. Catalog search — Confirmed (code today)

- **Now wired in, off by default.** New switch: `FKGRID_CATALOG_MODE`
  (default `fixture`, i.e. the synthetic 600-record `DeterministicCatalog`
  via `CatalogSearchPortAdapter`; `retrieval` swaps in
  `src/fkgrid/catalog/adapter.py`'s `RetrievalCatalogAdapter`). Mirrors
  `FKGRID_CART_MODE` exactly — see `ApiRuntime.__init__`/`from_environment`
  in `api/runtime.py`.
- `RetrievalCatalogAdapter` calls RA's `/api/search` over HTTP for
  `search()`, resolves `product_id`/`offer_id`/`catalog_version` (RA's
  response has none of these — confirmed against `RA/search_catalog.py`
  directly) via one extra join against `offers`/`skus`, the same table
  `DatabaseCartAdapter` itself reads.
- **The catalog_version fix is real, not just documented**:
  `catalog_mode=retrieval` pins `ApiRuntime.catalog_version` (and every
  session's `CompatibilityTuple`) to `DATABASE_CART_CATALOG_VERSION`
  ("flipkart_v1", imported straight from `cart/adapter.py` — one source of
  truth, can't drift). Verified end-to-end on a local run: with
  `FKGRID_CART_MODE=database FKGRID_CATALOG_MODE=retrieval` and no local
  MySQL reachable, `/readyz` now reports `cart_error:
  "CART_DATABASE_UNAVAILABLE"` — a real connectivity failure, not the old
  version-mismatch failure. Only a reachable DB (local or an SSH tunnel to
  RunPod's) stands between this and a green `/readyz`.
- Also verified live through the real orchestrator (not just the adapter
  in isolation): with RA unreachable, `search_catalog`'s trace stage shows
  `status: UNAVAILABLE`, `warnings: ["RETRIEVAL_API_UNAVAILABLE:URLError"]`,
  and the turn still completes cleanly (`CLARIFY` → `NO_ELIGIBLE_MATCH`,
  no crash) — the failure mode is graceful.
- `recovery`/`references` (the reference-resolution behind "show me the
  first one" and confidence-gated recovery) deliberately stay on the
  fixture `DeterministicCatalog` in both catalog modes — both are pure
  functions of the *previous turn's own* acknowledged entries, not of the
  fixture's stored records, so they work unchanged against real search
  results too (see `tools/catalog.py`'s `resolve_reference`).
- **Not yet tested against a live RA + MySQL** (no RA process or reachable
  MySQL from this laptop) — only the graceful-failure and readiness paths
  are proven so far.
- Explicitly open, not solved by that adapter: `rerank_score` passed
  through raw/unnormalized (the CLARIFY/RECOVER confidence thresholds were
  tuned against the fixture's own heuristic, not BGE's real output range);
  `unknown_terms` always empty; category-constraint identity (taxonomy
  node vs. RA's plain `category` string) is best-effort only.

## 4. The one fact I couldn't verify myself — please confirm

`database_cart_readiness()` hardcodes `DATABASE_CART_CATALOG_VERSION =
"flipkart_v1"`. Please run this against the real DB and confirm it still
matches:
```sql
SELECT catalog_version, status FROM catalog_versions WHERE status = 'ACTIVE';
```

## 5. Deployment — Open decision, nothing built yet

This FastAPI app has never run on RunPod (or anywhere but a laptop). There's
no Dockerfile, no CI/CD — deployment here is manual, same pattern as
`api.sh`/`webui.sh`. Standing it up needs: a decision on which port (8000
and 8002 are taken), a tmux session, `pip install -e .` (or `uv sync`), and
the env vars in §2 pointed at the real DB. Not attempted yet — flagging so
it's a decision, not an assumption.

## 6. Suggested next steps

1. You confirm §4.
2. I push `vatsalya/cart-subsystem`, PR becomes a clean diff against
   `manan/final-agentic-chat` instead of a duplicate-file collision.
3. Test `FKGRID_CART_MODE=database FKGRID_CATALOG_MODE=retrieval` against
   a real reachable MySQL + running RA (SSH tunnel to RunPod, or a local
   instance) — the wiring and failure paths are proven; a live end-to-end
   ADD_ITEM-from-real-search-result run is not, yet.
4. Decide who owns actually starting this on RunPod, and on which port.
