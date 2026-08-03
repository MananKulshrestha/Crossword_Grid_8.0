"""Deterministic fake ports used to run the agentic pipeline before integrations exist."""

from __future__ import annotations

import hashlib
import base64
import hmac
import unicodedata
from datetime import datetime, timezone
from typing import Any, Sequence

from .contracts import (
    Action,
    ActiveResultBinding,
    Availability,
    CartItem,
    CartSummary,
    CartSnapshot,
    CommerceEligibility,
    CommitCommand,
    CommitResult,
    CompatibilityTuple,
    ConfidenceDecision,
    EnhancementOutput,
    EvidenceRef,
    Fact,
    FactScope,
    GraphContextRequest,
    GraphContextResult,
    EnhancementConflict,
    IntentDeltaV1,
    MarkdownHandoff,
    Money,
    OnlineSearchResult,
    ProductBinding,
    ProductDetails,
    ProjectedMemoryCandidate,
    ProjectedPurchaseContext,
    PurchaseContext,
    QueryState,
    ReferenceDraft,
    ReferenceResolution,
    Reservation,
    ResolvedReference,
    ResearchDecision,
    ResearchSource,
    SearchEntry,
    SearchRequest,
    SearchResult,
    ShopperResponse,
    Suggestion,
    SuggestionCandidate,
    SuggestionSet,
    SuggestionSelectionRequest,
    SuggestionSelectionResult,
    TerminalState,
    ToolStatus,
    TruthStatus,
    TurnRequest,
    TurnSnapshot,
    UpdateCartRequest,
    UpdateCartResult,
    ValidatedResearch,
)
from .ports import (
    CartPort,
    CatalogSearchPort,
    ClockPort,
    GraphContextPort,
    IdSourcePort,
    MarkdownPipelinePort,
    QueryEnhancementPort,
    MemorySnapshotPort,
    CommerceHistoryPort,
    RecoveryPort,
    ReferenceResolverPort,
    ResearchPort,
    SessionStatePort,
    SuggestionPort,
)
from .validation import canonical_hash, validate_research_synthesis


class FakeClock(ClockPort):
    def __init__(self, epoch_ms: int = 1_754_000_000_000) -> None:
        self.epoch_ms = epoch_ms
        self.monotonic = 0

    def now_utc(self) -> datetime:
        return datetime.fromtimestamp(self.epoch_ms / 1000, tz=timezone.utc)

    def monotonic_ms(self) -> int:
        return self.monotonic

    def advance(self, milliseconds: int) -> None:
        self.monotonic += milliseconds
        self.epoch_ms += milliseconds


class SequentialIds(IdSourcePort):
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def new_id(self, prefix: str) -> str:
        self.counts[prefix] = self.counts.get(prefix, 0) + 1
        return f"{prefix}_{self.counts[prefix]}"


class FakeTraceSink:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def append(self, event: Any) -> None:
        self.events.append(event)


class InMemorySessionState(SessionStatePort):
    """Reservation/snapshot fake, deliberately not a database implementation."""

    def __init__(self, snapshot: TurnSnapshot) -> None:
        self.snapshot = snapshot
        self.reservations: dict[str, tuple[str, Reservation]] = {}
        self.committed: dict[str, ShopperResponse] = {}
        self.reservation_calls = 0
        self.commit_calls = 0

    def load_snapshot(self, session_id: str, expected_state_version: int) -> TurnSnapshot:
        if session_id != self.snapshot.session_id:
            raise KeyError("SESSION_NOT_FOUND")
        if expected_state_version != self.snapshot.state_version:
            raise ValueError("STATE_CONFLICT")
        return self.snapshot.model_copy(deep=True)

    def reserve(self, request: TurnRequest, request_hash: str, trace_id: str) -> Reservation:
        self.reservation_calls += 1
        key = f"{request.session_id}:{request.idempotency_key}"
        existing = self.reservations.get(key)
        if existing:
            old_hash, reservation = existing
            if old_hash != request_hash:
                return Reservation(status="CONFLICT", reservation_id=reservation.reservation_id)
            if reservation.status == "REPLAY" and reservation.stored_response:
                return Reservation(
                    status="REPLAY",
                    reservation_id=reservation.reservation_id,
                    stored_response=reservation.stored_response,
                )
            return Reservation(status="IN_PROGRESS", reservation_id=reservation.reservation_id, status_ref=key)
        reservation = Reservation(status="NEW", reservation_id=f"reservation_{len(self.reservations) + 1}")
        self.reservations[key] = (request_hash, reservation)
        return reservation

    def commit(self, command: CommitCommand) -> CommitResult:
        self.commit_calls += 1
        if command.expected_state_version != self.snapshot.state_version:
            return CommitResult(
                committed=False,
                status="STATE_CONFLICT",
                state_version=self.snapshot.state_version,
                cart_version=self.snapshot.cart_version,
                safe_reason="SESSION_STATE_CONFLICT",
            )
        if command.expected_cart_version != self.snapshot.cart_version:
            return CommitResult(
                committed=False,
                status="STATE_CONFLICT",
                state_version=self.snapshot.state_version,
                cart_version=self.snapshot.cart_version,
                safe_reason="CART_VERSION_CONFLICT",
            )
        new_state_version = self.snapshot.state_version + 1
        new_cart_version = self.snapshot.cart_version + (1 if command.cart_changed else 0)
        new_state = command.proposed_state.model_copy(update={"state_version": new_state_version})
        new_cart = command.response.cart or self.snapshot.cart
        new_cart = new_cart.model_copy(
            update={"state_version": new_state_version, "cart_version": new_cart_version}
        )
        self.snapshot = self.snapshot.model_copy(
            update={
                "state": new_state,
                "state_version": new_state_version,
                "cart_version": new_cart_version,
                "cart": new_cart,
                "pending_clarification": new_state.pending_clarification,
                "acknowledged_result_set_id": command.acknowledged_result_set_id,
                "acknowledged_entries": command.acknowledged_entries,
                "recent_turns": command.recent_turns,
            },
            deep=True,
        )
        self.committed[command.response.response_id] = command.response
        for key, (_, reservation) in list(self.reservations.items()):
            if reservation.reservation_id == command.reservation_id:
                self.reservations[key] = (
                    self.reservations[key][0],
                    reservation.model_copy(update={"status": "REPLAY", "stored_response": command.response}),
                )
        return CommitResult(
            committed=True,
            status="COMMITTED",
            response=command.response,
            state_version=new_state_version,
            cart_version=new_cart_version,
        )

    def attach_final_response(self, response: ShopperResponse) -> None:
        """Post-commit response/suggestion update; does not advance state versions."""

        self.committed[response.response_id] = response
        for key, (_, reservation) in list(self.reservations.items()):
            if reservation.stored_response and reservation.stored_response.response_id == response.response_id:
                self.reservations[key] = (
                    self.reservations[key][0],
                    reservation.model_copy(update={"stored_response": response}),
                )


class DeterministicEnhancer(QueryEnhancementPort):
    def __init__(
        self,
        clock: ClockPort,
        id_source: IdSourcePort,
        policy_version: str = "enhancement-v1",
        memory: MemorySnapshotPort | None = None,
        history: CommerceHistoryPort | None = None,
        projection_secret: bytes = b"test-only-projection-secret",
    ) -> None:
        self.clock = clock
        self.id_source = id_source
        self.policy_version = policy_version
        self.memory = memory
        self.history = history
        self.projection_secret = projection_secret
        self.calls = 0

    def enhance(self, request: TurnRequest, snapshot: TurnSnapshot, deadline_ms: int) -> EnhancementOutput:
        self.calls += 1
        if request.message is None:
            raise ValueError("ENHANCEMENT_REQUIRES_FREE_TEXT")
        verbatim = request.message
        normalized = unicodedata.normalize("NFKC", verbatim).casefold()
        token_count = max(1, len(normalized.split()))
        fallback = "NONE"
        warnings: list[str] = []
        if deadline_ms < 75:
            fallback = "CURRENT_SESSION_ONLY"
        memory_candidates = []
        purchases = []
        conflicts: list[EnhancementConflict] = []
        included_source_ids: list[str] = []
        excluded_source_ids: list[str] = []
        if (
            deadline_ms >= 75
            and not request.ignore_history
            and snapshot.memory_profile_id
            and snapshot.memory_version is not None
        ):
            try:
                if self.memory:
                    memory_candidates = self.memory.load_active(
                        snapshot.memory_profile_id,
                        snapshot.memory_version,
                        50,
                    )
                if self.history:
                    purchases = self.history.load_verified(snapshot.memory_profile_id, 10)
                # Deterministic relevance/deduplication. Current text remains
                # complete and authoritative; lower-priority context is only a
                # bounded candidate source.
                deduped: dict[tuple[str, str | None, str], Any] = {}
                for item in memory_candidates:
                    key = (item.kind, item.field_id, repr(item.typed_value))
                    deduped.setdefault(key, item)
                memory_candidates = sorted(
                    deduped.values(),
                    key=lambda item: (
                        -(
                            4
                            if item.field_id and item.field_id.casefold() in normalized
                            else 0
                        )
                        - (2 if item.confidence == "EXPLICIT" else 0)
                        - (1 if item.age_seconds <= 90 * 86400 else 0),
                        item.memory_item_id,
                    ),
                )
                for item in memory_candidates:
                    if item.field_id and item.field_id.casefold() in normalized:
                        conflicts.append(
                            EnhancementConflict(
                                conflict_id=f"conflict_{item.memory_item_id}",
                                field_scope=item.field_id,
                                memory_item_ids=[item.memory_item_id],
                                reason="CURRENT_MESSAGE_PRECEDENCE",
                            )
                        )
                # Keep current message/state/recent turns inside the fixed
                # budget; historical items are the first material removed.
                fixed_tokens = min(token_count, 100) + 300 + len(snapshot.recent_turns) * 50 + 50
                memory_budget = max(0, 1500 - fixed_tokens)
                allowed_memory = max(0, memory_budget // 10)
                excluded_source_ids.extend(
                    item.memory_item_id for item in memory_candidates[allowed_memory:]
                )
                memory_candidates = memory_candidates[:allowed_memory]
                purchase_budget = max(0, memory_budget - len(memory_candidates) * 10)
                allowed_purchases = max(0, purchase_budget // 12)
                excluded_source_ids.extend(
                    item.source_purchase_id for item in purchases[allowed_purchases:]
                )
                purchases = purchases[:allowed_purchases]
                included_source_ids.extend(item.memory_item_id for item in memory_candidates)
                included_source_ids.extend(item.source_purchase_id for item in purchases)
            except Exception:
                # Ownership/version-integrity failures must not leak or invent
                # context. The same-schema current-session fallback is safe.
                memory_candidates = []
                purchases = []
                fallback = "CURRENT_SESSION_ONLY"
                warnings.append("PERSISTENT_CONTEXT_UNAVAILABLE")
        elif request.ignore_history:
            warnings.append("HISTORY_IGNORED_FOR_TURN")
        if not memory_candidates and self.memory and snapshot.memory_profile_id:
            excluded_source_ids.append("persistent_memory_not_selected")
        envelope = {
            "enhancement_id": self.id_source.new_id("enhancement"),
            "session_id": snapshot.session_id,
            "originating_client_turn_id": request.client_turn_id,
            "state_version": snapshot.state_version,
            "memory_profile_id": snapshot.memory_profile_id,
            "memory_version": snapshot.memory_version,
            "locale": request.locale,
            "current_message_verbatim": verbatim,
            "normalized_current_message": normalized,
            "current_state": snapshot.state,
            "recent_turn_context": snapshot.recent_turns,
            "persistent_memory_candidates": memory_candidates,
            "verified_purchase_context": purchases,
            "active_result_bindings": snapshot.acknowledged_entries,
            "cart_summary": CartSummary(
                cart_id=snapshot.cart.cart_id,
                cart_version=snapshot.cart.cart_version,
                item_count=snapshot.cart.item_count,
                total_quantity=snapshot.cart.total_quantity,
                item_ids=[item.cart_item_id for item in snapshot.cart.items],
            ),
            "conflicts": conflicts,
            "exclusions_summary": [
                {"reason_code": warning, "count": 1} for warning in warnings
            ],
            "context_truncated": False,
            "token_count": min(
                1500,
                min(token_count, 100)
                + 300
                + len(snapshot.recent_turns) * 50
                + len(memory_candidates) * 10
                + len(purchases) * 12
                + 50,
            ),
            "token_count_approximate": True,
            "enhancement_policy_version": self.policy_version,
            "context_hash": "pending",
        }
        envelope["context_hash"] = "pending"
        from .contracts import EnhancedQueryEnvelope, IntentContextProjectionV1, FallbackState

        validated_envelope = EnhancedQueryEnvelope.model_validate(envelope)
        def age_bucket(age_seconds: int) -> str:
            if age_seconds <= 7 * 86400:
                return "0_7_DAYS"
            if age_seconds <= 30 * 86400:
                return "8_30_DAYS"
            if age_seconds <= 90 * 86400:
                return "31_90_DAYS"
            if age_seconds <= 180 * 86400:
                return "91_180_DAYS"
            return "OLDER"

        def context_ref(kind: str, source_id: str) -> str:
            digest = hmac.new(
                self.projection_secret,
                f"{request.client_turn_id}\0{kind}\0{source_id}".encode("utf-8"),
                "sha256",
            ).digest()
            return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")[:22]

        projected_memory = [
            ProjectedMemoryCandidate(
                context_ref=context_ref("memory", item.memory_item_id),
                kind=item.kind,
                field_id=item.field_id,
                typed_value=item.typed_value,
                taxonomy_scope_ids=item.taxonomy_scope_ids,
                confidence=item.confidence,
                age_bucket=age_bucket(item.age_seconds),
            )
            for item in memory_candidates
        ]
        projected_purchases = [
            ProjectedPurchaseContext(
                context_ref=context_ref("purchase", item.source_purchase_id),
                public_product_name=item.public_product_name,
                taxonomy_node_id=item.taxonomy_node_id,
                selected_attributes=item.selected_attributes,
                age_bucket="OLDER",
                provenance_type=item.provenance_type,
            )
            for item in purchases
        ]
        projection_state = snapshot.state.model_copy(
            update={"latest_result_set_id": None, "active_suggestion_set_id": None}
        )
        projection = IntentContextProjectionV1(
            current_message_verbatim=verbatim,
            normalized_current_message=normalized,
            current_state=projection_state,
            recent_turn_context=snapshot.recent_turns,
            persistent_memory_candidates=projected_memory,
            verified_purchase_context=projected_purchases,
            active_result_bindings=snapshot.acknowledged_entries,
            cart_summary=validated_envelope.cart_summary,
            projection_hash=canonical_hash(
                {
            "current_message_verbatim": verbatim,
                    "current_state": projection_state,
                    "recent_turn_context": snapshot.recent_turns,
                    "active_result_bindings": snapshot.acknowledged_entries,
                    "persistent_memory_candidates": projected_memory,
                    "verified_purchase_context": projected_purchases,
                }
            ),
        )
        validated_envelope = validated_envelope.model_copy(
            update={
                "context_hash": canonical_hash(
                    {
                        "envelope": validated_envelope,
                        "projection_schema_version": projection.schema_version,
                        "projection_hash": projection.projection_hash,
                    }
                )
            }
        )
        return EnhancementOutput(
            envelope=validated_envelope,
            projection=projection,
            fallback=FallbackState(fallback),
            token_count=validated_envelope.token_count,
            latency_ms=0,
            included_source_ids=included_source_ids,
            excluded_source_ids=excluded_source_ids,
            warnings=warnings,
        )


class FakeMemorySnapshotPort(MemorySnapshotPort):
    def __init__(self, items: Sequence[Any] = ()) -> None:
        self.items = list(items)
        self.calls = 0

    def load_active(self, memory_profile_id: str, memory_version: int, limit: int) -> list[Any]:
        self.calls += 1
        return list(self.items[:limit])


class FakeCommerceHistoryPort(CommerceHistoryPort):
    def __init__(self, purchases: Sequence[PurchaseContext] = ()) -> None:
        self.purchases = list(purchases)
        self.calls = 0

    def load_verified(self, memory_profile_id: str, limit: int) -> list[PurchaseContext]:
        self.calls += 1
        return list(self.purchases[:limit])


class FakeReferenceResolver(ReferenceResolverPort):
    def resolve(
        self,
        session_id: str,
        references: Sequence[ReferenceDraft],
        action: Action,
        snapshot: TurnSnapshot,
    ) -> ReferenceResolution:
        if not references:
            return ReferenceResolution(status="NOT_FOUND", reason_code="REFERENCE_REQUIRED")
        resolved: list[ResolvedReference] = []
        for reference in references:
            if reference.kind == "ORDINAL":
                try:
                    position = int(reference.value)
                except ValueError:
                    return ReferenceResolution(status="NOT_FOUND", reason_code="ORDINAL_INVALID")
                match = next(
                    (entry for entry in snapshot.acknowledged_entries if entry.display_position == position), None
                )
            else:
                match = next(
                    (
                        entry
                        for entry in snapshot.acknowledged_entries
                        if entry.result_entry_id == reference.value
                        or entry.context_ref == reference.value
                        or entry.binding.product_id == reference.value
                        or entry.binding.sku_id == reference.value
                        or entry.binding.offer_id == reference.value
                    ),
                    None,
                )
            if match is None:
                return ReferenceResolution(status="STALE", reason_code="REFERENCE_NOT_IN_ACKNOWLEDGED_SET")
            resolved.append(
                ResolvedReference(
                    result_entry_id=match.result_entry_id,
                    display_position=match.display_position,
                    binding=match.binding,
                )
            )
        expected = 1 if action in {Action.PRODUCT_DETAILS, Action.CHECK_AVAILABILITY, Action.UPDATE_CART} else 2 if action is Action.COMPARE else 1
        if len(resolved) < expected:
            return ReferenceResolution(status="AMBIGUOUS", reason_code="REFERENCE_COUNT", references=resolved)
        return ReferenceResolution(status="RESOLVED", references=resolved[:4])


class FakeCatalogPort(CatalogSearchPort):
    def __init__(self, entries: Sequence[SearchEntry], availability: Availability | None = None) -> None:
        self.entries = list(entries)
        self.availability = availability
        self.search_calls = 0
        self.details_calls = 0
        self.compare_calls = 0

    def search(self, request: SearchRequest, deadline_ms: int) -> SearchResult:
        self.search_calls += 1
        entries = self.entries[: request.top_k]
        return SearchResult(
            status=ToolStatus.OK if entries else ToolStatus.NOT_FOUND,
            result_set_id=f"fake-result-set-{self.search_calls}",
            entries=entries,
            eligible_count=len(entries),
            confidence_signals={"coverage": 1.0 if entries else 0.0},
            hard_filter_hash=canonical_hash(request.query_state.hard_constraints),
            warnings=[] if entries else ["NO_ELIGIBLE_MATCH"],
        )

    def get_details(self, binding: ProductBinding, compatibility: CompatibilityTuple, deadline_ms: int) -> ProductDetails:
        self.details_calls += 1
        entry = next((item for item in self.entries if item.binding == binding), None)
        if entry is None:
            return ProductDetails(status=ToolStatus.NOT_FOUND, binding=binding)
        return ProductDetails(status=ToolStatus.OK, binding=binding, title=entry.title, facts=entry.facts)

    def compare(self, bindings: Sequence[ProductBinding], compatibility: CompatibilityTuple, deadline_ms: int) -> Any:
        self.compare_calls += 1
        from .contracts import Comparison, ComparisonCell, ComparisonRow

        rows = []
        for field_id in ("title", "price"):
            cells = []
            for binding in bindings:
                entry = next((item for item in self.entries if item.binding == binding), None)
                fact = next((fact for fact in (entry.facts if entry else []) if fact.label == field_id), None)
                cells.append(
                    ComparisonCell(
                        field_id=field_id,
                        value=fact.typed_value if fact else None,
                        status=fact.status if fact else TruthStatus.UNKNOWN,
                        evidence_refs=fact.evidence_refs if fact else [],
                    )
                )
            rows.append(ComparisonRow(field_id=field_id, label=field_id.title(), cells=cells))
        return Comparison(status=ToolStatus.OK, bindings=list(bindings), rows=rows)

    def check_availability(self, binding: ProductBinding, compatibility: CompatibilityTuple, deadline_ms: int) -> Availability:
        if self.availability and self.availability.binding == binding:
            return self.availability
        return Availability(
            status=ToolStatus.OK,
            binding=binding,
            availability_status="UNKNOWN",
            truth_status=TruthStatus.UNKNOWN,
        )

    def check_eligibility(self, binding: ProductBinding, purpose: str, deadline_ms: int) -> CommerceEligibility:
        entry_exists = any(entry.binding == binding for entry in self.entries)
        return CommerceEligibility(
            eligible=entry_exists,
            binding=binding,
            policy_status="ALLOWED" if entry_exists else "NOT_FOUND",
            price=Money(amount_paise=100_000) if entry_exists else None,
            availability_status="AVAILABLE" if entry_exists else None,
            reasons=[] if entry_exists else ["BINDING_NOT_FOUND"],
        )


class FakeRecoveryPort(RecoveryPort):
    def __init__(self) -> None:
        self.assess_calls = 0
        self.recover_calls = 0

    def assess(self, result: SearchResult, query_state: QueryState) -> ConfidenceDecision:
        self.assess_calls += 1
        return ConfidenceDecision(
            decision="ACCEPT" if result.entries else "NO_SAFE_RECOVERY",
            reasons=[] if result.entries else ["NO_ELIGIBLE_MATCH"],
            hard_filter_hash=result.hard_filter_hash,
            signals=result.confidence_signals,
        )

    def recover(self, result: SearchResult, query_state: QueryState, compatibility: CompatibilityTuple, deadline_ms: int) -> SearchResult:
        self.recover_calls += 1
        return result


class FakeGraphContextPort(GraphContextPort):
    """Disabled-by-default graph seam; never supplies product truth."""

    def __init__(self) -> None:
        self.calls = 0

    def lookup(self, request: GraphContextRequest) -> GraphContextResult:
        self.calls += 1
        return GraphContextResult(
            status="UNAVAILABLE",
            graph_version=request.graph_version,
            warnings=["GRAPH_CONTEXT_DISABLED"],
        )


def empty_cart(session_id: str, compatibility: CompatibilityTuple) -> CartSnapshot:
    return CartSnapshot(
        cart_id=f"cart_{session_id}",
        session_id=session_id,
        cart_version=0,
        state_version=0,
        items=[],
        item_count=0,
        total_quantity=0,
        subtotal=Money(amount_paise=0),
    )


class FakeCartPort(CartPort):
    def __init__(self, snapshot: CartSnapshot) -> None:
        self.snapshot = snapshot
        self.show_calls = 0
        self.update_calls = 0

    def show_cart(self, session_id: str, known_cart_version: int, deadline_ms: int) -> CartSnapshot:
        self.show_calls += 1
        return self.snapshot.model_copy(deep=True)

    def update_cart(self, request: UpdateCartRequest, deadline_ms: int) -> UpdateCartResult:
        self.update_calls += 1
        if request.expected_cart_version != self.snapshot.cart_version:
            return UpdateCartResult(status="CONFLICT", cart=self.snapshot)
        items = {item.cart_item_id: item.model_copy(deep=True) for item in self.snapshot.items}
        applied: list[str] = []
        for operation in request.operations:
            if operation.type == "ADD_ITEM":
                existing = next((item for item in items.values() if item.binding == operation.binding), None)
                if existing:
                    new_quantity = existing.quantity + operation.quantity
                    if new_quantity > 99:
                        return UpdateCartResult(status="REJECTED", cart=self.snapshot, warnings=["QUANTITY_LIMIT"])
                    existing.quantity = new_quantity
                    existing.line_subtotal = Money(amount_paise=existing.quantity * existing.unit_price.amount_paise)
                else:
                    unit = operation.expected_unit_price or Money(amount_paise=100_000)
                    item_id = f"cart_item_{len(items) + 1}"
                    items[item_id] = CartItem(
                        cart_item_id=item_id,
                        binding=operation.binding,
                        selected_attributes={},
                        quantity=operation.quantity,
                        unit_price=unit,
                        line_subtotal=Money(amount_paise=unit.amount_paise * operation.quantity),
                        availability_status="AVAILABLE",
                    )
                applied.append(operation.operation_id)
            elif operation.type == "REMOVE_ITEM":
                if operation.cart_item_id not in items:
                    return UpdateCartResult(status="REJECTED", cart=self.snapshot, warnings=["CART_ITEM_NOT_FOUND"])
                del items[operation.cart_item_id]
                applied.append(operation.operation_id)
            elif operation.type in {"SET_QUANTITY", "INCREMENT_ITEM", "DECREMENT_ITEM"}:
                item_id = operation.cart_item_id
                if item_id not in items:
                    return UpdateCartResult(status="REJECTED", cart=self.snapshot, warnings=["CART_ITEM_NOT_FOUND"])
                item = items[item_id]
                if operation.type == "SET_QUANTITY":
                    new_quantity = operation.quantity
                elif operation.type == "INCREMENT_ITEM":
                    new_quantity = item.quantity + operation.amount
                else:
                    new_quantity = item.quantity - operation.amount
                if not 1 <= new_quantity <= 99:
                    return UpdateCartResult(status="REJECTED", cart=self.snapshot, warnings=["QUANTITY_LIMIT"])
                item.quantity = new_quantity
                item.line_subtotal = Money(amount_paise=item.quantity * item.unit_price.amount_paise)
                applied.append(operation.operation_id)
            else:
                if operation.confirmation_token != "fake-clear-confirmation":
                    return UpdateCartResult(status="REJECTED", cart=self.snapshot, warnings=["CLEAR_CONFIRMATION_REQUIRED"])
                items.clear()
                applied.append(operation.operation_id)
        new_items = list(items.values())
        cart = self.snapshot.model_copy(
            update={
                "items": new_items,
                "item_count": len(new_items),
                "total_quantity": sum(item.quantity for item in new_items),
                "subtotal": Money(amount_paise=sum(item.line_subtotal.amount_paise for item in new_items)),
                "cart_version": self.snapshot.cart_version + 1,
            },
            deep=True,
        )
        self.snapshot = cart
        return UpdateCartResult(status="UPDATED", applied_operation_ids=applied, cart=cart)


class FakeResearchPort(ResearchPort):
    def __init__(self, clock: ClockPort, sources: Sequence[ResearchSource] = ()) -> None:
        self.clock = clock
        self.sources = list(sources)
        self.detect_calls = 0
        self.search_calls = 0

    def detect_need(self, intent: IntentDeltaV1, current_message: str, selected_entity_ids: Sequence[str]) -> ResearchDecision:
        self.detect_calls += 1
        if intent.primary_action is not Action.RESEARCH_EXTERNAL:
            return ResearchDecision(
                decision="NOT_NEEDED",
                reason_code="CATALOG_PATH",
                question_type="CATALOG_FACT",
                canonical_query=current_message,
            )
        return ResearchDecision(
            decision="REQUIRED",
            reason_code="EXPLICIT_EXTERNAL_REQUEST",
            question_type="CURRENT_EXTERNAL",
            canonical_query=current_message,
            selected_entity_ids=list(selected_entity_ids),
        )

    def search(self, decision: ResearchDecision, deadline_ms: int) -> OnlineSearchResult:
        self.search_calls += 1
        return OnlineSearchResult(
            status="SUCCESS" if self.sources else "NO_RESULTS",
            provider="fake-search",
            executed_query=decision.canonical_query,
            searched_at=self.clock.now_utc(),
            sources=self.sources[:5],
        )

    def validate(self, decision: ResearchDecision, search_result: OnlineSearchResult, synthesis: Any | None) -> ValidatedResearch:
        if not synthesis:
            return ValidatedResearch(status="UNAVAILABLE", warnings=["SYNTHESIS_UNAVAILABLE"])
        return validate_research_synthesis(synthesis, search_result)


class FakeSuggestionPort(SuggestionPort):
    def __init__(self) -> None:
        self.calls = 0

    def build_and_store(self, response: ShopperResponse, snapshot: TurnSnapshot, trace_id: str, deadline_ms: int) -> SuggestionSet | None:
        self.calls += 1
        if deadline_ms < 75 or response.terminal_state is TerminalState.CLARIFICATION_REQUIRED:
            return None
        candidates: list[SuggestionCandidate] = []
        if response.search_entries:
            candidates.append(
                SuggestionCandidate(
                    candidate_id="candidate_compare",
                    action_type=Action.COMPARE,
                    safe_default_label="Compare the top two",
                    payload={},
                    target_ids=[entry.result_entry_id for entry in response.search_entries[:2]],
                    reason_code="RESULTS_READY",
                )
            )
            candidates.append(
                SuggestionCandidate(
                    candidate_id="candidate_details",
                    action_type=Action.PRODUCT_DETAILS,
                    safe_default_label="View the first result",
                    payload={},
                    target_ids=[response.search_entries[0].result_entry_id],
                    reason_code="RESULTS_READY",
                )
            )
        if response.cart and response.cart.item_count:
            candidates.append(
                SuggestionCandidate(
                    candidate_id="candidate_cart",
                    action_type=Action.SHOW_CART,
                    safe_default_label="Show cart",
                    payload={},
                    target_ids=[],
                    reason_code="CART_AVAILABLE",
                )
            )
        suggestions = [
            Suggestion(
                suggestion_id=f"suggestion_{candidate.candidate_id}",
                candidate=candidate,
                label=candidate.safe_default_label,
                signed_action_token=canonical_hash(
                    {"session_id": snapshot.session_id, "state_version": snapshot.state_version, "candidate": candidate}
                ),
            )
            for candidate in candidates[:3]
        ]
        if not suggestions:
            return None
        return SuggestionSet(
            suggestion_set_id=f"suggestion_set_{response.response_id}",
            session_id=snapshot.session_id,
            state_version=snapshot.state_version,
            cart_version=snapshot.cart_version,
            active_result_set_id=snapshot.acknowledged_result_set_id,
            suggestions=suggestions,
            generator_version="deterministic-suggestions-v1",
        )

    def select(self, request: SuggestionSelectionRequest) -> SuggestionSelectionResult:
        return SuggestionSelectionResult(status="SUGGESTION_STALE", reason="FAKE_SELECTION_REQUIRES_STORE")


class FakeMarkdownPipeline(MarkdownPipelinePort):
    """Records the typed handoff; it intentionally does not generate markdown."""

    def __init__(self) -> None:
        self.handoffs: list[ShopperResponse] = []

    def handoff(self, response: ShopperResponse, trace: Any) -> MarkdownHandoff:
        self.handoffs.append(response)
        return MarkdownHandoff(
            response_id=response.response_id,
            status="READY_FOR_MARKDOWN",
            handoff_ref=f"typed-response:{response.response_id}",
        )
