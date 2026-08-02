"""Bounded, typed shopper-turn orchestration and provider integration seams."""

from .contracts import (
    Action,
    CompatibilityTuple,
    TerminalState,
    TurnRequest,
    TurnResult,
)
from .capabilities import SHOPPER_CAPABILITIES
from .gateway import FakeModelGateway, Qwen36ModelAdapter, StructuredModelGateway
from .orchestrator import TurnOrchestrator

__all__ = [
    "Action",
    "CompatibilityTuple",
    "FakeModelGateway",
    "Qwen36ModelAdapter",
    "SHOPPER_CAPABILITIES",
    "StructuredModelGateway",
    "TerminalState",
    "TurnOrchestrator",
    "TurnRequest",
    "TurnResult",
]
