"""Synthetic query set + ground-truth definitions for the reranker eval.

Three tiers of ambiguity, each backed by a SQL definition of "what counts as
relevant" against the live `product_metadata` table:

  Tier 1 (low ambiguity):  hard_constraints = category + max_price + in_stock.
                            soft_query_text names the product specifically.
                            Ground truth = category + price + product_name LIKE keywords.
  Tier 2 (medium):          same hard_constraints as tier 1, but soft_query_text
                            is generic (no product-name-specific words).
                            Ground truth = category + price only (keywords dropped).
  Tier 3 (high ambiguity):  hard_constraints = category only.
                            soft_query_text is a single vague word/phrase.
                            Ground truth = category only.

The reranker's /api/search endpoint only accepts these hard_constraints keys
(verified by probing): category, max_price, stock_status, size. There is no
min_price/brand/material/rating filter, so "Price" in the tiering spec means
an upper bound only.

Keyword phrases for tier 1 were chosen from the actual product_name
vocabulary per category (most frequent non-brand tokens), and max_price was
set to just above the observed max price for that category+keyword slice, so
the price constraint doesn't accidentally exclude the ground truth it's
supposed to allow.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class QuerySpec:
    tier: int
    query_id: str
    soft_query_text: str
    hard_constraints: dict
    # Ground-truth SQL knobs.
    category: str
    max_price: float | None
    keywords: list[str] = field(default_factory=list)


def _hc(category: str, max_price: float | None) -> dict:
    hc = {"category": category}
    if max_price is not None:
        hc["max_price"] = max_price
        hc["stock_status"] = "in_stock"
    return hc


# (query_id, soft_query_text, category, max_price, keywords)
_TIER1_RAW = [
    ("t1-clothing-tshirt", "cotton t-shirt for men", "Clothing", 3500, ["T-Shirt"]),
    # Deliberately broad keyword ("Shirt"+"Blue" over-generalizes across many
    # unrelated cuts/necklines) - empirically measured near-zero nDCG@1/3.
    ("t1-clothing-shirt", "blue casual shirt", "Clothing", 4300, ["Shirt", "Blue"]),
    ("t1-clothing-bra", "cotton bra for women", "Clothing", 7200, ["Bra"]),
    ("t1-clothing-jeans", "slim fit jeans", "Clothing", 9000, ["Jeans"]),
    # Broad two-word combo ("Necklace"+"Gold") diluted across the whole
    # Jewellery catalog - empirically measured near-zero nDCG.
    ("t1-jewellery-necklace", "gold necklace set", "Jewellery", 27000, ["Necklace", "Gold"]),
    ("t1-jewellery-ring", "diamond ring", "Jewellery", 44000, ["Ring"]),
    ("t1-jewellery-bangle", "gold bangle set", "Jewellery", 3600, ["Bangle"]),
    # Bare color word over a huge, heterogeneous Footwear catalog -
    # empirically measured near-zero nDCG@1.
    ("t1-footwear-blue", "blue running shoes", "Footwear", 1500, ["Blue"]),
    ("t1-footwear-boots", "leather boots", "Footwear", 10000, ["Boots"]),
    ("t1-footwear-loafers", "casual loafers", "Footwear", 2800, ["Loafers"]),
    ("t1-footwear-heels", "party heels", "Footwear", 3500, ["Heels"]),
    # Bare "Cover" spans book covers, flip covers, back covers alike -
    # empirically measured low nDCG.
    ("t1-mobiles-back-cover", "mobile phone back cover", "Mobiles & Accessories", 500, ["Cover"]),
    ("t1-mobiles-book-cover", "tablet book cover", "Mobiles & Accessories", 3500, ["Book Cover"]),
    ("t1-automotive-helmet", "bike helmet", "Automotive", 800, ["Helmet"]),
    ("t1-homedecor-showpiece", "decorative showpiece", "Home Decor & Festive Needs", 48000, ["Showpiece"]),
    # Bare "Wall" spans clocks, stickers, shelves alike - empirically
    # measured low nDCG@1.
    ("t1-homedecor-wall-sticker", "wall hanging home decor", "Home Decor & Festive Needs", 1000, ["Wall"]),
    ("t1-homedecor-clock", "analog wall clock", "Home Decor & Festive Needs", 2000, ["Clock"]),
    ("t1-beauty-cream", "face cream", "Beauty and Personal Care", 2900, ["Cream"]),
    ("t1-beauty-lipstick", "matte lipstick shade", "Beauty and Personal Care", 1300, ["Lipstick"]),
    ("t1-homefurnishing-curtain", "eyelet door curtain", "Home Furnishing", 3200, ["Curtain"]),
    ("t1-homefurnishing-cushion", "sofa cushion cover", "Home Furnishing", 1600, ["Cushion"]),
    ("t1-kitchen-mug", "ceramic coffee mug", "Kitchen & Dining", 1100, ["Mug"]),
    # Bare "Steel" spans juicers, cookers, mugs, racks alike - empirically
    # measured near-zero nDCG@1/3.
    ("t1-kitchen-steel", "steel kitchen storage set", "Kitchen & Dining", 1000, ["Steel"]),
    ("t1-computers-mouse", "wireless computer mouse", "Computers", 350, ["Mouse"]),
    ("t1-computers-router", "wifi router", "Computers", 21300, ["Router"]),
    ("t1-watches-digital", "digital wrist watch", "Watches", 7500, ["Digital"]),
    ("t1-watches-black", "black analog watch", "Watches", 1700, ["Black"]),
    ("t1-babycare-sticker", "baby room wall sticker", "Baby Care", 3500, ["Sticker"]),
    ("t1-tools-gardening", "gardening tool kit", "Tools & Hardware", 2300, ["Gardening"]),
    ("t1-toys-cartoon", "cartoon toy for kids", "Toys & School Supplies", 650, ["Cartoon"]),
    ("t1-pens-pencil", "pencil box set", "Pens & Stationery", 700, ["Pencil"]),
    ("t1-bags-backpack", "casual backpack", "Bags, Wallets & Belts", 4000, ["Backpack"]),
    # Bare color word spans backpacks, wallets, belts alike - empirically
    # measured near-zero nDCG.
    ("t1-bags-clutch", "black leather wallet", "Bags, Wallets & Belts", 2000, ["Black"]),
    ("t1-sports-cricket", "cricket sports kit", "Sports & Fitness", 14300, ["Cricket"]),
]

TIER1: list[QuerySpec] = [
    QuerySpec(1, qid, text, _hc(cat, price), cat, price, kws)
    for qid, text, cat, price, kws in _TIER1_RAW
]

# Same category + max_price as TIER1 (product-name specificity dropped from
# both the query text and the ground-truth filter).
# A handful of these use razor-thin price bands (single-digit ground-truth
# pools) rather than the generous bands elsewhere - this stresses whether
# max_price is enforced tightly by the reranker rather than trivially
# satisfied by a huge, forgiving ground truth.
_TIER2_RAW = [
    ("t2-clothing-01", "cheap clothing item", "Clothing", 100),
    ("t2-clothing-02", "stylish outfit for men", "Clothing", 4300),
    ("t2-clothing-03", "comfortable womens clothing", "Clothing", 7200),
    ("t2-clothing-04", "nice clothing item", "Clothing", 9000),
    ("t2-jewellery-01", "cheap jewellery piece", "Jewellery", 100),
    ("t2-jewellery-02", "elegant jewellery gift", "Jewellery", 44000),
    ("t2-jewellery-03", "traditional jewellery item", "Jewellery", 3600),
    ("t2-footwear-01", "very cheap footwear", "Footwear", 180),
    ("t2-footwear-02", "durable footwear", "Footwear", 10000),
    ("t2-footwear-03", "casual footwear", "Footwear", 2800),
    ("t2-footwear-04", "footwear for a party", "Footwear", 3500),
    ("t2-mobiles-01", "very cheap mobile accessory", "Mobiles & Accessories", 100),
    ("t2-mobiles-02", "tablet accessory", "Mobiles & Accessories", 3500),
    ("t2-automotive-01", "automotive safety gear", "Automotive", 800),
    ("t2-homedecor-01", "home decor item", "Home Decor & Festive Needs", 48000),
    ("t2-homedecor-02", "decor for the walls", "Home Decor & Festive Needs", 12300),
    ("t2-homedecor-03", "festive home decor", "Home Decor & Festive Needs", 2000),
    ("t2-beauty-01", "skin care product", "Beauty and Personal Care", 2900),
    ("t2-beauty-02", "makeup product", "Beauty and Personal Care", 1300),
    ("t2-homefurnishing-01", "home furnishing item", "Home Furnishing", 3200),
    ("t2-homefurnishing-02", "living room accessory", "Home Furnishing", 1600),
    ("t2-kitchen-01", "kitchen essential", "Kitchen & Dining", 1100),
    ("t2-kitchen-02", "dining essential", "Kitchen & Dining", 4900),
    ("t2-computers-01", "very cheap computer accessory", "Computers", 90),
    ("t2-computers-02", "networking device", "Computers", 21300),
    ("t2-watches-01", "nice wrist watch", "Watches", 7500),
    ("t2-watches-02", "affordable watch", "Watches", 1700),
    ("t2-babycare-01", "baby room decor", "Baby Care", 3500),
    ("t2-tools-01", "home improvement tool", "Tools & Hardware", 2300),
    ("t2-toys-01", "toy for kids", "Toys & School Supplies", 650),
    ("t2-pens-01", "stationery item", "Pens & Stationery", 700),
    ("t2-bags-01", "bag for daily use", "Bags, Wallets & Belts", 4000),
    ("t2-bags-02", "stylish bag", "Bags, Wallets & Belts", 1900),
    ("t2-sports-01", "sports equipment", "Sports & Fitness", 14300),
]

TIER2: list[QuerySpec] = [
    QuerySpec(2, qid, text, _hc(cat, price), cat, price, [])
    for qid, text, cat, price in _TIER2_RAW
]

# Category only - vague single/couple-word queries, categories repeated with
# different phrasings to reach the same query count as tiers 1 and 2.
# A handful of these target obscure, single-digit-inventory "category"
# values that exist in product_metadata as mislabeled one-off brand/product
# strings (e.g. "Pout Brass Bangle" has 3 rows total) rather than a clean
# top-level category. With so few members, any category-filter leakage or
# semantic fallback by the reranker becomes visible in the score instead of
# being absorbed by a huge, forgiving ground truth.
_TIER3_RAW = [
    ("t3-clothing-01", "clothing", "Clothing"),
    ("t3-clothing-02", "clothes", "Clothing"),
    ("t3-clothing-03", "apparel", "Clothing"),
    ("t3-clothing-04", "outfit", "Clothing"),
    ("t3-jewellery-01", "jewellery", "Jewellery"),
    ("t3-jewellery-02", "jewelry", "Jewellery"),
    ("t3-jewellery-03", "ornaments", "Jewellery"),
    ("t3-footwear-01", "shoes", "Footwear"),
    ("t3-footwear-02", "footwear", "Footwear"),
    ("t3-footwear-03", "sandals", "Footwear"),
    ("t3-footwear-04", "boots", "Footwear"),
    ("t3-mobiles-01", "mobile accessory", "Mobiles & Accessories"),
    ("t3-mobiles-02", "household item", "Household Supplies"),
    ("t3-automotive-01", "aviator sunglasses", "Olvin Aviator Sunglasses"),
    ("t3-homedecor-01", "home decor", "Home Decor & Festive Needs"),
    ("t3-homedecor-02", "decor item", "Home Decor & Festive Needs"),
    ("t3-homedecor-03", "festive decor", "Home Decor & Festive Needs"),
    ("t3-beauty-01", "beauty product", "Beauty and Personal Care"),
    ("t3-beauty-02", "personal care", "Beauty and Personal Care"),
    ("t3-homefurnishing-01", "home furnishing", "Home Furnishing"),
    ("t3-homefurnishing-02", "womens flats footwear", "Pu-Good Women Flats"),
    ("t3-kitchen-01", "kitchen item", "Kitchen & Dining"),
    ("t3-kitchen-02", "dining item", "Kitchen & Dining"),
    ("t3-computers-01", "computer", "Computers"),
    ("t3-computers-02", "computer accessory", "Computers"),
    ("t3-watches-01", "watch", "Watches"),
    ("t3-watches-02", "wrist watch", "Watches"),
    ("t3-babycare-01", "baby girls top combo", "Lilliput Top Baby Girls Combo"),
    ("t3-tools-01", "tools", "Tools & Hardware"),
    ("t3-toys-01", "toys", "Toys & School Supplies"),
    ("t3-pens-01", "stationery", "Pens & Stationery"),
    ("t3-bags-01", "bag", "Bags, Wallets & Belts"),
    ("t3-bags-02", "wallet", "Bags, Wallets & Belts"),
    ("t3-sports-01", "brass bangle", "Pout Brass Bangle"),
]

TIER3: list[QuerySpec] = [
    QuerySpec(3, qid, text, _hc(cat, None), cat, None, [])
    for qid, text, cat in _TIER3_RAW
]

ALL_QUERIES: list[QuerySpec] = TIER1 + TIER2 + TIER3
