
"""Graph-extraction subset selector (lightrag-implementation.md, section 5):
chooses which SKUs get full LightRAG entity/relation extraction
(``process_options=""``) versus chunk-embedding-only
(``process_options="!"``, LightRAG's native ``skip_kg`` flag), by coverage
over structured metadata instead of random sampling.

Does not call LightRAG, Ollama, or any LLM -- this only reads
flipkart_catalog_structured.jsonl and the description text already used by
BM25/LightRAG (via load_documents.build_documents(), the same source, per
retrieval-architecture.md's "one text corpus, two search engines" rule) and
produces a plain list of SKU IDs to run full extraction on.

Algorithm (lightrag-implementation.md section 5.4):
  1. Collapse near-duplicate SKUs into "product families" (same brand +
     category + color-stripped product name) -- one representative per
     family, preferring the richest (longest) description.
  2. Build a coverage-target set from structured metadata: every non-
     fallback category, every brand with >= MIN_BRAND_SKUS SKUs, every
     material with >= MIN_MATERIAL_SKUS SKUs, plus a fixed occasion/style
     keyword vocabulary scanned over the description text (a proxy signal,
     since OCCASION/STYLE have no structured column at all).
  3. Greedily pick the family whose target set covers the most currently
     under-covered targets, until every target has TARGET_MULTIPLICITY
     representatives or the budget is exhausted.
  4. Top up any category that's still below CATEGORY_FLOOR after the greedy
     pass, so no category is left with near-zero internal relational
     richness.
"""

import json
import os
import re
from collections import Counter, defaultdict

from config import SOURCE_JSONL
from load_documents import build_documents

# --- Tunables (lightrag-implementation.md section 5.4/5.5) -----------------
MIN_BRAND_SKUS = 3        # below this, a brand can never form a SAME_BRAND_AS edge
MIN_MATERIAL_SKUS = 10    # below this, a material value is mostly spelling-variant noise
TARGET_MULTIPLICITY = 2   # exemplars needed per target for a relationship to form at all
CATEGORY_FLOOR = 5        # minimum chosen SKUs per real category, enforced after greedy
MAX_SUBSET_SIZE = 3000    # safety ceiling, not a target -- the greedy loop stops
                          # itself once no remaining candidate adds coverage (measured
                          # at 2,658 products on the real catalog); this cap only
                          # guards against a pathological run, see lightrag-implementation.md 5.5

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "graph_sampling_output")
SUBSET_PATH = os.path.join(OUTPUT_DIR, "full_extraction_skus.txt")
REPORT_PATH = os.path.join(OUTPUT_DIR, "coverage_report.json")

# Stripped from product_name before family-grouping -- variant color words,
# not real distinguishing information between otherwise-identical listings.
COLOR_WORDS = {
    "black", "white", "red", "blue", "green", "yellow", "pink", "purple",
    "grey", "gray", "brown", "navy", "maroon", "orange", "beige", "gold",
    "silver", "multicolor", "multicoloured", "tan", "cream", "khaki",
}

# OCCASION and STYLE have no structured column (lightrag-implementation.md
# section 5.2) -- this fixed vocabulary is a cheap regex proxy used only to
# steer *sampling*, never fed into LightRAG itself.
OCCASION_KEYWORDS = [
    "wedding", "party", "festive", "casual", "formal", "office",
    "daily wear", "sports", "outdoor", "travel", "gifting", "gift",
    "diwali", "valentine", "anniversary", "birthday", "beach", "monsoon",
    "winter", "summer",
]
STYLE_KEYWORDS = [
    "solid", "printed", "striped", "checked", "embroidered",
    "slim fit", "regular fit", "skinny fit", "loose fit",
    "traditional", "western", "designer", "vintage", "modern",
    "floral", "graphic", "plain", "textured",
]

_WORD_RE = re.compile(r"[a-zA-Z']+")


def load_structured_records():
    records = []
    with open(SOURCE_JSONL, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def normalize_product_name(name):
    """Lowercase, strip punctuation, drop color words -- collapses variant
    SKUs (different print/color of the same base product) onto the same
    family key. No stemming, matching bm25_index.tokenize's convention."""
    if not name:
        return ""
    tokens = [t for t in _WORD_RE.findall(name.lower()) if t not in COLOR_WORDS]
    return " ".join(tokens)


def keyword_present(keyword, text_lower):
    return keyword in text_lower


def candidate_targets(rec, text_lower, category_targets, brand_targets, material_targets):
    targets = set()
    if not rec.get("category_is_fallback") and rec.get("category") in category_targets:
        targets.add(("category", rec["category"]))
    if rec.get("brand") in brand_targets:
        targets.add(("brand", rec["brand"]))
    if rec.get("material") in material_targets:
        targets.add(("material", rec["material"]))
    for kw in OCCASION_KEYWORDS:
        if keyword_present(kw, text_lower):
            targets.add(("occasion", kw))
    for kw in STYLE_KEYWORDS:
        if keyword_present(kw, text_lower):
            targets.add(("style", kw))
    return targets


def build_target_registry(records):
    category_counts = Counter(
        r["category"] for r in records if not r.get("category_is_fallback") and r.get("category")
    )
    brand_counts = Counter(r.get("brand") for r in records if r.get("brand"))
    material_counts = Counter(r.get("material") for r in records if r.get("material"))

    categories = set(category_counts)
    brands = {b for b, c in brand_counts.items() if c >= MIN_BRAND_SKUS}
    materials = {m for m, c in material_counts.items() if c >= MIN_MATERIAL_SKUS}
    return categories, brands, materials


def build_families(records, descriptions, category_targets, brand_targets, material_targets):
    """Groups records into (brand, category, normalized_name) families.
    Only records with a usable description (present in `descriptions`) are
    eligible -- a SKU with no description can't be usefully extracted
    regardless, so it's never a candidate exemplar (it will simply end up
    skip_kg, correctly).

    Returns dict: family_key -> chosen representative dict with keys
    sku_id, category, text_len, targets.
    """
    family_members = defaultdict(list)
    for rec in records:
        sku_id = rec["sku_id"]
        text = descriptions.get(sku_id)
        if text is None:
            continue
        key = (rec.get("brand") or "", rec.get("category") or "", normalize_product_name(rec.get("product_name")))
        family_members[key].append((rec, text))

    families = {}
    for key, members in family_members.items():
        rec, text = max(members, key=lambda pair: (len(pair[1]), pair[0]["sku_id"]))
        text_lower = text.lower()
        families[key] = {
            "sku_id": rec["sku_id"],
            "category": rec.get("category") if not rec.get("category_is_fallback") else None,
            "text_len": len(text),
            "targets": candidate_targets(rec, text_lower, category_targets, brand_targets, material_targets),
        }
    return families


def greedy_select(families, target_multiplicity=TARGET_MULTIPLICITY, max_subset_size=MAX_SUBSET_SIZE):
    covered_count = defaultdict(int)
    chosen = []
    chosen_skus = set()
    remaining = list(families.values())

    def need(target):
        return max(0, target_multiplicity - covered_count[target])

    while remaining and len(chosen) < max_subset_size:
        best = None
        best_score = 0
        for cand in remaining:
            score = sum(1 for t in cand["targets"] if need(t) > 0)
            if score > best_score or (
                score == best_score and score > 0 and
                best is not None and cand["text_len"] > best["text_len"]
            ):
                best, best_score = cand, score
        if best is None or best_score == 0:
            break
        chosen.append(best)
        chosen_skus.add(best["sku_id"])
        for t in best["targets"]:
            covered_count[t] += 1
        remaining.remove(best)

    return chosen, chosen_skus, covered_count


def enforce_category_floor(chosen, chosen_skus, families, category_targets, floor=CATEGORY_FLOOR):
    families_by_category = defaultdict(list)
    for fam in families.values():
        if fam["category"]:
            families_by_category[fam["category"]].append(fam)

    category_counts = Counter(fam["category"] for fam in chosen if fam["category"])
    additions = 0
    for category in category_targets:
        have = category_counts.get(category, 0)
        if have >= floor:
            continue
        pool = [
            fam for fam in families_by_category.get(category, [])
            if fam["sku_id"] not in chosen_skus
        ]
        pool.sort(key=lambda fam: -fam["text_len"])
        for fam in pool:
            if have >= floor:
                break
            chosen.append(fam)
            chosen_skus.add(fam["sku_id"])
            have += 1
            additions += 1
    return additions


def run():
    records = load_structured_records()
    documents, _skipped = build_documents(limit=None)
    descriptions = dict(documents)

    category_targets, brand_targets, material_targets = build_target_registry(records)
    families = build_families(records, descriptions, category_targets, brand_targets, material_targets)

    chosen, chosen_skus, covered_count = greedy_select(families)
    floor_additions = enforce_category_floor(chosen, chosen_skus, families, category_targets)

    covered_categories = {t[1] for t in covered_count if t[0] == "category" and covered_count[t] > 0}
    covered_categories |= {fam["category"] for fam in chosen if fam["category"]}
    covered_brands = {t[1] for t in covered_count if t[0] == "brand" and covered_count[t] > 0}
    covered_materials = {t[1] for t in covered_count if t[0] == "material" and covered_count[t] > 0}
    covered_occasions = sorted(t[1] for t in covered_count if t[0] == "occasion" and covered_count[t] > 0)
    covered_styles = sorted(t[1] for t in covered_count if t[0] == "style" and covered_count[t] > 0)

    report = {
        "total_skus": len(records),
        "skus_with_usable_description": len(descriptions),
        "total_families": len(families),
        "chosen_count": len(chosen_skus),
        "budget": MAX_SUBSET_SIZE,
        "category_floor_additions": floor_additions,
        "category_targets": len(category_targets),
        "category_covered": len(covered_categories & category_targets),
        "brand_targets": len(brand_targets),
        "brand_covered": len(covered_brands),
        "material_targets": len(material_targets),
        "material_covered": len(covered_materials),
        "occasion_keywords": OCCASION_KEYWORDS,
        "occasion_covered": covered_occasions,
        "style_keywords": STYLE_KEYWORDS,
        "style_covered": covered_styles,
    }

    # No mid-run checkpoint: the whole selection is a single in-memory pass
    # over the full catalog (no LLM/network calls, no per-item state to
    # resume), so a hard interrupt just means re-running `python
    # graph_sampling.py` from scratch -- cheap at this catalog size. What DOES
    # matter is that a crash mid-write never leaves ingest.py reading a
    # truncated/partial subset file: write to a temp file and os.replace()
    # (atomic on POSIX) so SUBSET_PATH is either the previous good version or
    # the new complete one, never a half-written one.
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    tmp_subset_path = SUBSET_PATH + ".tmp"
    with open(tmp_subset_path, "w", encoding="utf-8") as f:
        for sku_id in sorted(chosen_skus):
            f.write(sku_id + "\n")
    os.replace(tmp_subset_path, SUBSET_PATH)

    tmp_report_path = REPORT_PATH + ".tmp"
    with open(tmp_report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    os.replace(tmp_report_path, REPORT_PATH)

    return chosen_skus, report


if __name__ == "__main__":
    chosen_skus, report = run()
    print("=== Graph sampling summary ===")
    print(f"Total SKUs in catalog:          {report['total_skus']}")
    print(f"SKUs with usable description:   {report['skus_with_usable_description']}")
    print(f"Product families after dedup:   {report['total_families']}")
    print(f"Chosen for full extraction:     {report['chosen_count']} "
          f"({report['chosen_count'] / report['total_skus'] * 100:.1f}% of catalog)")
    print(f"Category floor top-up added:    {report['category_floor_additions']}")
    print()
    print(f"Category coverage:  {report['category_covered']}/{report['category_targets']}")
    print(f"Brand coverage:     {report['brand_covered']}/{report['brand_targets']}")
    print(f"Material coverage:  {report['material_covered']}/{report['material_targets']}")
    print(f"Occasion keywords hit: {len(report['occasion_covered'])}/{len(OCCASION_KEYWORDS)} "
          f"{report['occasion_covered']}")
    print(f"Style keywords hit:    {len(report['style_covered'])}/{len(STYLE_KEYWORDS)} "
          f"{report['style_covered']}")
    print()
    print(f"Subset written to:  {SUBSET_PATH}")
    print(f"Report written to:  {REPORT_PATH}")
