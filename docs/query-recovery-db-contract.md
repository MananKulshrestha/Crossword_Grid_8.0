# Query Recovery Database Contract

The query-recovery workflow does not own the session/catalog/lexicon database. It consumes immutable, version-compatible projections and writes a sanitized recovery event. The eventual DB owner can implement the same port with SQLAlchemy 2/PostgreSQL; the included SQLite adapter exists for contract tests and local integration only.

## Shared read tables required from other owners

`SqliteRecoveryRepository.lookup()` expects the Catalog Language owner’s `lexicon_mappings` table to expose:

```text
mapping_id, normalized_phrase, original_phrase, canonical_target_id,
canonical_label, concept_type, mapping_type, expansion_action, locale,
taxonomy_scope_id, attribute_id, lexicon_version, catalog_version,
taxonomy_version, category_schema_version, evidence_band, priority, status
```

The query requires `status='APPROVED'`, the active compatibility versions, locale `en-IN`, and either an unscoped mapping or the current taxonomy scope. It never reads raw history, memory, purchase context, or provider output.

The included `recovery_allowed_concepts` table is a materialized read projection populated by the catalog/taxonomy owner. Rows are keyed by the complete catalog/taxonomy/schema/lexicon tuple and contain only active taxonomy, attribute, value, or brand IDs that the planner may choose. A reviewed lexicon mapping may target a taxonomy parent such as `formal-wear`; the deterministic retrieval owner must expand that canonical taxonomy node to its active descendants and rank any direct parent matches before descendant matches.

## Recovery-owned writes

`recovery_events` is append-only evidence for every gate outcome, including a skipped confident query. It records:

- baseline/direct/generative run IDs and the complete compatibility tuple;
- normalized unresolved original terms (bounded; never a raw transcript/message);
- before/after hard-filter hashes and mutation count;
- mapping IDs, planner action/validation codes, comparator decisions;
- retrieval count, budget/latency, terminal outcome, and sanitized warnings.
- planner input hash, token/latency counters, and the bounded allowed-concept
  count when Tier 2 is called.

The DB owner should insert the event in the same authoritative turn/outbox transaction where available, or enqueue it through the transactional outbox. Event-write failure must not change the shopper response.

## Required query behavior

- Lookup is parameterized and bounded to three mappings for a query.
- Constraint retrieval is parameterized, version-pinned, scope-filtered, active-only, and bounded.
- Retrieval summaries must expose a trusted `popular_result_count` when the
  catalog has an approved popularity signal. Recovery may trigger on a low
  count or low popularity, but popularity is never a substitute for eligibility
  or relevance ranking.
- Event persistence is one insert; replay/duplicate handling belongs to the turn/idempotency owner and should use `event_id` uniqueness.
- No query accepts a model-selected table, column, URL, tool name, or arbitrary SQL fragment.

## Catalog retrieval seam required from the DB/catalog owner

The recovery package does not create or populate product, SKU, offer, taxonomy,
or popularity tables. The injected `RetrievalPort` must nevertheless expose a
single read path with these parameterized inputs:

```text
catalog_version, index_version, taxonomy_version, category_schema_version,
taxonomy_scope_id, hard_filter_hash, normalized_query_terms,
approved_taxonomy_target_ids, result_limit
```

That read must return exact `product_id`/`sku_id`/`offer_id` identities, the
eligibility count, ordered result IDs, score/coverage diagnostics, and the
trusted `popular_result_count`/`top_popularity_score` fields used by the gate.
For a reviewed compound mapping such as `formal wear -> formal-wear`, the
catalog query or its deterministic retrieval adapter must join the active
taxonomy-descendant projection for the pinned taxonomy version, apply hard
filters before ranking, and order a direct `formal-wear` match before its
descendants. It must not treat an absent popularity value as zero.

The concrete PostgreSQL implementation can use the catalog team's indexed
search projection and taxonomy-closure table; this branch intentionally leaves
those physical tables and index choice to that owner while freezing the port
inputs, outputs, version pinning, and ordering behavior here.
