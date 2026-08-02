-- Tier 1 Quality Sentinel schema handoff.
-- This file is intentionally not executed by the application.  The database
-- owner should translate it into the repository's Alembic migration and add
-- dialect-specific JSON/index details after the shared schema is available.

CREATE TABLE quality_signals (
    signal_id VARCHAR(128) PRIMARY KEY,
    idempotency_key VARCHAR(128) NOT NULL UNIQUE,
    request_hash CHAR(64) NOT NULL,
    source_type VARCHAR(32) NOT NULL,
    signal_type VARCHAR(32) NOT NULL,
    severity VARCHAR(16) NOT NULL,
    product_id VARCHAR(128) NOT NULL,
    sku_id VARCHAR(128),
    listing_id VARCHAR(128),
    catalog_version VARCHAR(128) NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    rating SMALLINT,
    objective_issue_flag BOOLEAN,
    redacted_text VARCHAR(2000) NOT NULL DEFAULT '',
    redaction_report_json TEXT NOT NULL,
    asset_refs_json TEXT NOT NULL,
    source_id VARCHAR(256) NOT NULL,
    source_checksum CHAR(64) NOT NULL,
    source_system VARCHAR(128) NOT NULL,
    retention_class VARCHAR(64) NOT NULL,
    reporter_group_id VARCHAR(128),
    correlation_group_id VARCHAR(128),
    source_class VARCHAR(64) NOT NULL,
    verified_direct_system_signal BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(32) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX quality_signals_group_idx
    ON quality_signals (product_id, sku_id, listing_id, signal_type, occurred_at);
CREATE INDEX quality_signals_source_idx
    ON quality_signals (source_class, reporter_group_id, occurred_at);

CREATE TABLE quality_signal_groups (
    group_key VARCHAR(1024) PRIMARY KEY,
    scope_kind VARCHAR(16) NOT NULL,
    scope_id VARCHAR(128) NOT NULL,
    product_id VARCHAR(128) NOT NULL,
    sku_id VARCHAR(128),
    listing_id VARCHAR(128),
    signal_type VARCHAR(32) NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    window_end TIMESTAMPTZ NOT NULL,
    policy_version VARCHAR(128) NOT NULL,
    qualification_json TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE quality_cases (
    case_id VARCHAR(128) PRIMARY KEY,
    group_key VARCHAR(1024) NOT NULL UNIQUE REFERENCES quality_signal_groups(group_key),
    product_id VARCHAR(128) NOT NULL,
    catalog_version VARCHAR(128) NOT NULL,
    policy_version VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL,
    trigger VARCHAR(128) NOT NULL,
    signal_ids_json TEXT NOT NULL,
    evidence_ids_json TEXT NOT NULL,
    assessment_json TEXT,
    route_json TEXT,
    opened_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    reopened_from_case_id VARCHAR(128)
);

CREATE TABLE quality_case_evidence (
    case_id VARCHAR(128) NOT NULL REFERENCES quality_cases(case_id),
    evidence_id VARCHAR(128) NOT NULL,
    evidence_kind VARCHAR(32) NOT NULL,
    signal_id VARCHAR(128),
    product_id VARCHAR(128) NOT NULL,
    sku_id VARCHAR(128),
    listing_id VARCHAR(128),
    catalog_version VARCHAR(128) NOT NULL,
    source_type VARCHAR(32) NOT NULL,
    source_class VARCHAR(64) NOT NULL,
    severity VARCHAR(16) NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    normalized_issue_type VARCHAR(32) NOT NULL,
    redacted_excerpt VARCHAR(500) NOT NULL DEFAULT '',
    catalog_fact_ids_json TEXT NOT NULL,
    evidence_hash CHAR(64) NOT NULL,
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    source_count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (case_id, evidence_id),
    UNIQUE (case_id, product_id, normalized_issue_type, evidence_hash, source_class)
);

CREATE TABLE quality_assessments (
    case_id VARCHAR(128) PRIMARY KEY REFERENCES quality_cases(case_id),
    issue_class VARCHAR(40) NOT NULL,
    confidence NUMERIC(5,4) NOT NULL,
    supporting_evidence_ids_json TEXT NOT NULL,
    contradicting_evidence_ids_json TEXT NOT NULL,
    missing_information_json TEXT NOT NULL,
    bounded_summary VARCHAR(500) NOT NULL,
    model_alias VARCHAR(128) NOT NULL,
    prompt_version VARCHAR(128) NOT NULL,
    validation_status VARCHAR(40) NOT NULL,
    validation_reasons_json TEXT NOT NULL
);

CREATE TABLE quality_routes (
    case_id VARCHAR(128) PRIMARY KEY REFERENCES quality_cases(case_id),
    issue_class VARCHAR(40) NOT NULL,
    queue_name VARCHAR(128) NOT NULL,
    priority VARCHAR(16) NOT NULL,
    sla_minutes INTEGER NOT NULL,
    policy_version VARCHAR(128) NOT NULL,
    immutable_case_reference VARCHAR(256) NOT NULL,
    queued_at TIMESTAMPTZ NOT NULL,
    delivery_key VARCHAR(256) NOT NULL UNIQUE
);

CREATE TABLE quality_case_decisions (
    decision_id VARCHAR(128) PRIMARY KEY,
    case_id VARCHAR(128) NOT NULL REFERENCES quality_cases(case_id),
    decision VARCHAR(40) NOT NULL,
    reviewer_role VARCHAR(32) NOT NULL CHECK (reviewer_role = 'QUALITY_REVIEWER'),
    reviewer_id VARCHAR(128) NOT NULL,
    reason VARCHAR(1000) NOT NULL,
    follow_up_recommendation VARCHAR(500),
    decided_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE quality_case_events (
    event_id VARCHAR(128) PRIMARY KEY,
    case_id VARCHAR(128) NOT NULL REFERENCES quality_cases(case_id),
    from_status VARCHAR(32),
    to_status VARCHAR(32) NOT NULL,
    actor_type VARCHAR(32) NOT NULL,
    actor_id VARCHAR(128) NOT NULL,
    reason VARCHAR(500) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    previous_event_hash CHAR(64),
    event_hash CHAR(64) NOT NULL UNIQUE
);

CREATE INDEX quality_cases_queue_idx ON quality_cases (status, updated_at);
CREATE INDEX quality_case_events_case_idx ON quality_case_events (case_id, created_at);
