# Wiring `RetrievalCatalogAdapter` in — for Manan

`RetrievalCatalogAdapter` (`adapter.py`, this package) is a complete,
tested `CatalogSearchPort` implementation backed by the real Retrieval API
+ live MySQL. It is **not wired in anywhere yet** — nothing outside this
package has been touched. This is what's left, all of it in
`src/fkgrid/api/runtime.py`.

## 1. Add a `catalog_mode` switch (mirrors the existing `cart_mode` switch)

`ApiRuntime.create_session` currently builds `catalog=` unconditionally:

```python
catalog=self.tooling.shopper_catalog,
```

Needs an `if/else` alongside the existing `cart_mode` branch:

```python
if self.catalog_mode == "retrieval":
    catalog = RetrievalCatalogAdapter()
else:
    catalog = self.tooling.shopper_catalog
```

`self.catalog_mode` itself doesn't exist yet either — add it the same way
`self.cart_mode` is threaded through `__init__`/`from_environment`
(new env var, e.g. `FKGRID_CATALOG_MODE`, default `"fixture"`).

## 2. Fix `demo_compatibility()`'s hardcoded catalog_version

```python
catalog_version="catalog-fixture-v1",   # api/runtime.py:78
```

must become `"flipkart_v1"` for sessions running in `catalog_mode="retrieval"`.
`RetrievalCatalogAdapter.search()` checks this on every call and returns
`STALE` if it doesn't match — verified directly (see Test 7 in the
scratchpad smoke test).

## 3. Fix `self.catalog_version` used by the startup readiness check

Separate from #2 — easy to miss. In `ApiRuntime.__init__`:

```python
self.catalog_version = (
    self.catalog_entries[0].binding.catalog_version   # the FIXTURE list
    if self.catalog_entries else "catalog-fixture-v1"
)
self.cart_error = database_cart_readiness(self.catalog_version) if self.cart_mode == "database" else None
```

This must also resolve to `"flipkart_v1"` in retrieval+database mode, or
`cart_ready` stays `False` (`CART_CATALOG_VERSION_MISMATCH`) at startup
even after #1 and #2 are done.

## Env vars to set alongside the existing ones

```
FKGRID_CART_MODE=database          # already exists
FKGRID_CATALOG_MODE=retrieval      # new, from step 1
FKGRID_RETRIEVAL_API_URL=<RA base URL, e.g. http://127.0.0.1:8002 on the pod>
FLIPKART_DB_HOST / PORT / USER / PASSWORD / NAME   # already exist, reused as-is
```

## Confirmed safe, no action needed

- `references=` / `recovery=` / `suggestions=` stay wired to `self.tooling`
  (fixture-backed) — checked directly, not assumed. `references` rebuilds
  its state from the session snapshot every call, never the fixture
  catalog's own memory. `recovery.recover()` only re-queries the fixture
  catalog when `result.unknown_terms` is non-empty, which is structurally
  always empty for this adapter's output (shape mismatch means it's
  always a same-result pass-through). `suggestions` derives its
  candidates from the response's own `search_entries`, not an independent
  catalog lookup.

## One known, separate, non-blocking gap

The homepage carousel and `/category/:key` pages call `GET /v1/catalog`
directly (`frontend/src/api/shopper.ts`), bypassing the orchestrator/
`CatalogSearchPort` entirely — that stays on fixture data regardless of
the above. Already self-labeled fixture-only on both ends (backend route
tag `"testing catalog"`, frontend copy "Browse demo catalog"), so not a
regression from this work, just a pre-existing separate surface.

## Verified before handoff

Full loop tested against the real deployed Retrieval API and real live
DB: `search()` → real `ProductBinding` → `check_eligibility()` →
**unmodified** `DatabaseCartAdapter.update_cart(ADD_ITEM)` → real
persisted cart row, confirmed via `show_cart()`, then cleaned up. Zero
changes were needed in `cart/adapter.py` for that to work.
