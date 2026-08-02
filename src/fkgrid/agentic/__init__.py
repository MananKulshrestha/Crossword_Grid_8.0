"""Bounded, typed shopper-turn orchestration and provider integration seams."""

from .contracts import (
    Action,
    CompatibilityTuple,
    TerminalState,
    TurnRequest,
    TurnResult,
)
from .capabilities import SHOPPER_CAPABILITIES
from .gateway import FakeModelGateway, Gemma4ModelAdapter, StructuredModelGateway
from .orchestrator import TurnOrchestrator

__all__ = [
    "Action",
    "CompatibilityTuple",
    "FakeModelGateway",
    "Gemma4ModelAdapter",
    "SHOPPER_CAPABILITIES",
    "StructuredModelGateway",
    "TerminalState",
    "TurnOrchestrator",
    "TurnRequest",
    "TurnResult",
]
