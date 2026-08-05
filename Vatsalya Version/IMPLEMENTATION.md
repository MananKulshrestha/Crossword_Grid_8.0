# Implementation Notes — Flipkart Dataset Pipeline

This document explains how the raw Flipkart product export is turned into
three downstream artifacts, so an agent picking up this repo later can
understand the design and extend it safely without re-deriving the intent
from scratch.

## Source of truth

- Script: `flipkart_to_lightrag.py`
- Input: `flipkart_com-ecommerce_sample.csv` (raw Flipkart scrape). Relevant
  columns: `uniq_id`, `product_url`, `product_name`, `product_category_tree`,
  `pid`, `retail_price`, `discounted_price`, `description`, `product_rating`,
  `overall_rating`, `brand`, `product_specifications`.
- The CSV has **no real inventory/quantity column** — any stock/quantity
  field downstream is synthetic, deterministically generated.

## Why three outputs, and why they don't overlap

The pipeline deliberately separates data into three artifacts by purpose,
not by convenience:

1. **`flipkart_lightrag_corpus.md`** — free-text only (product name, ID,
   description). This is what gets embedded/retrieved by a RAG system.
2. **`flipkart_catalog_structured.jsonl`** — one JSON object per product
   with every derived/structured field, including the nested
   `specifications` dict. This is the full structured record, kept for
   downstream tooling that wants everything (e.g. re-deriving the SQL, or
   feeding a search index).
3. **`flipkart_metadata.sql`** — a flat relational table
   (`product_metadata`) of only the fields useful for structured lookups
   (price, brand, category, stock, etc). Excludes the nested
   `specifications` dict (doesn't fit a flat schema) and the description
   text (belongs to the RAG corpus, not to structured metadata).

**Rule of thumb for future changes:** deterministic/structured facts go in
JSONL + SQL; free text that needs semantic search goes in the Markdown
corpus. Don't duplicate structured facts into the Markdown blocks — the
RAG corpus is deliberately minimal (see `build_lightrag_block`) so
retrieval isn't polluted with facts a SQL query already answers precisely.

## Step-by-step pipeline (what `main()` does)

For every row in the CSV:

### 1. Text cleaning (`clean_text`)
Applied to any free-text field (product name, description, spec
keys/values). Handles scrape artifacts specific to this dataset:
- HTML-unescapes entities, strips HTML tags.
- Strips a wrapping `[...]` (the CSV sometimes stores strings as a
  single-element Python list literal).
- Strips stray quote characters, collapses whitespace.
- `dedupe_view_more()`: the raw `description` field frequently contains a
  truncated preview immediately followed by `View More <same text in
  full>`. This function finds the longest suffix/prefix overlap between
  the truncated tail and what follows `View More` and merges them into one
  clean string, instead of keeping the duplicate.

### 2. Category resolution (`clean_category_tree`, `resolve_category`)
`product_category_tree` is meant to be a `>>`-delimited hierarchy (e.g.
`"Clothing >> Women's Clothing >> ..."`). In a meaningful slice of rows
this hierarchy is missing/truncated — the first segment is either
implausibly long or ends in `...` (`is_malformed_category`). When that
happens, there's nothing real to parse, so category is instead guessed via
a keyword match against the product name
(`CATEGORY_KEYWORD_FALLBACKS` — e.g. "kurta" → Clothing, "sneaker" →
Footwear), falling further back to `"Uncategorized"` if no keyword
matches. Every record carries `category_is_fallback` so this guess stays
auditable/distinguishable from a real parsed category downstream.

### 3. Specification parsing (`parse_specifications`)
`product_specifications` is a messy semi-JSON string using `key=>value`
Python-dict-literal syntax rather than valid JSON. Parse order:
`ast.literal_eval` → `json.loads` → regex fallback
(`key\s*:\s*...value\s*:\s*...`). Normalizes into a flat `{key: value}`
dict via `add_spec`, which itself runs values through `clean_text`.

### 4. Derived fields
- `brand` (`guess_brand`): CSV `brand` column first (authoritative when
  present) → spec dict lookup for a `brand` key → first word of the
  product name → `"Unknown"`.
- `material` / `size` (`guess_material`, `guess_size`): pulled out of the
  parsed spec dict by key name match.
- `retail_price` / `discounted_price` (`parse_price`): float parse,
  `None` on failure/NaN.
- `rating` (`parse_rating`): float parse; treats
  `"No rating available"` / `"nan"` / `""` as missing (`None`).
- `sku_id` (`resolve_sku_id`): `pid` if present and non-empty, else
  `uniq_id`. This is the primary key used everywhere downstream (JSONL,
  SQL). Note: `pid` is not perfectly unique across the dataset — as of the
  current sample there are 2 duplicate `sku_id` values out of 20,000 rows;
  SQL import handles this with `INSERT IGNORE` (see README.md), the JSONL
  output does not dedupe.

### 5. Synthetic stock (`synthesize_stock`)
No real inventory data exists in the source CSV. Stock status and quantity
are synthesized, but **deterministically seeded per SKU**
(`random.Random(f"stock-{sku_id}")`) so re-running the script produces
identical values every time — this matters because a downstream
`check_availability`-style feature depends on stable answers across
ingestion runs, not fresh random values each time.

Distribution (roughly, over ~20k rows):
- 5% chance → `out_of_stock`, quantity `0`
- 10% chance → `low_stock`, quantity `randint(1, 4)`
- 85% chance → `in_stock`, quantity `randint(5, 50)`

This produces two fields: `stock_status` and `quantity` (the single
"total quantity" number — an earlier iteration had a second, separate
1–5 `quantity` column plus this one as `stock_quantity`; that was
collapsed into just this one field since having two different
"quantity" columns was redundant and confusing).

### 6. Record assembly (`build_structured_record`)
Combines all of the above into one dict per product — this dict is the
single shared shape written to both JSONL and SQL (SQL just excludes the
`specifications` key). Field list as of now:

```
sku_id, product_name, brand, category, category_is_fallback,
subcategory_path, material, size, retail_price, discounted_price,
rating, stock_status, quantity, specifications
```

`specifications` is JSONL-only.

### 7. Output writing (`main`)
- Markdown blocks (`build_lightrag_block`) joined with `\n\n---\n\n`
  separators → `flipkart_lightrag_corpus.md`.
- JSONL: one `json.dumps(record)` per line → `flipkart_catalog_structured.jsonl`.
- SQL: a `CREATE TABLE IF NOT EXISTS product_metadata (...)` header
  (MySQL dialect — see `SQL_CREATE_TABLE`) followed by one `INSERT INTO`
  statement per row (`build_sql_insert`, values escaped via `sql_value` —
  handles `None` → `NULL`, bools → `0`/`1`, strings escaped for backslash
  and single-quote). Rows with no resolvable `sku_id` are skipped (would
  violate the primary key) — currently 0 such rows in the sample data.
  Output → `flipkart_metadata.sql`.

Run summary stats are printed at the end: counts of fallback categories,
empty descriptions, skipped no-SKU rows, and the stock/quantity status
distribution.

## Current `product_metadata` SQL schema (MySQL)

```sql
CREATE TABLE IF NOT EXISTS product_metadata (
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
);
```

See `README.md` for how this is loaded into a Docker MySQL container
(credentials, port, import command).

## Extension points / things to know before changing this

- If you add a new structured field, add it to the dict in
  `build_structured_record`, then to `SQL_CREATE_TABLE` and the `columns`
  list in `build_sql_insert` if it belongs in SQL. JSONL picks up new
  dict keys automatically.
- Keep the RAG Markdown corpus free-text-only. Don't add structured
  fields (price, stock, etc.) to `build_lightrag_block` — that duplication
  was deliberately designed out.
- Any new synthetic/mocked field should be seeded per-SKU
  (`random.Random(f"<namespace>-{sku_id}")`) with its own namespace
  string, so it stays stable across reruns and doesn't collide with the
  `stock-` seed used by `synthesize_stock`.
- `sku_id` is not guaranteed globally unique in the source data (rare
  `pid` collisions). If exact-once semantics become important, dedupe
  during `build_structured_record` (e.g. keep first occurrence) rather
  than relying on `INSERT IGNORE` at import time.
