from __future__ import annotations

import json

import pytest

from fkgrid.adapters.catalog_language.artifacts import FilesystemLexiconArtifactStore
from fkgrid.adapters.catalog_language.evaluation import (
    DeterministicRegressionRunner,
    DeterministicShadowEvaluator,
)
from fkgrid.domain.catalog_language import RegressionCase, ShadowCase
from tests.test_catalog_language_workflow import request, workflow


def test_candidate_artifact_is_checksum_verified_and_active_pointer_filters_approved_ids(
    tmp_path,
) -> None:
    app, _, _ = workflow()
    result = app.run(request())
    assert result.candidate is not None
    assert result.regression is not None and result.shadow is not None

    store = FilesystemLexiconArtifactStore(tmp_path)
    manifest = store.write_candidate(result.candidate, result.regression, result.shadow)
    store.pointer_path.write_text(
        json.dumps({"candidate_version": "lex-1", "mapping_ids": []}),
        encoding="utf-8",
    )
    receipt = store.activate_lexicon_version(
        request=__import__(
            "fkgrid.domain.catalog_language", fromlist=["ActivationRequest"]
        ).ActivationRequest(
            candidate_version=result.candidate.candidate_version,
            expected_active_version="lex-1",
            approved_mapping_ids=result.candidate.proposed_mapping_ids,
            actor_id="reviewer-test",
        )
    )

    assert manifest.mapping_count == len(result.candidate.mappings)
    assert receipt.activated is True
    active = store.load_active_mappings(
        result.candidate.candidate_version,
        result.candidate.compatibility,
    )
    assert {mapping.mapping_id for mapping in active} == set(result.candidate.proposed_mapping_ids)
    assert all(mapping.status.value == "APPROVED" for mapping in active)

    mapping_file = tmp_path / result.candidate.candidate_version / "mappings.jsonl"
    mapping_file.write_text(mapping_file.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        store.load_active_mappings(
            result.candidate.candidate_version, result.candidate.compatibility
        )


def test_deterministic_regression_and_shadow_harness_measure_recovery_and_protection() -> None:
    app, _, _ = workflow()
    result = app.run(request())
    assert result.candidate is not None
    assert result.regression is not None

    regression = DeterministicRegressionRunner(
        minimum_precision=0.5, minimum_recovery=0.5
    ).run_lexicon_regression(
        result.candidate,
        [],
        [
            RegressionCase(
                case_id="recovery-1",
                query="trainer",
                taxonomy_node_id="footwear",
                expected_target_ids=["athletic-shoes"],
                expects_expansion=True,
            )
        ],
        "regression-v1",
    )
    shadow = DeterministicShadowEvaluator().shadow_evaluate_lexicon(
        result.candidate,
        [],
        [
            ShadowCase(
                case_id="shadow-1",
                query="trainer",
                taxonomy_node_id="footwear",
                baseline_target_ids=[],
                expected_target_ids=["athletic-shoes"],
            )
        ],
        "shadow-v1",
    )

    assert regression.passed is True
    assert regression.expected_recovery_rate == 1.0
    assert shadow.passed is True
    assert shadow.cases_improved == 1
