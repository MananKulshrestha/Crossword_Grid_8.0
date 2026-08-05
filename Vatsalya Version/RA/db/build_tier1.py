"""Builds the Tier 1 catalog-foundation schema (database schema and
requirements.md, section 4.2) from the existing product_metadata table, and
points a product_metadata VIEW at the result so sql_filter.py needs zero
code changes.

Scope: only products/skus/offers and their supporting version/taxonomy/
attribute tables (catalog_versions, taxonomy_nodes, category_schemas,
product_attributes, sku_attributes, index_versions) -- the tables the
Flipkart catalog actually populates. Sessions/cart/chat/Quality Sentinel/
Catalog Language/Query Recovery/Catalog Operations tables belong to other
worktrees and are out of scope here.

Idempotent: safe to re-run. On the first run it renames the existing
product_metadata TABLE to product_metadata_legacy and treats that as the
permanent source of truth for the migration; on later runs it detects
product_metadata is already a VIEW and re-migrates straight from
product_metadata_legacy, so repeated runs don't stack renames.

Known, accepted simplifications (see the session's field-mapping
discussion for the full reasoning):
  - category_is_fallback is dropped -- no test depends on it and there is
    no equivalent flag in taxonomy_nodes.
  - material is stored as a product-level attribute (shared across every
    SKU in a family), even though the original flat table stored it per
    SKU row. If any family's members genuinely had divergent material
    values, this migration collapses them to the first one seen.
  - index_versions only gets a row for BM25 (the one artifact that is
    actually built and populated over the real catalog right now). Qdrant
    and the LightRAG graph are wired up but hold no real documents yet
    (blocked on Ollama), so no row is created for them until ingest.py
    actually runs.
"""

import hashlib
import json
import os
import sys
from datetime import datetime, timezone

import pymysql
import pymysql.cursors

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from graph_sampling import normalize_product_name  # noqa: E402

DB_HOST = os.environ.get("FLIPKART_DB_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("FLIPKART_DB_PORT", "3307"))
DB_USER = os.environ.get("FLIPKART_DB_USER", "flipkart_user")
DB_PASSWORD = os.environ.get("FLIPKART_DB_PASSWORD", "flipkart_pass")
DB_NAME = os.environ.get("FLIPKART_DB_NAME", "flipkart")

SCHEMA_SQL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema_tier1.sql")

CATALOG_VERSION = "flipkart_v1"
TAXONOMY_VERSION = "flipkart_taxonomy_v1"
CATEGORY_SCHEMA_VERSION = "flipkart_category_schema_v1"

STOCK_STATUS_MAP = {
    "in_stock": "IN_STOCK",
    "low_stock": "LOW_STOCK",
    "out_of_stock": "OUT_OF_STOCK",
}

# Reconstructs product_metadata exactly -- see the session's field-mapping
# analysis for why each column is derived the way it is (unit conversion
# for price_paise -> rupees, SUBSTRING_INDEX to split taxonomy_nodes.path
# back into today's separate category/subcategory_path columns, etc.)
_PRODUCT_METADATA_VIEW_SQL = """
CREATE VIEW product_metadata AS
SELECT
    s.sku_id                                                          AS sku_id,
    p.title                                                           AS product_name,
    p.brand_name                                                      AS brand,
    SUBSTRING_INDEX(tn.path, ' > ', 1)                                AS category,
    CASE WHEN LOCATE(' > ', tn.path) = 0 THEN NULL
         ELSE SUBSTRING(tn.path, LOCATE(' > ', tn.path) + 3) END      AS subcategory_path,
    mat.normalized_value                                              AS material,
    sz.normalized_value                                               AS size,
    CAST(o.list_price_paise / 100.0 AS DECIMAL(10,2))                 AS retail_price,
    CAST(o.price_paise / 100.0 AS DECIMAL(10,2))                      AS discounted_price,
    p.rating                                                          AS rating,
    CASE o.availability_status
        WHEN 'IN_STOCK' THEN 'in_stock'
        WHEN 'LOW_STOCK' THEN 'low_stock'
        WHEN 'OUT_OF_STOCK' THEN 'out_of_stock'
    END                                                                AS stock_status,
    o.quantity                                                        AS quantity
FROM skus s
JOIN products p          ON p.catalog_version = s.catalog_version AND p.product_id = s.product_id
JOIN taxonomy_nodes tn   ON tn.taxonomy_node_id = p.taxonomy_node_id
JOIN offers o            ON o.catalog_version = s.catalog_version AND o.sku_id = s.sku_id
JOIN catalog_versions cv ON cv.catalog_version = s.catalog_version AND cv.status = 'ACTIVE'
LEFT JOIN product_attributes mat
       ON mat.catalog_version = p.catalog_version AND mat.product_id = p.product_id
      AND mat.attribute_id = 'attr_material'
LEFT JOIN sku_attributes sz
       ON sz.catalog_version = s.catalog_version AND sz.sku_id = s.sku_id
      AND sz.attribute_id = 'attr_size'
"""


def _connect():
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD,
        database=DB_NAME, cursorclass=pymysql.cursors.DictCursor, autocommit=False,
    )


def _short_hash(*parts):
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:24]


def _ensure_legacy_table(cursor):
    cursor.execute(
        "SELECT TABLE_NAME, TABLE_TYPE FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = %s AND TABLE_NAME IN ('product_metadata', 'product_metadata_legacy')",
        (DB_NAME,),
    )
    found = {r["TABLE_NAME"]: r["TABLE_TYPE"] for r in cursor.fetchall()}

    if "product_metadata_legacy" in found:
        # A previous run already renamed the original table (possibly one
        # that failed partway through afterwards) -- reuse it as-is rather
        # than trying to rename again.
        return "product_metadata_legacy"

    if "product_metadata" not in found:
        raise RuntimeError(
            "Neither product_metadata nor product_metadata_legacy exists -- "
            "run the original flipkart_metadata.sql import first."
        )
    if found["product_metadata"] != "BASE TABLE":
        raise RuntimeError(
            f"product_metadata exists but is a {found['product_metadata']}, not a BASE TABLE, "
            "and no product_metadata_legacy exists to migrate from."
        )
    cursor.execute("RENAME TABLE product_metadata TO product_metadata_legacy")
    return "product_metadata_legacy"


def _drop_generated_objects(cursor):
    cursor.execute("DROP VIEW IF EXISTS product_metadata")
    for table in (
        "sku_attributes", "product_attributes", "offers", "skus", "products",
        "category_schemas", "taxonomy_nodes", "index_versions", "catalog_versions",
    ):
        cursor.execute(f"DROP TABLE IF EXISTS {table}")


def _run_schema_ddl(cursor):
    with open(SCHEMA_SQL_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()
    # Strip full comment-only lines before splitting on ';' -- some of them
    # contain a literal semicolon in their prose (e.g. "...here; the bytes
    # stay...") which would otherwise split a statement mid-comment.
    sql_text = "\n".join(line for line in lines if not line.strip().startswith("--"))
    for statement in sql_text.split(";"):
        statement = statement.strip()
        if statement:
            cursor.execute(statement)


def _build_taxonomy(rows):
    """Returns (nodes_by_id, leaf_id_for_sku): nodes_by_id maps
    taxonomy_node_id -> INSERT-ready dict; leaf_id_for_sku maps each row's
    sku_id -> the taxonomy_node_id of its most specific (leaf) node.
    path is built as "category > sub > sub..." so the view can invert it
    with SUBSTRING_INDEX exactly."""
    nodes = {}
    leaf_id_for_sku = {}
    for row in rows:
        category = (row["category"] or "").strip()
        if not category:
            raise ValueError(f"sku_id {row['sku_id']!r} has no category -- cannot place in taxonomy")
        segments = [category]
        if row["subcategory_path"]:
            segments += [s.strip() for s in row["subcategory_path"].split(">") if s.strip()]

        parent_id = None
        node_id = None
        path_so_far = []
        for depth, segment in enumerate(segments):
            path_so_far.append(segment)
            path = " > ".join(path_so_far)
            node_id = "tax_" + _short_hash(path)
            if node_id not in nodes:
                nodes[node_id] = {
                    "taxonomy_version": TAXONOMY_VERSION,
                    "taxonomy_node_id": node_id,
                    "parent_taxonomy_node_id": parent_id,
                    "canonical_name": segment,
                    "normalized_name": segment.lower(),
                    "path": path,
                    "depth": depth,
                    "active": 1,
                }
            parent_id = node_id
        leaf_id_for_sku[row["sku_id"]] = node_id
    return nodes, leaf_id_for_sku


def _build_families(rows):
    """Groups rows into product families using the same (brand, category,
    color-stripped-name) key graph_sampling.py already uses for coverage
    sampling -- reuses that tested grouping logic instead of inventing a
    second one."""
    families = {}
    family_for_sku = {}
    for row in rows:
        key = (row["brand"] or "", row["category"] or "", normalize_product_name(row["product_name"]))
        product_id = "prod_" + _short_hash(*key)
        family_for_sku[row["sku_id"]] = product_id
        if product_id not in families:
            families[product_id] = {
                "product_id": product_id,
                "title": row["product_name"] or "",
                "brand_name": row["brand"],
                "rating": row["rating"],
                "sku_id_for_taxonomy": row["sku_id"],
            }
        elif families[product_id]["rating"] is None and row["rating"] is not None:
            families[product_id]["rating"] = row["rating"]
    return families, family_for_sku


def main():
    conn = _connect()
    try:
        with conn.cursor() as cursor:
            legacy_table = _ensure_legacy_table(cursor)
            conn.commit()

            cursor.execute(f"SELECT * FROM {legacy_table}")
            rows = cursor.fetchall()
            print(f"Read {len(rows)} rows from {legacy_table}")

            _drop_generated_objects(cursor)
            _run_schema_ddl(cursor)
            conn.commit()

            taxonomy_nodes, leaf_id_for_sku = _build_taxonomy(rows)
            families, family_for_sku = _build_families(rows)
            print(f"{len(taxonomy_nodes)} taxonomy nodes, {len(families)} product families "
                  f"from {len(rows)} SKU rows")

            now = datetime.now(timezone.utc).replace(tzinfo=None)

            cursor.execute(
                "INSERT INTO catalog_versions "
                "(catalog_version, taxonomy_version, category_schema_version, status, "
                " source_batch_id, data_uri, created_at, activated_at) "
                "VALUES (%s,%s,%s,'ACTIVE',%s,%s,%s,%s)",
                (CATALOG_VERSION, TAXONOMY_VERSION, CATEGORY_SCHEMA_VERSION,
                 "flipkart_metadata_migration", "Vatsalya Version/flipkart_metadata.sql", now, now),
            )

            cursor.executemany(
                "INSERT INTO taxonomy_nodes "
                "(taxonomy_version, taxonomy_node_id, parent_taxonomy_node_id, canonical_name, "
                " normalized_name, path, depth, active) VALUES "
                "(%(taxonomy_version)s,%(taxonomy_node_id)s,%(parent_taxonomy_node_id)s,"
                "%(canonical_name)s,%(normalized_name)s,%(path)s,%(depth)s,%(active)s)",
                list(taxonomy_nodes.values()),
            )

            leaf_ids = {leaf_id_for_sku[fam["sku_id_for_taxonomy"]] for fam in families.values()}
            schema_rows = []
            for leaf_id in leaf_ids:
                schema_rows.append((CATEGORY_SCHEMA_VERSION, leaf_id, "attr_material", "material",
                                     "STRING", None, None, 1, 1, 1, 0, "ALLOW_UNKNOWN"))
                schema_rows.append((CATEGORY_SCHEMA_VERSION, leaf_id, "attr_size", "size",
                                     "STRING", None, None, 1, 0, 1, 0, "ALLOW_UNKNOWN"))
            cursor.executemany(
                "INSERT INTO category_schemas "
                "(category_schema_version, taxonomy_node_id, attribute_id, attribute_name, "
                " value_type, unit, allowed_values_json, filterable, searchable, displayable, "
                " required_for_action, unknown_policy) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                schema_rows,
            )

            product_rows = []
            for fam in families.values():
                leaf_id = leaf_id_for_sku[fam["sku_id_for_taxonomy"]]
                product_rows.append((
                    CATALOG_VERSION, fam["product_id"], leaf_id, None, fam["brand_name"],
                    fam["title"], None, fam["rating"], "ACTIVE", "flipkart_csv",
                    fam["sku_id_for_taxonomy"], now, now,
                ))
            cursor.executemany(
                "INSERT INTO products "
                "(catalog_version, product_id, taxonomy_node_id, brand_id, brand_name, title, "
                " description, rating, status, source_system, source_row_id, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                product_rows,
            )

            sku_rows = []
            for row in rows:
                size = row["size"]
                variant_json = json.dumps({"size": size}) if size else "{}"
                sku_rows.append((
                    CATALOG_VERSION, row["sku_id"], family_for_sku[row["sku_id"]], row["sku_id"],
                    size, variant_json, "ACTIVE", now, now,
                ))
            cursor.executemany(
                "INSERT INTO skus "
                "(catalog_version, sku_id, product_id, source_variant_key, variant_label, "
                " variant_attributes_json, status, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                sku_rows,
            )

            offer_rows = []
            for row in rows:
                stock_status = row["stock_status"]
                if stock_status not in STOCK_STATUS_MAP:
                    raise ValueError(f"sku_id {row['sku_id']!r} has unmapped stock_status {stock_status!r}")
                price_paise = None if row["discounted_price"] is None else round(float(row["discounted_price"]) * 100)
                list_price_paise = None if row["retail_price"] is None else round(float(row["retail_price"]) * 100)
                offer_rows.append((
                    CATALOG_VERSION, "off_" + row["sku_id"], row["sku_id"],
                    list_price_paise, price_paise, "INR", now,
                    STOCK_STATUS_MAP[stock_status], now, row["quantity"], "ACTIVE", "MOCK_STATIC_SNAPSHOT",
                ))
            cursor.executemany(
                "INSERT INTO offers "
                "(catalog_version, offer_id, sku_id, list_price_paise, price_paise, currency, "
                " price_as_of, availability_status, availability_as_of, quantity, status, mock_semantics) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                offer_rows,
            )

            material_for_product = {}
            for row in rows:
                pid = family_for_sku[row["sku_id"]]
                if row["material"] and pid not in material_for_product:
                    material_for_product[pid] = row["material"]
            pa_rows = [
                (CATALOG_VERSION, pid, "attr_material", json.dumps(material), material, "KNOWN", now)
                for pid, material in material_for_product.items()
            ]
            cursor.executemany(
                "INSERT INTO product_attributes "
                "(catalog_version, product_id, attribute_id, typed_value_json, normalized_value, "
                " truth_status, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                pa_rows,
            )

            sa_rows = [
                (CATALOG_VERSION, row["sku_id"], "attr_size", json.dumps(row["size"]), row["size"], "KNOWN", now)
                for row in rows if row["size"]
            ]
            cursor.executemany(
                "INSERT INTO sku_attributes "
                "(catalog_version, sku_id, attribute_id, typed_value_json, normalized_value, "
                " truth_status, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                sa_rows,
            )

            cursor.execute(
                "INSERT INTO index_versions "
                "(index_version, catalog_version, backend, artifact_uri, embedding_model_id, "
                " dimension, document_count, status, created_at) "
                "VALUES ('idx_bm25_v1', %s, 'bm25', 'RA/bm25_storage/index.pkl', NULL, NULL, 19998, 'ACTIVE', %s)",
                (CATALOG_VERSION, now),
            )

            cursor.execute(_PRODUCT_METADATA_VIEW_SQL)

            conn.commit()
            print("Tier 1 schema built and product_metadata view created.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
