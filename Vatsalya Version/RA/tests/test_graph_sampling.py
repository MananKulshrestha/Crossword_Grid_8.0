"""Tests for graph_sampling against the real catalog -- no mocking, since
this module's whole job is picking a real, reviewable subset of real SKUs
(lightrag-implementation.md, section 5). Runs the actual greedy selection
and checks properties of its real output, the same discipline as
tests/test_sql_filter.py and tests/test_bm25.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import graph_sampling as gs  # noqa: E402


def _run():
    records = gs.load_structured_records()
    from load_documents import build_documents

    descriptions = dict(build_documents(limit=None)[0])
    category_targets, brand_targets, material_targets = gs.build_target_registry(records)
    families = gs.build_families(records, descriptions, category_targets, brand_targets, material_targets)
    chosen, chosen_skus, _ = gs.greedy_select(families)
    gs.enforce_category_floor(chosen, chosen_skus, families, category_targets)
    return records, category_targets, brand_targets, material_targets, chosen, chosen_skus, families


def test_subset_is_within_budget_plus_floor_topup():
    _, category_targets, _, _, chosen, chosen_skus, _ = _run()
    assert len(chosen_skus) == len(chosen)
    # MAX_SUBSET_SIZE is a safety ceiling, not a target -- the greedy loop
    # stops itself once nothing adds coverage (measured at 2,658 products on
    # the real catalog); this just guards against a pathological run.
    assert len(chosen_skus) <= gs.MAX_SUBSET_SIZE + len(category_targets) * gs.CATEGORY_FLOOR


def test_no_duplicate_skus_in_chosen_set():
    _, _, _, _, chosen, chosen_skus, _ = _run()
    assert len(chosen) == len(chosen_skus)


def test_every_real_category_meets_the_floor_or_is_exhausted():
    # "Available" must be counted in FAMILIES, not raw SKU rows: some
    # `category` values (a genuine data-quality quirk, not a bug here --
    # e.g. "Olvin Aviator Sunglasses") are themselves a single product's
    # name, so every one of that category's SKUs collapses into one family
    # and top-up can never reach the floor -- that's correct, not a failure.
    _, category_targets, _, _, chosen, _, families = _run()
    from collections import Counter

    catalog_family_counts = Counter(
        fam["category"] for fam in families.values() if fam["category"]
    )
    chosen_category_counts = Counter(fam["category"] for fam in chosen if fam["category"])
    for category in category_targets:
        available_families = catalog_family_counts[category]
        have = chosen_category_counts.get(category, 0)
        assert have >= min(gs.CATEGORY_FLOOR, available_families), (
            f"{category!r}: only {have} chosen, expected >= "
            f"min({gs.CATEGORY_FLOOR}, {available_families} available families)"
        )


def test_high_volume_variant_cluster_collapses_to_one_representative():
    # TheLostPuppy's "back cover for Apple iPad Air" line is a real,
    # measured 229-SKU near-duplicate cluster (lightrag-implementation.md,
    # section 5.3) -- confirms family-dedup actually suppresses redundant
    # variants instead of picking several near-identical SKUs from it.
    records, _, _, _, _, chosen_skus, _ = _run()
    family_skus = {
        r["sku_id"]
        for r in records
        if r.get("brand") == "TheLostPuppy"
        and "ipad air" in (r.get("product_name") or "").lower()
    }
    assert len(family_skus) >= 100  # sanity-check the cluster is really there
    assert len(family_skus & chosen_skus) == 1


def test_brand_target_registry_excludes_singleton_and_low_frequency_brands():
    records, _, brand_targets, _, _, _, _ = _run()
    from collections import Counter

    brand_counts = Counter(r.get("brand") for r in records if r.get("brand"))
    assert all(brand_counts[b] >= gs.MIN_BRAND_SKUS for b in brand_targets)
    # A brand known to appear exactly once must not be a coverage target --
    # it could never form a SAME_BRAND_AS edge regardless of sampling.
    singleton_brands = {b for b, c in brand_counts.items() if c == 1}
    assert singleton_brands - brand_targets == singleton_brands


def test_output_files_are_consistent_with_a_fresh_run():
    import json

    chosen_skus, report = gs.run()
    with open(gs.SUBSET_PATH, encoding="utf-8") as f:
        written_skus = {line.strip() for line in f if line.strip()}
    assert written_skus == chosen_skus
    assert report["chosen_count"] == len(chosen_skus)
    with open(gs.REPORT_PATH, encoding="utf-8") as f:
        written_report = json.load(f)
    assert written_report["chosen_count"] == len(chosen_skus)
