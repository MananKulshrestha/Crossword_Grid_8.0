"""Deterministic catalog truth, retrieval, reference resolution, and read tools."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Any

from .contracts import (
    Availability,
    CatalogRecord,
    CommerceEligibility,
    Comparison,
    CompatibilityTuple,
    EvidenceRef,
    ProductBinding,
    ProductDetails,
    QueryState,
    ReferenceResolution,
    SearchEntry,
    SearchRequest,
    SearchResult,
)
from .determinism import canonical_hash, normalize_text, stable_id, tokenize, unique_sorted


def _binding_key(binding: ProductBinding) -> tuple[str, str, str, str | None]:
    return binding.product_id, binding.sku_id, binding.offer_id, binding.variant_id


def _record_evidence(record: CatalogRecord, compatibility: CompatibilityTuple) -> list[EvidenceRef]:
    if record.evidence_refs:
        return list(record.evidence_refs)
    return [
        EvidenceRef(
            evidence_id=stable_id("ev", {"binding": record.binding, "field": "catalog"}),
            source="catalog_record",
            field="catalog_record",
            version=compatibility.catalog_version,
        )
    ]


class DeterministicCatalog:
    """In-memory reference implementation of the CatalogSearchPort contract.

    It is intentionally small and exact: eligibility happens before ranking,
    missing values remain unknown, and every returned entry is bound to one
    compatibility tuple. A database or hybrid index can replace this adapter
    without changing the request/result shapes.
    """

    def __init__(
        self, records: Iterable[CatalogRecord] = (), compatibility: CompatibilityTuple | None = None
    ) -> None:
        self.compatibility = compatibility or CompatibilityTuple()
        self._records = {_binding_key(record.binding): record for record in records}

    @property
    def records(self) -> list[CatalogRecord]:
        return sorted(self._records.values(), key=lambda record: _binding_key(record.binding))

    def replace(self, records: Iterable[CatalogRecord]) -> None:
        self._records = {_binding_key(record.binding): record for record in records}

    def _record(self, binding: ProductBinding) -> CatalogRecord | None:
        return self._records.get(_binding_key(binding))

    def check_commerce_eligibility(
        self,
        binding: ProductBinding,
        purpose: str = "read",
        compatibility: CompatibilityTuple | None = None,
    ) -> CommerceEligibility:
        pinned = compatibility or self.compatibility
        record = self._record(binding)
        if record is None:
            return CommerceEligibility(
                status="UNKNOWN",
                eligible=None,
                binding=binding,
                purpose=purpose,
                availability_status="UNKNOWN",
                reason_codes=["BINDING_NOT_FOUND"],
                compatibility=pinned,
            )
        if pinned.catalog_version != self.compatibility.catalog_version:
            return CommerceEligibility(
                status="STALE",
                eligible=None,
                binding=binding,
                purpose=purpose,
                policy_status="STALE",
                availability_status=record.availability,
                price=record.price,
                reason_codes=["CATALOG_VERSION_MISMATCH"],
                compatibility=pinned,
                evidence_refs=_record_evidence(record, self.compatibility),
            )
        if purpose == "CART_UPDATE" and record.availability != "AVAILABLE":
            return CommerceEligibility(
                status="INELIGIBLE",
                eligible=False,
                binding=binding,
                purpose=purpose,
                policy_status="UNAVAILABLE",
                availability_status=record.availability,
                price=record.price,
                reason_codes=["PROTOTYPE_UNAVAILABLE"],
                compatibility=pinned,
                evidence_refs=_record_evidence(record, pinned),
            )
        if not record.eligible:
            return CommerceEligibility(
                status="INELIGIBLE",
                eligible=False,
                binding=binding,
                purpose=purpose,
                policy_status="BLOCKED",
                availability_status=record.availability,
                price=record.price,
                reason_codes=["RECORD_NOT_ELIGIBLE"],
                compatibility=pinned,
                evidence_refs=_record_evidence(record, pinned),
            )
        return CommerceEligibility(
            status="ELIGIBLE",
            eligible=True,
            binding=binding,
            purpose=purpose,
            policy_status="ALLOWED",
            availability_status=record.availability,
            price=record.price,
            reason_codes=[],
            compatibility=pinned,
            evidence_refs=_record_evidence(record, pinned),
        )

    @staticmethod
    def _matches_hard_filters(record: CatalogRecord, state: QueryState) -> tuple[bool, list[str]]:
        filters = dict(state.hard_filters)
        if state.category_id is not None:
            filters.setdefault("category_id", state.category_id)
        reasons: list[str] = []
        if filters.get("unsupported_constraints"):
            return False, ["UNSUPPORTED_HARD_CONSTRAINT"]
        if "category_id" in filters:
            expected_categories = filters["category_id"]
            expected_categories = (
                expected_categories
                if isinstance(expected_categories, list)
                else [expected_categories]
            )
            if record.category_id not in {
                normalize_text(str(value)) for value in expected_categories
            }:
                return False, ["CATEGORY_MISMATCH"]
        if "brand" in filters:
            expected_brands = filters["brand"]
            expected_brands = (
                expected_brands if isinstance(expected_brands, list) else [expected_brands]
            )
            if record.brand is None or normalize_text(record.brand) not in {
                normalize_text(str(value)) for value in expected_brands
            }:
                return False, ["BRAND_MISMATCH"]
        if "min_price" in filters:
            if record.price is None:
                return False, ["PRICE_UNKNOWN"]
            if record.price < float(filters["min_price"]):
                return False, ["BELOW_MIN_PRICE"]
        if "min_price_exclusive" in filters:
            if record.price is None:
                return False, ["PRICE_UNKNOWN"]
            if record.price <= float(filters["min_price_exclusive"]):
                return False, ["NOT_ABOVE_MIN_PRICE"]
        if "max_price" in filters:
            if record.price is None:
                return False, ["PRICE_UNKNOWN"]
            if record.price > float(filters["max_price"]):
                return False, ["ABOVE_MAX_PRICE"]
        if "max_price_exclusive" in filters:
            if record.price is None:
                return False, ["PRICE_UNKNOWN"]
            if record.price >= float(filters["max_price_exclusive"]):
                return False, ["NOT_BELOW_MAX_PRICE"]
        if "price_values" in filters:
            if record.price is None:
                return False, ["PRICE_UNKNOWN"]
            if record.price not in {float(value) for value in filters["price_values"]}:
                return False, ["PRICE_NOT_IN_SET"]
        if "availability" in filters:
            expected_availability = filters["availability"]
            expected_availability = (
                expected_availability
                if isinstance(expected_availability, list)
                else [expected_availability]
            )
            if record.availability not in {str(value) for value in expected_availability}:
                return False, ["AVAILABILITY_MISMATCH"]
        for name, expected in dict(filters.get("attributes", {})).items():
            actual = record.attributes.get(name)
            if actual is None:
                return False, [f"ATTRIBUTE_UNKNOWN:{name}"]
            expected_values = expected if isinstance(expected, list) else [expected]
            if normalize_text(actual) not in {
                normalize_text(str(value)) for value in expected_values
            }:
                return False, [f"ATTRIBUTE_MISMATCH:{name}"]
        for name, expected_values in dict(filters.get("attribute_all_of", {})).items():
            actual = record.attributes.get(name)
            if actual is None:
                return False, [f"ATTRIBUTE_UNKNOWN:{name}"]
            if not all(
                normalize_text(str(value)) in normalize_text(actual) for value in expected_values
            ):
                return False, [f"ATTRIBUTE_MISMATCH:{name}"]
        for name, bounds in dict(filters.get("attribute_ranges", {})).items():
            actual = record.attributes.get(name)
            if actual is None:
                return False, [f"ATTRIBUTE_UNKNOWN:{name}"]
            try:
                actual_value = float(actual)
                lower, upper = (float(value) for value in bounds[:2])
            except (TypeError, ValueError):
                return False, [f"ATTRIBUTE_NOT_NUMERIC:{name}"]
            if not lower <= actual_value <= upper:
                return False, [f"ATTRIBUTE_OUT_OF_RANGE:{name}"]
        for key, field in (
            ("exclude_product_ids", record.binding.product_id),
            ("exclude_sku_ids", record.binding.sku_id),
            ("exclude_offer_ids", record.binding.offer_id),
        ):
            if field in set(str(value) for value in filters.get(key, [])):
                return False, [f"{key.upper()}_EXCLUDED"]
        if state.excluded_terms:
            haystack = normalize_text(" ".join([record.title, record.description, *record.aliases]))
            if any(normalize_text(term) in haystack for term in state.excluded_terms):
                return False, ["EXCLUDED_TERM"]
        return True, reasons

    @staticmethod
    def _score(record: CatalogRecord, terms: Sequence[str]) -> tuple[float, dict[str, float]]:
        normalized_title = normalize_text(record.title)
        normalized_description = normalize_text(record.description)
        normalized_brand = normalize_text(record.brand or "")
        normalized_attributes = normalize_text(
            " ".join(f"{key} {value}" for key, value in record.attributes.items())
        )
        components = {"title": 0.0, "brand": 0.0, "description": 0.0, "attributes": 0.0}
        for term in terms:
            normalized_term = normalize_text(term)
            if not normalized_term:
                continue
            if normalized_term in normalized_title:
                components["title"] += 1.0
            if normalized_term and normalized_term == normalized_brand:
                components["brand"] += 1.0
            if normalized_term in normalized_description:
                components["description"] += 0.35
            if normalized_term in normalized_attributes:
                components["attributes"] += 0.5
        score = (
            components["title"] * 3.0
            + components["brand"] * 2.0
            + components["attributes"]
            + components["description"]
        )
        return round(score, 6), components

    def search(self, request: SearchRequest, deadline_ms: int = 75) -> SearchResult:
        del deadline_ms  # deterministic adapter does not sleep or call a provider
        state = request.query_state
        terms = unique_sorted([*state.normalized_terms, *tokenize(state.text), *state.soft_terms])
        known_tokens = set(
            tokenize(
                " ".join(
                    item
                    for record in self.records
                    for item in (
                        record.title,
                        record.description,
                        record.category_id,
                        record.brand or "",
                        *record.attributes.values(),
                        *record.aliases,
                    )
                )
            )
        )
        unknown_terms = [term for term in terms if term not in known_tokens]
        eligible: list[tuple[float, dict[str, float], CatalogRecord]] = []
        zero_score_candidates: list[tuple[float, dict[str, float], CatalogRecord]] = []
        excluded = {str(item) for item in request.exclusions}
        for record in self.records:
            if excluded.intersection(
                {record.binding.product_id, record.binding.sku_id, record.binding.offer_id}
            ):
                continue
            matches, _ = self._matches_hard_filters(record, state)
            if not matches or not record.eligible:
                continue
            score, components = self._score(record, terms)
            if terms and score <= 0:
                zero_score_candidates.append((score, components, record))
                continue
            eligible.append((score, components, record))
        if not eligible and zero_score_candidates and state.hard_filters and state.soft_terms:
            # A known category/attribute constraint can safely anchor an
            # approximate soft preference (for example, "red t-shirts" when
            # the synthetic catalog has maroon but no red t-shirt). Hard
            # filters remain unchanged; only deterministic fallback ranking is
            # used for the soft term.
            eligible = zero_score_candidates
        eligible.sort(key=lambda item: (-item[0], _binding_key(item[2].binding)))
        selected = eligible[: request.top_k]
        query_hash = canonical_hash({"state": state, "compatibility": request.compatibility})
        result_set_id = stable_id(
            "rs",
            {
                "query_hash": query_hash,
                "entries": [_binding_key(item[2].binding) for item in selected],
            },
        )
        entries = [
            SearchEntry(
                rank=index,
                binding=record.binding,
                title=record.title,
                category_id=record.category_id,
                score=score,
                score_components=components,
                attributes=record.attributes,
                price=record.price,
                availability=record.availability,
                evidence_refs=_record_evidence(record, request.compatibility),
            )
            for index, (score, components, record) in enumerate(selected, start=1)
        ]
        status = "OK" if entries else "NO_MATCH"
        confidence = (
            0.0 if not eligible else min(1.0, selected[0][0] / 4.0 if selected[0][0] else 0.2)
        )
        return SearchResult(
            status=status,
            result_set_id=result_set_id,
            eligible_count=len(eligible),
            entries=entries,
            compatibility=request.compatibility,
            confidence_signals={
                "top_score": round(confidence, 6),
                "eligible_count": float(len(eligible)),
            },
            unknown_terms=unknown_terms,
            query_hash=query_hash,
        )

    def _version_ok(self, compatibility: CompatibilityTuple) -> bool:
        return compatibility.catalog_version == self.compatibility.catalog_version

    def get_product_details(
        self,
        binding: ProductBinding,
        compatibility: CompatibilityTuple | None = None,
        deadline_ms: int = 75,
    ) -> ProductDetails:
        del deadline_ms
        pinned = compatibility or self.compatibility
        record = self._record(binding)
        if not self._version_ok(pinned):
            return ProductDetails(status="STALE", binding=binding, compatibility=pinned)
        if record is None:
            return ProductDetails(status="NOT_FOUND", binding=binding, compatibility=pinned)
        return ProductDetails(
            status="OK",
            binding=binding,
            title=record.title,
            category_id=record.category_id,
            brand=record.brand,
            description=record.description,
            attributes=record.attributes,
            price=record.price,
            availability=record.availability,
            compatibility=pinned,
            evidence_refs=_record_evidence(record, pinned),
        )

    def compare_products(
        self,
        bindings: Sequence[ProductBinding],
        compatibility: CompatibilityTuple | None = None,
        deadline_ms: int = 75,
    ) -> Comparison:
        del deadline_ms
        pinned = compatibility or self.compatibility
        if not self._version_ok(pinned):
            return Comparison(status="STALE", bindings=list(bindings), compatibility=pinned)
        records = [self._record(binding) for binding in bindings]
        if not records or any(record is None for record in records):
            return Comparison(status="NOT_FOUND", bindings=list(bindings), compatibility=pinned)
        assert all(record is not None for record in records)
        keys = sorted(
            {"title", "category_id", "brand", "price", "availability"}
            | {key for record in records for key in record.attributes}
        )
        rows: dict[str, list[Any]] = {}
        evidence: list[EvidenceRef] = []
        for key in keys:
            values: list[Any] = []
            for record in records:
                assert record is not None
                value = (
                    record.attributes.get(key)
                    if key not in {"title", "category_id", "brand", "price", "availability"}
                    else getattr(record, key)
                )
                values.append(value)
                evidence.extend(_record_evidence(record, pinned))
            rows[key] = values
        unique_evidence = {item.evidence_id: item for item in evidence}
        return Comparison(
            status="OK",
            bindings=list(bindings),
            rows=rows,
            compatibility=pinned,
            evidence_refs=list(unique_evidence.values()),
        )

    def check_availability(
        self,
        binding: ProductBinding,
        compatibility: CompatibilityTuple | None = None,
        deadline_ms: int = 75,
    ) -> Availability:
        del deadline_ms
        pinned = compatibility or self.compatibility
        record = self._record(binding)
        if not self._version_ok(pinned):
            return Availability(
                status="STALE",
                binding=binding,
                availability="UNKNOWN",
                reason="CATALOG_VERSION_MISMATCH",
                compatibility=pinned,
            )
        if record is None:
            return Availability(
                status="UNKNOWN",
                binding=binding,
                availability="UNKNOWN",
                reason="BINDING_NOT_FOUND",
                compatibility=pinned,
            )
        return Availability(
            status=record.availability,
            binding=binding,
            availability=record.availability,
            reason="CATALOGUE_SEMANTICS_ONLY",
            compatibility=pinned,
            evidence_refs=_record_evidence(record, pinned),
        )

    @staticmethod
    def _ordinal(value: str) -> int | None:
        text = normalize_text(value)
        match = re.fullmatch(r"(?:the )?(\d+)(?:st|nd|rd|th)?(?: one)?", text)
        if match:
            return int(match.group(1))
        words = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}
        return words.get(text.removeprefix("the ").replace(" one", ""))

    def resolve_reference(
        self,
        references: Sequence[str],
        active_result: SearchResult | None,
        compatibility: CompatibilityTuple | None = None,
        result_set_id: str | None = None,
    ) -> ReferenceResolution:
        pinned = compatibility or self.compatibility
        if active_result is None:
            return ReferenceResolution(
                status="NOT_FOUND", unresolved=list(references), compatibility=pinned
            )
        if result_set_id is not None and result_set_id != active_result.result_set_id:
            return ReferenceResolution(
                status="STALE",
                unresolved=list(references),
                result_set_id=active_result.result_set_id,
                compatibility=pinned,
            )
        if active_result.compatibility != pinned:
            return ReferenceResolution(
                status="STALE",
                unresolved=list(references),
                result_set_id=active_result.result_set_id,
                compatibility=pinned,
            )
        resolved: list[ProductBinding] = []
        unresolved: list[str] = []
        ambiguous: dict[str, list[ProductBinding]] = {}
        by_id: dict[str, list[ProductBinding]] = defaultdict(list)
        for entry in active_result.entries:
            for identifier in (
                entry.binding.product_id,
                entry.binding.sku_id,
                entry.binding.offer_id,
            ):
                by_id[identifier].append(entry.binding)
        for reference in references:
            ordinal = self._ordinal(reference)
            if ordinal is not None:
                if 1 <= ordinal <= len(active_result.entries):
                    resolved.append(active_result.entries[ordinal - 1].binding)
                else:
                    unresolved.append(reference)
                continue
            matches = by_id.get(reference, [])
            if len(matches) == 1:
                resolved.append(matches[0])
            elif len(matches) > 1:
                ambiguous[reference] = matches
            else:
                unresolved.append(reference)
        if ambiguous:
            status = "AMBIGUOUS"
        elif unresolved:
            status = "NOT_FOUND"
        else:
            status = "RESOLVED"
        return ReferenceResolution(
            status=status,
            resolved=resolved,
            unresolved=unresolved,
            ambiguous=ambiguous,
            result_set_id=active_result.result_set_id,
            compatibility=pinned,
        )
