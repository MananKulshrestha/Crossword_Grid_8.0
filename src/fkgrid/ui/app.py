"""Simple Streamlit tester for report/review intake and Tier 1 case review."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

import streamlit as st

from fkgrid.ui.client import QualityApiClient, QualityApiError

API_DEFAULT = "http://127.0.0.1:8001"
DEMO_BINDING = {
    "product_id": "product_demo",
    "sku_id": "sku_demo",
    "listing_id": "listing_demo",
    "catalog_version": "catalog_demo_v1",
}
RISK_LEVELS = ("CRITICAL", "HIGH", "MEDIUM", "LOW")


def _new_token(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _source_checksum(source_id: str) -> str:
    return hashlib.sha256(source_id.encode("utf-8")).hexdigest()


def _timestamps() -> tuple[str, str]:
    now = datetime.now(UTC).replace(microsecond=0)
    return now.isoformat().replace("+00:00", "Z"), now.isoformat().replace("+00:00", "Z")


def build_signal_payload(
    *,
    source_type: str,
    signal_type: str,
    severity: str,
    text: str,
    reporter_group_id: str,
    source_id: str,
    rating: int | None,
    objective_issue_flag: bool | None,
    binding: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    occurred_at, received_at = _timestamps()
    return {
        "signal_id": _new_token("signal"),
        "idempotency_key": _new_token("idem"),
        "source_type": source_type,
        "signal_type": signal_type,
        "severity": severity,
        "binding": binding or DEMO_BINDING,
        "occurred_at": occurred_at,
        "received_at": received_at,
        "rating": rating,
        "objective_issue_flag": objective_issue_flag,
        "text": text,
        "asset_refs": [],
        "source_reference": {
            "source_id": source_id,
            "source_checksum": _source_checksum(source_id),
            "source_system": "streamlit-quality-sentinel",
        },
        "retention_class": "PROTOTYPE_REDACTED",
        "reporter_group_id": reporter_group_id,
        "correlation_group_id": None,
        "source_class": "REVIEW" if source_type == "POOR_REVIEW" else "REPORT",
        "verified_direct_system_signal": False,
    }


def _show_risk(risk_rating: str | None) -> None:
    if not risk_rating:
        st.info("Risk will appear once a case is qualified.")
        return
    text = f"Risk rating: {risk_rating}"
    if risk_rating == "CRITICAL":
        st.error(text)
    elif risk_rating == "HIGH":
        st.warning(text)
    elif risk_rating == "MEDIUM":
        st.info(text)
    else:
        st.success(text)


def _render_result(result: dict[str, Any]) -> None:
    outcome = result.get("outcome", "UNKNOWN")
    if outcome == "QUALIFIED_CASE":
        st.success("Quality Sentinel case opened and routed for human review.")
    elif outcome == "NOT_QUALIFIED":
        st.info("Signal accepted but the low prototype threshold has not been reached yet.")
    elif outcome == "SAFE_TRIAGE":
        st.warning("Evidence was stored safely, but the workflow needs more evidence.")
    else:
        st.warning(f"Workflow outcome: {outcome}")

    qualification = result.get("qualification") or {}
    if qualification:
        columns = st.columns(3)
        columns[0].metric("Independent reporters", qualification.get("independent_group_count", 0))
        columns[1].metric("Signals in group", qualification.get("qualified_count", 0))
        columns[2].metric("Trigger", qualification.get("trigger", "-"))

    case = result.get("case") or {}
    assessment = case.get("assessment") or {}
    if assessment:
        _show_risk(assessment.get("risk_rating"))
        st.write(
            f"Assessment: **{assessment.get('issue_class', 'UNKNOWN')}** · "
            f"confidence **{assessment.get('confidence', 0):.2f}** · "
            f"model **{assessment.get('model_alias', 'unknown')}**"
        )
        st.caption(assessment.get("bounded_summary", ""))
    if case.get("case_id"):
        st.code(case["case_id"], language="text")
        st.session_state["last_case_id"] = case["case_id"]
    warnings = result.get("warnings") or []
    if warnings:
        st.warning(" · ".join(str(item) for item in warnings))
    with st.expander("Safe workflow response"):
        st.json(result)


def _common_fields(prefix: str) -> tuple[dict[str, str | None], str, str]:
    reporter_key = f"{prefix}_reporter"
    source_key = f"{prefix}_source"
    if st.session_state.pop(f"{prefix}_rotate_identity", False):
        st.session_state[reporter_key] = _new_token("reporter")
        st.session_state[source_key] = _new_token(prefix)
    st.session_state.setdefault(reporter_key, _new_token("reporter"))
    st.session_state.setdefault(source_key, _new_token(prefix))
    with st.expander("Product identity", expanded=True):
        product_id = st.text_input(
            "Product ID", value=DEMO_BINDING["product_id"], key=f"{prefix}_product"
        )
        sku_id = st.text_input("SKU ID", value=DEMO_BINDING["sku_id"], key=f"{prefix}_sku")
        listing_id = st.text_input(
            "Listing ID", value=DEMO_BINDING["listing_id"], key=f"{prefix}_listing"
        )
        catalog_version = st.text_input(
            "Catalog version", value=DEMO_BINDING["catalog_version"], key=f"{prefix}_catalog"
        )
    reporter = st.text_input(
        "Reporter/reviewer group ID",
        key=reporter_key,
        help="Use a new value for each independent report or review.",
    )
    source_id = st.text_input("Source ID", key=source_key)
    return (
        {
            "product_id": product_id,
            "sku_id": sku_id or None,
            "listing_id": listing_id or None,
            "catalog_version": catalog_version,
        },
        reporter,
        source_id,
    )


def _report_form(client: QualityApiClient) -> None:
    st.subheader("Submit a report")
    st.caption(
        "Two independent reports with the same reason and product open the prototype pipeline."
    )
    with st.form("report_form"):
        binding, reporter, source_id = _common_fields("report")
        reason_label = st.selectbox(
            "Reason",
            [
                "Product/listing is objectively incorrect",
                "Safety concern",
                "Fulfillment or packaging mismatch",
                "Subjective quality feedback",
            ],
        )
        reason_map = {
            "Product/listing is objectively incorrect": "OBJECTIVE_INCORRECT",
            "Safety concern": "SAFETY_URGENT",
            "Fulfillment or packaging mismatch": "FULFILLMENT_MISMATCH",
            "Subjective quality feedback": "SUBJECTIVE_QUALITY",
        }
        severity = st.selectbox("Severity", ["LOW", "MEDIUM", "HIGH", "URGENT"], index=1)
        text = st.text_area(
            "What happened?",
            placeholder="Example: The listed material is different from the delivered item.",
            height=120,
        )
        submitted = st.form_submit_button("Send report", type="primary")
    if submitted:
        if not text.strip():
            st.error("Write a short report before submitting.")
            return
        payload = build_signal_payload(
            source_type="EXPLICIT_REPORT",
            signal_type=reason_map[reason_label],
            severity=severity,
            text=text,
            reporter_group_id=reporter,
            source_id=source_id,
            rating=None,
            objective_issue_flag=reason_label != "Subjective quality feedback",
            binding=binding,
        )
        try:
            _render_result(client.submit_signal(payload))
            st.session_state["report_rotate_identity"] = True
        except QualityApiError as exc:
            st.error(str(exc))


def _review_form(client: QualityApiClient) -> None:
    st.subheader("Submit a review")
    st.caption("Low-star reviews are accepted as evidence; two independent reviews can qualify.")
    with st.form("review_form"):
        binding, reporter, source_id = _common_fields("review")
        rating = st.slider("Stars", min_value=1, max_value=5, value=1)
        review_kind = st.selectbox(
            "Review type",
            [
                "Product/listing problem",
                "Fulfillment or packaging",
                "Preference or experience",
            ],
        )
        kind_map = {
            "Product/listing problem": ("OBJECTIVE_INCORRECT", True),
            "Fulfillment or packaging": ("FULFILLMENT_MISMATCH", True),
            "Preference or experience": ("SUBJECTIVE_QUALITY", False),
        }
        signal_type, default_objective = kind_map[review_kind]
        objective = st.checkbox(
            "This review describes an objective problem",
            value=default_objective,
            help="This is recorded as typed evidence, not as an instruction to the model.",
        )
        severity = st.selectbox("Severity", ["LOW", "MEDIUM", "HIGH", "URGENT"], index=1)
        text = st.text_area(
            "Review text",
            placeholder=(
                "Example: The product arrived with the wrong material and the listing is "
                "misleading."
            ),
            height=120,
        )
        submitted = st.form_submit_button("Send review", type="primary")
    if submitted:
        if not text.strip():
            st.error("Write a review before submitting.")
            return
        payload = build_signal_payload(
            source_type="POOR_REVIEW",
            signal_type=signal_type,
            severity=severity,
            text=text,
            reporter_group_id=reporter,
            source_id=source_id,
            rating=rating,
            objective_issue_flag=objective,
            binding=binding,
        )
        try:
            _render_result(client.submit_signal(payload))
            st.session_state["review_rotate_identity"] = True
        except QualityApiError as exc:
            st.error(str(exc))


def _case_review(client: QualityApiClient) -> None:
    st.subheader("Review a case")
    case_id = st.text_input("Case ID", value=st.session_state.get("last_case_id", ""))
    if st.button("Load case", type="primary") and case_id.strip():
        try:
            st.session_state["loaded_case"] = client.get_case(case_id.strip())
            st.session_state["loaded_events"] = client.get_events(case_id.strip())
        except QualityApiError as exc:
            st.error(str(exc))
    case = st.session_state.get("loaded_case")
    if not case:
        st.info("Submit two matching reports/reviews first, then load the returned case ID here.")
        return
    assessment = case.get("assessment") or {}
    columns = st.columns(3)
    columns[0].metric("Status", case.get("status", "-"))
    columns[1].metric("Issue", assessment.get("issue_class", "-"))
    columns[2].metric("Risk", assessment.get("risk_rating", "-"))
    st.write(assessment.get("bounded_summary", "No assessment summary."))
    with st.expander("Evidence and route", expanded=True):
        st.json({"evidence_ids": case.get("evidence_ids", []), "route": case.get("route")})
    with st.form("decision_form"):
        decision = st.selectbox(
            "Reviewer decision",
            [
                "NO_ACTION",
                "CORRECT_CATALOG",
                "ESCALATE_SAFETY",
                "ESCALATE_FULFILLMENT",
                "REQUEST_MORE_EVIDENCE",
            ],
        )
        reviewer_id = st.text_input("Reviewer ID", value="demo-reviewer")
        reason = st.text_area("Decision reason", value="Reviewed in the prototype tester.")
        decide = st.form_submit_button("Record decision")
    if decide:
        try:
            st.session_state["loaded_case"] = client.record_decision(
                case_id, decision=decision, reviewer_id=reviewer_id, reason=reason
            )
            st.success("Human decision recorded.")
        except QualityApiError as exc:
            st.error(str(exc))
    lifecycle_columns = st.columns(2)
    if lifecycle_columns[0].button("Close case"):
        try:
            st.session_state["loaded_case"] = client.lifecycle(
                case_id,
                action="CLOSE",
                actor_id="demo-reviewer",
                reason="Closed from prototype UI.",
            )
            st.success("Case closed.")
        except QualityApiError as exc:
            st.error(str(exc))
    if lifecycle_columns[1].button("Reopen case"):
        try:
            st.session_state["loaded_case"] = client.lifecycle(
                case_id,
                action="REOPEN",
                actor_id="demo-reviewer",
                reason="Reopened for more evidence.",
            )
            st.success("Case reopened.")
        except QualityApiError as exc:
            st.error(str(exc))
    with st.expander("Append-only case events"):
        st.json(st.session_state.get("loaded_events", []))


def main() -> None:
    st.set_page_config(page_title="Quality Sentinel Tester", page_icon="🛡️", layout="wide")
    st.title("🛡️ Catalog Quality Sentinel")
    st.caption("A small operator tester for reports, reviews, evidence, risk, and human review.")

    st.session_state.setdefault("api_base_url", API_DEFAULT)
    with st.sidebar:
        st.header("Connection")
        base_url = st.text_input("Quality API base URL", key="api_base_url")
        client = QualityApiClient(base_url)
        try:
            demo_state = client.demo_state()
            st.success("API connected")
            st.metric("Classifier", demo_state.get("classifier", "unknown"))
            st.metric("Open cases in demo", demo_state.get("case_count", 0))
        except QualityApiError as exc:
            st.error(str(exc))
            demo_state = {}
        st.divider()
        st.subheader("Risk ground truth")
        st.markdown(
            "**CRITICAL** — immediate safety danger  \n"
            "**HIGH** — repeated objective defect or listing mismatch  \n"
            "**MEDIUM** — fulfillment, packaging, or service harm  \n"
            "**LOW** — preference, minor, or insufficient evidence"
        )
        st.caption(
            "The final risk rating is normalized deterministically from structured evidence."
        )

    report_tab, review_tab, case_tab = st.tabs(["Report", "Review", "Case review"])
    with report_tab:
        _report_form(client)
    with review_tab:
        _review_form(client)
    with case_tab:
        _case_review(client)


if __name__ == "__main__":
    main()
