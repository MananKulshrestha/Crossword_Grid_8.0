"""Flat, minimal contract set for the live-only shopper chat pipeline.

Deliberately not versioned/schema-pinned like the previous implementation:
one catalog source, one reranker, one LLM. If the shape needs to change,
change it here directly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Chat / session memory
# --------------------------------------------------------------------------


class Role(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"


class ChatTurn(BaseModel):
    role: Role
    content: str
    created_at: datetime = Field(default_factory=_now)


# --------------------------------------------------------------------------
# Query extraction / enhancement
# --------------------------------------------------------------------------


class Action(str, Enum):
    SEARCH = "SEARCH"
    REFINE = "REFINE"
    PRODUCT_DETAILS = "PRODUCT_DETAILS"
    COMPARE = "COMPARE"
    CHECK_AVAILABILITY = "CHECK_AVAILABILITY"
    SHOW_CART = "SHOW_CART"
    UPDATE_CART = "UPDATE_CART"


class Constraint(BaseModel):
    field: Literal["max_price", "min_price", "brand", "category", "stock_status"]
    value: Any


class Reference(BaseModel):
    """Points at an entry in the shopper's last search results, e.g. 'the second one'."""

    ordinal: int | None = None
    sku_id: str | None = None


class CartOperationType(str, Enum):
    ADD_ITEM = "ADD_ITEM"
    SET_QUANTITY = "SET_QUANTITY"
    REMOVE_ITEM = "REMOVE_ITEM"
    CLEAR_CART = "CLEAR_CART"


class CartOperationDraft(BaseModel):
    type: CartOperationType
    reference: Reference | None = None
    cart_item_id: str | None = None
    quantity: int | None = None
    confirmation: bool = False
    # Populated by the orchestrator after resolving `reference` against
    # session.last_results - never trusted from the LLM directly.
    sku_id: str | None = None
    product_id: str | None = None
    offer_id: str | None = None


class QueryExtraction(BaseModel):
    action: Action
    query_terms: list[str] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)
    references: list[Reference] = Field(default_factory=list)
    cart_operations: list[CartOperationDraft] = Field(default_factory=list)


class RerankerRequest(BaseModel):
    """Mirrors the /api/search payload exactly."""

    soft_query_text: str
    hard_constraints: dict[str, Any] = Field(default_factory=dict)
    top_n: int = 10


# --------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------


class SearchEntry(BaseModel):
    sku_id: str
    product_id: str
    offer_id: str
    title: str
    brand: str | None = None
    category: str | None = None
    price_paise: int | None = None
    rating: float | None = None
    availability_status: str | None = None
    quantity: int | None = None
    rerank_score: float | None = None


class SearchResult(BaseModel):
    entries: list[SearchEntry] = Field(default_factory=list)
    result_set_id: str | None = None
    # Every sku_id the reranker returned, split by whether MySQL actually
    # has it. hallucinated_sku_ids exist only in the reranker's output, not
    # the catalog - the CLI prints these in red.
    verified_sku_ids: list[str] = Field(default_factory=list)
    hallucinated_sku_ids: list[str] = Field(default_factory=list)


class ProductDetails(BaseModel):
    found: bool
    entry: SearchEntry | None = None


class ComparisonCell(BaseModel):
    sku_id: str
    value: Any = None


class ComparisonRow(BaseModel):
    field: str
    cells: list[ComparisonCell]


class Comparison(BaseModel):
    rows: list[ComparisonRow] = Field(default_factory=list)


class Availability(BaseModel):
    found: bool
    availability_status: str | None = None
    quantity: int | None = None


# --------------------------------------------------------------------------
# Cart
# --------------------------------------------------------------------------


class CartItem(BaseModel):
    cart_item_id: str
    sku_id: str
    product_id: str
    offer_id: str
    title: str
    quantity: int
    unit_price_paise: int
    line_subtotal_paise: int
    availability_status: str


class CartSnapshot(BaseModel):
    cart_version: int
    item_count: int
    total_quantity: int
    subtotal_paise: int
    items: list[CartItem] = Field(default_factory=list)


class UpdateCartRequest(BaseModel):
    session_id: str
    idempotency_key: str
    expected_cart_version: int
    operations: list[CartOperationDraft]


class UpdateCartResult(BaseModel):
    status: Literal["UPDATED", "CONFLICT", "REJECTED"]
    applied_operation_ids: list[str] = Field(default_factory=list)
    cart: CartSnapshot | None = None
    reason: str | None = None


# --------------------------------------------------------------------------
# Follow-ups
# --------------------------------------------------------------------------


class FollowUpSuggestion(BaseModel):
    label: str
    action: Action
    params: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------
# Session state
# --------------------------------------------------------------------------


class SessionState(BaseModel):
    session_id: str
    chat_history: list[ChatTurn] = Field(default_factory=list)
    last_results: list[SearchEntry] = Field(default_factory=list)
    last_reranker_request: RerankerRequest | None = None
    cart_version: int = 0


# --------------------------------------------------------------------------
# Turn request/response
# --------------------------------------------------------------------------


class TurnRequest(BaseModel):
    session_id: str
    message: str


class TurnStatus(str, Enum):
    OK = "OK"
    ERROR = "ERROR"


class TraceStep(BaseModel):
    """One stage of a turn: exactly what went in and what came out."""

    stage: str
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    ok: bool = True


class TurnResult(BaseModel):
    status: TurnStatus
    message: str
    action: Action | None = None
    search_result: SearchResult | None = None
    product_details: ProductDetails | None = None
    comparison: Comparison | None = None
    availability: Availability | None = None
    cart: CartSnapshot | None = None
    followups: list[FollowUpSuggestion] = Field(default_factory=list)
    error_code: str | None = None
    trace: list[TraceStep] = Field(default_factory=list)
