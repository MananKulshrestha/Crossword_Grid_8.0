from __future__ import annotations

from fkgrid.tools.catalog import DeterministicCatalog
from fkgrid.tools.catalog_language import (
    LexiconStore,
    build_lexicon_candidate,
    load_canonical_vocabulary,
    normalize_surface_form,
    propose_canonical_mapping,
    record_lexicon_review,
    review_lexicon_diff,
    run_lexicon_regression,
    validate_mapping,
)
from fkgrid.tools.catalog_operations import (
    CatalogVersionStore,
    build_and_smoke_test_index,
    build_catalog_diff,
    ingest_batch,
    normalize_product,
    resolve_product_identity,
    score_change_risk,
    validate_product,
)
from fkgrid.tools.contracts import (
    CatalogRecord,
    CompatibilityTuple,
    EvidenceRef,
    ProductBinding,
    QueryState,
    ReviewDecision,
    SearchRequest,
)
from fkgrid.tools.model_gateway import DeterministicModelGateway
from fkgrid.tools.quality import (
    QualityCaseStore,
    assemble_case_evidence,
    classify_quality_issue,
    close_or_reopen_case,
    get_catalog_snapshot,
    ingest_quality_signal,
    qualify_signal_group,
    record_human_case_decision,
    route_quality_case,
    validate_quality_assessment,
)
from fkgrid.tools.recovery import (
    assess_retrieval_confidence,
    compare_retrieval_runs,
    validate_recovery_plan,
)
from fkgrid.tools.registry import CART_TOOL_NAMES, TOOL_SPECS, build_default_registry
from fkgrid.tools.research import (
    detect_research_need,
    fetch_research_source,
    online_search,
    synthesize_research_answer,
    validate_research_claims,
)
from fkgrid.tools.runtime import (
    detect_follow_up_need,
    enhance_chat_query,
    generate_clarifying_question,
    resolve_intent_and_delta,
    validate_clarifying_question,
)
from fkgrid.tools.suggestions import (
    SuggestionStore,
    build_follow_up_candidates,
    validate_suggestion_set,
)


def _record(
    product_id: str, title: str, *, category: str = "phones", price: float = 20000.0
) -> CatalogRecord:
    binding = ProductBinding(
        product_id=product_id, sku_id=f"{product_id}-sku", offer_id=f"{product_id}-offer"
    )
    return CatalogRecord(
        binding=binding,
        title=title,
        category_id=category,
        brand="Acme",
        description="reliable prototype device",
        attributes={"ram": "8 GB", "colour": "black"},
        price=price,
        availability="AVAILABLE",
        evidence_refs=[
            EvidenceRef(
                evidence_id=f"e-{product_id}",
                source="fixture",
                field="title",
                version="catalog-demo-v1",
            )
        ],
    )


def test_catalog_search_read_tools_and_reference_resolution_are_exact() -> None:
    catalog = DeterministicCatalog(
        [_record("p1", "Acme Phone 8", price=18000), _record("p2", "Acme Phone Pro", price=28000)]
    )
    state = QueryState(text="phone", normalized_terms=["phone"], hard_filters={"max_price": 20000})
    result = catalog.search(SearchRequest(query_state=state))
    assert result.status == "OK"
    assert [entry.binding.product_id for entry in result.entries] == ["p1"]
    assert catalog.resolve_reference(["the first one"], result).resolved[0].product_id == "p1"
    assert catalog.resolve_reference(["the second one"], result).status == "NOT_FOUND"
    assert catalog.get_product_details(result.entries[0].binding).status == "OK"
    assert catalog.compare_products([result.entries[0].binding]).status == "OK"
    assert catalog.check_availability(result.entries[0].binding).status == "AVAILABLE"
    assert catalog.check_commerce_eligibility(result.entries[0].binding, "details").eligible is True


def test_runtime_precedence_intent_and_clarification() -> None:
    enhancement = enhance_chat_query(
        "black phone under 20000",
        memory_terms=["laptop"],
        recent_turns=[{"normalized_terms": ["tablet"]}],
    )
    assert "phone" in enhancement.normalized_terms
    assert "laptop" in enhancement.soft_context_terms
    intent = resolve_intent_and_delta("please compare the first two")
    assert intent.action == "COMPARE"
    need = detect_follow_up_need(intent)
    assert need.blocking is True
    question = generate_clarifying_question(QueryState(), need.reason)
    assert validate_clarifying_question(question) is True


def test_recovery_confidence_plan_and_comparator_preserve_filters() -> None:
    catalog = DeterministicCatalog([_record("p1", "Acme Phone")])
    state = QueryState(
        text="unknown", normalized_terms=["unknown"], hard_filters={"max_price": 20000}
    )
    baseline = catalog.search(SearchRequest(query_state=state))
    decision = assess_retrieval_confidence(baseline, state)
    assert decision.status == "RECOVER"
    recovered_state = state.model_copy(update={"text": "phone", "normalized_terms": ["phone"]})
    assert recovered_state.hard_filters == state.hard_filters
    candidate = catalog.search(SearchRequest(query_state=recovered_state))
    comparison = compare_retrieval_runs(
        __import__("fkgrid.tools.contracts", fromlist=["RetrievalRun"]).RetrievalRun(
            run_id="b", kind="BASELINE", result=baseline, query_state=state
        ),
        __import__("fkgrid.tools.contracts", fromlist=["RetrievalRun"]).RetrievalRun(
            run_id="c", kind="DIRECT_RECOVERY", result=candidate, query_state=recovered_state
        ),
    )
    assert comparison.status == "ACCEPT_RECOVERY"
    assert validate_recovery_plan


def test_catalog_language_candidate_review_and_atomic_activation() -> None:
    records = [_record("p1", "Acme Phone")]
    vocabulary = load_canonical_vocabulary(records, "catalog-demo-v1")
    term = normalize_surface_form("Acme")
    proposal = propose_canonical_mapping(
        term,
        vocabulary.brands,
        [EvidenceRef(evidence_id="e1", source="fixture", field="brand", version="catalog-demo-v1")],
    )
    assert validate_mapping(proposal, vocabulary) == []
    candidate = build_lexicon_candidate([proposal], "catalog-demo-v1")
    assert run_lexicon_regression(candidate).status == "PASS"
    decision = review_lexicon_diff(candidate, run_lexicon_regression(candidate)).model_copy(
        update={"decision": "APPROVED", "reviewer_id": "human-1"}
    )
    approved = record_lexicon_review(candidate, decision)
    store = LexiconStore()
    store.write_candidate(approved)
    store.record_review(decision)
    assert store.activate_lexicon_version(candidate.version).status == "ACTIVE"


def test_catalog_operations_validate_diff_publish_index_and_rollback() -> None:
    raw = {
        "title": "Acme Phone",
        "category_id": "phones",
        "brand": "Acme",
        "price": "20,000",
        "attributes": {"ram": "8 GB"},
    }
    batch = ingest_batch("fixture", [raw])
    assert batch.payload_hash
    normalized = normalize_product(raw)
    assert validate_product(normalized).valid
    binding = resolve_product_identity(raw)
    assert binding.product_id
    diff = build_catalog_diff(normalized.record, None)
    assert score_change_risk(diff).level in {"LOW", "MEDIUM", "HIGH"}
    store = CatalogVersionStore()
    decision = ReviewDecision(
        subject_id="v1", decision="APPROVED", reviewer_id="human-1", reason="fixture"
    )
    receipt = store.publish_catalog_version("v1", [normalized.record], decision)
    assert receipt.status == "PUBLISHED"
    assert build_and_smoke_test_index("v1", store.records()).status == "PASS"
    assert store.rollback_catalog_version("v1").status == "ROLLED_BACK"


def test_quality_sentinel_classifies_routes_and_requires_human_decision() -> None:
    signal = ingest_quality_signal(
        "delivery_report", "p1", "wrong item; contact me at user@example.com"
    )
    assert "[REDACTED_EMAIL]" in signal.message
    assert qualify_signal_group([signal]).qualified
    packet = assemble_case_evidence(
        "case-1", [signal], get_catalog_snapshot([_record("p1", "Acme Phone")], "v1")
    )
    assessment = classify_quality_issue(packet)
    assert validate_quality_assessment(assessment, packet).valid
    assert route_quality_case("case-1", assessment).destination == "CATALOG_REVIEW"
    store = QualityCaseStore()
    decision = ReviewDecision(
        subject_id="case-1", decision="APPROVED", reviewer_id="human-1", reason="reviewed"
    )
    assert record_human_case_decision(store, "case-1", decision).reviewer_id == "human-1"
    assert close_or_reopen_case(store, "case-1", False, "resolved").status == "CLOSED"


def test_research_is_explicit_cited_and_network_free() -> None:
    decision = detect_research_need("what is the latest research on this?")
    disabled = online_search(decision, [{"url": "https://example.com/a", "extract": "fact"}])
    assert disabled.status == "UNAVAILABLE"
    enabled = online_search(
        decision,
        [{"url": "https://example.com/a", "extract": "fact", "domain": "example.com"}],
        allow_domains=["example.com"],
        enabled=True,
    )
    source = fetch_research_source(enabled.sources[0])
    answer = synthesize_research_answer(decision.query, [source])
    assert validate_research_claims(answer, [source]).status == "OK"


def test_suggestions_are_signed_stale_safe_and_non_cart() -> None:
    bindings = [_record("p1", "Acme Phone").binding]
    assert all(
        item.action.action_type not in {"SHOW_CART", "UPDATE_CART"}
        for item in build_follow_up_candidates("SEARCH", bindings)
    )
    store = SuggestionStore(b"test-secret")
    suggestion_set = store.build_and_store(
        "s1", "t1", "SEARCH", bindings, CompatibilityTuple(), now_ms=100
    )
    assert suggestion_set is not None
    assert validate_suggestion_set(suggestion_set, "s1", 101, b"test-secret")
    assert (
        store.select(
            "s1", suggestion_set.suggestion_set_id, suggestion_set.suggestions[0].suggestion_id, 101
        ).status
        == "SELECTED"
    )
    assert (
        store.select(
            "s1",
            suggestion_set.suggestion_set_id,
            suggestion_set.suggestions[0].suggestion_id,
            suggestion_set.expires_at_ms,
        ).status
        == "SUGGESTION_STALE"
    )


def test_model_gateway_and_registry_allowlist() -> None:
    gateway = DeterministicModelGateway()
    response = gateway.complete_json(
        __import__("fkgrid.tools.contracts", fromlist=["ModelCallRequest"]).ModelCallRequest(
            call_name="resolve_intent_and_delta", payload={"text": "show phones"}
        )
    )
    assert response.status == "OK"
    registry = build_default_registry(DeterministicCatalog([]))
    assert len(TOOL_SPECS) == 73
    assert set(registry.names) == set(registry._handlers)
    assert not CART_TOOL_NAMES.intersection(registry.names)
    assert "search_catalog" in registry.names
    assert "update_cart" not in registry.names
