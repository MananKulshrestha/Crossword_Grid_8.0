-- Query Recovery Agent Tier 2, recovery-owned migration fragment.
--
-- This migration deliberately does not create sessions, catalog, taxonomy,
-- category-schema, lexicon, or turn tables. Those are shared application
-- contracts owned by the database/catalog-language teams. The adapter expects
-- `lexicon_mappings` with the columns documented in docs/query-recovery-db-contract.md.

CREATE TABLE IF NOT EXISTS recovery_allowed_concepts (
    catalog_version TEXT NOT NULL,
    taxonomy_version TEXT NOT NULL,
    category_schema_version TEXT NOT NULL,
    lexicon_version TEXT NOT NULL,
    concept_id TEXT NOT NULL,
    concept_type TEXT NOT NULL CHECK (concept_type IN ('TAXONOMY', 'ATTRIBUTE', 'VALUE', 'BRAND')),
    label TEXT NOT NULL CHECK (length(label) BETWEEN 1 AND 256),
    canonical_term TEXT NOT NULL CHECK (length(canonical_term) BETWEEN 1 AND 256),
    taxonomy_scope_id TEXT,
    attribute_id TEXT,
    value_id TEXT,
    locale TEXT NOT NULL DEFAULT 'en-IN',
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    PRIMARY KEY (
        catalog_version, taxonomy_version, category_schema_version,
        lexicon_version, concept_id
    )
);

CREATE INDEX IF NOT EXISTS ix_recovery_allowed_concepts_scope
    ON recovery_allowed_concepts (
        catalog_version, taxonomy_version, category_schema_version,
        lexicon_version, locale, taxonomy_scope_id, active
    );

CREATE TABLE IF NOT EXISTS recovery_events (
    event_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    terminal_state TEXT NOT NULL,
    trigger_reasons_json TEXT NOT NULL,
    original_terms_json TEXT NOT NULL,
    hard_filter_hash_before TEXT NOT NULL CHECK (length(hard_filter_hash_before) = 64),
    hard_filter_hash_after TEXT NOT NULL CHECK (length(hard_filter_hash_after) = 64),
    baseline_run_id TEXT NOT NULL,
    direct_run_id TEXT,
    generative_run_id TEXT,
    mapping_ids_json TEXT NOT NULL,
    planner_action TEXT,
    planner_called INTEGER NOT NULL CHECK (planner_called IN (0, 1)),
    planner_validation_codes_json TEXT NOT NULL,
    comparator_decisions_json TEXT NOT NULL,
    tool_calls_json TEXT NOT NULL,
    retrieval_run_count INTEGER NOT NULL CHECK (retrieval_run_count BETWEEN 1 AND 3),
    hard_filter_mutation_count INTEGER NOT NULL CHECK (hard_filter_mutation_count >= 0),
    added_latency_ms INTEGER NOT NULL CHECK (added_latency_ms >= 0),
    budget_ms INTEGER NOT NULL CHECK (budget_ms > 0),
    budget_used_ms INTEGER NOT NULL CHECK (budget_used_ms >= 0),
    model_prompt_version TEXT,
    model_alias TEXT,
    compatibility_json TEXT NOT NULL,
    cache_hit INTEGER NOT NULL DEFAULT 0 CHECK (cache_hit IN (0, 1)),
    warnings_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_recovery_events_session_created
    ON recovery_events (session_id, created_at);

CREATE INDEX IF NOT EXISTS ix_recovery_events_outcome_created
    ON recovery_events (outcome, created_at);
