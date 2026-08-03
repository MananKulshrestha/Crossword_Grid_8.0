"""Machine-checked non-cart capability inventory and typed dispatch seam."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .catalog import DeterministicCatalog
from .catalog_language import (
    activate_lexicon_version,
    aggregate_query_gap_events,
    build_lexicon_candidate,
    cluster_surface_forms,
    critique_mapping,
    load_canonical_vocabulary,
    lookup_approved_expansions,
    mine_candidate_terms,
    normalize_surface_form,
    propose_canonical_mapping,
    record_lexicon_review,
    retrieve_candidate_targets,
    review_lexicon_diff,
    run_lexicon_regression,
    score_mapping_evidence,
    shadow_evaluate_lexicon,
    validate_mapping,
)
from .catalog_operations import (
    build_and_smoke_test_index,
    build_catalog_diff,
    classify_taxonomy,
    compare_with_current,
    extract_supported_attributes,
    get_allowed_taxonomy_children,
    get_category_schema,
    ingest_batch,
    normalize_product,
    publish_catalog_version,
    record_review_decision,
    resolve_product_identity,
    rollback_catalog_version,
    route_review_case,
    score_change_risk,
    validate_product,
)
from .contracts import ToolRequest, ToolResult
from .quality import (
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
from .recovery import (
    apply_recovery_plan,
    assess_retrieval_confidence,
    compare_retrieval_runs,
    get_recovery_constraints,
    plan_constrained_repair,
    record_recovery_event,
    resolve_query_state,
    validate_recovery_plan,
)
from .research import (
    detect_research_need,
    fetch_research_source,
    online_search,
    synthesize_research_answer,
    validate_research_claims,
)
from .runtime import (
    build_clarification,
    build_query_enhancement_context,
    clean_request_summary,
    detect_follow_up_need,
    enhance_chat_query,
    generate_clarifying_question,
    resolve_intent_and_delta,
    validate_clarifying_question,
)
from .suggestions import (
    build_follow_up_candidates,
    generate_follow_up_suggestions,
    select_suggestion,
    validate_suggestion_set,
)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    owner: str
    tier: str
    input_contract: str
    output_contract: str
    deterministic: bool = True
    implemented: bool = True


def _spec(name: str, owner: str, tier: str, input_contract: str, output_contract: str) -> ToolSpec:
    return ToolSpec(name, owner, tier, input_contract, output_contract)


TOOL_SPECS: tuple[ToolSpec, ...] = tuple(
    [
        _spec(
            "enhance_chat_query",
            "shopper-runtime",
            "runtime",
            "current_text + owned context",
            "QueryEnhancement",
        ),
        _spec(
            "build_query_enhancement_context",
            "shopper-runtime",
            "runtime",
            "authorized state + bounded context",
            "EnhancementContext",
        ),
        _spec(
            "clean_request_summary",
            "shopper-runtime",
            "runtime",
            "current message",
            "bounded summary",
        ),
        _spec(
            "resolve_intent_and_delta",
            "shared-model-gateway",
            "runtime",
            "bounded text + QueryState",
            "IntentDelta",
        ),
        _spec(
            "detect_follow_up_need",
            "shopper-runtime",
            "runtime",
            "IntentDelta + QueryState",
            "FollowUpNeed",
        ),
        _spec(
            "generate_clarifying_question",
            "shopper-runtime",
            "runtime",
            "QueryState + reason",
            "ClarifyingQuestion",
        ),
        _spec(
            "build_clarification",
            "shopper-runtime",
            "runtime",
            "reason + supplied options",
            "ClarificationPacket",
        ),
        _spec(
            "validate_clarifying_question",
            "shopper-runtime",
            "runtime",
            "ClarifyingQuestion",
            "bool",
        ),
        _spec(
            "check_commerce_eligibility",
            "catalog",
            "runtime",
            "ProductBinding + purpose + compatibility",
            "CommerceEligibility",
        ),
        _spec("search_catalog", "catalog", "runtime", "SearchRequest", "SearchResult"),
        _spec(
            "assess_retrieval_confidence",
            "query-recovery",
            "runtime",
            "SearchResult + QueryState",
            "ConfidenceDecision",
        ),
        _spec(
            "lookup_approved_expansions",
            "catalog-language",
            "runtime",
            "terms + pinned lexicon",
            "ApprovedExpansion[]",
        ),
        _spec(
            "get_recovery_constraints",
            "query-recovery",
            "runtime",
            "QueryState + unknown terms",
            "RecoveryConstraint[]",
        ),
        _spec(
            "apply_recovery_plan",
            "query-recovery",
            "runtime",
            "QueryState + RecoveryPlan",
            "QueryState",
        ),
        _spec(
            "validate_recovery_plan",
            "query-recovery",
            "runtime",
            "RecoveryPlan + QueryState + compatibility",
            "error[]",
        ),
        _spec(
            "compare_retrieval_runs",
            "query-recovery",
            "runtime",
            "baseline + candidate RetrievalRun",
            "RetrievalComparison",
        ),
        _spec(
            "plan_constrained_repair",
            "query-recovery",
            "tier-2",
            "RecoveryContext + allowlisted concepts",
            "ConstrainedRepairPlan",
        ),
        _spec(
            "record_recovery_event",
            "query-recovery",
            "runtime",
            "outcome + pinned recovery context",
            "RecoveryEventRecord",
        ),
        _spec(
            "resolve_query_state",
            "query-recovery",
            "runtime",
            "message + session QueryState",
            "QueryState",
        ),
        _spec(
            "resolve_reference",
            "shopper-runtime",
            "runtime",
            "references + active SearchResult",
            "ReferenceResolution",
        ),
        _spec(
            "get_product_details",
            "catalog",
            "runtime",
            "exact ProductBinding + compatibility",
            "ProductDetails",
        ),
        _spec(
            "compare_products",
            "catalog",
            "runtime",
            "exact ProductBinding[] + compatibility",
            "Comparison",
        ),
        _spec(
            "check_availability",
            "catalog",
            "runtime",
            "exact ProductBinding + compatibility",
            "Availability",
        ),
        _spec(
            "detect_research_need",
            "research",
            "runtime",
            "explicit shopper message",
            "ResearchDecision",
        ),
        _spec(
            "online_search",
            "research",
            "runtime",
            "ResearchDecision + bounded policy",
            "OnlineSearchResult",
        ),
        _spec("fetch_research_source", "research", "runtime", "provider extract", "ResearchSource"),
        _spec(
            "synthesize_research_answer",
            "shared-model-gateway",
            "runtime",
            "query + cited sources",
            "ResearchAnswer",
        ),
        _spec(
            "validate_research_claims",
            "research",
            "runtime",
            "ResearchAnswer + sources",
            "ResearchAnswer",
        ),
        _spec(
            "build_follow_up_candidates",
            "suggestions",
            "runtime",
            "committed response projection",
            "SuggestionItem[]",
        ),
        _spec(
            "generate_follow_up_suggestions",
            "shared-model-gateway",
            "runtime",
            "candidate IDs + safe labels",
            "FollowUpPhrasing",
        ),
        _spec(
            "validate_suggestion_set",
            "suggestions",
            "runtime",
            "signed SuggestionSet + session",
            "bool",
        ),
        _spec(
            "select_suggestion",
            "suggestions",
            "runtime",
            "session + set ID + suggestion ID",
            "SuggestionSelection",
        ),
        _spec(
            "load_canonical_vocabulary",
            "catalog-language",
            "tier-1",
            "approved catalog records",
            "Vocabulary",
        ),
        _spec(
            "normalize_surface_form",
            "catalog-language",
            "tier-1",
            "surface text + locale",
            "CandidateTerm",
        ),
        _spec(
            "mine_candidate_terms",
            "catalog-language",
            "tier-1",
            "privacy-safe evidence",
            "CandidateTerm[]",
        ),
        _spec(
            "aggregate_query_gap_events",
            "catalog-language",
            "tier-2",
            "de-identified events",
            "CandidateTerm[]",
        ),
        _spec(
            "cluster_surface_forms",
            "catalog-language",
            "tier-2",
            "CandidateTerm[]",
            "CandidateTerm[][]",
        ),
        _spec(
            "retrieve_candidate_targets",
            "catalog-language",
            "tier-1",
            "CandidateTerm + Vocabulary",
            "target_id[]",
        ),
        _spec(
            "propose_canonical_mapping",
            "catalog-language",
            "tier-1",
            "CandidateTerm + allowlist",
            "MappingProposal",
        ),
        _spec(
            "critique_mapping",
            "catalog-language",
            "tier-2",
            "MappingProposal + allowlist",
            "concern_code[]",
        ),
        _spec(
            "score_mapping_evidence",
            "catalog-language",
            "tier-2",
            "MappingProposal + evidence counts",
            "float",
        ),
        _spec(
            "validate_mapping",
            "catalog-language",
            "tier-1",
            "MappingProposal + Vocabulary",
            "error[]",
        ),
        _spec(
            "build_lexicon_candidate",
            "catalog-language",
            "tier-1",
            "validated mappings",
            "LexiconCandidate",
        ),
        _spec(
            "run_lexicon_regression",
            "catalog-language",
            "tier-1",
            "LexiconCandidate + golden cases",
            "RegressionReport",
        ),
        _spec(
            "shadow_evaluate_lexicon",
            "catalog-language",
            "tier-2",
            "LexiconCandidate + replay terms",
            "shadow report",
        ),
        _spec(
            "review_lexicon_diff",
            "catalog-language",
            "tier-1",
            "LexiconCandidate + regression",
            "ReviewDecision",
        ),
        _spec(
            "record_lexicon_review",
            "catalog-language",
            "tier-1",
            "candidate + human decision",
            "LexiconCandidate",
        ),
        _spec(
            "activate_lexicon_version",
            "catalog-language",
            "tier-1",
            "approved version",
            "LexiconCandidate",
        ),
        _spec(
            "ingest_batch", "catalog-operations", "tier-2", "source + raw records", "IngestBatch"
        ),
        _spec(
            "resolve_product_identity",
            "catalog-operations",
            "tier-1",
            "raw product record",
            "ProductBinding",
        ),
        _spec(
            "normalize_product",
            "catalog-operations",
            "tier-1",
            "raw product record",
            "NormalizedProduct",
        ),
        _spec(
            "validate_product",
            "catalog-operations",
            "tier-1",
            "NormalizedProduct + schema",
            "ValidationResult",
        ),
        _spec(
            "compare_with_current",
            "catalog-operations",
            "tier-2",
            "candidate + current record",
            "CatalogDiff",
        ),
        _spec(
            "get_category_schema", "catalog-operations", "tier-1", "category ID", "schema object"
        ),
        _spec(
            "extract_supported_attributes",
            "catalog-operations",
            "tier-1",
            "candidate + schema",
            "AttributeExtraction",
        ),
        _spec(
            "get_allowed_taxonomy_children",
            "catalog-operations",
            "tier-1",
            "parent ID + taxonomy",
            "category_id[]",
        ),
        _spec(
            "classify_taxonomy",
            "catalog-operations",
            "tier-1",
            "candidate + allowlist",
            "TaxonomyClassification",
        ),
        _spec(
            "build_catalog_diff",
            "catalog-operations",
            "tier-2",
            "candidate + current record",
            "CatalogDiff",
        ),
        _spec("score_change_risk", "catalog-operations", "tier-2", "CatalogDiff", "ChangeRisk"),
        _spec(
            "route_review_case",
            "catalog-operations",
            "tier-2",
            "subject + ChangeRisk",
            "ReviewRoute",
        ),
        _spec(
            "record_review_decision",
            "catalog-operations",
            "tier-2",
            "subject + human decision",
            "ReviewDecision",
        ),
        _spec(
            "publish_catalog_version",
            "catalog-operations",
            "tier-2",
            "approved records + decision",
            "CatalogVersionReceipt",
        ),
        _spec(
            "build_and_smoke_test_index",
            "catalog-operations",
            "tier-2",
            "catalog version + records",
            "IndexSmokeReport",
        ),
        _spec(
            "rollback_catalog_version",
            "catalog-operations",
            "tier-2",
            "version",
            "CatalogVersionReceipt",
        ),
        _spec("ingest_quality_signal", "quality-sentinel", "tier-1", "raw signal", "QualitySignal"),
        _spec(
            "qualify_signal_group",
            "quality-sentinel",
            "tier-1",
            "QualitySignal[]",
            "QualityQualification",
        ),
        _spec(
            "get_catalog_snapshot",
            "quality-sentinel",
            "tier-1",
            "records + version",
            "CatalogSnapshot",
        ),
        _spec(
            "assemble_case_evidence",
            "quality-sentinel",
            "tier-1",
            "case + qualified signals",
            "EvidencePacket",
        ),
        _spec(
            "classify_quality_issue",
            "quality-sentinel",
            "tier-1",
            "EvidencePacket",
            "QualityAssessment",
        ),
        _spec(
            "validate_quality_assessment",
            "quality-sentinel",
            "tier-1",
            "assessment + packet",
            "ValidationResult",
        ),
        _spec(
            "route_quality_case", "quality-sentinel", "tier-1", "case + assessment", "QualityRoute"
        ),
        _spec(
            "record_human_case_decision",
            "quality-sentinel",
            "tier-1",
            "case + human decision",
            "ReviewDecision",
        ),
        _spec(
            "close_or_reopen_case",
            "quality-sentinel",
            "tier-1",
            "case + policy event",
            "QualityCaseStatus",
        ),
    ]
)


CART_TOOL_NAMES = frozenset(
    {"show_cart", "update_cart", "resolve_cart_target", "revalidate_cart", "clear_cart"}
)
if CART_TOOL_NAMES & {spec.name for spec in TOOL_SPECS}:
    raise RuntimeError("cart tools must not be registered in the non-cart registry")


def tool_inventory() -> list[dict[str, object]]:
    return [
        {
            "name": spec.name,
            "owner": spec.owner,
            "tier": spec.tier,
            "input_contract": spec.input_contract,
            "output_contract": spec.output_contract,
            "deterministic": spec.deterministic,
            "implemented": spec.implemented,
        }
        for spec in sorted(TOOL_SPECS, key=lambda item: item.name)
    ]


class ToolRegistry:
    """Static allowlist; callers cannot register tools from model output."""

    def __init__(self, handlers: dict[str, Callable[..., object]] | None = None) -> None:
        self._handlers = dict(handlers or {})

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in TOOL_SPECS)

    def register(self, name: str, handler: Callable[..., object]) -> None:
        if name not in self.names:
            raise ValueError("TOOL_NOT_ALLOWLISTED")
        self._handlers[name] = handler

    def call(self, request: ToolRequest) -> ToolResult:
        handler = self._handlers.get(request.tool_name)
        if handler is None:
            return ToolResult(
                tool_name=request.tool_name,
                tool_version=request.tool_version,
                status="UNAVAILABLE",
                warnings=["HANDLER_NOT_BOUND"],
            )
        try:
            output = handler(**request.payload)
        except (KeyError, TypeError, ValueError) as exc:
            return ToolResult(
                tool_name=request.tool_name,
                tool_version=request.tool_version,
                status="REJECTED",
                warnings=[type(exc).__name__],
            )
        if hasattr(output, "model_dump"):
            output = output.model_dump(mode="json")
        return ToolResult(
            tool_name=request.tool_name,
            tool_version=request.tool_version,
            status="OK",
            output=dict(output) if isinstance(output, dict) else {"value": output},
        )


def build_default_registry(
    catalog: DeterministicCatalog,
    suggestion_handlers: dict[str, Callable[..., object]] | None = None,
) -> ToolRegistry:
    handlers: dict[str, Callable[..., object]] = {
        "enhance_chat_query": enhance_chat_query,
        "build_query_enhancement_context": build_query_enhancement_context,
        "clean_request_summary": clean_request_summary,
        "resolve_intent_and_delta": resolve_intent_and_delta,
        "detect_follow_up_need": detect_follow_up_need,
        "generate_clarifying_question": generate_clarifying_question,
        "build_clarification": build_clarification,
        "validate_clarifying_question": validate_clarifying_question,
        "check_commerce_eligibility": catalog.check_commerce_eligibility,
        "search_catalog": catalog.search,
        "get_product_details": catalog.get_product_details,
        "compare_products": catalog.compare_products,
        "check_availability": catalog.check_availability,
        "resolve_reference": catalog.resolve_reference,
        "assess_retrieval_confidence": assess_retrieval_confidence,
        "lookup_approved_expansions": lookup_approved_expansions,
        "get_recovery_constraints": get_recovery_constraints,
        "resolve_query_state": resolve_query_state,
        "apply_recovery_plan": apply_recovery_plan,
        "validate_recovery_plan": validate_recovery_plan,
        "compare_retrieval_runs": compare_retrieval_runs,
        "plan_constrained_repair": plan_constrained_repair,
        "record_recovery_event": record_recovery_event,
        "detect_research_need": detect_research_need,
        "online_search": online_search,
        "fetch_research_source": fetch_research_source,
        "synthesize_research_answer": synthesize_research_answer,
        "validate_research_claims": validate_research_claims,
        "build_follow_up_candidates": build_follow_up_candidates,
        "generate_follow_up_suggestions": generate_follow_up_suggestions,
        "validate_suggestion_set": validate_suggestion_set,
        "select_suggestion": select_suggestion,
        "load_canonical_vocabulary": load_canonical_vocabulary,
        "normalize_surface_form": normalize_surface_form,
        "mine_candidate_terms": mine_candidate_terms,
        "aggregate_query_gap_events": aggregate_query_gap_events,
        "cluster_surface_forms": cluster_surface_forms,
        "retrieve_candidate_targets": retrieve_candidate_targets,
        "propose_canonical_mapping": propose_canonical_mapping,
        "critique_mapping": critique_mapping,
        "score_mapping_evidence": score_mapping_evidence,
        "validate_mapping": validate_mapping,
        "build_lexicon_candidate": build_lexicon_candidate,
        "run_lexicon_regression": run_lexicon_regression,
        "shadow_evaluate_lexicon": shadow_evaluate_lexicon,
        "review_lexicon_diff": review_lexicon_diff,
        "record_lexicon_review": record_lexicon_review,
        "activate_lexicon_version": activate_lexicon_version,
        "ingest_batch": ingest_batch,
        "resolve_product_identity": resolve_product_identity,
        "normalize_product": normalize_product,
        "validate_product": validate_product,
        "compare_with_current": compare_with_current,
        "get_category_schema": get_category_schema,
        "extract_supported_attributes": extract_supported_attributes,
        "get_allowed_taxonomy_children": get_allowed_taxonomy_children,
        "classify_taxonomy": classify_taxonomy,
        "build_catalog_diff": build_catalog_diff,
        "score_change_risk": score_change_risk,
        "route_review_case": route_review_case,
        "record_review_decision": record_review_decision,
        "publish_catalog_version": publish_catalog_version,
        "build_and_smoke_test_index": build_and_smoke_test_index,
        "rollback_catalog_version": rollback_catalog_version,
        "ingest_quality_signal": ingest_quality_signal,
        "qualify_signal_group": qualify_signal_group,
        "get_catalog_snapshot": get_catalog_snapshot,
        "assemble_case_evidence": assemble_case_evidence,
        "classify_quality_issue": classify_quality_issue,
        "validate_quality_assessment": validate_quality_assessment,
        "route_quality_case": route_quality_case,
        "record_human_case_decision": record_human_case_decision,
        "close_or_reopen_case": close_or_reopen_case,
    }
    handlers.update(suggestion_handlers or {})
    return ToolRegistry(handlers)
