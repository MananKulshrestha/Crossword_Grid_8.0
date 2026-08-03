"""Deterministic resolution for the small, reviewed fixture catalog lexicon.

The model remains responsible for interpreting the shopper's overall intent,
but obvious catalog terms must not disappear when a provider returns a valid
yet incomplete delta.  This module only turns explicit words in the current
message into typed operations; it never reads memory or invents catalog facts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .contracts import (
    Action,
    ActiveResultBinding,
    AddScopeOperation,
    ConstraintOperator,
    DeltaOperation,
    IntentDeltaV1,
    ReferenceDraft,
    RemoveHardOperation,
    RemoveScopeOperation,
    RemoveSoftOperation,
    SetHardOperation,
    SetSoftOperation,
    SoftOperator,
)


@dataclass(frozen=True)
class _TermMatch:
    value: str
    start: int
    end: int


# These aliases are intentionally bounded to the taxonomy represented by the
# local fixture.  A production catalog-language artifact can replace this
# table without changing the orchestrator contract.
_CATEGORY_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("tshirts", (r"t[\s-]*shirts?", r"tees?")),
    ("shirts", (r"shirts?",)),
    ("jeans", (r"jeans?",)),
    ("sneakers", (r"shoes?", r"sneakers?", r"trainers?", r"kicks?")),
    ("backpacks", (r"backpacks?", r"rucksacks?", r"laptop[\s-]*bags?")),
    ("headphones", (r"headphones?", r"headsets?")),
    ("laptops", (r"laptops?", r"notebooks?")),
    ("smartphones", (r"smartphones?", r"mobiles?", r"phones?")),
    ("tablets", (r"tablets?",)),
    ("smartwatches", (r"smartwatches?", r"watches?")),
    ("speakers", (r"speakers?",)),
    ("fitness-bands", (r"fitness[\s-]*bands?", r"fitness[\s-]*trackers?")),
)

_COLOR_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("red", (r"red",)),
    ("maroon", (r"maroon",)),
    ("burgundy", (r"burgundy",)),
    ("coral", (r"coral",)),
    ("orange", (r"orange",)),
    ("pink", (r"pink",)),
    ("purple", (r"purple",)),
    ("rose", (r"rose",)),
    ("black", (r"black",)),
    ("white", (r"white",)),
    ("navy", (r"navy",)),
    ("blue", (r"blue",)),
    ("olive", (r"olive",)),
    ("green", (r"green",)),
    ("grey", (r"grey", r"gray")),
    ("silver", (r"silver",)),
    ("gold", (r"gold",)),
    ("tan", (r"tan",)),
    ("sage", (r"sage",)),
    ("charcoal", (r"charcoal",)),
    ("indigo", (r"indigo",)),
    ("stonewash", (r"stonewash",)),
    ("midblue", (r"mid[\s-]*blue",)),
)

_SIZE_PATTERN = re.compile(
    r"\bsize(?:\s+is)?\s*(xs|xl|s|m|l|7|8|9|10|30|32|34|36)\b",
    flags=re.IGNORECASE,
)
_NON_SEARCH_MARKERS = re.compile(
    r"\b(?:add|remove|delete|cart|compare|availability|available|details?|research|latest|reset|clear)\b",
    flags=re.IGNORECASE,
)
_CART_ADD_PATTERN = re.compile(
    r"\b(?:put|add|place|save)\b.{0,48}\bcart\b|\bcart\b.{0,48}\b(?:put|add|place|save)\b",
    flags=re.IGNORECASE,
)
_ORDINAL_PATTERN = re.compile(
    r"\b(first|1st|one|second|2nd|two|third|3rd|three|fourth|4th|four)\b",
    flags=re.IGNORECASE,
)
_NUMERIC_ORDINAL_PATTERN = re.compile(r"\b(1|2|3|4)\b", flags=re.IGNORECASE)
_EXPLICIT_QUANTITY_MARKER = re.compile(
    r"\b(?:unit|units|quantity|quantities|copies|of)\b", flags=re.IGNORECASE
)
_ORDINAL_VALUES = {
    "1": "1",
    "2": "2",
    "3": "3",
    "4": "4",
    "first": "1",
    "1st": "1",
    "one": "1",
    "second": "2",
    "2nd": "2",
    "two": "2",
    "third": "3",
    "3rd": "3",
    "three": "3",
    "fourth": "4",
    "4th": "4",
    "four": "4",
}

_GREETING_OR_HELP_MESSAGES = frozenset(
    {
        "hi",
        "hello",
        "hey",
        "hiya",
        "heya",
        "hi there",
        "hello there",
        "hey there",
        "hi bot",
        "hello bot",
        "hey bot",
        "good morning",
        "good afternoon",
        "good evening",
        "help",
        "what can you do",
        "what can you help me with",
    }
)

_DETERMINISTIC_CATALOG_ALLOWED_WORDS = frozenset(
    {
        "a",
        "an",
        "any",
        "are",
        "can",
        "color",
        "colored",
        "colour",
        "find",
        "for",
        "get",
        "give",
        "good",
        "in",
        "is",
        "item",
        "items",
        "l",
        "list",
        "me",
        "m",
        "my",
        "need",
        "of",
        "on",
        "only",
        "option",
        "options",
        "please",
        "product",
        "products",
        "recommend",
        "red",
        "remember",
        "search",
        "shirt",
        "shirts",
        "show",
        "size",
        "some",
        "s",
        "suggest",
        "t",
        "tee",
        "tees",
        "the",
        "that",
        "tshirt",
        "tshirts",
        "want",
        "with",
        "you",
        "xs",
        "xl",
    }
)
_DETERMINISTIC_CATALOG_UNSAFE_WORDS = frozenset(
    {
        "above",
        "add",
        "at",
        "available",
        "availability",
        "below",
        "brand",
        "budget",
        "cart",
        "cheaper",
        "compare",
        "cotton",
        "current",
        "delete",
        "detail",
        "details",
        "latest",
        "material",
        "more",
        "price",
        "quantity",
        "remove",
        "research",
        "rupees",
        "under",
    }
)


def is_greeting_or_help_message(text: str) -> bool:
    normalized = re.sub(r"[\s,!?;:.]+", " ", text.casefold()).strip()
    return normalized in _GREETING_OR_HELP_MESSAGES


def _cart_ordinal_match(text: str) -> re.Match[str] | None:
    word_match = _ORDINAL_PATTERN.search(text)
    if word_match is not None:
        return word_match
    numeric_match = _NUMERIC_ORDINAL_PATTERN.search(text)
    if numeric_match is not None and _EXPLICIT_QUANTITY_MARKER.search(text) is None:
        return numeric_match
    return None


def _matches(text: str, patterns: tuple[tuple[str, tuple[str, ...]], ...]) -> list[_TermMatch]:
    found: list[_TermMatch] = []
    for canonical, aliases in patterns:
        for alias in aliases:
            match = re.search(rf"\b(?:{alias})\b", text, flags=re.IGNORECASE)
            if match:
                found.append(_TermMatch(canonical, match.start(), match.end()))
    return found


def _replace_field_operations(
    operations: list[DeltaOperation], field_id: str
) -> list[DeltaOperation]:
    return [
        operation
        for operation in operations
        if not (
            isinstance(
                operation,
                (SetHardOperation, RemoveHardOperation, SetSoftOperation, RemoveSoftOperation),
            )
            and operation.field_id.casefold() == field_id.casefold()
        )
    ]


def _strict_color_request(text: str, color_matches: list[_TermMatch]) -> bool:
    if not color_matches:
        return False
    strict_words = r"only|exactly|must|strictly"
    return any(
        re.search(
            rf"\b(?:{strict_words})\b.{{0,24}}\b{re.escape(match.value)}\b",
            text,
            flags=re.IGNORECASE,
        )
        or re.search(
            rf"\b{re.escape(match.value)}\b.{{0,24}}\b(?:{strict_words})\b",
            text,
            flags=re.IGNORECASE,
        )
        for match in color_matches
    )


def _size_match(text: str) -> _TermMatch | None:
    match = _SIZE_PATTERN.search(text)
    if match is None:
        return None
    return _TermMatch(f"size_{match.group(1).casefold()}", match.start(), match.end())


def apply_explicit_catalog_terms(intent: IntentDeltaV1, message: str) -> IntentDeltaV1:
    """Overlay explicit category/color terms from the current shopper turn.

    Category words are hard taxonomy scope changes, so ``shoes`` replaces a
    prior T-shirt scope.  Ordinary color wording is a weighted preference: it
    allows the deterministic catalog adapter to show the closest available
    colors when an exact color is absent.  Phrases such as ``only red`` remain
    hard filters and therefore can safely return no match.
    """

    text = message.casefold()
    category_matches = sorted(
        _matches(text, _CATEGORY_PATTERNS),
        key=lambda item: (item.start, -(item.end - item.start), item.value),
    )
    color_matches = sorted(
        _matches(text, _COLOR_PATTERNS),
        key=lambda item: (item.start, item.value),
    )
    size_match = _size_match(text)
    has_explicit_catalog_terms = bool(category_matches or color_matches or size_match)
    is_explicit_non_search = _NON_SEARCH_MARKERS.search(text) is not None
    action = intent.primary_action
    if (
        action not in {Action.SEARCH, Action.REFINE}
        and has_explicit_catalog_terms
        and not is_explicit_non_search
    ):
        # The current message is authoritative.  A provider must not turn a
        # preference/search statement containing "remember" into a cart action
        # that then asks for a product reference.
        action = Action.REFINE
    if action not in {Action.SEARCH, Action.REFINE}:
        return intent

    operations = list(intent.delta_operations)

    if category_matches:
        # The current explicit category owns this turn.  This removes both a
        # model-proposed scope and any stale scope inherited from the session.
        operations = [
            operation
            for operation in operations
            if not isinstance(operation, (AddScopeOperation, RemoveScopeOperation))
        ]
        operations.append(AddScopeOperation(taxonomy_node_id=category_matches[0].value))

    if color_matches:
        unique_colors = list(dict.fromkeys(match.value for match in color_matches))
        operations = _replace_field_operations(operations, "color")
        first = color_matches[0]
        if _strict_color_request(text, color_matches):
            operations.append(
                SetHardOperation(
                    field_id="color",
                    operator=(
                        # A comma/or phrase is still a bounded multi-value
                        # hard request; otherwise use the simpler EQ shape.
                        ConstraintOperator.IN
                        if len(unique_colors) > 1
                        else ConstraintOperator.EQ
                    ),
                    typed_values=unique_colors,
                    evidence_span=(first.start, first.end),
                )
            )
        else:
            operations.append(
                SetSoftOperation(
                    field_id="color",
                    operator=(
                        SoftOperator.IN if len(unique_colors) > 1 else SoftOperator.EQ
                    ),
                    typed_values=unique_colors,
                    weight=10,
                    evidence_span=(first.start, first.end),
                )
            )

    if size_match is not None:
        operations = _replace_field_operations(operations, "size")
        operations.append(
            SetHardOperation(
                field_id="size",
                operator=ConstraintOperator.EQ,
                typed_values=[size_match.value],
                evidence_span=(size_match.start, size_match.end),
            )
        )

    if operations == intent.delta_operations and action is intent.primary_action:
        return intent
    return intent.model_copy(
        update={"primary_action": action, "delta_operations": operations}
    )


def deterministic_catalog_intent(
    message: str, *, refine_existing_query: bool = False
) -> IntentDeltaV1 | None:
    """Return a conservative fallback for fully recognized catalog wording.

    This is intentionally narrower than model intent extraction.  It accepts
    only the reviewed fixture vocabulary and refuses messages containing
    unsupported constraints or actions, so a provider timeout cannot silently
    drop price, material, brand, cart, or reference semantics.
    """

    text = message.casefold()
    tokens = re.findall(r"[a-z0-9]+", text)
    if not tokens or _DETERMINISTIC_CATALOG_UNSAFE_WORDS.intersection(tokens):
        return None
    if not set(tokens).issubset(_DETERMINISTIC_CATALOG_ALLOWED_WORDS):
        return None
    if not (
        _matches(text, _CATEGORY_PATTERNS)
        or _matches(text, _COLOR_PATTERNS)
        or _size_match(text) is not None
    ):
        return None
    base = IntentDeltaV1(
        primary_action=Action.REFINE if refine_existing_query else Action.SEARCH
    )
    resolved = apply_explicit_catalog_terms(base, message)
    return resolved if resolved.delta_operations else None


def apply_explicit_cart_terms(
    intent: IntentDeltaV1,
    message: str,
    active_result_bindings: list[ActiveResultBinding],
) -> IntentDeltaV1:
    """Build a safe ADD_ITEM operation for an explicit ordinal cart request.

    Provider output may correctly choose ``UPDATE_CART`` but omit the typed
    operation payload.  We recover only the narrow phrase/ordinal pattern and
    bind it to the immutable acknowledged result set; all cart policy and
    revalidation remains owned by the orchestrator and cart adapter.
    """

    text = message.casefold()
    if not _CART_ADD_PATTERN.search(text):
        return intent
    ordinal_match = _cart_ordinal_match(text)
    if ordinal_match is None:
        return intent
    ordinal = _ORDINAL_VALUES[ordinal_match.group(1).casefold()]
    entry = next(
        (
            item
            for item in active_result_bindings
            if item.display_position == int(ordinal)
        ),
        None,
    )
    action_parameters = dict(intent.action_parameters)
    if isinstance(action_parameters.get("operations"), list) and action_parameters["operations"]:
        return intent
    if entry is None:
        # Leave the intent untouched so the normal reference clarification is
        # returned rather than guessing against a stale or absent result set.
        return intent.model_copy(update={"primary_action": Action.UPDATE_CART})
    references = list(intent.references)
    if not any(
        reference.kind == "ORDINAL" and reference.value == ordinal
        for reference in references
    ):
        references.append(ReferenceDraft(kind="ORDINAL", value=ordinal))
    action_parameters["operations"] = [
        {
            "type": "ADD_ITEM",
            "operation_id": f"chat_add_ordinal_{ordinal}",
            "result_entry_id": entry.result_entry_id,
            "quantity": 1,
        }
    ]
    return intent.model_copy(
        update={
            "primary_action": Action.UPDATE_CART,
            "references": references,
            "action_parameters": action_parameters,
        }
    )
