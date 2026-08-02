from __future__ import annotations

from fkgrid.ui.app import build_signal_payload


def test_report_payload_is_compatible_with_typed_signal_contract() -> None:
    payload = build_signal_payload(
        source_type="EXPLICIT_REPORT",
        signal_type="OBJECTIVE_INCORRECT",
        severity="MEDIUM",
        text="The listed material differs from the delivered item.",
        reporter_group_id="reporter_1",
        source_id="report_1",
        rating=None,
        objective_issue_flag=True,
    )

    assert payload["source_type"] == "EXPLICIT_REPORT"
    assert payload["source_class"] == "REPORT"
    assert payload["rating"] is None
    assert len(payload["source_reference"]["source_checksum"]) == 64


def test_review_payload_carries_stars_and_review_provenance() -> None:
    payload = build_signal_payload(
        source_type="POOR_REVIEW",
        signal_type="SUBJECTIVE_QUALITY",
        severity="LOW",
        text="The colour is not my preference.",
        reporter_group_id="reviewer_1",
        source_id="review_1",
        rating=1,
        objective_issue_flag=False,
    )

    assert payload["source_type"] == "POOR_REVIEW"
    assert payload["source_class"] == "REVIEW"
    assert payload["rating"] == 1
    assert payload["objective_issue_flag"] is False
