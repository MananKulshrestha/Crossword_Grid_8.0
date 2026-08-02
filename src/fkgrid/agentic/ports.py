"""Integration ports owned by the shopper-facing orchestration layer.

No adapter or infrastructure dependency is imported here. Database, retrieval,
Qdrant, LightRAG, cart, research, and frontend teams can implement these ports
without changing the state machine or provider contracts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, Sequence

from .contracts import (
    Action,
    Availability,
    CartSnapshot,
    CommerceEligibility,
    CommitCommand,
    CommitResult,
    ConfidenceDecision,
    EnhancedQueryEnvelope,
    EnhancementOutput,
    IntentContextProjectionV1,
    IntentDeltaV1,
    MarkdownHandoff,
    ModelRequest,
    ModelResponse,
    OnlineSearchResult,
    ProductDetails,
    PublicTrace,
    QueryState,
    ReferenceDraft,
    ReferenceResolution,
    Reservation,
    SearchRequest,
    SearchResult,
    ShopperResponse,
    SuggestionSet,
    TerminalState,
    TurnRequest,
    TurnResult,
    TurnSnapshot,
    UpdateCartRequest,
    UpdateCartResult,
    ValidatedResearch,
    ResearchDecision,
    CompatibilityTuple,
    Comparison,
    GraphContextRequest,
    GraphContextResult,
    MemoryCandidate,
    PurchaseContext,
    SuggestionSelectionRequest,
    SuggestionSelectionResult,
)


class ClockPort(Protocol):
    def now_utc(self) -> datetime: ...

    def monotonic_ms(self) -> int: ...


class IdSourcePort(Protocol):
    def new_id(self, prefix: str) -> str: ...


class StructuredModelGateway(Protocol):
    def complete(self, request: ModelRequest) -> ModelResponse: ...


class SessionStatePort(Protocol):
    """Snapshot/reservation/commit seam; no ORM or transaction type crosses it."""

    def load_snapshot(self, session_id: str, expected_state_version: int) -> TurnSnapshot: ...

    def reserve(
        self,
        request: TurnRequest,
        request_hash: str,
        trace_id: str,
    ) -> Reservation: ...

    def commit(self, command: CommitCommand) -> CommitResult: ...

    def attach_final_response(self, response: ShopperResponse) -> None: ...


class QueryEnhancementPort(Protocol):
    def enhance(
        self,
        request: TurnRequest,
        snapshot: TurnSnapshot,
        deadline_ms: int,
    ) -> EnhancementOutput: ...


class MemorySnapshotPort(Protocol):
    def load_active(
        self,
        memory_profile_id: str,
        memory_version: int,
        limit: int,
    ) -> list[MemoryCandidate]: ...


class CommerceHistoryPort(Protocol):
    def load_verified(
        self,
        memory_profile_id: str,
        limit: int,
    ) -> list[PurchaseContext]: ...


class IntentSemanticValidatorPort(Protocol):
    def validate(
        self,
        intent: IntentDeltaV1,
        projection: IntentContextProjectionV1,
        snapshot: TurnSnapshot,
    ) -> list[str]: ...


class ReferenceResolverPort(Protocol):
    def resolve(
        self,
        session_id: str,
        references: Sequence[ReferenceDraft],
        action: Action,
        snapshot: TurnSnapshot,
    ) -> ReferenceResolution: ...


class CatalogSearchPort(Protocol):
    """Port for the future deterministic catalog/retrieval implementation."""

    def search(self, request: SearchRequest, deadline_ms: int) -> SearchResult: ...

    def get_details(self, binding: Any, compatibility: CompatibilityTuple, deadline_ms: int) -> ProductDetails: ...

    def compare(
        self,
        bindings: Sequence[Any],
        compatibility: CompatibilityTuple,
        deadline_ms: int,
    ) -> Comparison: ...

    def check_availability(self, binding: Any, compatibility: CompatibilityTuple, deadline_ms: int) -> Availability: ...

    def check_eligibility(self, binding: Any, purpose: str, deadline_ms: int) -> CommerceEligibility: ...


class RecoveryPort(Protocol):
    def assess(self, result: SearchResult, query_state: QueryState) -> ConfidenceDecision: ...

    def recover(
        self,
        result: SearchResult,
        query_state: QueryState,
        compatibility: CompatibilityTuple,
        deadline_ms: int,
    ) -> SearchResult: ...


class GraphContextPort(Protocol):
    """Optional advisory LightRAG/Graph RAG seam; never product truth authority."""

    def lookup(self, request: GraphContextRequest) -> GraphContextResult: ...


class CartPort(Protocol):
    def show_cart(self, session_id: str, known_cart_version: int, deadline_ms: int) -> CartSnapshot: ...

    def update_cart(self, request: UpdateCartRequest, deadline_ms: int) -> UpdateCartResult: ...


class ResearchPort(Protocol):
    def detect_need(
        self,
        intent: IntentDeltaV1,
        current_message: str,
        selected_entity_ids: Sequence[str],
    ) -> ResearchDecision: ...

    def search(self, decision: ResearchDecision, deadline_ms: int) -> OnlineSearchResult: ...

    def validate(
        self,
        decision: ResearchDecision,
        search_result: OnlineSearchResult,
        synthesis: Any | None,
    ) -> ValidatedResearch: ...


class SuggestionPort(Protocol):
    def build_and_store(
        self,
        response: ShopperResponse,
        snapshot: TurnSnapshot,
        trace_id: str,
        deadline_ms: int,
    ) -> SuggestionSet | None: ...

    def select(self, request: SuggestionSelectionRequest) -> SuggestionSelectionResult: ...


class MarkdownPipelinePort(Protocol):
    """Handoff only: another owner converts typed output to markdown."""

    def handoff(self, response: ShopperResponse, trace: PublicTrace) -> MarkdownHandoff: ...


class TraceSinkPort(Protocol):
    def append(self, event: Any) -> None: ...


class ModelTransportPort(Protocol):
    """Provider-neutral JSON transport used by the configured Gemma adapter."""

    def post_json(self, payload: dict[str, Any], timeout_ms: int) -> dict[str, Any]: ...
