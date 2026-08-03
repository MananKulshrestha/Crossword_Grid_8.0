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

The included `recovery_allowed_concepts` table is a materialized read projection populated by the catalog/taxonomy owner. Rows are keyed by the complete catalog/taxonomy/schema/lexicon tuple and contain only active taxonomy, attribute, value, or brand IDs that the planner may choose.

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
- Event persistence is one insert; replay/duplicate handling belongs to the turn/idempotency owner and should use `event_id` uniqueness.
- No query accepts a model-selected table, column, URL, tool name, or arbitrary SQL fragment.
