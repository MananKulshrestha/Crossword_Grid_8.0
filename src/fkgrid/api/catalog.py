"""Rich deterministic catalog fixtures and adapters for local API testing.

The records in this module are synthetic and reproducible, but the shopper
intent path is still served by the configured Gemma adapter. The fixture owns
catalog truth for local testing only; it is not a live-commerce integration.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fkgrid.agentic.contracts import (
    AddItemOperation,
    Availability,
    CommerceEligibility,
    Comparison,
    ComparisonCell,
    ComparisonRow,
    Constraint,
    ConstraintOperator,
    EvidenceRef,
    Fact,
    FactScope,
    Money,
    Preference,
    ProductBinding,
    ProductDetails,
    SearchEntry,
    SearchRequest,
    SearchResult,
    SoftOperator,
    ToolStatus,
    TruthStatus,
    TurnSnapshot,
    UpdateCartRequest,
    UpdateCartResult,
)
from fkgrid.agentic.fakes import FakeCartPort, FakeCatalogPort
from fkgrid.agentic.validation import canonical_hash

DEFAULT_CATALOG_SIZE = 600
DEFAULT_CATALOG_SEED = 20260801
MIN_CATALOG_SIZE = 12
MAX_CATALOG_SIZE = 1000
FIXTURE_PROFILE = "synthetic-shopping-fixture-v1"
FIXTURE_AS_OF = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)


@dataclass(frozen=True)
class CategoryTemplate:
    category: str
    title_prefix: str
    brands: tuple[str, ...]
    materials: tuple[str, ...]
    colors: tuple[str, ...]
    variants: tuple[str, ...]
    variant_field: str
    use_cases: tuple[str, ...]
    genders: tuple[str, ...]
    base_price_paise: int
    price_step_paise: int
    warranty_years: int


TEMPLATES: tuple[CategoryTemplate, ...] = (
    CategoryTemplate(
        "tshirts",
        "Everyday Cotton T-Shirt",
        ("UrbanThread", "Northstar", "DailyWeave"),
        ("cotton", "organic_cotton", "bamboo_blend"),
        ("black", "white", "navy", "olive", "maroon"),
        ("size_s", "size_m", "size_l", "size_xl"),
        "size",
        ("everyday", "casual", "travel"),
        ("men", "women", "unisex"),
        69900,
        2700,
        0,
    ),
    CategoryTemplate(
        "shirts",
        "Comfort Formal Shirt",
        ("OfficeLine", "Northstar", "MetroForm"),
        ("cotton", "linen", "cotton_linen"),
        ("white", "blue", "pink", "charcoal", "sage"),
        ("size_s", "size_m", "size_l", "size_xl"),
        "size",
        ("office", "formal", "travel"),
        ("men", "women", "unisex"),
        119900,
        3900,
        0,
    ),
    CategoryTemplate(
        "jeans",
        "Stretch Denim Jeans",
        ("BluePeak", "DenimCraft", "UrbanThread"),
        ("denim", "stretch_denim", "organic_denim"),
        ("indigo", "black", "stonewash", "midblue"),
        ("size_30", "size_32", "size_34", "size_36"),
        "size",
        ("casual", "everyday", "travel"),
        ("men", "women", "unisex"),
        169900,
        5100,
        0,
    ),
    CategoryTemplate(
        "sneakers",
        "Lightweight Everyday Sneakers",
        ("StrideLab", "Runway", "StreetStep"),
        ("mesh", "synthetic", "canvas"),
        ("black", "white", "grey", "red", "blue"),
        ("size_7", "size_8", "size_9", "size_10"),
        "size",
        ("walking", "casual", "fitness"),
        ("men", "women", "unisex"),
        249900,
        7300,
        1,
    ),
    CategoryTemplate(
        "backpacks",
        "Everyday Laptop Backpack",
        ("CarryWise", "TrailPack", "MetroCarry"),
        ("polyester", "recycled_polyester", "canvas"),
        ("black", "navy", "grey", "tan", "green"),
        ("capacity_20l", "capacity_25l", "capacity_30l"),
        "capacity",
        ("office", "college", "travel"),
        ("unisex",),
        129900,
        4600,
        1,
    ),
    CategoryTemplate(
        "headphones",
        "Noise Control Headphones",
        ("SoundArc", "WaveNest", "AudioForge"),
        ("plastic", "aluminum", "recycled_plastic"),
        ("black", "white", "silver", "blue"),
        ("storage_32gb", "storage_64gb", "storage_128gb"),
        "storage",
        ("music", "commute", "work"),
        ("unisex",),
        299900,
        8900,
        1,
    ),
    CategoryTemplate(
        "laptops",
        "Performance Work Laptop",
        ("ByteCraft", "NovaCompute", "WorkCore"),
        ("aluminum", "polycarbonate", "magnesium_alloy"),
        ("silver", "grey", "black", "blue"),
        ("storage_256gb", "storage_512gb", "storage_1tb"),
        "storage",
        ("office", "study", "creation"),
        ("unisex",),
        5499900,
        175000,
        2,
    ),
    CategoryTemplate(
        "smartphones",
        "Balanced Camera Smartphone",
        ("PixelNest", "MobileCore", "NovaMobile"),
        ("aluminum", "glass", "polycarbonate"),
        ("black", "white", "green", "blue", "gold"),
        ("storage_128gb", "storage_256gb", "storage_512gb"),
        "storage",
        ("communication", "photography", "gaming"),
        ("unisex",),
        1899900,
        85000,
        2,
    ),
    CategoryTemplate(
        "tablets",
        "Portable Study Tablet",
        ("Readly", "NovaMobile", "SlateWorks"),
        ("aluminum", "glass", "polycarbonate"),
        ("silver", "grey", "blue", "pink"),
        ("storage_64gb", "storage_128gb", "storage_256gb"),
        "storage",
        ("study", "reading", "creation"),
        ("unisex",),
        1599900,
        65000,
        1,
    ),
    CategoryTemplate(
        "smartwatches",
        "Health Tracking Smartwatch",
        ("PulseLoop", "TimeGrid", "ActiveDial"),
        ("aluminum", "stainless_steel", "polycarbonate"),
        ("black", "silver", "rose", "blue"),
        ("storage_16gb", "storage_32gb", "storage_64gb"),
        "storage",
        ("fitness", "health", "communication"),
        ("unisex",),
        399900,
        15000,
        1,
    ),
    CategoryTemplate(
        "speakers",
        "Compact Home Speaker",
        ("SoundArc", "RoomTone", "AudioForge"),
        ("plastic", "fabric", "aluminum"),
        ("black", "white", "grey", "red"),
        ("storage_16gb", "storage_32gb", "storage_64gb"),
        "storage",
        ("music", "home", "party"),
        ("unisex",),
        179900,
        6700,
        1,
    ),
    CategoryTemplate(
        "fitness-bands",
        "Activity Fitness Band",
        ("PulseLoop", "ActiveDial", "FitRibbon"),
        ("silicone", "recycled_silicone", "rubber"),
        ("black", "blue", "green", "pink"),
        ("size_s", "size_m", "size_l"),
        "size",
        ("fitness", "health", "walking"),
        ("unisex",),
        149900,
        5300,
        1,
    ),
)


def _pretty(value: str) -> str:
    return value.replace("_", " ").title()


def _evidence(
    *,
    entry_index: int,
    sku_id: str,
    field_id: str,
    catalog_version: str,
) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=f"fixture-evidence-{entry_index:04d}-{field_id}",
        entity_type="SKU",
        entity_id=sku_id,
        field_path=field_id,
        source_type="SYNTHETIC_FIXTURE",
        source_id="synthetic-shopping-fixture",
        version=catalog_version,
    )


def _fact(
    *,
    entry_index: int,
    sku_id: str,
    field_id: str,
    value: Any,
    catalog_version: str,
    status: TruthStatus = TruthStatus.VERIFIED,
    formatting_rule: str | None = None,
) -> Fact:
    return Fact(
        fact_id=f"fixture-fact-{entry_index:04d}-{field_id}",
        label=field_id,
        typed_value=value,
        status=status,
        scope=FactScope.CATALOG,
        provenance_type="SYNTHETIC_FIXTURE",
        evidence_refs=[
            _evidence(
                entry_index=entry_index,
                sku_id=sku_id,
                field_id=field_id,
                catalog_version=catalog_version,
            )
        ],
        as_of=FIXTURE_AS_OF,
        formatting_rule=formatting_rule,
    )


def build_catalog_entries(
    compatibility: Any,
    count: int = DEFAULT_CATALOG_SIZE,
    seed: int = DEFAULT_CATALOG_SEED,
) -> list[SearchEntry]:
    """Build reproducible SKU/offer records across twelve shopping categories."""

    if not MIN_CATALOG_SIZE <= count <= MAX_CATALOG_SIZE:
        raise ValueError("CATALOG_SIZE_OUT_OF_RANGE")
    entries: list[SearchEntry] = []
    family_count = (count + 1) // 2
    for family_index in range(family_count):
        template = TEMPLATES[(family_index * 7 + seed) % len(TEMPLATES)]
        brand = template.brands[(family_index + seed) % len(template.brands)]
        color = template.colors[(family_index * 3 + seed) % len(template.colors)]
        material = template.materials[(family_index * 5 + seed) % len(template.materials)]
        use_case = template.use_cases[(family_index + seed // 3) % len(template.use_cases)]
        gender = template.genders[(family_index + seed) % len(template.genders)]
        rating = round(3.7 + ((family_index * 13 + seed) % 13) / 10, 1)
        for variant_index in range(2):
            entry_index = family_index * 2 + variant_index + 1
            if entry_index > count:
                break
            variant = template.variants[
                (family_index + variant_index + seed) % len(template.variants)
            ]
            product_id = f"product_{family_index + 1:04d}"
            sku_id = f"sku_{family_index + 1:04d}_{variant_index + 1:02d}"
            offer_id = f"offer_{family_index + 1:04d}_{variant_index + 1:02d}"
            price = template.base_price_paise + (
                (family_index * template.price_step_paise + variant_index * 1700 + seed % 997)
                % 45000
            )
            availability_slot = (entry_index * 11 + seed) % 19
            if availability_slot == 0:
                availability = "RETIRED"
                quantity: int | None = 0
                availability_truth = TruthStatus.VERIFIED
            elif availability_slot == 1:
                availability = "UNKNOWN"
                quantity = None
                availability_truth = TruthStatus.UNKNOWN
            elif availability_slot == 2:
                availability = "UNAVAILABLE"
                quantity = 0
                availability_truth = TruthStatus.VERIFIED
            else:
                availability = "AVAILABLE"
                quantity = 4 + ((entry_index * 17 + seed) % 65)
                availability_truth = TruthStatus.VERIFIED
            delivery_days = 2 + ((entry_index + seed) % 7)
            title = (
                f"{brand} {template.title_prefix} {color.title()} "
                f"{_pretty(variant)} {entry_index:03d}"
            )
            facts = [
                _fact(
                    entry_index=entry_index,
                    sku_id=sku_id,
                    field_id="title",
                    value=title,
                    catalog_version=compatibility.catalog_version,
                ),
                _fact(
                    entry_index=entry_index,
                    sku_id=sku_id,
                    field_id="category",
                    value=template.category,
                    catalog_version=compatibility.catalog_version,
                ),
                _fact(
                    entry_index=entry_index,
                    sku_id=sku_id,
                    field_id="brand",
                    value=brand.casefold(),
                    catalog_version=compatibility.catalog_version,
                ),
                _fact(
                    entry_index=entry_index,
                    sku_id=sku_id,
                    field_id="price",
                    value=Money(amount_paise=price),
                    catalog_version=compatibility.catalog_version,
                    formatting_rule="inr_2dp",
                ),
                _fact(
                    entry_index=entry_index,
                    sku_id=sku_id,
                    field_id="color",
                    value=color,
                    catalog_version=compatibility.catalog_version,
                ),
                _fact(
                    entry_index=entry_index,
                    sku_id=sku_id,
                    field_id="material",
                    value=material,
                    catalog_version=compatibility.catalog_version,
                ),
                _fact(
                    entry_index=entry_index,
                    sku_id=sku_id,
                    field_id=template.variant_field,
                    value=variant,
                    catalog_version=compatibility.catalog_version,
                ),
                _fact(
                    entry_index=entry_index,
                    sku_id=sku_id,
                    field_id="rating",
                    value=rating,
                    catalog_version=compatibility.catalog_version,
                ),
                _fact(
                    entry_index=entry_index,
                    sku_id=sku_id,
                    field_id="use_case",
                    value=use_case,
                    catalog_version=compatibility.catalog_version,
                ),
                _fact(
                    entry_index=entry_index,
                    sku_id=sku_id,
                    field_id="gender",
                    value=gender,
                    catalog_version=compatibility.catalog_version,
                ),
                _fact(
                    entry_index=entry_index,
                    sku_id=sku_id,
                    field_id="availability",
                    value=availability,
                    catalog_version=compatibility.catalog_version,
                    status=availability_truth,
                ),
                _fact(
                    entry_index=entry_index,
                    sku_id=sku_id,
                    field_id="stock_quantity",
                    value=quantity,
                    catalog_version=compatibility.catalog_version,
                    status=(
                        TruthStatus.UNKNOWN
                        if quantity is None
                        else TruthStatus.VERIFIED
                    ),
                ),
                _fact(
                    entry_index=entry_index,
                    sku_id=sku_id,
                    field_id="delivery_days",
                    value=delivery_days,
                    catalog_version=compatibility.catalog_version,
                ),
                _fact(
                    entry_index=entry_index,
                    sku_id=sku_id,
                    field_id="warranty_years",
                    value=template.warranty_years,
                    catalog_version=compatibility.catalog_version,
                ),
            ]
            entries.append(
                SearchEntry(
                    result_entry_id=f"entry_{entry_index:04d}",
                    display_position=1,
                    binding=ProductBinding(
                        product_id=product_id,
                        sku_id=sku_id,
                        offer_id=offer_id,
                        catalog_version=compatibility.catalog_version,
                    ),
                    title=title,
                    facts=facts,
                )
            )
    return entries


def fixture_facets(entries: Sequence[SearchEntry]) -> dict[str, list[str]]:
    """Return safe, typed-value facets for Swagger exploration."""

    fields = ("category", "brand", "color", "material", "size", "storage", "capacity")
    output: dict[str, list[str]] = {}
    for field in fields:
        values = {
            str(fact.typed_value)
            for entry in entries
            for fact in entry.facts
            if fact.label == field
            and fact.status not in {TruthStatus.UNKNOWN, TruthStatus.NOT_MODELED}
        }
        output[field] = sorted(values)
    return output


def _text(value: Any) -> str:
    if isinstance(value, Money):
        return str(value.amount_paise)
    if isinstance(value, str):
        return value.casefold()
    return str(value).casefold()


def _number(value: Any) -> float | None:
    if isinstance(value, Money):
        return float(value.amount_paise)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict) and isinstance(value.get("amount_paise"), (int, float)):
        return float(value["amount_paise"])
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _normalise_field(field_id: str) -> str:
    return {
        "product_type": "category",
        "category_id": "category",
        # Shopper scope operations use taxonomy IDs; this fixture's taxonomy
        # IDs intentionally match its canonical category values.
        "taxonomy_node_id": "category",
        "max_price": "price",
        "min_price": "price",
        "budget": "price",
        "price_paise": "price",
    }.get(field_id.casefold(), field_id.casefold())


def _normalise_value(field_id: str, value: Any) -> Any:
    if field_id in {"size", "storage", "capacity"} and isinstance(value, str):
        normalized = value.casefold().replace(" ", "_")
        if field_id == "size" and normalized in {"s", "m", "l", "xl"}:
            return f"size_{normalized}"
        return normalized if normalized.startswith(f"{field_id}_") else value.casefold()
    return value


# A small, explicit color-distance policy keeps recommendations useful when a
# requested color is not modeled exactly.  It is a ranking preference only;
# an explicit "only/must be" color remains a hard constraint in the query
# state and is never relaxed here.
_COLOR_DISTANCE: dict[str, dict[str, int]] = {
    "red": {
        "red": 0,
        "maroon": 1,
        "burgundy": 1,
        "coral": 2,
        "rose": 2,
        "pink": 3,
        "orange": 3,
        "purple": 4,
        "black": 5,
        "navy": 6,
        "blue": 6,
        "olive": 7,
        "green": 7,
        "white": 8,
        "grey": 8,
        "gray": 8,
    },
    "blue": {
        "blue": 0,
        "navy": 1,
        "indigo": 1,
        "midblue": 1,
        "grey": 3,
        "gray": 3,
        "black": 4,
        "white": 5,
    },
    "green": {
        "green": 0,
        "olive": 1,
        "sage": 1,
        "tan": 3,
        "blue": 4,
        "black": 5,
        "white": 6,
    },
    "black": {"black": 0, "charcoal": 1, "navy": 2, "grey": 3, "gray": 3, "blue": 4, "white": 5},
    "white": {"white": 0, "silver": 1, "grey": 1, "gray": 1, "tan": 3, "black": 4},
    "grey": {"grey": 0, "gray": 0, "silver": 1, "black": 2, "white": 2, "blue": 3},
    "gray": {"grey": 0, "gray": 0, "silver": 1, "black": 2, "white": 2, "blue": 3},
}


def _color_similarity_score(expected: Any, actual: Any) -> float:
    expected_text = str(expected).casefold().replace(" ", "_")
    actual_text = str(actual).casefold().replace(" ", "_")
    distance = _COLOR_DISTANCE.get(expected_text, {}).get(actual_text)
    if distance is None:
        return 4.0 if expected_text == actual_text else 0.0
    return float(max(0, 9 - distance))


class FixtureCatalogPort(FakeCatalogPort):
    """Deterministic catalog adapter with eligibility and ranking semantics."""

    def _find_entry(self, binding: ProductBinding) -> SearchEntry | None:
        return next((entry for entry in self.entries if entry.binding == binding), None)

    @staticmethod
    def _fact(entry: SearchEntry, field_id: str) -> Fact | None:
        return next((fact for fact in entry.facts if fact.label == field_id), None)

    def _constraint_matches(self, entry: SearchEntry, constraint: Constraint) -> tuple[bool, bool]:
        field_id = _normalise_field(constraint.field_id)
        fact = self._fact(entry, field_id)
        if fact is None or fact.status in {TruthStatus.UNKNOWN, TruthStatus.NOT_MODELED}:
            return False, True
        actual = _normalise_value(field_id, fact.typed_value)
        values = [_normalise_value(field_id, value) for value in constraint.values]
        operator = constraint.operator
        if operator is ConstraintOperator.EQ:
            return _text(actual) == _text(values[0]), False
        if operator is ConstraintOperator.IN:
            return any(_text(actual) == _text(value) for value in values), False
        if operator is ConstraintOperator.ALL_OF:
            if not isinstance(actual, (list, tuple, set)):
                return False, False
            return all(_text(value) in {_text(item) for item in actual} for value in values), False
        actual_number = _number(actual)
        number_values = [_number(value) for value in values]
        if actual_number is None or any(value is None for value in number_values):
            return False, False
        if operator is ConstraintOperator.GTE:
            return actual_number >= number_values[0], False  # type: ignore[operator]
        if operator is ConstraintOperator.GT:
            return actual_number > number_values[0], False  # type: ignore[operator]
        if operator is ConstraintOperator.LTE:
            return actual_number <= number_values[0], False  # type: ignore[operator]
        if operator is ConstraintOperator.LT:
            return actual_number < number_values[0], False  # type: ignore[operator]
        if operator is ConstraintOperator.RANGE and len(number_values) >= 2:
            low, high = number_values[0], number_values[1]
            if low is None or high is None:
                return False, False
            return low <= actual_number <= high, False
        return False, False

    def _preference_score(self, entry: SearchEntry, preferences: Sequence[Preference]) -> float:
        score = 0.0
        for preference in preferences:
            field_id = _normalise_field(preference.field_id)
            fact = self._fact(entry, field_id)
            if fact is None or fact.status in {TruthStatus.UNKNOWN, TruthStatus.NOT_MODELED}:
                continue
            value = _number(fact.typed_value)
            if preference.operator is SoftOperator.PREFER_LOWER and value is not None:
                score += preference.weight * max(0.0, 10.0 - value / 500000.0)
            elif preference.operator is SoftOperator.PREFER_HIGHER and value is not None:
                score += preference.weight * min(10.0, value / 1000000.0)
            elif preference.operator in {SoftOperator.EQ, SoftOperator.IN}:
                expected = [_normalise_value(field_id, item) for item in preference.values]
                if field_id == "color":
                    score += preference.weight * max(
                        (_color_similarity_score(item, fact.typed_value) for item in expected),
                        default=0.0,
                    )
                elif any(_text(fact.typed_value) == _text(item) for item in expected):
                    score += preference.weight * 4.0
        return score

    def search(self, request: SearchRequest, deadline_ms: int) -> SearchResult:
        self.search_calls += 1
        candidates: list[tuple[float, SearchEntry, list[str], list[str]]] = []
        excluded = set(request.exclusions)
        query_terms = [
            term.casefold() for term in request.query_state.query_terms if len(term) >= 2
        ]
        for entry in self.entries:
            if entry.result_entry_id in excluded:
                continue
            matched: list[str] = []
            unknown: list[str] = []
            eligible = True
            for constraint in request.query_state.hard_constraints:
                is_match, is_unknown = self._constraint_matches(entry, constraint)
                if is_match:
                    matched.append(constraint.field_id)
                if is_unknown:
                    unknown.append(constraint.field_id)
                if not is_match:
                    eligible = False
            if not eligible:
                continue
            search_text = " ".join(
                [entry.title] + [str(fact.typed_value) for fact in entry.facts]
            ).casefold()
            query_hits = sum(1 for term in query_terms if term in search_text)
            score = (
                query_hits * 5.0
                + self._preference_score(entry, request.query_state.soft_preferences)
            )
            rating_fact = self._fact(entry, "rating")
            rating = _number(rating_fact.typed_value) if rating_fact else 0.0
            score += (rating or 0.0) / 10.0
            candidates.append((score, entry, matched, unknown))
        candidates.sort(
            key=lambda item: (
                -item[0],
                -(_number(self._fact(item[1], "rating").typed_value) or 0.0),
                _number(self._fact(item[1], "price").typed_value) or 0.0,
                item[1].result_entry_id,
            )
        )
        selected = candidates[: request.top_k]
        color_preferences = [
            preference
            for preference in request.query_state.soft_preferences
            if _normalise_field(preference.field_id) == "color"
            and preference.operator in {SoftOperator.EQ, SoftOperator.IN}
        ]
        warnings: list[str] = []
        if color_preferences and candidates:
            requested_colors = {
                _text(value)
                for preference in color_preferences
                for value in preference.values
            }
            exact_color_available = any(
                _text(self._fact(entry, "color").typed_value) in requested_colors
                for _, entry, _, _ in candidates
                if self._fact(entry, "color") is not None
            )
            if not exact_color_available:
                warnings.append("REQUESTED_COLOR_NOT_IN_DATASET")
                warnings.append("APPROXIMATE_COLOR_MATCH")
        results = [
            entry.model_copy(
                update={
                    "display_position": position,
                    "score_components": {
                        "deterministic_score": round(score, 4),
                        "query_match": float(
                            sum(1 for term in query_terms if term in entry.title.casefold())
                        ),
                        "color_similarity": round(
                            self._preference_score(entry, color_preferences), 4
                        )
                        if color_preferences
                        else 0.0,
                    },
                    "matched_criteria": matched[:8],
                    "unknown_criteria": unknown[:8],
                },
                deep=True,
            )
            for position, (score, entry, matched, unknown) in enumerate(selected, start=1)
        ]
        eligible_count = len(candidates)
        return SearchResult(
            status=ToolStatus.OK if results else ToolStatus.NOT_FOUND,
            result_set_id=f"fixture-result-set-{self.search_calls}",
            entries=results,
            eligible_count=eligible_count,
            confidence_signals={
                "coverage": min(1.0, eligible_count / max(1, request.top_k)),
                "eligible_ratio": eligible_count / max(1, len(self.entries)),
                "deterministic_ranking": 1.0,
            },
            hard_filter_hash=canonical_hash(request.query_state.hard_constraints),
            warnings=warnings if results else ["NO_ELIGIBLE_MATCH"],
        )

    def get_details(
        self,
        binding: ProductBinding,
        compatibility: Any,
        deadline_ms: int,
    ) -> ProductDetails:
        self.details_calls += 1
        entry = self._find_entry(binding)
        if entry is None:
            return ProductDetails(status=ToolStatus.NOT_FOUND, binding=binding)
        variants = [
            other.binding
            for other in self.entries
            if other.binding.product_id == binding.product_id and other.binding != binding
        ][:10]
        return ProductDetails(
            status=ToolStatus.OK,
            binding=binding,
            title=entry.title,
            facts=entry.facts,
            variants=variants,
        )

    def compare(
        self,
        bindings: Sequence[ProductBinding],
        compatibility: Any,
        deadline_ms: int,
    ) -> Comparison:
        self.compare_calls += 1
        fields = ("title", "category", "brand", "price", "rating", "availability")
        rows: list[ComparisonRow] = []
        for field_id in fields:
            cells: list[ComparisonCell] = []
            for binding in bindings:
                entry = self._find_entry(binding)
                fact = self._fact(entry, field_id) if entry else None
                cells.append(
                    ComparisonCell(
                        field_id=field_id,
                        value=(
                            entry.title
                            if field_id == "title" and entry
                            else fact.typed_value if fact else None
                        ),
                        status=fact.status if fact else TruthStatus.UNKNOWN,
                        evidence_refs=fact.evidence_refs if fact else [],
                    )
                )
            rows.append(ComparisonRow(field_id=field_id, label=field_id.title(), cells=cells))
        return Comparison(status=ToolStatus.OK, bindings=list(bindings), rows=rows)

    def check_availability(
        self,
        binding: ProductBinding,
        compatibility: Any,
        deadline_ms: int,
    ) -> Availability:
        entry = self._find_entry(binding)
        if entry is None:
            return Availability(
                status=ToolStatus.NOT_FOUND,
                binding=binding,
                availability_status="UNKNOWN",
                truth_status=TruthStatus.UNKNOWN,
                warnings=["BINDING_NOT_FOUND"],
            )
        availability_fact = self._fact(entry, "availability")
        quantity_fact = self._fact(entry, "stock_quantity")
        availability = str(availability_fact.typed_value) if availability_fact else "UNKNOWN"
        if availability not in {"AVAILABLE", "UNAVAILABLE", "UNKNOWN", "NOT_MODELED", "RETIRED"}:
            availability = "UNKNOWN"
        quantity = _number(quantity_fact.typed_value) if quantity_fact else None
        return Availability(
            status=ToolStatus.OK,
            binding=binding,
            availability_status=availability,
            quantity=int(quantity) if quantity is not None else None,
            as_of=availability_fact.as_of if availability_fact else None,
            truth_status=availability_fact.status if availability_fact else TruthStatus.UNKNOWN,
            scope=FactScope.CATALOG,
            provenance_type="SYNTHETIC_FIXTURE",
            evidence_refs=availability_fact.evidence_refs if availability_fact else [],
        )

    def check_eligibility(
        self,
        binding: ProductBinding,
        purpose: str,
        deadline_ms: int,
    ) -> CommerceEligibility:
        entry = self._find_entry(binding)
        if entry is None:
            return CommerceEligibility(
                eligible=False,
                binding=binding,
                policy_status="NOT_FOUND",
                reasons=["BINDING_NOT_FOUND"],
            )
        price_fact = self._fact(entry, "price")
        availability_fact = self._fact(entry, "availability")
        availability = str(availability_fact.typed_value) if availability_fact else "UNKNOWN"
        cart_blocked = purpose == "CART_UPDATE" and availability != "AVAILABLE"
        return CommerceEligibility(
            eligible=not cart_blocked,
            binding=binding,
            policy_status="ALLOWED" if not cart_blocked else "UNAVAILABLE",
            price=(
                price_fact.typed_value
                if price_fact and isinstance(price_fact.typed_value, Money)
                else None
            ),
            availability_status=availability,
            reasons=["PROTOTYPE_UNAVAILABLE"] if cart_blocked else [],
            evidence_refs=(price_fact.evidence_refs if price_fact else []),
        )

    def price_for_binding(self, binding: ProductBinding) -> Money | None:
        entry = self._find_entry(binding)
        fact = self._fact(entry, "price") if entry else None
        return fact.typed_value if fact and isinstance(fact.typed_value, Money) else None


class FixtureCartPort(FakeCartPort):
    """Use fixture prices when the model omits an optional expected price."""

    def __init__(
        self,
        snapshot: Any,
        catalog: FixtureCatalogPort,
        *,
        session_snapshot_provider: Callable[[], TurnSnapshot] | None = None,
    ) -> None:
        super().__init__(snapshot, session_snapshot_provider=session_snapshot_provider)
        self.catalog = catalog

    def update_cart(self, request: UpdateCartRequest, deadline_ms: int) -> UpdateCartResult:
        operations = [
            operation.model_copy(
                update={"expected_unit_price": self.catalog.price_for_binding(operation.binding)}
            )
            if isinstance(operation, AddItemOperation)
            and operation.expected_unit_price is None
            and self.catalog.price_for_binding(operation.binding) is not None
            else operation
            for operation in request.operations
        ]
        return super().update_cart(
            request.model_copy(update={"operations": operations}), deadline_ms
        )
