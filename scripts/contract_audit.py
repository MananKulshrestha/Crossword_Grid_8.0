"""Dependency-free one-by-one audit for the non-cart tool inventory."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fkgrid.tools import (  # noqa: E402
    TOOL_SPECS,
    CatalogLanguagePortAdapter,
    CatalogOperationsPortAdapter,
    CatalogVersionStore,
    DeterministicCatalog,
    QualityCaseStore,
    QualityPortAdapter,
    SuggestionStore,
    build_default_registry,
)
from fkgrid.tools.catalog_language import LexiconStore  # noqa: E402
from fkgrid.tools.catalog_operations import (  # noqa: E402
    compare_with_current,
)
from fkgrid.tools.contracts import (  # noqa: E402
    CandidateTerm,
    CompatibilityTuple,
    EvidenceRef,
    MappingProposal,
    QueryState,
    ResearchAnswer,
    ResearchSource,
    ReviewDecision,
    SearchRequest,
)
from fkgrid.tools.determinism import hard_filter_hash  # noqa: E402
from fkgrid.tools.registry import CART_TOOL_NAMES  # noqa: E402
from fkgrid.tools.runtime import resolve_intent_and_delta  # noqa: E402


def main() -> int:
    assert len(TOOL_SPECS) == 73, len(TOOL_SPECS)
    assert not CART_TOOL_NAMES.intersection(spec.name for spec in TOOL_SPECS)

    compatibility = CompatibilityTuple()
    binding = {
        "product_id": "p-1",
        "sku_id": "sku-1",
        "offer_id": "offer-1",
        "catalog_version": compatibility.catalog_version,
    }
    raw = {
        **binding,
        "title": "Acme Phone",
        "category_id": "phones",
        "brand": "Acme",
        "description": "A phone with a camera",
        "attributes": {"ram": "8 GB", "storage": "128 GB"},
        "price": 19999,
        "availability": "AVAILABLE",
    }
    from fkgrid.tools.catalog_operations import normalize_product

    normalized = normalize_product(raw, compatibility=compatibility)
    record = normalized.record
    record_two = record.model_copy(
        update={
            "binding": record.binding.model_copy(update={"offer_id": "offer-2"}),
            "title": "Acme Phone Pro",
        }
    )
    catalog = DeterministicCatalog([record, record_two], compatibility)
    state = QueryState(text="phone", normalized_terms=["phone"])
    request = SearchRequest(query_state=state, compatibility=compatibility)
    search = catalog.search(request)
    assert search.entries
    store = LexiconStore()
    term = CandidateTerm(surface_form="fone", normalized_form="fone", count=1)
    proposal = MappingProposal(
        surface_form="fone",
        normalized_form="fone",
        target_id="phone",
        canonical_term="phone",
        status="PROPOSED",
        evidence_refs=[
            EvidenceRef(evidence_id="e-1", source="fixture", field="term", version="v1")
        ],
    )
    from fkgrid.tools.catalog_language import build_lexicon_candidate

    candidate = build_lexicon_candidate([proposal], compatibility.catalog_version)
    store.write_candidate(candidate)
    review = ReviewDecision(
        subject_id=candidate.version,
        decision="APPROVED",
        reviewer_id="human-1",
        reason="fixture approval",
        recorded_at=datetime(1970, 1, 1, tzinfo=UTC),
    )
    store.record_review(review)
    active = store.activate_lexicon_version(candidate.version)
    source = ResearchSource(
        source_id="source-1",
        url="https://example.com/guide",
        domain="example.com",
        title="Guide",
        extract="A cited fixture extract.",
        citation_key="source-1",
    )
    from fkgrid.tools.quality import (
        assemble_case_evidence,
        classify_quality_issue,
        get_catalog_snapshot,
        ingest_quality_signal,
    )

    signal = ingest_quality_signal("REPORT", "p-1", "wrong item", "fixture")
    assert (
        __import__("fkgrid.tools.quality", fromlist=["qualify_signal_group"])
        .qualify_signal_group([signal])
        .qualified
    )
    snapshot = get_catalog_snapshot([record], compatibility.catalog_version)
    packet = assemble_case_evidence("case-1", [signal], snapshot, compatibility)
    assessment = classify_quality_issue(packet)
    recovery_store = __import__(
        "fkgrid.tools.recovery", fromlist=["RecoveryEventStore"]
    ).RecoveryEventStore()
    suggestion_store = SuggestionStore(b"audit-secret")
    suggestion_set = suggestion_store.build_and_store(
        "session-1",
        "turn-1",
        "SEARCH",
        [record.binding, record_two.binding],
        compatibility,
        0,
        state_version=1,
        cart_version=0,
        active_result_set_id=search.result_set_id,
    )
    assert suggestion_set is not None
    first_suggestion = suggestion_set.suggestions[0]
    retrieval_run = __import__("fkgrid.tools.contracts", fromlist=["RetrievalRun"]).RetrievalRun(
        run_id="run-1", kind="BASELINE", result=search, query_state=state
    )
    registry = build_default_registry(catalog)
    handlers = registry._handlers  # The audit intentionally checks the static binding table.
    assert set(handlers) >= {spec.name for spec in TOOL_SPECS}

    calls = {
        "enhance_chat_query": {"current_text": "phone", "query_state": state},
        "build_query_enhancement_context": {
            "session_id": "session-1",
            "client_turn_id": "turn-1",
            "state_version": 1,
            "current_message": "phone",
            "query_state": state,
        },
        "clean_request_summary": {"current_message": "phone please"},
        "resolve_intent_and_delta": {"text": "show phones", "query_state": state},
        "detect_follow_up_need": {
            "intent": resolve_intent_and_delta("show phones", state),
            "query_state": state,
        },
        "generate_clarifying_question": {"query_state": state, "reason": "missing preference"},
        "build_clarification": {
            "reason_code": "MISSING_FIELD",
            "target_field": "brand",
            "options": ["Acme"],
        },
        "validate_clarifying_question": {
            "question": __import__(
                "fkgrid.tools.runtime", fromlist=["generate_clarifying_question"]
            ).generate_clarifying_question(state)
        },
        "check_commerce_eligibility": {
            "binding": record.binding,
            "purpose": "read",
            "compatibility": compatibility,
        },
        "search_catalog": {"request": request},
        "assess_retrieval_confidence": {"result": search, "query_state": state},
        "lookup_approved_expansions": {
            "terms": ["fone"],
            "store": store,
            "compatibility": compatibility,
        },
        "get_recovery_constraints": {
            "query_state": state,
            "unknown_terms": ["fone"],
            "expansions": [],
            "compatibility": compatibility,
        },
        "apply_recovery_plan": {
            "query_state": state,
            "plan": __import__("fkgrid.tools.contracts", fromlist=["RecoveryPlan"]).RecoveryPlan(
                plan_id="plan-1",
                action="REWRITE",
                add_terms=["phone"],
                hard_filter_hash=hard_filter_hash(state),
                compatibility=compatibility,
            ),
        },
        "validate_recovery_plan": {
            "plan": __import__("fkgrid.tools.contracts", fromlist=["RecoveryPlan"]).RecoveryPlan(
                plan_id="plan-1",
                action="REWRITE",
                add_terms=["phone"],
                hard_filter_hash=hard_filter_hash(state),
                compatibility=compatibility,
            ),
            "query_state": state,
            "compatibility": compatibility,
        },
        "compare_retrieval_runs": {"baseline": retrieval_run, "candidate": retrieval_run},
        "plan_constrained_repair": {
            "context": {
                "unknown_terms": ["fone"],
                "allowed_concepts": ["phone"],
                "hard_filter_hash": hard_filter_hash(state),
            }
        },
        "record_recovery_event": {
            "store": recovery_store,
            "outcome": "NO_MATCH",
            "reason_codes": ["UNKNOWN_TERM"],
            "hard_filter_hash_value": hard_filter_hash(state),
            "compatibility": compatibility,
        },
        "resolve_query_state": {"message": "phones", "session_state": state},
        "resolve_reference": {
            "references": ["1"],
            "active_result": search,
            "compatibility": compatibility,
        },
        "get_product_details": {"binding": record.binding, "compatibility": compatibility},
        "compare_products": {
            "bindings": [record.binding, record_two.binding],
            "compatibility": compatibility,
        },
        "check_availability": {"binding": record.binding, "compatibility": compatibility},
        "detect_research_need": {
            "current_message": "research camera safety",
            "explicit_consent": True,
        },
        "online_search": {
            "decision": __import__(
                "fkgrid.tools.research", fromlist=["detect_research_need"]
            ).detect_research_need("research camera safety", True),
            "fixtures": [source],
            "enabled": True,
        },
        "fetch_research_source": {"source": source},
        "synthesize_research_answer": {"query": "camera safety", "sources": [source]},
        "validate_research_claims": {
            "answer": ResearchAnswer(status="OK", answer="A cited answer", citations=["source-1"]),
            "sources": [source],
        },
        "build_follow_up_candidates": {
            "primary_action": "SEARCH",
            "bindings": [record.binding, record_two.binding],
        },
        "generate_follow_up_suggestions": {
            "candidates": [
                {
                    "candidate_id": "compare",
                    "action_type": "COMPARE_RESULTS",
                    "safe_default_label": "Compare results",
                }
            ]
        },
        "validate_suggestion_set": {
            "suggestion_set": suggestion_set,
            "session_id": "session-1",
            "now_ms": 1,
            "secret": b"audit-secret",
        },
        "select_suggestion": {
            "store": suggestion_store,
            "session_id": "session-1",
            "suggestion_set_id": suggestion_set.suggestion_set_id,
            "suggestion_id": first_suggestion.suggestion_id,
            "now_ms": 1,
        },
        "load_canonical_vocabulary": {
            "records": [record],
            "catalog_version": compatibility.catalog_version,
        },
        "normalize_surface_form": {"text": "fone"},
        "mine_candidate_terms": {"inputs": ["fone"]},
        "aggregate_query_gap_events": {"events": [{"surface_form": "fone", "count": 1}]},
        "cluster_surface_forms": {"terms": [term]},
        "retrieve_candidate_targets": {
            "term": CandidateTerm(surface_form="phone", normalized_form="phone", count=1),
            "vocabulary": __import__(
                "fkgrid.tools.catalog_language", fromlist=["load_canonical_vocabulary"]
            ).load_canonical_vocabulary([record], compatibility.catalog_version),
        },
        "propose_canonical_mapping": {
            "term": term,
            "allowed_targets": ["phone"],
            "evidence_refs": [],
        },
        "critique_mapping": {"proposal": proposal, "allowed_targets": ["phone"]},
        "score_mapping_evidence": {"proposal": proposal, "support_count": 2, "source_diversity": 2},
        "validate_mapping": {
            "proposal": proposal,
            "vocabulary": __import__(
                "fkgrid.tools.catalog_language", fromlist=["load_canonical_vocabulary"]
            ).load_canonical_vocabulary([record], compatibility.catalog_version),
            "compatibility": compatibility,
        },
        "build_lexicon_candidate": {
            "mappings": [proposal],
            "catalog_version": compatibility.catalog_version,
        },
        "run_lexicon_regression": {
            "candidate": candidate,
            "golden_cases": [{"surface_form": "fone", "target_id": "phone"}],
        },
        "shadow_evaluate_lexicon": {"candidate": candidate, "replay_terms": ["fone"]},
        "review_lexicon_diff": {"candidate": candidate},
        "record_lexicon_review": {"candidate": candidate, "decision": review},
        "activate_lexicon_version": {"store": store, "version": active.version},
        "ingest_batch": {"source": "fixture", "payload": [raw]},
        "resolve_product_identity": {"raw_record": raw},
        "normalize_product": {"raw_record": raw},
        "validate_product": {"candidate": normalized},
        "compare_with_current": {"candidate": record, "current": record_two},
        "get_category_schema": {"category_id": "phones"},
        "extract_supported_attributes": {
            "candidate": normalized,
            "schema": {"supported_attributes": ["ram"]},
        },
        "get_allowed_taxonomy_children": {
            "parent_id": "electronics",
            "taxonomy": {"electronics": ["phones"]},
        },
        "classify_taxonomy": {"candidate": normalized, "allowed_children": ["phones"]},
        "build_catalog_diff": {"candidate": record, "current": record_two},
        "score_change_risk": {"diff": compare_with_current(record, record_two)},
        "route_review_case": {
            "subject_id": "p-1",
            "risk": __import__(
                "fkgrid.tools.catalog_operations", fromlist=["score_change_risk"]
            ).score_change_risk(compare_with_current(record, record_two)),
        },
        "record_review_decision": {
            "store": CatalogVersionStore(),
            "subject_id": "v1",
            "decision": review.model_copy(update={"subject_id": "v1"}),
        },
        "publish_catalog_version": {
            "store": CatalogVersionStore(),
            "version": "v1",
            "records": [record],
            "decision": review.model_copy(update={"subject_id": "v1"}),
        },
        "build_and_smoke_test_index": {
            "version": compatibility.catalog_version,
            "records": [record],
            "index_version": compatibility.index_version,
        },
        "rollback_catalog_version": {
            "store": CatalogVersionStore(active_version="v1", active_records=[record]),
            "version": "v1",
        },
        "ingest_quality_signal": {
            "signal_type": "REPORT",
            "entity_id": "p-1",
            "message": "wrong item",
            "source": "fixture",
        },
        "qualify_signal_group": {"signals": [signal]},
        "get_catalog_snapshot": {"records": [record], "version": compatibility.catalog_version},
        "assemble_case_evidence": {
            "case_id": "case-1",
            "signals": [signal],
            "snapshot": snapshot,
            "compatibility": compatibility,
        },
        "classify_quality_issue": {"packet": packet},
        "validate_quality_assessment": {"assessment": assessment, "packet": packet},
        "route_quality_case": {"case_id": "case-1", "assessment": assessment},
        "record_human_case_decision": {
            "store": QualityCaseStore(),
            "case_id": "case-1",
            "decision": review.model_copy(update={"subject_id": "case-1"}),
        },
        "close_or_reopen_case": {
            "store": QualityCaseStore(),
            "case_id": "case-1",
            "reopen": False,
            "reason": "fixture",
        },
    }

    passed = 0
    for spec in TOOL_SPECS:
        if spec.name not in calls:
            raise AssertionError(f"missing audit fixture for {spec.name}")
        result = handlers[spec.name](**calls[spec.name])
        if result is None:
            raise AssertionError(f"{spec.name} returned None")
        print(f"PASS {spec.name}")
        passed += 1

    # Exercise the specialist port seams as well as the registry handlers.
    language = CatalogLanguagePortAdapter([record], [{"surface_form": "fone", "count": 1}])
    assert language.load_canonical_vocabulary(
        "catalog-demo-v1", "taxonomy-demo-v1", "schema-demo-v1"
    )["checksum"]
    assert language.aggregate_query_gap_events({}, compatibility)
    quality = QualityPortAdapter()
    assert quality.route_quality_case("case-1", assessment)
    operations = CatalogOperationsPortAdapter()
    assert operations.build_catalog_diff(record, record_two).product_id == record.binding.product_id

    print(
        f"AUDIT PASS: {passed}/{len(TOOL_SPECS)} tools; cart tools excluded; "
        "specialist adapters exercised"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
