"""Generates dataset.json: ~50 multi-turn (<=3 turn) shopper conversations
with hand-derived expected hard/soft context per turn, SIMMC-2.0-style.

Each turn's "expected" is what the query extractor + enhancer SHOULD produce:
    hard: RerankerRequest.hard_constraints after this turn
    soft: RerankerRequest.soft_query_text after this turn (cumulative - a
          REFINE appends to the prior soft text, never replaces it)

Run once to (re)materialize dataset.json; evaluate.py only reads that file.
"""

from __future__ import annotations

import json
import os

# (soft_query_text seed, category) - 25 distinct products.
PRODUCTS = [
    ("blue running shoes", "Footwear"),
    ("red silk saree", "Clothing"),
    ("black leather wallet", "Accessories"),
    ("wireless bluetooth headphones", "Electronics"),
    ("green cotton kurta", "Clothing"),
    ("stainless steel water bottle", "Home"),
    ("gaming laptop", "Electronics"),
    ("white sneakers", "Footwear"),
    ("kids school backpack", "Bags"),
    ("formal shirt", "Clothing"),
    ("yoga mat", "Sports"),
    ("smart watch", "Electronics"),
    ("denim jacket", "Clothing"),
    ("office chair", "Furniture"),
    ("electric kettle", "Home"),
    ("sunglasses", "Accessories"),
    ("running shorts", "Clothing"),
    ("bluetooth speaker", "Electronics"),
    ("leather belt", "Accessories"),
    ("baby stroller", "Baby"),
    ("cricket bat", "Sports"),
    ("study table lamp", "Furniture"),
    ("winter jacket", "Clothing"),
    ("wireless mouse", "Electronics"),
    ("flip flops", "Footwear"),
]

BRANDS = ["Nike", "Adidas", "Samsung", "Puma", "Sony", "Levi's", "Titan", "Boat", "Hp", "Reebok"]
SIZES = ["M", "L", "XL", "8", "9", "42"]
PRICES = [500, 800, 1200, 1500, 2000, 2500, 3000, 5000, 8000, 10000, 15000, 25000]


def pattern_a(idx: int, seed: str, category: str) -> dict:
    """3-turn: SEARCH -> REFINE(add price) -> REFINE(add size, keep price)."""
    price = PRICES[idx % len(PRICES)]
    size = SIZES[idx % len(SIZES)]
    return {
        "conversation_id": f"c{idx:03d}-a",
        "pattern": "search -> add_price -> add_size",
        "turns": [
            {
                "user": seed,
                "expected": {"hard": {}, "soft": seed},
            },
            {
                "user": f"budget is {price} rs",
                "expected": {"hard": {"max_price": price}, "soft": seed},
            },
            {
                "user": f"size {size} please",
                "expected": {"hard": {"max_price": price, "size": size}, "soft": seed},
            },
        ],
    }


def pattern_b(idx: int, seed: str, category: str) -> dict:
    """3-turn: SEARCH -> REFINE(add size) -> REFINE(clear size, add in-stock)."""
    size = SIZES[(idx + 1) % len(SIZES)]
    return {
        "conversation_id": f"c{idx:03d}-b",
        "pattern": "search -> add_size -> clear_size_add_stock",
        "turns": [
            {
                "user": seed,
                "expected": {"hard": {}, "soft": seed},
            },
            {
                "user": f"size {size} only",
                "expected": {"hard": {"size": size}, "soft": seed},
            },
            {
                "user": "any size is fine, just show what's in stock",
                "expected": {"hard": {"stock_status": "in_stock"}, "soft": seed},
            },
        ],
    }


def pattern_c(idx: int, seed: str, category: str) -> dict:
    """3-turn: SEARCH -> REFINE(brand, soft-only) -> REFINE(add price)."""
    brand = BRANDS[idx % len(BRANDS)]
    price = PRICES[(idx + 3) % len(PRICES)]
    soft_2 = f"{seed} {brand.lower()}"
    return {
        "conversation_id": f"c{idx:03d}-c",
        "pattern": "search -> add_brand(soft) -> add_price",
        "turns": [
            {
                "user": seed,
                "expected": {"hard": {}, "soft": seed},
            },
            {
                "user": f"only {brand}",
                "expected": {"hard": {}, "soft": soft_2},
            },
            {
                "user": f"under {price} rupees",
                "expected": {"hard": {"max_price": price}, "soft": soft_2},
            },
        ],
    }


def pattern_d(idx: int, seed: str, category: str) -> dict:
    """2-turn: SEARCH(with price already stated) -> REFINE(clear price)."""
    price = PRICES[(idx + 5) % len(PRICES)]
    return {
        "conversation_id": f"c{idx:03d}-d",
        "pattern": "search_with_price -> clear_price",
        "turns": [
            {
                "user": f"{seed} under {price} rs",
                "expected": {"hard": {"max_price": price}, "soft": seed},
            },
            {
                "user": "actually forget the budget, show me everything",
                "expected": {"hard": {}, "soft": seed},
            },
        ],
    }


PATTERNS = [pattern_a, pattern_b, pattern_c, pattern_d]


def build_dataset() -> list[dict]:
    conversations = []
    for idx, (seed, category) in enumerate(PRODUCTS):
        # 2 patterns per product, rotated so pattern usage is balanced across
        # the 25 products -> 50 conversations total.
        p1 = PATTERNS[idx % len(PATTERNS)]
        p2 = PATTERNS[(idx + 1) % len(PATTERNS)]
        conversations.append(p1(idx, seed, category))
        conversations.append(p2(idx, seed, category))
    return conversations


def main() -> None:
    conversations = build_dataset()
    out_path = os.path.join(os.path.dirname(__file__), "dataset.json")
    with open(out_path, "w") as f:
        json.dump(conversations, f, indent=2)
    print(f"Wrote {len(conversations)} conversations ({sum(len(c['turns']) for c in conversations)} turns) to {out_path}")


if __name__ == "__main__":
    main()
