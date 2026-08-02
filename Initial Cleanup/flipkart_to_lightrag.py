import ast
import html
import json
import re

import pandas as pd

INPUT_CSV = "flipkart_com-ecommerce_sample.csv"
OUTPUT_MD = "flipkart_lightrag_corpus.md"


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
    return text


def clean_category_tree(value):
    text = clean_text(value)
    parts = [p.strip() for p in text.split(">>") if p.strip()]
    return parts


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


def guess_brand(product_name, specs):
    for key in specs:
        if key.lower() == "brand":
            return specs[key]
    if isinstance(product_name, str) and product_name.strip():
        return product_name.strip().split()[0]
    return "Unknown"


def guess_material(specs):
    for key, val in specs.items():
        if "material" in key.lower():
            return val
    return None


def format_price(row):
    retail = row.get("retail_price")
    discounted = row.get("discounted_price")
    parts = []
    if pd.notna(retail):
        parts.append(f"Retail Price: {retail}")
    if pd.notna(discounted):
        parts.append(f"Discounted Price: {discounted}")
    return parts


def build_markdown_block(row):
    product_name = clean_text(row.get("product_name"))
    category_parts = clean_category_tree(row.get("product_category_tree"))
    category = category_parts[0] if category_parts else "Unknown"
    subcategories = " > ".join(category_parts[1:]) if len(category_parts) > 1 else ""
    specs = parse_specifications(row.get("product_specifications"))
    brand = guess_brand(product_name, specs)
    material = guess_material(specs)
    description = clean_text(row.get("description"))
    price_lines = format_price(row)
    rating = row.get("product_rating")
    pid = row.get("pid") or row.get("uniq_id")

    lines = []
    lines.append(f"# Product: {product_name if product_name else 'Unknown Product'}")
    lines.append("")
    lines.append("## Attributes")
    lines.append(f"- Product ID: {pid}")
    lines.append(f"- Brand: {brand}")
    lines.append(f"- Category: {category}")
    if subcategories:
        lines.append(f"- Subcategory Path: {subcategories}")
    if material:
        lines.append(f"- Material: {material}")
    for line in price_lines:
        lines.append(f"- {line}")
    if pd.notna(rating):
        lines.append(f"- Rating: {rating}")

    if specs:
        lines.append("")
        lines.append("## Specifications")
        for key, val in specs.items():
            lines.append(f"- {key}: {val}")

    if description:
        lines.append("")
        lines.append("## Description")
        lines.append(description)

    return "\n".join(lines)


def main():
    df = pd.read_csv(INPUT_CSV)

    blocks = []
    for _, row in df.iterrows():
        block = build_markdown_block(row)
        blocks.append(block)

    corpus = "\n\n---\n\n".join(blocks)

    with open(OUTPUT_MD, "w", encoding="utf-8") as f:
        f.write(corpus)

    print(f"Wrote {len(blocks)} product documents to {OUTPUT_MD}")


if __name__ == "__main__":
    main()
