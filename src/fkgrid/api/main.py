"""FastAPI routes for exercising the Catalog Language Tier 2 contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import uuid4

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from fkgrid.api.container import CatalogLanguageApiContainer, create_gemma_container
from fkgrid.domain.catalog_language import (
    CATALOG_LANGUAGE_DEFAULT_MODEL_DEADLINE_MS,
    CATALOG_LANGUAGE_MODEL_MAX_DEADLINE_MS,
    EvidenceWindow,
    GuidedLexiconRunRequest,
    LexiconCompatibility,
    LexiconLookupRequest,
    LexiconLookupResult,
    LexiconWorkflowRequest,
    LexiconWorkflowResult,
)
from fkgrid.workflows.catalog_language import (
    CATALOG_LANGUAGE_CAPABILITIES,
    CATALOG_LANGUAGE_FORBIDDEN_CAPABILITIES,
)


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
        response_model=LexiconWorkflowResult,
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
    ) -> LexiconWorkflowResult:
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
            return container.run_guided(request)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="guided catalog-language workflow failed safely; inspect server telemetry",
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
