"""Parameterized SQL query catalog for the database owner.

The workflow consumes ports; this module makes the required database operations
explicit without opening a connection or selecting a database driver.  Every
query uses named parameters and the adapter must bind values through its driver,
never string interpolation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SqlQuery:
    name: str
    sql: str
    required_parameters: frozenset[str]

    def validate_parameters(self, parameters: dict[str, object]) -> None:
        missing = self.required_parameters.difference(parameters)
        if missing:
            raise ValueError(f"missing SQL parameters for {self.name}: {sorted(missing)}")


class CatalogLanguageQueries:
    """Dialect-neutral query shapes; JSON columns are text for SQLite portability."""

    LOAD_EVIDENCE_GROUPS = SqlQuery(
        name="load_evidence_groups",
        sql="""
            SELECT group_id, normalized_term, surface_forms_json, locale,
                   taxonomy_node_id, attribute_id, support_count,
                   distinct_source_groups, source_classes_json, source_concentration,
                   recovery_success_count, contradiction_count,
                   first_observed_at, last_observed_at, privacy_safe
              FROM lexicon_evidence_aggregates
             WHERE catalog_version = :catalog_version
               AND taxonomy_version = :taxonomy_version
               AND category_schema_version = :category_schema_version
               AND last_observed_at >= :window_start
               AND last_observed_at < :window_end
               AND privacy_safe = 1
             ORDER BY locale, normalized_term, group_id
        """,
        required_parameters=frozenset(
            {
                "catalog_version",
                "taxonomy_version",
                "category_schema_version",
                "window_start",
                "window_end",
            }
        ),
    )

    LOAD_VOCABULARY = SqlQuery(
        name="load_canonical_vocabulary",
        sql="""
            SELECT target_type, target_id, canonical_name, normalized_name,
                   locale, taxonomy_node_id, parent_taxonomy_node_id, attribute_id,
                   normalizer_version, active
              FROM lexicon_vocabulary_snapshot
             WHERE catalog_version = :catalog_version
               AND taxonomy_version = :taxonomy_version
               AND category_schema_version = :category_schema_version
               AND active = 1
             ORDER BY locale, target_type, target_id
        """,
        required_parameters=frozenset(
            {"catalog_version", "taxonomy_version", "category_schema_version"}
        ),
    )

    LOAD_ACTIVE_MAPPINGS = SqlQuery(
        name="load_active_mappings",
        sql="""
            SELECT mapping_id, surface_form, normalized_form, locale, mapping_kind,
                   target_type, target_id, scope_json, direction, expansion_action,
                   compound_semantics, evidence_band, origin, evidence_ids_json,
                   status, compatibility_json, created_at, reviewed_at
              FROM lexicon_mappings
             WHERE lexicon_version = :lexicon_version
               AND status = 'APPROVED'
             ORDER BY locale, normalized_form, mapping_id
        """,
        required_parameters=frozenset({"lexicon_version"}),
    )

    INSERT_PROPOSAL = SqlQuery(
        name="insert_lexicon_proposal",
        sql="""
            INSERT INTO lexicon_proposals (
                proposal_id, run_id, candidate_version, group_id, source_form,
                normalized_form, target_type, target_id, mapping_json, critic_json,
                evidence_score_json, status, validation_codes_json, created_at
            ) VALUES (
                :proposal_id, :run_id, :candidate_version, :group_id, :source_form,
                :normalized_form, :target_type, :target_id, :mapping_json, :critic_json,
                :evidence_score_json, :status, :validation_codes_json, :created_at
            )
        """,
        required_parameters=frozenset(
            {
                "proposal_id",
                "run_id",
                "candidate_version",
                "group_id",
                "source_form",
                "normalized_form",
                "target_type",
                "target_id",
                "mapping_json",
                "critic_json",
                "evidence_score_json",
                "status",
                "validation_codes_json",
                "created_at",
            }
        ),
    )

    INSERT_REGRESSION = SqlQuery(
        name="insert_regression_run",
        sql="""
            INSERT INTO lexicon_regression_runs (
                regression_run_id, candidate_version, policy_version, report_json,
                report_checksum, passed, created_at
            ) VALUES (
                :regression_run_id, :candidate_version, :policy_version, :report_json,
                :report_checksum, :passed, :created_at
            )
        """,
        required_parameters=frozenset(
            {
                "regression_run_id",
                "candidate_version",
                "policy_version",
                "report_json",
                "report_checksum",
                "passed",
                "created_at",
            }
        ),
    )

    INSERT_SHADOW = SqlQuery(
        name="insert_shadow_run",
        sql="""
            INSERT INTO lexicon_shadow_runs (
                shadow_run_id, candidate_version, policy_version, report_json,
                report_checksum, passed, created_at
            ) VALUES (
                :shadow_run_id, :candidate_version, :policy_version, :report_json,
                :report_checksum, :passed, :created_at
            )
        """,
        required_parameters=frozenset(
            {
                "shadow_run_id",
                "candidate_version",
                "policy_version",
                "report_json",
                "report_checksum",
                "passed",
                "created_at",
            }
        ),
    )

    RECORD_REVIEW = SqlQuery(
        name="record_lexicon_review",
        sql="""
            INSERT INTO lexicon_reviews (
                review_id, candidate_version, reviewer_id, approved,
                approved_mapping_ids_json, rejected_mapping_ids_json,
                decision_reason_code, decided_at
            ) VALUES (
                :review_id, :candidate_version, :reviewer_id, :approved,
                :approved_mapping_ids_json, :rejected_mapping_ids_json,
                :decision_reason_code, :decided_at
            )
        """,
        required_parameters=frozenset(
            {
                "review_id",
                "candidate_version",
                "reviewer_id",
                "approved",
                "approved_mapping_ids_json",
                "rejected_mapping_ids_json",
                "decision_reason_code",
                "decided_at",
            }
        ),
    )

    RETIRE_ACTIVE = SqlQuery(
        name="retire_active_lexicon",
        sql="""
            UPDATE lexicon_versions
               SET status = 'RETIRED', activated_at = NULL
             WHERE lexicon_version = :expected_active_version
               AND status = 'ACTIVE'
        """,
        required_parameters=frozenset({"expected_active_version"}),
    )

    PROMOTE_CANDIDATE = SqlQuery(
        name="promote_approved_lexicon",
        sql="""
            UPDATE lexicon_versions
               SET status = 'ACTIVE', activated_at = :activated_at, actor_id = :actor_id
             WHERE lexicon_version = :candidate_version
               AND status = 'APPROVED'
        """,
        required_parameters=frozenset({"candidate_version", "activated_at", "actor_id"}),
    )

    ACTIVATE_CAS = SqlQuery(
        name="activate_lexicon_version_cas",
        sql="""
            INSERT INTO lexicon_activation_events (
                event_id, candidate_version, expected_active_version,
                activated_mapping_ids_json, actor_id, activated_at
            )
            SELECT :event_id, :candidate_version, :expected_active_version,
                   :activated_mapping_ids_json, :actor_id, :activated_at
             WHERE EXISTS (
                 SELECT 1 FROM lexicon_versions
                  WHERE lexicon_version = :expected_active_version
                    AND status = 'ACTIVE'
             )
        """,
        required_parameters=frozenset(
            {
                "event_id",
                "candidate_version",
                "expected_active_version",
                "activated_mapping_ids_json",
                "actor_id",
                "activated_at",
            }
        ),
    )

    ALL = (
        LOAD_EVIDENCE_GROUPS,
        LOAD_VOCABULARY,
        LOAD_ACTIVE_MAPPINGS,
        INSERT_PROPOSAL,
        INSERT_REGRESSION,
        INSERT_SHADOW,
        RECORD_REVIEW,
        RETIRE_ACTIVE,
        PROMOTE_CANDIDATE,
        ACTIVATE_CAS,
    )
