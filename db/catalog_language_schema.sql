-- Catalog Language-owned schema only.
-- The database owner runs this through the project's migration system.
-- No statement here creates or migrates the core catalog/session tables.

CREATE TABLE IF NOT EXISTS lexicon_vocabulary_snapshot (
    catalog_version TEXT NOT NULL,
    taxonomy_version TEXT NOT NULL,
    category_schema_version TEXT NOT NULL,
    normalizer_version TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    locale TEXT NOT NULL,
    taxonomy_node_id TEXT,
    parent_taxonomy_node_id TEXT,
    attribute_id TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    PRIMARY KEY (
        catalog_version,
        taxonomy_version,
        category_schema_version,
        target_type,
        target_id
    )
);

CREATE INDEX IF NOT EXISTS idx_lexicon_vocab_lookup
    ON lexicon_vocabulary_snapshot (
        catalog_version,
        taxonomy_version,
        category_schema_version,
        locale,
        normalized_name
    );

CREATE TABLE IF NOT EXISTS lexicon_versions (
    lexicon_version TEXT PRIMARY KEY,
    parent_lexicon_version TEXT,
    catalog_version TEXT NOT NULL,
    taxonomy_version TEXT NOT NULL,
    category_schema_version TEXT NOT NULL,
    normalizer_version TEXT NOT NULL,
    mapping_schema_version TEXT NOT NULL,
    rank_policy_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('CANDIDATE', 'APPROVED', 'ACTIVE', 'RETIRED', 'FAILED')),
    artifact_checksum TEXT NOT NULL CHECK (length(artifact_checksum) = 64),
    created_at TEXT NOT NULL,
    approved_at TEXT,
    activated_at TEXT,
    actor_id TEXT,
    reviewer_id TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_lexicon_one_active
    ON lexicon_versions (status) WHERE status = 'ACTIVE';

CREATE TABLE IF NOT EXISTS lexicon_mappings (
    mapping_id TEXT PRIMARY KEY,
    lexicon_version TEXT NOT NULL REFERENCES lexicon_versions(lexicon_version) ON DELETE RESTRICT,
    surface_form TEXT NOT NULL,
    normalized_form TEXT NOT NULL,
    locale TEXT NOT NULL,
    mapping_kind TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    direction TEXT NOT NULL,
    expansion_action TEXT NOT NULL,
    compound_semantics TEXT NOT NULL CHECK (compound_semantics IN ('NONE', 'AND', 'OR')),
    evidence_band TEXT NOT NULL,
    origin TEXT NOT NULL CHECK (origin IN ('TIER_1_CURATED', 'TIER_2_EVIDENCE')),
    evidence_ids_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('DRAFT', 'IN_REVIEW', 'APPROVED', 'REJECTED', 'RETIRED')),
    compatibility_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    UNIQUE (
        lexicon_version,
        normalized_form,
        locale,
        target_type,
        target_id,
        scope_json
    )
);

CREATE INDEX IF NOT EXISTS idx_lexicon_mapping_lookup
    ON lexicon_mappings (lexicon_version, locale, normalized_form, status);

CREATE TABLE IF NOT EXISTS lexicon_evidence_aggregates (
    aggregate_id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    catalog_version TEXT NOT NULL,
    taxonomy_version TEXT NOT NULL,
    category_schema_version TEXT NOT NULL,
    locale TEXT NOT NULL,
    normalized_term TEXT NOT NULL,
    surface_forms_json TEXT NOT NULL,
    taxonomy_node_id TEXT,
    attribute_id TEXT,
    support_count INTEGER NOT NULL CHECK (support_count > 0),
    distinct_source_groups INTEGER NOT NULL CHECK (distinct_source_groups > 0),
    source_classes_json TEXT NOT NULL,
    source_concentration REAL NOT NULL CHECK (source_concentration >= 0 AND source_concentration <= 1),
    recovery_success_count INTEGER NOT NULL CHECK (recovery_success_count >= 0),
    contradiction_count INTEGER NOT NULL CHECK (contradiction_count >= 0),
    first_observed_at TEXT NOT NULL,
    last_observed_at TEXT NOT NULL,
    privacy_safe INTEGER NOT NULL CHECK (privacy_safe = 1),
    UNIQUE (group_id, catalog_version, taxonomy_version, category_schema_version)
);

CREATE INDEX IF NOT EXISTS idx_lexicon_evidence_window
    ON lexicon_evidence_aggregates (
        catalog_version,
        taxonomy_version,
        category_schema_version,
        last_observed_at,
        locale,
        normalized_term
    );

CREATE TABLE IF NOT EXISTS lexicon_proposals (
    proposal_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    candidate_version TEXT,
    group_id TEXT NOT NULL,
    source_form TEXT NOT NULL,
    normalized_form TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    mapping_json TEXT,
    critic_json TEXT,
    evidence_score_json TEXT,
    status TEXT NOT NULL CHECK (status IN ('ABSTAINED', 'REJECTED', 'VALID', 'REVIEW_PENDING', 'ACTIVATED')),
    validation_codes_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_lexicon_proposal_run
    ON lexicon_proposals (run_id, status, created_at);

CREATE TABLE IF NOT EXISTS lexicon_regression_runs (
    regression_run_id TEXT PRIMARY KEY,
    candidate_version TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    report_json TEXT NOT NULL,
    report_checksum TEXT NOT NULL CHECK (length(report_checksum) = 64),
    passed INTEGER NOT NULL CHECK (passed IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lexicon_shadow_runs (
    shadow_run_id TEXT PRIMARY KEY,
    candidate_version TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    report_json TEXT NOT NULL,
    report_checksum TEXT NOT NULL CHECK (length(report_checksum) = 64),
    passed INTEGER NOT NULL CHECK (passed IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lexicon_reviews (
    review_id TEXT PRIMARY KEY,
    candidate_version TEXT NOT NULL,
    reviewer_id TEXT NOT NULL,
    approved INTEGER NOT NULL CHECK (approved IN (0, 1)),
    approved_mapping_ids_json TEXT NOT NULL,
    rejected_mapping_ids_json TEXT NOT NULL,
    decision_reason_code TEXT NOT NULL,
    decided_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lexicon_activation_events (
    event_id TEXT PRIMARY KEY,
    candidate_version TEXT NOT NULL,
    expected_active_version TEXT NOT NULL,
    activated_mapping_ids_json TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    activated_at TEXT NOT NULL,
    UNIQUE (candidate_version, expected_active_version)
);

