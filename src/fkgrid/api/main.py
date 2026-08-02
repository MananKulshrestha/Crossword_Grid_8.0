"""FastAPI delivery layer over the existing Tier 1 Quality Sentinel workflow.

This module owns HTTP validation, status mapping, and dependency composition
only. Qualification, evidence bounds, assessment validation, routing, and case
transitions remain in the existing workflow/application layers.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import Body, Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import ConfigDict, Field

from fkgrid.adapters.model.fake_quality_classifier import DeterministicQualityClassifier
from fkgrid.adapters.model.gemma_quality_classifier import Gemma4QualityClassifier
from fkgrid.adapters.quality.in_memory import (
    InMemoryAuditSink,
    InMemoryCatalogSnapshotReader,
    InMemoryQualityPersistence,
    InMemoryReviewQueue,
    StaticPolicyProvider,
    SystemClock,
    UuidIdGenerator,
)
from fkgrid.application.quality_service import default_quality_policy
from fkgrid.domain.quality import (
    CaseDecision,
    CaseEvent,
    CatalogFact,
    CatalogSnapshot,
    QualityCase,
    QualityEntityBinding,
    QualityPolicy,
    QualitySignalInput,
    QualityWorkflowResult,
    StrictModel,
)
from fkgrid.ports.quality import (
    QualityClassifier,
    QualityPersistence,
    QualityPolicyProvider,
    ReviewQueue,
)
from fkgrid.workflows.quality_sentinel import QualitySentinelWorkflow


class HealthResponse(StrictModel):
    status: Literal["ok", "ready"]
    service: str = "fkgrid-quality-sentinel"
    mode: Literal["demo-in-memory"] = "demo-in-memory"


class ToolInventoryResponse(StrictModel):
    allowed_tools: list[str]
    forbidden_capabilities: list[str]
    tool_version: str


class QualityDecisionRequest(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=False)

    decision: CaseDecision
    reviewer_role: Literal["QUALITY_REVIEWER"] = "QUALITY_REVIEWER"
    reviewer_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=1000)
    follow_up_recommendation: str | None = Field(default=None, max_length=500)


class CaseLifecycleRequest(StrictModel):
    action: Literal["CLOSE", "REOPEN"]
    actor_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=500)


class QualitySignalRequest(QualitySignalInput):
    """JSON-friendly HTTP model revalidated into the strict domain input."""

    model_config = ConfigDict(extra="forbid", strict=False)

    def to_domain(self) -> QualitySignalInput:
        return QualitySignalInput.model_validate_json(self.model_dump_json())


class QualityApiState:
    """Composition root for HTTP handlers; production adapters can replace each port."""

    def __init__(
        self,
        workflow: QualitySentinelWorkflow,
        persistence: QualityPersistence,
        queue: ReviewQueue,
        policy_provider: QualityPolicyProvider,
        classifier_name: str,
    ) -> None:
        self.workflow = workflow
        self.persistence = persistence
        self.queue = queue
        self.policy_provider = policy_provider
        self.classifier_name = classifier_name


def _demo_snapshot() -> CatalogSnapshot:
    binding = QualityEntityBinding(
        product_id="product_demo",
        sku_id="sku_demo",
        listing_id="listing_demo",
        catalog_version="catalog_demo_v1",
    )
    return CatalogSnapshot(
        snapshot_id="snapshot_demo_v1",
        binding=binding,
        captured_at=datetime(2026, 1, 1, tzinfo=UTC),
        facts=[
            CatalogFact(
                field_path="attributes.material",
                value="cotton",
                truth_status="VERIFIED",
                evidence_id="catalog-fact-demo-material",
            ),
            CatalogFact(
                field_path="title",
                value="Demo cotton shirt",
                truth_status="VERIFIED",
                evidence_id="catalog-fact-demo-title",
            ),
        ],
        source_checksum="c" * 64,
    )


def build_demo_state(*, use_gemma: bool = False) -> QualityApiState:
    """Build an in-memory composition for Swagger and local contract testing."""

    persistence = InMemoryQualityPersistence()
    queue = InMemoryReviewQueue()
    policy_provider = StaticPolicyProvider(default_quality_policy())
    classifier: QualityClassifier
    if use_gemma:
        classifier = Gemma4QualityClassifier.from_environment()
        classifier_name = classifier.model_alias
    else:
        classifier = DeterministicQualityClassifier()
        classifier_name = "deterministic-demo"
    workflow = QualitySentinelWorkflow(
        persistence=persistence,
        policy_provider=policy_provider,
        catalog=InMemoryCatalogSnapshotReader([_demo_snapshot()]),
        classifier=classifier,
        review_queue=queue,
        clock=SystemClock(),
        ids=UuidIdGenerator(),
        audit=InMemoryAuditSink(),
    )
    return QualityApiState(workflow, persistence, queue, policy_provider, classifier_name)


def get_api_state(request: Request) -> QualityApiState:
    return request.app.state.quality_api


ApiStateDependency = Annotated[QualityApiState, Depends(get_api_state)]


SignalBody = Annotated[
    QualitySignalRequest,
    Body(
        ...,
        openapi_examples={
            "single-report": {
                "summary": "One report (stored, usually not yet qualified)",
                "value": {
                    "signal_id": "signal_demo_1",
                    "idempotency_key": "idem_demo_1",
                    "source_type": "EXPLICIT_REPORT",
                    "signal_type": "OBJECTIVE_INCORRECT",
                    "severity": "MEDIUM",
                    "binding": {
                        "product_id": "product_demo",
                        "sku_id": "sku_demo",
                        "listing_id": "listing_demo",
                        "catalog_version": "catalog_demo_v1",
                    },
                    "occurred_at": "2026-07-27T10:00:00Z",
                    "received_at": "2026-07-27T10:01:00Z",
                    "rating": 2,
                    "objective_issue_flag": True,
                    "text": "The listed material does not match the delivered shirt.",
                    "asset_refs": [],
                    "source_reference": {
                        "source_id": "report_demo_1",
                        "source_checksum": "a" * 64,
                        "source_system": "swagger-demo",
                    },
                    "retention_class": "PROTOTYPE_REDACTED",
                    "reporter_group_id": "reporter_demo_1",
                    "correlation_group_id": None,
                    "source_class": "REPORT",
                    "verified_direct_system_signal": False,
                },
            }
        },
    ),
]


def _state_from_environment() -> QualityApiState:
    use_gemma = os.environ.get("FKGRID_USE_GEMMA", "false").casefold() in {
        "1",
        "true",
        "yes",
    }
    return build_demo_state(use_gemma=use_gemma)


def _case_or_404(state: QualityApiState, case_id: str) -> QualityCase:
    case = state.persistence.get_case(case_id)
    if case is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "CASE_NOT_FOUND", "message": "Quality case not found."},
        )
    return case


def _workflow_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PermissionError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "REVIEWER_NOT_AUTHORIZED",
                "message": "Only QUALITY_REVIEWER may decide cases.",
            },
        )
    if isinstance(exc, ValueError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "QUALITY_REQUEST_INVALID", "message": str(exc)},
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={"code": "QUALITY_WORKFLOW_ERROR", "message": "Quality workflow failed safely."},
    )


def create_app(state: QualityApiState | None = None) -> FastAPI:
    """Create an app with injectable ports while keeping a ready-to-run demo app."""

    app = FastAPI(
        title="FK GRiD Catalog Quality Sentinel",
        summary="Tier 1 review-only catalog quality workflow",
        description=(
            "Swagger endpoints for submitting redacted quality signals, inspecting cases, "
            "recording reviewer decisions, and closing or reopening cases. "
            "This prototype never publishes, suppresses, ranks, refunds, or contacts sellers."
        ),
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    app.state.quality_api = state or _state_from_environment()

    @app.get("/healthz", response_model=HealthResponse, tags=["system"])
    def healthz() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get("/readyz", response_model=HealthResponse, tags=["system"])
    def readyz(api_state: ApiStateDependency) -> HealthResponse:
        if api_state.workflow is None:  # pragma: no cover - defensive composition guard
            raise HTTPException(status_code=503, detail="Quality workflow is not configured.")
        return HealthResponse(status="ready")

    @app.get("/api/v1/quality/tools", response_model=ToolInventoryResponse, tags=["system"])
    def tools(api_state: ApiStateDependency) -> ToolInventoryResponse:
        registry = api_state.workflow.registry
        return ToolInventoryResponse(
            allowed_tools=sorted(tool.value for tool in registry.allowed_tools),
            forbidden_capabilities=sorted(registry.forbidden_capabilities),
            tool_version=api_state.workflow.tool_version,
        )

    @app.get("/api/v1/quality/policy", response_model=QualityPolicy, tags=["system"])
    def policy(api_state: ApiStateDependency) -> QualityPolicy:
        return api_state.policy_provider.active_policy()

    @app.post(
        "/api/v1/quality/signals",
        response_model=QualityWorkflowResult,
        status_code=status.HTTP_200_OK,
        tags=["quality workflow"],
        summary="Submit one quality signal to the complete Tier 1 workflow",
        description=(
            "The workflow redacts untrusted text, groups by exact identity and time bucket, "
            "qualifies deterministically, assembles bounded evidence, classifies once, and "
            "routes only to review. Repeat the same idempotency_key to observe replay behavior."
        ),
        responses={
            409: {
                "model": QualityWorkflowResult,
                "description": "The idempotency key was reused for a different request.",
            }
        },
    )
    def submit_signal(
        signal_request: SignalBody,
        api_state: ApiStateDependency,
    ) -> QualityWorkflowResult | JSONResponse:
        signal_input = signal_request.to_domain()
        result = api_state.workflow.run(signal_input)
        if result.outcome == "CONFLICT":
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content=result.model_dump(mode="json"),
            )
        return result

    @app.get(
        "/api/v1/quality/cases/{case_id}",
        response_model=QualityCase,
        tags=["review"],
        summary="Get one quality case",
        responses={404: {"description": "Quality case not found."}},
    )
    def get_case(case_id: str, api_state: ApiStateDependency) -> QualityCase:
        return _case_or_404(api_state, case_id)

    @app.get(
        "/api/v1/quality/cases/{case_id}/events",
        response_model=list[CaseEvent],
        tags=["review"],
        summary="Get append-only case events",
        responses={404: {"description": "Quality case not found."}},
    )
    def get_case_events(case_id: str, api_state: ApiStateDependency) -> list[CaseEvent]:
        _case_or_404(api_state, case_id)
        return api_state.persistence.list_case_events(case_id)

    @app.post(
        "/api/v1/quality/cases/{case_id}/decision",
        response_model=QualityCase,
        tags=["review"],
        summary="Record a human reviewer decision",
        responses={
            403: {"description": "Reviewer role is not authorized."},
            404: {"description": "Quality case not found."},
            422: {"description": "Case is not in a reviewable state."},
        },
    )
    def decide_case(
        case_id: str,
        request_body: QualityDecisionRequest,
        api_state: ApiStateDependency,
    ) -> QualityCase:
        _case_or_404(api_state, case_id)
        try:
            return api_state.workflow.record_human_case_decision(
                case_id,
                request_body.decision.value,
                request_body.reviewer_role,
                request_body.reviewer_id,
                request_body.reason,
                request_body.follow_up_recommendation,
            )
        except Exception as exc:
            raise _workflow_error(exc) from exc

    @app.post(
        "/api/v1/quality/cases/{case_id}/lifecycle",
        response_model=QualityCase,
        tags=["review"],
        summary="Close or reopen a case",
        responses={404: {"description": "Quality case not found."}},
    )
    def lifecycle_case(
        case_id: str,
        request_body: CaseLifecycleRequest,
        api_state: ApiStateDependency,
    ) -> QualityCase:
        _case_or_404(api_state, case_id)
        try:
            return api_state.workflow.close_or_reopen_case(
                case_id,
                request_body.action,
                request_body.actor_id,
                request_body.reason,
            )
        except Exception as exc:
            raise _workflow_error(exc) from exc

    @app.get(
        "/api/v1/quality/demo-state",
        response_model=dict[str, object],
        tags=["system"],
        summary="Inspect safe local demo metadata",
        description=(
            "Returns only local mode metadata; no secrets, raw text, or model prompts are returned."
        ),
    )
    def demo_state(api_state: ApiStateDependency) -> dict[str, object]:
        return {
            "mode": "demo-in-memory",
            "classifier": api_state.classifier_name,
            "case_count": len(getattr(api_state.persistence, "cases_by_id", {})),
            "queued_route_count": len(getattr(api_state.queue, "routes", [])),
        }

    return app


app = create_app()


if __name__ == "__main__":  # pragma: no cover - manual local entry point
    import uvicorn

    uvicorn.run("fkgrid.api.main:app", host="127.0.0.1", port=8000, reload=False)
