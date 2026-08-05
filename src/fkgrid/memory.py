"""In-process session and chat memory.

Holds recent chat history and the shopper's last search results, so
references like "the first one" and REFINE constraints can be resolved.
Not persisted across process restarts by design - this is session memory,
not a durable store.
"""

from __future__ import annotations

import threading

from .contracts import ChatTurn, Role, SearchEntry, SessionState

_MAX_HISTORY_TURNS = 10

_sessions: dict[str, SessionState] = {}
_lock = threading.Lock()


def create_session(session_id: str) -> SessionState:
    with _lock:
        state = SessionState(session_id=session_id)
        _sessions[session_id] = state
        return state


def get_session(session_id: str) -> SessionState | None:
    with _lock:
        return _sessions.get(session_id)


def append_turn(session_id: str, role: Role, content: str) -> None:
    with _lock:
        state = _sessions[session_id]
        state.chat_history.append(ChatTurn(role=role, content=content))
        state.chat_history = state.chat_history[-_MAX_HISTORY_TURNS:]


def set_last_results(session_id: str, entries: list[SearchEntry], reranker_request) -> None:
    with _lock:
        state = _sessions[session_id]
        state.last_results = entries
        state.last_reranker_request = reranker_request


def bump_cart_version(session_id: str, new_version: int) -> None:
    with _lock:
        _sessions[session_id].cart_version = new_version
