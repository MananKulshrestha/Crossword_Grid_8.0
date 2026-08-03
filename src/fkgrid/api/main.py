"""FastAPI routes for exercising the Catalog Language Tier 2 contracts."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from fkgrid.api.container import CatalogLanguageApiContainer, create_gemma_container
from fkgrid.catalog_language.normalization import normalize_surface_form
from fkgrid.domain.catalog_language import (
    CATALOG_LANGUAGE_DEFAULT_MODEL_DEADLINE_MS,
    CATALOG_LANGUAGE_MODEL_MAX_DEADLINE_MS,
    EvidenceBand,
    EvidenceWindow,
    ExpansionAction,
    GuidedLexiconRunRequest,
    LexiconCompatibility,
    LexiconLookupRequest,
    LexiconLookupResult,
    LexiconPreviewResult,
    LexiconScope,
    LexiconWorkflowRequest,
    LexiconWorkflowResult,
    MappingDirection,
    MappingKind,
    TargetType,
)
from fkgrid.workflows.catalog_language import (
    CATALOG_LANGUAGE_CAPABILITIES,
    CATALOG_LANGUAGE_FORBIDDEN_CAPABILITIES,
)

DEMO_UI_DIR = Path(__file__).resolve().parents[1] / "ui"


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("expected an ISO-8601 datetime") from exc
    raise TypeError("expected an ISO-8601 datetime")


ApiDateTime = Annotated[datetime, BeforeValidator(_parse_datetime)]


class WorkflowEvidenceWindowBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    window_start: ApiDateTime
    window_end: ApiDateTime
    min_observation_days: int = Field(default=7, ge=1, le=365)
    min_support_count: int = Field(default=5, ge=1, le=1_000_000)
    min_distinct_source_groups: int = Field(default=5, ge=1, le=1_000_000)
    min_source_classes: int = Field(default=2, ge=1, le=6)
    max_source_concentration: float = Field(default=0.4, gt=0.0, le=1.0)


class LexiconWorkflowApiRequest(BaseModel):
    """JSON-friendly HTTP form of the strict workflow request."""

    model_config = ConfigDict(extra="forbid", strict=True)

    run_id: str = Field(min_length=1, max_length=128)
    evidence_window: WorkflowEvidenceWindowBody
    compatibility: LexiconCompatibility
    active_lexicon_version: str = Field(min_length=1, max_length=128)
    max_proposals: int = Field(default=100, ge=1, le=500)
    proposer_deadline_ms: int = Field(
        default=CATALOG_LANGUAGE_DEFAULT_MODEL_DEADLINE_MS,
        ge=1,
        le=CATALOG_LANGUAGE_MODEL_MAX_DEADLINE_MS,
    )
    critic_deadline_ms: int = Field(
        default=CATALOG_LANGUAGE_DEFAULT_MODEL_DEADLINE_MS,
        ge=1,
        le=CATALOG_LANGUAGE_MODEL_MAX_DEADLINE_MS,
    )
    regression_policy_version: str = Field(min_length=1, max_length=128)
    shadow_policy_version: str = Field(min_length=1, max_length=128)

    def to_domain(self) -> LexiconWorkflowRequest:
        return LexiconWorkflowRequest(
            run_id=self.run_id,
            evidence_window=EvidenceWindow(
                window_start=self.evidence_window.window_start,
                window_end=self.evidence_window.window_end,
                min_observation_days=self.evidence_window.min_observation_days,
                min_support_count=self.evidence_window.min_support_count,
                min_distinct_source_groups=self.evidence_window.min_distinct_source_groups,
                min_source_classes=self.evidence_window.min_source_classes,
                max_source_concentration=self.evidence_window.max_source_concentration,
            ),
            compatibility=self.compatibility,
            active_lexicon_version=self.active_lexicon_version,
            max_proposals=self.max_proposals,
            proposer_deadline_ms=self.proposer_deadline_ms,
            critic_deadline_ms=self.critic_deadline_ms,
            regression_policy_version=self.regression_policy_version,
            shadow_policy_version=self.shadow_policy_version,
        )


class ApiStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    service: str
    status: str
    mode: str
    ready: bool
    detail: str


class CapabilitiesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    tier: str
    capabilities: list[str]
    forbidden_capabilities: list[str]


class GuidedInputSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    term: str
    locale: str
    category: str | None = None
    attribute_id: str | None = None


class GuidedExpansion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    mapping_id: str
    source_form: str
    normalized_form: str
    target_type: TargetType
    target_id: str
    mapping_kind: MappingKind
    direction: MappingDirection
    expansion_action: ExpansionAction
    scope: LexiconScope
    evidence_band: EvidenceBand


class GuidedExpansionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    normalized_query: str
    expanded_to: list[GuidedExpansion] = Field(default_factory=list, max_length=5)


class GuidedModelSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    mode: str
    active: bool
    proposer_status: str
    critic_status: str


class GuidedGateSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    candidate_version: str | None = None
    validation: str
    regression: str
    shadow: str
    review: str
    activation: str
    activated: bool
    active_lexicon_version: str | None = None


class GuidedRunResponse(BaseModel):
    """Compact presentation model for the guided Swagger route."""

    model_config = ConfigDict(extra="forbid", strict=True)

    run_id: str
    status: str
    input: GuidedInputSummary
    output: GuidedExpansionOutput
    model: GuidedModelSummary
    gates: GuidedGateSummary
    warnings: list[str] = Field(default_factory=list, max_length=32)


GUIDED_RESPONSE_EXAMPLE = {
    "run_id": "guided-example-001",
    "status": "COMPLETED",
    "input": {
        "term": "sneakers",
        "locale": "en-IN",
        "category": "footwear",
        "attribute_id": None,
    },
    "output": {
        "normalized_query": "sneakers",
        "expanded_to": [
            {
                "mapping_id": "mapping-0005",
                "source_form": "sneakers",
                "normalized_form": "sneakers",
                "target_type": "TAXONOMY_NODE",
                "target_id": "athletic-shoes",
                "mapping_kind": "SYNONYM",
                "direction": "QUERY_TO_CANONICAL",
                "expansion_action": "CANONICAL_SYNONYM",
                "scope": {"locale": "en-IN", "taxonomy_node_id": "footwear"},
                "evidence_band": "MEDIUM",
            }
        ],
    },
    "model": {
        "mode": "gemma",
        "active": True,
        "proposer_status": "OK",
        "critic_status": "OK",
    },
    "gates": {
        "candidate_version": "lexicon-candidate-0005",
        "validation": "VALID",
        "regression": "PASSED",
        "shadow": "PASSED",
        "review": "APPROVED",
        "activation": "ACTIVATED",
        "activated": True,
        "active_lexicon_version": "lexicon-candidate-0005",
    },
    "warnings": [],
}


WORKFLOW_EXAMPLE = {
    "run_id": "swagger-demo-run-001",
    "evidence_window": {
        "window_start": "2026-07-01T00:00:00Z",
        "window_end": "2026-08-01T00:00:00Z",
        "min_observation_days": 7,
        "min_support_count": 5,
        "min_distinct_source_groups": 5,
        "min_source_classes": 2,
        "max_source_concentration": 0.4,
    },
    "compatibility": {
        "catalog_version": "cat-demo-1",
        "taxonomy_version": "tax-demo-1",
        "category_schema_version": "schema-demo-1",
        "lexicon_version": "lex-demo-1",
        "normalizer_version": "normalizer-v1",
        "mapping_schema_version": "mapping-v1",
        "rank_policy_version": "rank-v1",
    },
    "active_lexicon_version": "lex-demo-1",
    "max_proposals": 100,
    "proposer_deadline_ms": 10000,
    "critic_deadline_ms": 10000,
    "regression_policy_version": "regression-v1",
    "shadow_policy_version": "shadow-v1",
}


LOOKUP_EXAMPLE = {
    "term": "trainers",
    "locale": "en-IN",
    "taxonomy_node_id": "footwear",
    "lexicon_version": "lex-demo-1",
    "max_mappings": 3,
}


OPENAPI_TAGS = [
    {
        "name": "guided-demo",
        "description": (
            "Human-friendly input: enter a query term and scope as fields. "
            "The server runs the same validated Tier 2 workflow."
        ),
    },
    {
        "name": "catalog-language",
        "description": "Advanced JSON workflow and capability contracts.",
    },
    {
        "name": "deterministic-lookup",
        "description": "Model-free lookup against the active reviewed lexicon.",
    },
    {"name": "system", "description": "Health and readiness probes."},
]


def _trace_status(result: LexiconWorkflowResult | LexiconPreviewResult, step: str) -> str:
    statuses = [event.status for event in result.trace if event.step == step]
    return statuses[-1] if statuses else "NOT_RUN"


def _guided_response(
    request: GuidedLexiconRunRequest,
    result: LexiconWorkflowResult,
    container: CatalogLanguageApiContainer,
) -> GuidedRunResponse:
    proposed_ids = {
        decision.mapping_id for decision in result.decisions if decision.mapping_id is not None
    }
    mappings = (
        [mapping for mapping in result.candidate.mappings if mapping.mapping_id in proposed_ids]
        if result.candidate is not None
        else []
    )
    activation = result.activation
    review = result.review
    return GuidedRunResponse(
        run_id=result.run_id,
        status=result.status.value,
        input=GuidedInputSummary(
            term=request.term,
            locale=request.locale,
            category=request.taxonomy_node_id,
            attribute_id=request.attribute_id,
        ),
        output=GuidedExpansionOutput(
            normalized_query=normalize_surface_form(request.term, request.locale),
            expanded_to=[
                GuidedExpansion(
                    mapping_id=mapping.mapping_id,
                    source_form=mapping.surface_form,
                    normalized_form=mapping.normalized_form,
                    target_type=mapping.target_type,
                    target_id=mapping.target_id,
                    mapping_kind=mapping.mapping_kind,
                    direction=mapping.direction,
                    expansion_action=mapping.expansion_action,
                    scope=mapping.scope,
                    evidence_band=mapping.evidence_band,
                )
                for mapping in mappings
            ],
        ),
        model=GuidedModelSummary(
            mode=container.mode,
            active=container.mode == "gemma",
            proposer_status=_trace_status(result, "propose_canonical_mapping"),
            critic_status=_trace_status(result, "critique_mapping"),
        ),
        gates=GuidedGateSummary(
            candidate_version=(result.candidate.candidate_version if result.candidate else None),
            validation=_trace_status(result, "validate_mapping"),
            regression=_trace_status(result, "run_lexicon_regression"),
            shadow=_trace_status(result, "shadow_evaluate_lexicon"),
            review=(
                "APPROVED"
                if review is not None and review.approved
                else "REJECTED"
                if review is not None
                else "NOT_RUN"
            ),
            activation=_trace_status(result, "activate_lexicon_version"),
            activated=activation.activated if activation is not None else False,
            active_lexicon_version=(
                activation.active_lexicon_version if activation is not None else None
            ),
        ),
        warnings=list(result.warnings),
    )


def _guided_preview_response(
    request: GuidedLexiconRunRequest,
    result: LexiconPreviewResult,
    container: CatalogLanguageApiContainer,
) -> GuidedRunResponse:
    mapping = result.mapping
    return GuidedRunResponse(
        run_id=result.run_id,
        status=result.status,
        input=GuidedInputSummary(
            term=request.term,
            locale=request.locale,
            category=request.taxonomy_node_id,
            attribute_id=request.attribute_id,
        ),
        output=GuidedExpansionOutput(
            normalized_query=normalize_surface_form(request.term, request.locale),
            expanded_to=(
                [
                    GuidedExpansion(
                        mapping_id=mapping.mapping_id,
                        source_form=mapping.surface_form,
                        normalized_form=mapping.normalized_form,
                        target_type=mapping.target_type,
                        target_id=mapping.target_id,
                        mapping_kind=mapping.mapping_kind,
                        direction=mapping.direction,
                        expansion_action=mapping.expansion_action,
                        scope=mapping.scope,
                        evidence_band=mapping.evidence_band,
                    )
                ]
                if mapping is not None
                else []
            ),
        ),
        model=GuidedModelSummary(
            mode=container.mode,
            active=container.mode == "gemma",
            proposer_status=(
                result.proposer.status.value if result.proposer is not None else "NOT_RUN"
            ),
            critic_status="SKIPPED_PREVIEW",
        ),
        gates=GuidedGateSummary(
            candidate_version=None,
            validation=_trace_status(result, "validate_mapping"),
            regression="SKIPPED_PREVIEW",
            shadow="SKIPPED_PREVIEW",
            review="SKIPPED_PREVIEW",
            activation="SKIPPED_PREVIEW",
            activated=False,
            active_lexicon_version=None,
        ),
        warnings=list(result.warnings),
    )


def get_container(request: Request) -> CatalogLanguageApiContainer:
    return request.app.state.catalog_language


catalog_language_container_dependency = Depends(get_container)


def create_app(container: CatalogLanguageApiContainer | None = None) -> FastAPI:
    """Create an app with injected adapters or the real Gemma default."""

    selected_container = container or create_gemma_container()
    app = FastAPI(
        title="FK GRiD Catalog Language API",
        summary="Swagger-accessible Tier 2 evidence-driven lexicon operations",
        description=(
            "Offline/admin-facing API for the Catalog Language Tier 2 workflow. "
            "The default app uses Gemma for proposer and critic calls; production callers "
            "must inject database, retrieval, review, and activation adapters."
        ),
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        openapi_tags=OPENAPI_TAGS,
        swagger_ui_parameters={
            "defaultModelsExpandDepth": -1,
            "displayRequestDuration": True,
            "docExpansion": "list",
            "filter": True,
            "tryItOutEnabled": True,
        },
    )
    app.state.catalog_language = selected_container
    app.mount("/demo/static", StaticFiles(directory=DEMO_UI_DIR), name="demo-static")

    def close_model_client() -> None:
        close = getattr(selected_container.workflow.model, "close", None)
        if callable(close):
            close()

    app.router.add_event_handler("shutdown", close_model_client)

    @app.get("/demo", include_in_schema=False)
    def demo() -> FileResponse:
        """Serve the human-friendly live demo without changing workflow behavior."""

        return FileResponse(DEMO_UI_DIR / "demo.html", media_type="text/html")

    @app.get("/health", response_model=ApiStatus, tags=["system"])
    def health() -> ApiStatus:
        return ApiStatus(
            service="fkgrid-catalog-language",
            status="ok",
            mode=selected_container.mode,
            ready=True,
            detail="process is serving requests",
        )

    @app.get("/ready", response_model=ApiStatus, tags=["system"])
    def ready() -> ApiStatus:
        is_ready, detail = selected_container.check_readiness()
        return ApiStatus(
            service="fkgrid-catalog-language",
            status="ready" if is_ready else "not_ready",
            mode=selected_container.mode,
            ready=is_ready,
            detail=detail,
        )

    @app.get(
        "/api/v1/catalog-language/capabilities",
        response_model=CapabilitiesResponse,
        tags=["catalog-language"],
    )
    def capabilities() -> CapabilitiesResponse:
        return CapabilitiesResponse(
            tier="TIER_2",
            capabilities=sorted(CATALOG_LANGUAGE_CAPABILITIES),
            forbidden_capabilities=sorted(CATALOG_LANGUAGE_FORBIDDEN_CAPABILITIES),
        )

    @app.post(
        "/api/v1/catalog-language/tier2/guided-run",
        response_model=GuidedRunResponse,
        status_code=status.HTTP_200_OK,
        tags=["guided-demo"],
        summary="Guided run — enter a query term without editing JSON",
        description=(
            "Enter a shopper-language term, locale, and optional category/attribute in the "
            "Swagger parameter fields. The input becomes bounded demo evidence and then flows "
            "through the same Tier 2 proposer, critic, deterministic validation, regression, "
            "review, and activation workflow as the advanced JSON route. The default app calls "
            "the configured Gemma model; local fixture adapters stand in for the future DB "
            "and artifact owners."
        ),
        responses={
            status.HTTP_200_OK: {
                "description": "Compact guided result with the query expansion and workflow gates.",
                "content": {"application/json": {"example": GUIDED_RESPONSE_EXAMPLE}},
            }
        },
    )
    def run_guided(
        term: Annotated[
            str,
            Query(
                min_length=1,
                max_length=256,
                description="The shopper-language term or phrase to expand.",
                examples=["sneakers", "water proof shoes"],
            ),
        ],
        locale: Annotated[
            str,
            Query(
                min_length=2,
                max_length=32,
                description="Locale scope for the proposal.",
                examples=["en-IN"],
            ),
        ] = "en-IN",
        category: Annotated[
            str,
            Query(
                min_length=1,
                max_length=128,
                description="Canonical taxonomy scope. Use footwear for the included demo catalog.",
                examples=["footwear"],
            ),
        ] = "footwear",
        attribute_id: Annotated[
            str | None,
            Query(
                max_length=128,
                description="Optional canonical attribute scope; leave blank when not needed.",
            ),
        ] = None,
        proposer_deadline_ms: Annotated[
            int,
            Query(
                ge=1,
                le=CATALOG_LANGUAGE_MODEL_MAX_DEADLINE_MS,
                description="Remote Gemma proposer budget. 30,000 is the guided default.",
            ),
        ] = CATALOG_LANGUAGE_MODEL_MAX_DEADLINE_MS,
        critic_deadline_ms: Annotated[
            int,
            Query(
                ge=1,
                le=CATALOG_LANGUAGE_MODEL_MAX_DEADLINE_MS,
                description="Remote Gemma critic budget. 30,000 is the guided default.",
            ),
        ] = CATALOG_LANGUAGE_MODEL_MAX_DEADLINE_MS,
        container: CatalogLanguageApiContainer = catalog_language_container_dependency,
    ) -> GuidedRunResponse:
        is_ready, detail = container.check_readiness()
        if not is_ready:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=detail,
            )
        if not term.strip() or not category.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="term and category must contain non-whitespace text",
            )
        request = GuidedLexiconRunRequest(
            run_id=f"guided-{uuid4().hex}",
            term=term.strip(),
            locale=locale.strip(),
            taxonomy_node_id=category.strip() or None,
            attribute_id=attribute_id.strip() if attribute_id and attribute_id.strip() else None,
            proposer_deadline_ms=proposer_deadline_ms,
            critic_deadline_ms=critic_deadline_ms,
        )
        try:
            result = container.run_guided(request)
            return _guided_response(request, result, container)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="guided catalog-language workflow failed safely; inspect server telemetry",
            ) from exc

    @app.post(
        "/api/v1/catalog-language/tier2/guided-preview",
        response_model=GuidedRunResponse,
        status_code=status.HTTP_200_OK,
        tags=["guided-demo"],
        summary="Fast preview — one proposer call, never activates",
        description=(
            "Runs the live proposer and deterministic target validation only. The critic, "
            "regression, shadow, review, and activation steps are intentionally skipped; "
            "this route can never change the active lexicon. Use the full guided-run route "
            "for a complete Tier 2 result."
        ),
    )
    def run_guided_preview(
        term: Annotated[
            str,
            Query(
                min_length=1,
                max_length=256,
                description="The shopper-language term or phrase to preview.",
                examples=["sneakers", "water proof shoes"],
            ),
        ],
        locale: Annotated[
            str,
            Query(min_length=2, max_length=32, description="Locale scope for the preview."),
        ] = "en-IN",
        category: Annotated[
            str,
            Query(
                min_length=1,
                max_length=128,
                description="Canonical taxonomy scope. Use footwear for the demo catalog.",
            ),
        ] = "footwear",
        attribute_id: Annotated[
            str | None,
            Query(max_length=128, description="Optional canonical attribute scope."),
        ] = None,
        proposer_deadline_ms: Annotated[
            int,
            Query(
                ge=1,
                le=CATALOG_LANGUAGE_MODEL_MAX_DEADLINE_MS,
                description="Remote Gemma proposer budget for the fast preview.",
            ),
        ] = CATALOG_LANGUAGE_DEFAULT_MODEL_DEADLINE_MS,
        container: CatalogLanguageApiContainer = catalog_language_container_dependency,
    ) -> GuidedRunResponse:
        is_ready, detail = container.check_readiness()
        if not is_ready:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=detail,
            )
        if not term.strip() or not category.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="term and category must contain non-whitespace text",
            )
        request = GuidedLexiconRunRequest(
            run_id=f"preview-{uuid4().hex}",
            term=term.strip(),
            locale=locale.strip(),
            taxonomy_node_id=category.strip() or None,
            attribute_id=attribute_id.strip() if attribute_id and attribute_id.strip() else None,
            proposer_deadline_ms=proposer_deadline_ms,
            critic_deadline_ms=CATALOG_LANGUAGE_DEFAULT_MODEL_DEADLINE_MS,
        )
        try:
            result = container.run_guided_preview(request)
            return _guided_preview_response(request, result, container)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="guided preview failed safely; inspect server telemetry",
            ) from exc

    @app.post(
        "/api/v1/catalog-language/tier2/runs",
        response_model=LexiconWorkflowResult,
        status_code=status.HTTP_200_OK,
        tags=["catalog-language"],
        summary="Run one bounded Tier 2 proposal/review/activation attempt",
        description=(
            "Runs the existing explicit workflow. It aggregates privacy-safe evidence, "
            "calls the proposer and critic ports, validates mappings, runs regression "
            "and shadow checks, then uses review and activation ports."
        ),
    )
    def run_tier2(
        request: Annotated[
            LexiconWorkflowApiRequest,
            Body(
                ...,
                openapi_examples={
                    "gemma": {
                        "summary": "Gemma local run",
                        "description": (
                            "Requires the configured Gemma model, default DeepInfra "
                            "google/gemma-4-26B-A4B-it, "
                            "to be available from the provider."
                        ),
                        "value": WORKFLOW_EXAMPLE,
                    }
                },
            ),
        ],
        container: CatalogLanguageApiContainer = catalog_language_container_dependency,
    ) -> LexiconWorkflowResult:
        is_ready, detail = container.check_readiness()
        if not is_ready:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=detail,
            )
        try:
            return container.workflow.run(request.to_domain())
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="catalog-language workflow failed safely; inspect server telemetry",
            ) from exc

    @app.post(
        "/api/v1/catalog-language/lookup",
        response_model=LexiconLookupResult,
        status_code=status.HTTP_200_OK,
        tags=["deterministic-lookup"],
        summary="Resolve one term through the deterministic active lexicon",
        description=(
            "Model-free bounded lookup. It never calls the proposer, reads history, "
            "creates hard filters, or mutates the active lexicon."
        ),
    )
    def lookup(
        request: Annotated[
            LexiconLookupRequest,
            Body(
                ...,
                openapi_examples={
                    "demo": {
                        "summary": "Resolve trainers in footwear",
                        "value": LOOKUP_EXAMPLE,
                    }
                },
            ),
        ],
        container: CatalogLanguageApiContainer = catalog_language_container_dependency,
    ) -> LexiconLookupResult:
        return container.lookup.lookup_expansions(request)

    return app


app = create_app()
