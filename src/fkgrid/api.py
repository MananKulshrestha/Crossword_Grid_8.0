"""FastAPI app: create a session, send turns. No fixtures, live services only."""

from __future__ import annotations

import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import memory, orchestrator
from .contracts import TurnRequest, TurnResult, TurnStatus

app = FastAPI(title="fkgrid chat")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreateSessionResponse(BaseModel):
    session_id: str


class TurnBody(BaseModel):
    message: str
    mode: str = "deep"


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post("/v1/sessions", response_model=CreateSessionResponse)
def create_session() -> CreateSessionResponse:
    session_id = uuid.uuid4().hex
    memory.create_session(session_id)
    return CreateSessionResponse(session_id=session_id)


@app.get("/v1/sessions/{session_id}")
def get_session(session_id: str):
    state = memory.get_session(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="SESSION_NOT_FOUND")
    return state


@app.post("/v1/sessions/{session_id}/turns", response_model=TurnResult)
def post_turn(session_id: str, body: TurnBody) -> TurnResult:
    if memory.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="SESSION_NOT_FOUND")
    mode = body.mode if body.mode in ("fast", "constrain") else "deep"
    request = TurnRequest(session_id=session_id, message=body.message, mode=mode)
    result = orchestrator.handle_turn(request)
    if result.status == TurnStatus.ERROR:
        raise HTTPException(status_code=502, detail={"message": result.message, "error_code": result.error_code})
    return result
