"""FastAPI/Swagger delivery layer around QueryRecoveryWorkflow."""

from __future__ import annotations

from typing import Literal

from fastapi import Body, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from ..query_recovery.domain import RecoveryRequest, RecoveryResponse, StrictModel
from .demo import (
    DemoRecoveryInput,
    DemoRecoveryResponse,
    build_demo_recovery_request,
    build_demo_recovery_response,
)
from .demo_ui import DEMO_PAGE
from .dependencies import RecoveryApiDependencies, build_default_dependencies
from .examples import build_example_request
from .schemas import ApiRecoveryRequest


class HealthResponse(StrictModel):
    status: Literal["ok", "not_ready"]
    service: str
    mode: str
    planner_model: str
    data_mode: str


class ReadinessResponse(StrictModel):
    ready: bool
    reasons: list[str]
    mode: str
    data_mode: str


class CapabilitiesResponse(StrictModel):
    service: str
    workflow: str
    mode: str
    planner_model: str
    data_mode: str
    endpoints: list[str]


_RUN_TURN_BODY = Body(
    ...,
    openapi_examples={
        "demo_formal_wear_recovery": {
            "summary": "Direct formal-wear match followed by approved descendants",
            "description": "Copy the response from GET /v1/query-recovery/example.",
            "value": build_example_request().model_dump(mode="json"),
        }
    },
)


def create_app(dependencies: RecoveryApiDependencies | None = None) -> FastAPI:
    resolved = dependencies or build_default_dependencies()
    application = FastAPI(
        title="FK GRiD Query Recovery API",
        summary="Swagger-accessible API for the confidence-gated recovery workflow.",
        description=(
            "Thin delivery layer over the existing QueryRecoveryWorkflow. "
            "The API validates transport input and delegates all recovery policy "
            "to the typed workflow; it does not own catalog truth or database migrations."
        ),
        version="0.2.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    application.state.recovery_dependencies = resolved

    @application.get("/healthz", tags=["system"], response_model=HealthResponse)
    def healthz(request: Request) -> HealthResponse:
        current: RecoveryApiDependencies = request.app.state.recovery_dependencies
        return HealthResponse(
            status="ok" if current.ready else "not_ready",
            service="fkgrid-query-recovery-api",
            mode=current.mode,
            planner_model=current.planner_model,
            data_mode=current.data_mode,
        )

    @application.get("/readyz", tags=["system"], response_model=ReadinessResponse)
    def readyz(request: Request) -> ReadinessResponse:
        current: RecoveryApiDependencies = request.app.state.recovery_dependencies
        return ReadinessResponse(
            ready=current.ready,
            reasons=list(current.readiness_reasons),
            mode=current.mode,
            data_mode=current.data_mode,
        )

    @application.get(
        "/v1/query-recovery/capabilities",
        tags=["query-recovery"],
        response_model=CapabilitiesResponse,
    )
    def capabilities(request: Request) -> CapabilitiesResponse:
        current: RecoveryApiDependencies = request.app.state.recovery_dependencies
        return CapabilitiesResponse(
            service="fkgrid-query-recovery-api",
            workflow="QueryRecoveryWorkflow",
            mode=current.mode,
            planner_model=current.planner_model,
            data_mode=current.data_mode,
            endpoints=[
                "GET /healthz",
                "GET /readyz",
                "GET /v1/query-recovery/example",
                "POST /v1/query-recovery/turn",
                "GET /demo",
                "POST /v1/query-recovery/demo-turn",
            ],
        )

    @application.get(
        "/demo",
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    def demo_page() -> HTMLResponse:
        """Serve the small actathon input surface without changing API contracts."""

        return HTMLResponse(DEMO_PAGE)

    @application.post(
        "/v1/query-recovery/demo-turn",
        tags=["actathon-demo"],
        response_model=DemoRecoveryResponse,
        response_model_exclude_none=False,
        status_code=status.HTTP_200_OK,
        summary="Run recovery from a simple query and three visible filters",
        description=(
            "Actathon-friendly input adapter. It translates the query and a few "
            "explicit mock filters into the canonical RecoveryRequest, then runs "
            "the same bounded QueryRecoveryWorkflow used by the full API."
        ),
    )
    def run_demo_turn(
        request: Request,
        payload: DemoRecoveryInput,
    ) -> DemoRecoveryResponse:
        current: RecoveryApiDependencies = request.app.state.recovery_dependencies
        if not current.ready:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "RECOVERY_API_NOT_READY"},
            )
        try:
            canonical_request = build_demo_recovery_request(payload)
            result = current.workflow.run(canonical_request)
            return build_demo_recovery_response(payload, result)
        except Exception as exc:  # noqa: BLE001 - sanitize the delivery boundary
            del exc
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "RECOVERY_UNAVAILABLE"},
            ) from None

    @application.get(
        "/v1/query-recovery/example",
        tags=["query-recovery"],
        response_model=RecoveryRequest,
        summary="Get a ready-to-submit Swagger example request",
    )
    def example_request(request: Request) -> RecoveryRequest:
        del request
        return build_example_request()

    @application.post(
        "/v1/query-recovery/turn",
        tags=["query-recovery"],
        response_model=RecoveryResponse,
        response_model_exclude_none=False,
        status_code=status.HTTP_200_OK,
        summary="Run one confidence-gated query-recovery turn",
        description=(
            "Accepts the canonical baseline/gate/compatibility snapshot and runs "
            "the existing bounded workflow. Safe outcomes such as clarification, "
            "baseline preservation, and no-safe recovery are returned as 200 responses."
        ),
    )
    def run_turn(
        request: Request,
        payload: ApiRecoveryRequest = _RUN_TURN_BODY,
    ) -> RecoveryResponse:
        current: RecoveryApiDependencies = request.app.state.recovery_dependencies
        if not current.ready:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "RECOVERY_API_NOT_READY"},
            )
        try:
            strict_payload = RecoveryRequest.model_validate_json(payload.model_dump_json())
            return current.workflow.run(strict_payload)
        except Exception as exc:  # noqa: BLE001 - sanitize the delivery boundary
            del exc
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "RECOVERY_UNAVAILABLE"},
            ) from None

    return application


app = create_app()

__all__ = ["app", "create_app"]
