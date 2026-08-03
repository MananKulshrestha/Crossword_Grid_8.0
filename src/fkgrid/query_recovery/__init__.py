"""Confidence-gated Tier 2 query recovery.

The public surface is intentionally small. Import contracts and ports from their
modules so the package can be embedded in the shopper orchestrator without
coupling it to a database, provider SDK, or retrieval implementation.
"""

from .domain import (
    ComparatorDecision,
    RecoveryOutcome,
    RecoveryPlan,
    RecoveryRequest,
    RecoveryResponse,
)
from .workflow import QueryRecoveryWorkflow

__all__ = [
    "ComparatorDecision",
    "QueryRecoveryWorkflow",
    "RecoveryOutcome",
    "RecoveryPlan",
    "RecoveryRequest",
    "RecoveryResponse",
]
