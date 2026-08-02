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
    AddScopeOperation,
    ConstraintOperator,
    DeltaOperation,
    IntentDeltaV1,
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


def apply_explicit_catalog_terms(intent: IntentDeltaV1, message: str) -> IntentDeltaV1:
    """Overlay explicit category/color terms from the current shopper turn.

    Category words are hard taxonomy scope changes, so ``shoes`` replaces a
    prior T-shirt scope.  Ordinary color wording is a weighted preference: it
    allows the deterministic catalog adapter to show the closest available
    colors when an exact color is absent.  Phrases such as ``only red`` remain
    hard filters and therefore can safely return no match.
    """

    if intent.primary_action not in {Action.SEARCH, Action.REFINE}:
        return intent

    text = message.casefold()
    category_matches = sorted(
        _matches(text, _CATEGORY_PATTERNS),
        key=lambda item: (item.start, -(item.end - item.start), item.value),
    )
    color_matches = sorted(
        _matches(text, _COLOR_PATTERNS),
        key=lambda item: (item.start, item.value),
    )
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

    if operations == intent.delta_operations:
        return intent
    return intent.model_copy(update={"delta_operations": operations})
