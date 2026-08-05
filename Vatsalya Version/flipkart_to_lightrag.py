import ast
import html
import json
import random
import re

import pandas as pd

INPUT_CSV = "flipkart_com-ecommerce_sample.csv"
OUTPUT_MD = "flipkart_lightrag_corpus.md"
OUTPUT_JSONL = "flipkart_catalog_structured.jsonl"
OUTPUT_SQL = "flipkart_metadata.sql"
SQL_TABLE_NAME = "product_metadata"

# Category values from a healthy row are short top-level names ("Clothing",
# "Footwear", ...). A meaningful slice of the source data has no real ">>"
# hierarchy at all (the raw product_category_tree is a single truncated
# string), which makes the first segment either overlong or end in "...".
# There's nothing better to parse out of that field in those rows -- the
# hierarchy simply isn't there -- so malformed values get a deterministic,
# keyword-based fallback instead of being trusted as a category.
CATEGORY_KEYWORD_FALLBACKS = [
    ("kurta", "Clothing"),
    ("kurti", "Clothing"),
    ("anarkali", "Clothing"),
    ("saree", "Clothing"),
    ("legging", "Clothing"),
    ("stole", "Clothing"),
    ("clutch", "Bags, Wallets & Belts"),
    ("bellies", "Footwear"),
    ("sandal", "Footwear"),
    ("loafer", "Footwear"),
    ("sneaker", "Footwear"),
    ("heel", "Footwear"),
    ("shoe", "Footwear"),
]

MISSING_RATING_VALUES = {"no rating available", "nan", ""}


def dedupe_view_more(text):
    """Collapse '<truncated preview>...View More <same text in full>'
    scrape artifacts down to just the full text, instead of keeping both
    the truncated preview and its duplicate."""
    marker = "View More"
    guard = 0
    while marker in text and guard < 20:
        guard += 1
        idx = text.index(marker)
        before = text[:idx]
        after = text[idx + len(marker):].lstrip()
        # The truncated preview commonly ends in an ellipsis right at the
        # cut point ("...bru...View More") -- strip it before comparing,
        # otherwise the trailing dots break the exact-overlap match.
        before_trimmed = before.rstrip().rstrip(".").rstrip()
        window = before_trimmed[-500:]
        best_len = 0
        max_check = min(len(window), len(after))
        for length in range(max_check, 15, -1):
            if window[-length:] == after[:length]:
                best_len = length
                break
        if best_len:
            cut_point = len(before_trimmed) - best_len
            text = (before_trimmed[:cut_point].rstrip(" .") + " " + after).strip()
        else:
            text = (before.rstrip(" .") + " " + after).strip()
    return text


def clean_text(value):
    if pd.isna(value):
        return ""
    text = str(value)
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    text = text.replace('"', "").replace("'", "")
    text = re.sub(r"\s+", " ", text).strip()
    text = dedupe_view_more(text)
    return text


def clean_category_tree(value):
    text = clean_text(value)
    parts = [p.strip() for p in text.split(">>") if p.strip()]
    return parts


def is_malformed_category(value):
    if not value:
        return True
    if value.endswith("..."):
        return True
    if len(value) > 30:
        return True
    return False


def resolve_category(category_parts, product_name):
    """Returns (category, is_fallback). is_fallback is True whenever the
    value came from the keyword map or the Uncategorized default rather
    than a real category-tree segment, so it stays auditable downstream."""
    primary = category_parts[0] if category_parts else ""
    if not is_malformed_category(primary):
        return primary, False
    name_lower = (product_name or "").lower()
    for keyword, category in CATEGORY_KEYWORD_FALLBACKS:
        if keyword in name_lower:
            return category, True
    return "Uncategorized", True


def parse_specifications(value):
    if pd.isna(value):
        return {}
    raw = str(value).strip()
    if not raw or raw.lower() == "nan":
        return {}

    raw = raw.replace("=>", ":")

    try:
        parsed = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            parsed = None

    specs = {}

    def add_spec(key, val):
        key = clean_text(key)
        val = clean_text(val)
        if key and val:
            specs[key] = val

    if isinstance(parsed, dict):
        items = parsed.get("product_specification") or parsed.get("specifications")
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    key = item.get("key") or item.get("name")
                    val = item.get("value")
                    if key and val:
                        add_spec(key, val)
                    elif val:
                        add_spec("Feature", val)
        else:
            for key, val in parsed.items():
                add_spec(key, val)
    elif isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict):
                key = item.get("key") or item.get("name")
                val = item.get("value")
                if key and val:
                    add_spec(key, val)
                elif val:
                    add_spec("Feature", val)
    else:
        pairs = re.findall(r"key\s*:\s*=?>?\s*([^,{}\[\]]+?)\s*,?\s*value\s*:\s*=?>?\s*([^,{}\[\]]+)", raw, re.IGNORECASE)
        for key, val in pairs:
            add_spec(key, val)

    return specs


def guess_brand(row, specs):
    # The CSV's own brand column is the authoritative source when present --
    # prefer it over guessing from specs or the product title.
    csv_brand = row.get("brand")
    if pd.notna(csv_brand) and str(csv_brand).strip():
        return clean_text(csv_brand)
    for key in specs:
        if key.lower() == "brand":
            return specs[key]
    product_name = row.get("product_name")
    if isinstance(product_name, str) and product_name.strip():
        return clean_text(product_name).split()[0]
    return "Unknown"


def guess_material(specs):
    for key, val in specs.items():
        if "material" in key.lower():
            return val
    return None


def guess_size(specs):
    for key, val in specs.items():
        if key.lower() == "size":
            return val
    return None


def parse_price(value):
    if pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_rating(value):
    if pd.isna(value):
        return None
    text = str(value).strip()
    if text.lower() in MISSING_RATING_VALUES:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def resolve_sku_id(row):
    pid = row.get("pid")
    if pd.notna(pid) and str(pid).strip():
        return str(pid).strip()
    uniq_id = row.get("uniq_id")
    if pd.notna(uniq_id) and str(uniq_id).strip():
        return str(uniq_id).strip()
    return None


def synthesize_stock(sku_id):
    """Deterministic, seeded per SKU so re-running ingestion doesn't change
    availability answers. No real inventory exists in the source data --
    this is a mocked field for the prototype catalog, matching
    check_availability's existing contract."""
    rng = random.Random(f"stock-{sku_id}")
    roll = rng.random()
    if roll < 0.05:
        return "out_of_stock", 0
    if roll < 0.15:
        return "low_stock", rng.randint(1, 4)
    return "in_stock", rng.randint(5, 50)


def build_structured_record(row):
    product_name = clean_text(row.get("product_name"))
    category_parts = clean_category_tree(row.get("product_category_tree"))
    category, category_is_fallback = resolve_category(category_parts, product_name)
    subcategories = " > ".join(category_parts[1:]) if len(category_parts) > 1 else None
    specs = parse_specifications(row.get("product_specifications"))
    brand = guess_brand(row, specs)
    material = guess_material(specs)
    size = guess_size(specs)
    sku_id = resolve_sku_id(row)
    stock_status, quantity = synthesize_stock(sku_id)

    return {
        "sku_id": sku_id,
        "product_name": product_name if product_name else None,
        "brand": brand,
        "category": category,
        "category_is_fallback": category_is_fallback,
        "subcategory_path": subcategories,
        "material": material,
        "size": size,
        "retail_price": parse_price(row.get("retail_price")),
        "discounted_price": parse_price(row.get("discounted_price")),
        "rating": parse_rating(row.get("product_rating")),
        "stock_status": stock_status,
        "quantity": quantity,
        "specifications": specs,
    }


SQL_CREATE_TABLE = f"""CREATE TABLE IF NOT EXISTS {SQL_TABLE_NAME} (
    sku_id                VARCHAR(64) PRIMARY KEY,
    product_name          VARCHAR(512),
    brand                 VARCHAR(255),
    category              VARCHAR(255),
    category_is_fallback  TINYINT(1),
    subcategory_path      VARCHAR(512),
    material              VARCHAR(255),
    size                  VARCHAR(100),
    retail_price          DECIMAL(10, 2),
    discounted_price      DECIMAL(10, 2),
    rating                DECIMAL(3, 2),
    stock_status          ENUM('in_stock', 'low_stock', 'out_of_stock'),
    quantity              INT
);"""


def sql_value(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def build_sql_insert(record):
    columns = [
        "sku_id", "product_name", "brand", "category", "category_is_fallback",
        "subcategory_path", "material", "size", "retail_price",
        "discounted_price", "rating", "stock_status", "quantity",
    ]
    values = ", ".join(sql_value(record[col]) for col in columns)
    return f"INSERT INTO {SQL_TABLE_NAME} ({', '.join(columns)}) VALUES ({values});"


def build_lightrag_block(record, description):
    # Description-only by design: brand/category/material/price/stock are
    # already deterministic facts that live in the structured record (SQL's
    # job) -- LightRAG only sees the free text that structured fields can't
    # already answer.
    lines = [f"# Product: {record['product_name'] or 'Unknown Product'}", ""]
    lines.append(f"Product ID: {record['sku_id']}")
    lines.append("")
    lines.append("## Description")
    lines.append(description if description else "(No description available)")
    return "\n".join(lines)


def main():
    df = pd.read_csv(INPUT_CSV)

    md_blocks = []
    jsonl_lines = []
    sql_inserts = []

    fallback_category_count = 0
    empty_description_count = 0
    skipped_no_sku_count = 0
    stock_counts = {"in_stock": 0, "low_stock": 0, "out_of_stock": 0}

    for _, row in df.iterrows():
        record = build_structured_record(row)
        description = clean_text(row.get("description"))

        if record["category_is_fallback"]:
            fallback_category_count += 1
        if not description:
            empty_description_count += 1
        stock_counts[record["stock_status"]] += 1

        jsonl_lines.append(json.dumps(record, ensure_ascii=False))
        md_blocks.append(build_lightrag_block(record, description))

        if record["sku_id"]:
            sql_inserts.append(build_sql_insert(record))
        else:
            skipped_no_sku_count += 1

    with open(OUTPUT_MD, "w", encoding="utf-8") as f:
        f.write("\n\n---\n\n".join(md_blocks))

    with open(OUTPUT_JSONL, "w", encoding="utf-8") as f:
        f.write("\n".join(jsonl_lines) + "\n")

    with open(OUTPUT_SQL, "w", encoding="utf-8") as f:
        f.write(SQL_CREATE_TABLE + "\n\n")
        f.write("\n".join(sql_inserts) + "\n")

    print(f"Wrote {len(md_blocks)} LightRAG documents to {OUTPUT_MD}")
    print(f"Wrote {len(jsonl_lines)} structured records to {OUTPUT_JSONL}")
    print(f"Wrote {len(sql_inserts)} metadata rows to {OUTPUT_SQL}")
    print(f"Category fallback applied to {fallback_category_count} products")
    print(f"Empty description for {empty_description_count} products")
    print(f"Skipped {skipped_no_sku_count} products with no resolvable sku_id (SQL primary key)")
    print(f"Synthetic stock distribution: {stock_counts}")


if __name__ == "__main__":
    main()
