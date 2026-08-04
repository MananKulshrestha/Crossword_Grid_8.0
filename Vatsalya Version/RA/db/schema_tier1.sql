-- Tier 1 catalog-foundation schema (database schema and requirements.md,
-- section 4.2 "Catalog and retrieval foundation"). Scope: only the tables
-- the Flipkart catalog actually populates -- products/skus/offers and their
-- supporting version/taxonomy/attribute tables. Sessions, cart, chat,
-- Quality Sentinel, Catalog Language, Query Recovery, and Catalog
-- Operations tables belong to other worktrees and are not created here.
--
-- Two practical Tier 1 extensions beyond the base spec, because real data
-- for both already exists in the current product_metadata table and
-- dropping it would be a real regression, not a simplification:
--   - products.rating        (no rating column exists anywhere in the
--     spec's Tier 1 product tables)
--   - offers.list_price_paise (the spec's offers table has one price field;
--     the Flipkart data has a genuine separate original/retail price)
-- Per section 12's own guidance ("plan the first migration so every Tier 1
-- table can receive Tier 2 columns without renaming core IDs"), additive
-- columns like these are consistent with the spec's intent.

-- ---------------------------------------------------------------------
-- catalog_versions
-- ---------------------------------------------------------------------
CREATE TABLE catalog_versions (
    catalog_version         VARCHAR(64) NOT NULL,
    taxonomy_version        VARCHAR(64) NOT NULL,
    category_schema_version VARCHAR(64) NOT NULL,
    status                  ENUM('CANDIDATE','APPROVED','ACTIVE','RETIRED','FAILED') NOT NULL,
    source_batch_id         VARCHAR(128) NULL,
    data_uri                VARCHAR(512) NULL,
    created_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    activated_at            DATETIME NULL,
    -- Section 4.1: "Constraint: one active catalog version in the local
    -- deployment." Generated column + unique index gives this for free --
    -- MySQL unique indexes ignore NULLs, so only ACTIVE rows can collide.
    active_flag TINYINT AS (IF(status = 'ACTIVE', 1, NULL)) STORED,
    PRIMARY KEY (catalog_version),
    UNIQUE KEY uq_one_active_catalog (active_flag)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- taxonomy_nodes
-- ---------------------------------------------------------------------
CREATE TABLE taxonomy_nodes (
    taxonomy_version         VARCHAR(64) NOT NULL,
    taxonomy_node_id         VARCHAR(64) NOT NULL,
    parent_taxonomy_node_id  VARCHAR(64) NULL,
    canonical_name           VARCHAR(255) NOT NULL,
    normalized_name          VARCHAR(255) NOT NULL,
    path                     VARCHAR(1024) NOT NULL,
    depth                    INT NOT NULL,
    active                   TINYINT(1) NOT NULL DEFAULT 1,
    PRIMARY KEY (taxonomy_version, taxonomy_node_id),
    -- Plain (non-composite) unique key so products/category_schemas can FK
    -- to a node by taxonomy_node_id alone -- the spec's own products table
    -- only stores taxonomy_node_id, not taxonomy_version, and this Tier 1
    -- deployment only ever has one taxonomy_version in play at a time.
    UNIQUE KEY uq_taxonomy_node_id (taxonomy_node_id),
    KEY ix_taxonomy_parent (taxonomy_version, parent_taxonomy_node_id),
    CONSTRAINT fk_taxonomy_parent FOREIGN KEY (parent_taxonomy_node_id)
        REFERENCES taxonomy_nodes (taxonomy_node_id)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- category_schemas
-- ---------------------------------------------------------------------
CREATE TABLE category_schemas (
    category_schema_version VARCHAR(64) NOT NULL,
    taxonomy_node_id         VARCHAR(64) NOT NULL,
    attribute_id             VARCHAR(64) NOT NULL,
    attribute_name           VARCHAR(128) NOT NULL,
    value_type               VARCHAR(32) NOT NULL,
    unit                     VARCHAR(32) NULL,
    allowed_values_json      JSON NULL,
    filterable               TINYINT(1) NOT NULL DEFAULT 0,
    searchable               TINYINT(1) NOT NULL DEFAULT 0,
    displayable              TINYINT(1) NOT NULL DEFAULT 1,
    required_for_action      TINYINT(1) NOT NULL DEFAULT 0,
    unknown_policy           VARCHAR(32) NOT NULL DEFAULT 'ALLOW_UNKNOWN',
    PRIMARY KEY (category_schema_version, taxonomy_node_id, attribute_id),
    CONSTRAINT fk_category_schemas_taxonomy FOREIGN KEY (taxonomy_node_id)
        REFERENCES taxonomy_nodes (taxonomy_node_id)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- products
-- ---------------------------------------------------------------------
CREATE TABLE products (
    catalog_version   VARCHAR(64) NOT NULL,
    product_id        VARCHAR(64) NOT NULL,
    taxonomy_node_id  VARCHAR(64) NOT NULL,
    brand_id          VARCHAR(64) NULL,
    brand_name        VARCHAR(255) NULL,
    title             VARCHAR(512) NOT NULL,
    description       MEDIUMTEXT NULL,
    rating            DECIMAL(3,2) NULL,  -- Tier 1 extension, see header
    status            ENUM('ACTIVE','RETIRED','QUARANTINED','FAILED') NOT NULL DEFAULT 'ACTIVE',
    source_system     VARCHAR(64) NOT NULL DEFAULT 'flipkart_csv',
    source_row_id     VARCHAR(64) NULL,
    created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (catalog_version, product_id),
    KEY ix_products_taxonomy (taxonomy_node_id),
    CONSTRAINT fk_products_catalog FOREIGN KEY (catalog_version)
        REFERENCES catalog_versions (catalog_version),
    CONSTRAINT fk_products_taxonomy FOREIGN KEY (taxonomy_node_id)
        REFERENCES taxonomy_nodes (taxonomy_node_id)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- skus
-- ---------------------------------------------------------------------
CREATE TABLE skus (
    catalog_version         VARCHAR(64) NOT NULL,
    sku_id                  VARCHAR(64) NOT NULL,
    product_id              VARCHAR(64) NOT NULL,
    source_variant_key      VARCHAR(64) NOT NULL,
    variant_label           VARCHAR(255) NULL,
    variant_attributes_json JSON NULL,
    status                  ENUM('ACTIVE','RETIRED','QUARANTINED','FAILED') NOT NULL DEFAULT 'ACTIVE',
    created_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (catalog_version, sku_id),
    UNIQUE KEY uq_product_variant (catalog_version, product_id, source_variant_key),
    CONSTRAINT fk_skus_product FOREIGN KEY (catalog_version, product_id)
        REFERENCES products (catalog_version, product_id)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- offers
-- ---------------------------------------------------------------------
CREATE TABLE offers (
    catalog_version      VARCHAR(64) NOT NULL,
    offer_id             VARCHAR(64) NOT NULL,
    sku_id               VARCHAR(64) NOT NULL,
    seller_id            VARCHAR(64) NOT NULL DEFAULT 'flipkart_mock_seller',
    seller_name          VARCHAR(255) NOT NULL DEFAULT 'Flipkart (mock)',
    source_offer_key     VARCHAR(64) NULL,
    list_price_paise     BIGINT NULL,  -- Tier 1 extension, see header (original retail_price)
    -- Nullable, not NOT NULL: a real fraction of the Flipkart rows have no
    -- price at all. Per Shared Rule 4 ("Unknown is not false"), that's
    -- represented as NULL, not a fabricated 0 or sentinel value.
    price_paise          BIGINT NULL,
    currency             CHAR(3) NOT NULL DEFAULT 'INR',
    price_as_of          DATETIME NOT NULL,
    availability_status  ENUM('IN_STOCK','LOW_STOCK','OUT_OF_STOCK') NOT NULL,
    availability_as_of   DATETIME NOT NULL,
    quantity             INT NULL,
    status               ENUM('ACTIVE','RETIRED') NOT NULL DEFAULT 'ACTIVE',
    mock_semantics       VARCHAR(64) NOT NULL DEFAULT 'MOCK_STATIC_SNAPSHOT',
    PRIMARY KEY (catalog_version, offer_id),
    KEY ix_offers_sku (catalog_version, sku_id),
    CONSTRAINT fk_offers_sku FOREIGN KEY (catalog_version, sku_id)
        REFERENCES skus (catalog_version, sku_id),
    CONSTRAINT chk_offers_quantity_non_negative CHECK (quantity IS NULL OR quantity >= 0)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- product_attributes / sku_attributes
-- ---------------------------------------------------------------------
CREATE TABLE product_attributes (
    catalog_version    VARCHAR(64) NOT NULL,
    product_id         VARCHAR(64) NOT NULL,
    attribute_id       VARCHAR(64) NOT NULL,
    typed_value_json   JSON NOT NULL,
    normalized_value   VARCHAR(255) NULL,
    truth_status       VARCHAR(32) NOT NULL DEFAULT 'KNOWN',
    updated_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (catalog_version, product_id, attribute_id),
    CONSTRAINT fk_product_attributes_product FOREIGN KEY (catalog_version, product_id)
        REFERENCES products (catalog_version, product_id)
) ENGINE=InnoDB;

CREATE TABLE sku_attributes (
    catalog_version    VARCHAR(64) NOT NULL,
    sku_id             VARCHAR(64) NOT NULL,
    attribute_id       VARCHAR(64) NOT NULL,
    typed_value_json   JSON NOT NULL,
    normalized_value   VARCHAR(255) NULL,
    truth_status       VARCHAR(32) NOT NULL DEFAULT 'KNOWN',
    updated_at         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (catalog_version, sku_id, attribute_id),
    CONSTRAINT fk_sku_attributes_sku FOREIGN KEY (catalog_version, sku_id)
        REFERENCES skus (catalog_version, sku_id)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- index_versions -- the vector/FTS/graph artifacts RA already built
-- (Qdrant, BM25, LightRAG graph) get one row each here; the bytes stay
-- external (Qdrant container, bm25_storage/, lightrag_storage/), per
-- section 4.2: "The vector/FTS/index bytes stay outside the DB."
-- ---------------------------------------------------------------------
CREATE TABLE index_versions (
    index_version       VARCHAR(64) NOT NULL,
    catalog_version     VARCHAR(64) NOT NULL,
    backend              VARCHAR(64) NOT NULL,
    artifact_uri         VARCHAR(512) NULL,
    embedding_model_id   VARCHAR(128) NULL,
    dimension            INT NULL,
    document_count       INT NULL,
    status                VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    created_at            DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (index_version),
    CONSTRAINT fk_index_versions_catalog FOREIGN KEY (catalog_version)
        REFERENCES catalog_versions (catalog_version)
) ENGINE=InnoDB;
