# Detailed tool report

## Scope and audit basis

The repository-wide audit used the binding plans in `technical plans/00-INDEX.md`,
plans 01–08, plans 19, 21, 23, and 24, the four specialist workflow documents,
and the four existing agent worktrees. The current committed target is Catalog
Operations Tier 2, Catalog Language Tier 1, Query Recovery Tier 2, Quality
Sentinel Tier 1, plus the shopper runtime contracts. Tier 3 remains
feature-flagged and is not implemented here.

The latest agentic chat branch supplied the shopper orchestrator and speech
workflow, while the specialist worktrees supplied local contracts and port
shapes. This branch supplies the deterministic non-cart implementation and
explicit adapters, then wires those adapters into the API runtime without
changing the specialist worktrees or taking ownership of cart policy.

## Complete non-cart inventory

Every item below is present in `TOOL_SPECS` and has a callable handler in the
default registry. The output names are the shared strict models in
`src/fkgrid/tools/contracts.py`.

### Shopper runtime and recovery

| Tool | Input -> output |
|---|---|
| `enhance_chat_query` | bounded current/owned context -> `QueryEnhancement` |
| `build_query_enhancement_context` | authorized state + bounded current/history/memory context -> `EnhancementContext` |
| `clean_request_summary` | current message -> bounded deterministic summary |
| `resolve_intent_and_delta` | bounded text + state -> `IntentDelta` |
| `detect_follow_up_need` | intent + state -> `FollowUpNeed` |
| `generate_clarifying_question` | state + reason -> `ClarifyingQuestion` |
| `build_clarification` | reason + allowlisted options -> `ClarificationPacket` |
| `validate_clarifying_question` | question + state -> boolean |
| `check_commerce_eligibility` | exact binding + purpose + version -> `CommerceEligibility` |
| `search_catalog` | `SearchRequest` -> `SearchResult` |
| `assess_retrieval_confidence` | result + state -> `ConfidenceDecision` |
| `lookup_approved_expansions` | terms + pinned lexicon -> `ApprovedExpansion[]` |
| `get_recovery_constraints` | state + unknown terms -> `RecoveryConstraint[]` |
| `apply_recovery_plan` | state + validated plan -> `QueryState` |
| `validate_recovery_plan` | plan + state + version -> error list |
| `compare_retrieval_runs` | baseline + candidate -> `RetrievalComparison` |
| `plan_constrained_repair` | recovery context + allowlisted concepts -> `ConstrainedRepairPlan` |
| `record_recovery_event` | outcome + pinned recovery context -> `RecoveryEventRecord` |
| `resolve_query_state` | message + state -> `QueryState` |
| `resolve_reference` | references + active result set -> `ReferenceResolution` |
| `get_product_details` | exact binding + version -> `ProductDetails` |
| `compare_products` | exact bindings + version -> `Comparison` |
| `check_availability` | exact binding + version -> `Availability` |
| `detect_research_need` | explicit message -> `ResearchDecision` |
| `online_search` | decision + bounded policy -> `OnlineSearchResult` |
| `fetch_research_source` | provider extract -> normalized `ResearchSource` |
| `synthesize_research_answer` | query + cited sources -> `ResearchAnswer` |
| `validate_research_claims` | answer + sources -> validated `ResearchAnswer` |
| `build_follow_up_candidates` | committed response projection -> `SuggestionItem[]` |
| `generate_follow_up_suggestions` | safe candidate IDs + labels -> `FollowUpPhrasing` |
| `validate_suggestion_set` | signed set + session/time -> boolean |
| `select_suggestion` | session + set ID + action ID -> `SuggestionSelection` |

### Catalog Language

| Tool | Input -> output |
|---|---|
| `load_canonical_vocabulary` | approved catalog records -> `Vocabulary` |
| `normalize_surface_form` | surface text + locale -> `CandidateTerm` |
| `mine_candidate_terms` | privacy-safe evidence -> `CandidateTerm[]` |
| `aggregate_query_gap_events` | de-identified events -> `CandidateTerm[]` |
| `cluster_surface_forms` | candidate terms -> deterministic clusters |
| `retrieve_candidate_targets` | term + vocabulary -> allowed target IDs |
| `propose_canonical_mapping` | term + target allowlist -> `MappingProposal` |
| `critique_mapping` | proposal + allowlist -> concern codes |
| `score_mapping_evidence` | proposal + evidence counts -> score |
| `validate_mapping` | proposal + vocabulary + version -> error list |
| `build_lexicon_candidate` | validated mappings -> `LexiconCandidate` |
| `run_lexicon_regression` | candidate + golden cases -> `RegressionReport` |
| `shadow_evaluate_lexicon` | candidate + replay terms -> shadow report |
| `review_lexicon_diff` | candidate + regression -> `ReviewDecision` |
| `record_lexicon_review` | candidate + human decision -> updated candidate |
| `activate_lexicon_version` | approved version -> active candidate |

### Catalog Operations

| Tool | Input -> output |
|---|---|
| `ingest_batch` | source + raw records -> `IngestBatch` |
| `resolve_product_identity` | raw record -> exact `ProductBinding` |
| `normalize_product` | raw record -> `NormalizedProduct` |
| `validate_product` | normalized product + schema -> `ValidationResult` |
| `compare_with_current` | candidate + current -> `CatalogDiff` |
| `get_category_schema` | category ID -> schema object |
| `extract_supported_attributes` | candidate + schema -> `AttributeExtraction` |
| `get_allowed_taxonomy_children` | parent + taxonomy -> category IDs |
| `classify_taxonomy` | candidate + allowlist -> `TaxonomyClassification` |
| `build_catalog_diff` | candidate + current -> `CatalogDiff` |
| `score_change_risk` | diff -> `ChangeRisk` |
| `route_review_case` | subject + risk -> `ReviewRoute` |
| `record_review_decision` | subject + human decision -> `ReviewDecision` |
| `publish_catalog_version` | approved records + decision -> `CatalogVersionReceipt` |
| `build_and_smoke_test_index` | version + records -> `IndexSmokeReport` |
| `rollback_catalog_version` | version -> `CatalogVersionReceipt` |

### Quality Sentinel

| Tool | Input -> output |
|---|---|
| `ingest_quality_signal` | raw signal -> redacted `QualitySignal` |
| `qualify_signal_group` | signal group -> `QualityQualification` |
| `get_catalog_snapshot` | records + version -> `CatalogSnapshot` |
| `assemble_case_evidence` | case + signals + snapshot -> `EvidencePacket` |
| `classify_quality_issue` | evidence packet -> `QualityAssessment` |
| `validate_quality_assessment` | assessment + packet -> `ValidationResult` |
| `route_quality_case` | case + assessment -> `QualityRoute` |
| `record_human_case_decision` | case + human decision -> `ReviewDecision` |
| `close_or_reopen_case` | case + policy event -> `QualityCaseStatus` |

The registry has 73 tools total. The six additions above are required by
plans 07, 23, and 24 and were not present in the earlier 67-tool draft. Cart
tools remain intentionally excluded.

## Workflow input/output reconciliation

| Existing workflow | Shared adapter | Input check | Output check |
|---|---|---|---|
| chat `CatalogSearchPort` | `CatalogSearchPortAdapter` | `SearchRequest`, exact `ProductBinding`, `CompatibilityTuple`, deadline | `SearchResult`, `ProductDetails`, `Comparison`, `Availability`, `CommerceEligibility` with exact IDs/evidence/version |
| chat `ReferenceResolverPort` | `ReferenceResolverPortAdapter` | session references are resolved only against the active `SearchResult` | `ReferenceResolution` returns resolved bindings or `AMBIGUOUS`/`STALE`/`NOT_FOUND`; no guessing |
| chat `RecoveryPort` | `RecoveryPortAdapter` | result/state/version/deadline | confidence and recovered `SearchResult`; hard filters are retained |
| query-recovery expansion/constraint/retrieval ports | `QueryRecoveryPortAdapter` | terms, query state, compatibility, run kind, remaining budget | approved expansions, constraints, and version-pinned `SearchResult` |
| chat research port | `ResearchPortAdapter` | intent/current text/entity IDs; decision/deadline | explicit decision, bounded search result, cited claim validation |
| suggestion port | `SuggestionPortAdapter` | session/turn/action/bindings/version/time | signed expiring set and stored action-ID selection; no label reparsing |
| catalog-language workflow | `CatalogLanguagePortAdapter` + `LexiconStore` | vocabulary, evidence groups, targets, mappings, regression/shadow/review/activation/lookup | specialist field projection; activation requires recorded approval |
| catalog-operations workflow | `CatalogOperationsPortAdapter` + `CatalogVersionStore` | diff, review, publication, smoke, rollback | atomic version receipt, checksum, smoke report, rollback receipt |
| quality-sentinel workflow | `QualityPortAdapter` + `QualityCaseStore` | shared redacted signal/evidence packet/human reviewer seam | evidence-backed assessment, route, decision, lifecycle status; the host must map the richer local binding/source schema |

The existing worktrees use their own local Pydantic classes. The adapters expose
the same method names and specialist field semantics; callers may pass local
model classes through `LanguageModelTypes`/`ChatModelTypes` for strict
`model_validate()` checks. No feature worktree imports another worktree's
internal models.

The quality worktree's inbound signal and assessment contracts intentionally
carry more operational metadata than the compact shared reference models. That
is a known adapter boundary, not silently discarded production data: a quality
host must supply the richer local model class and mapping before connecting a
durable queue or database.

## Determinism and safety decisions

- Catalog eligibility is evaluated before scoring. Unknown price/attributes do
  not satisfy hard constraints.
- Ranking ties use the exact `(product_id, sku_id, offer_id, variant_id)` tuple.
- Result-set IDs are content-derived and ordinals resolve only against the
  active acknowledged set; stale sets fail closed.
- Recovery validates compatibility and the hard-filter hash before applying a
  plan. It may add/rewrite soft terms but cannot relax hard filters.
- Research cannot supply catalog truth, action IDs, cart targets, or mutations.
  The local search adapter consumes fixtures only; it never performs arbitrary
  browsing or direct HTTP fetches.
- Suggestions are signed with HMAC, session-bound, expiry-checked, capped at
  three, and restricted to non-cart action types.
- Model calls return strict structured JSON from one gateway. A model cannot
  register tools, emit provider SDK types, invent IDs, or authorize a write.
- Quality signals redact email and phone patterns before storage.
- Catalog/lexicon activation requires an explicit human approval record and
  switches the active pointer atomically in the reference store.

## Deliberately not implemented

No cart tools were added. Tier 3-only tools such as multi-hypothesis recovery,
image evidence, incident graphs, cross-record intelligence, root-cause
hypotheses, broader incident scope, and autonomous enrichment remain deferred
behind the plan-set feature gates. Production database, RBAC, migrations,
artifact activation, hybrid dense/lexical indexing, live provider adapters,
durable session/memory/suggestion stores, and the full orchestrator CAS path are
integration work, not silently represented as complete here.

## Verification

The bundled Python 3.12 runtime ran:

```text
python -m compileall -q src
python scripts/contract_audit.py
AUDIT PASS: 73/73 tools; cart tools excluded; specialist adapters exercised
```

The full local pytest suite, contract audit, and compile check pass. Ruff still
reports formatting and unused-import cleanup in the imported reference files;
that is not represented as green. The final worktree was the only worktree
edited. See `source_audit.md`.
