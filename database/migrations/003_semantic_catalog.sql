BEGIN;

-- Versioned, provenance-carrying knowledge assets for Schema/Value Linking.
-- The current retrieval path keeps using the legacy public tables until a
-- later feature-gated release; this migration only builds the compatible base.
CREATE SCHEMA IF NOT EXISTS semantic_catalog;
REVOKE ALL ON SCHEMA semantic_catalog FROM PUBLIC;

CREATE TABLE IF NOT EXISTS semantic_catalog.semantic_assets (
    tenant_id VARCHAR(128) NOT NULL DEFAULT 'default',
    asset_id VARCHAR(320) NOT NULL,
    asset_type VARCHAR(32) NOT NULL CHECK (asset_type IN
        ('DATABASE','SCHEMA','TABLE','COLUMN','METRIC','QUERY_EXAMPLE','BUSINESS_RULE')),
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    aliases TEXT[] NOT NULL DEFAULT '{}',
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version > 0),
    content_version INTEGER NOT NULL DEFAULT 1 CHECK (content_version > 0),
    source_uri TEXT NOT NULL,
    source_hash CHAR(64) NOT NULL CHECK (source_hash ~ '^[0-9a-f]{64}$'),
    trust_level VARCHAR(12) NOT NULL DEFAULT 'HIGH' CHECK (trust_level IN
        ('LOW','MEDIUM','HIGH')),
    sensitivity VARCHAR(16) NOT NULL DEFAULT 'INTERNAL' CHECK (sensitivity IN
        ('PUBLIC','INTERNAL','CONFIDENTIAL','RESTRICTED')),
    status VARCHAR(16) NOT NULL DEFAULT 'PUBLISHED' CHECK (status IN
        ('DRAFT','PUBLISHED','RETIRED')),
    effective_from TIMESTAMPTZ,
    effective_to TIMESTAMPTZ,
    owner_name VARCHAR(160),
    payload JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, asset_id),
    CHECK (effective_to IS NULL OR effective_from IS NULL OR effective_to > effective_from)
);

CREATE TABLE IF NOT EXISTS semantic_catalog.schema_relations (
    tenant_id VARCHAR(128) NOT NULL DEFAULT 'default',
    relation_id VARCHAR(512) NOT NULL,
    left_asset_id VARCHAR(320) NOT NULL,
    right_asset_id VARCHAR(320) NOT NULL,
    relation_type VARCHAR(24) NOT NULL CHECK (relation_type IN
        ('FOREIGN_KEY','LOGICAL_JOIN')),
    relation_status VARCHAR(16) NOT NULL CHECK (relation_status IN
        ('CANDIDATE','CONFIRMED','REJECTED')),
    cardinality VARCHAR(24),
    join_expression TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version > 0),
    source_uri TEXT NOT NULL,
    source_hash CHAR(64) NOT NULL CHECK (source_hash ~ '^[0-9a-f]{64}$'),
    trust_level VARCHAR(12) NOT NULL DEFAULT 'HIGH' CHECK (trust_level IN
        ('LOW','MEDIUM','HIGH')),
    sensitivity VARCHAR(16) NOT NULL DEFAULT 'INTERNAL' CHECK (sensitivity IN
        ('PUBLIC','INTERNAL','CONFIDENTIAL','RESTRICTED')),
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, relation_id),
    FOREIGN KEY (tenant_id, left_asset_id)
        REFERENCES semantic_catalog.semantic_assets(tenant_id, asset_id),
    FOREIGN KEY (tenant_id, right_asset_id)
        REFERENCES semantic_catalog.semantic_assets(tenant_id, asset_id),
    CHECK (left_asset_id <> right_asset_id),
    CHECK (relation_status <> 'CANDIDATE' OR trust_level <> 'HIGH')
);

CREATE TABLE IF NOT EXISTS semantic_catalog.value_dictionary (
    tenant_id VARCHAR(128) NOT NULL DEFAULT 'default',
    value_id VARCHAR(512) NOT NULL,
    column_asset_id VARCHAR(320) NOT NULL,
    canonical_value TEXT NOT NULL,
    aliases TEXT[] NOT NULL DEFAULT '{}',
    content_version INTEGER NOT NULL DEFAULT 1 CHECK (content_version > 0),
    source_uri TEXT NOT NULL,
    source_hash CHAR(64) NOT NULL CHECK (source_hash ~ '^[0-9a-f]{64}$'),
    trust_level VARCHAR(12) NOT NULL DEFAULT 'HIGH' CHECK (trust_level IN
        ('LOW','MEDIUM','HIGH')),
    sensitivity VARCHAR(16) NOT NULL DEFAULT 'INTERNAL' CHECK (sensitivity IN
        ('PUBLIC','INTERNAL','CONFIDENTIAL','RESTRICTED')),
    status VARCHAR(16) NOT NULL DEFAULT 'PUBLISHED' CHECK (status IN
        ('DRAFT','PUBLISHED','RETIRED')),
    effective_from TIMESTAMPTZ,
    effective_to TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, value_id),
    FOREIGN KEY (tenant_id, column_asset_id)
        REFERENCES semantic_catalog.semantic_assets(tenant_id, asset_id),
    CHECK (effective_to IS NULL OR effective_from IS NULL OR effective_to > effective_from)
);

CREATE INDEX IF NOT EXISTS idx_semantic_assets_type_status
    ON semantic_catalog.semantic_assets(tenant_id, asset_type, status);
CREATE INDEX IF NOT EXISTS idx_semantic_assets_effective
    ON semantic_catalog.semantic_assets(tenant_id, effective_from, effective_to);
CREATE INDEX IF NOT EXISTS idx_schema_relations_left_status
    ON semantic_catalog.schema_relations(tenant_id, left_asset_id, relation_status);
CREATE INDEX IF NOT EXISTS idx_schema_relations_right_status
    ON semantic_catalog.schema_relations(tenant_id, right_asset_id, relation_status);
CREATE INDEX IF NOT EXISTS idx_value_dictionary_column_status
    ON semantic_catalog.value_dictionary(tenant_id, column_asset_id, status);

-- Lift the existing curated schema knowledge into stable semantic assets.
INSERT INTO semantic_catalog.semantic_assets AS target (
    tenant_id, asset_id, asset_type, title, description, aliases,
    schema_version, content_version, source_uri, source_hash,
    trust_level, sensitivity, status, payload
)
SELECT
    'default',
    CASE
        WHEN column_name IS NULL THEN 'table:public.' || table_name
        ELSE 'column:public.' || table_name || '.' || column_name
    END,
    CASE WHEN column_name IS NULL THEN 'TABLE' ELSE 'COLUMN' END,
    table_name || COALESCE('.' || column_name, ''),
    description,
    aliases,
    1,
    1,
    'db://public/schema_catalog/' || id::text,
    encode(sha256(convert_to(
        concat_ws('|', table_name, COALESCE(column_name, ''), description,
                  array_to_string(aliases, ','), is_sensitive::text),
        'UTF8'
    )), 'hex'),
    'HIGH',
    CASE WHEN is_sensitive THEN 'RESTRICTED' ELSE 'INTERNAL' END,
    'PUBLISHED',
    jsonb_build_object(
        'database_schema', 'public',
        'table_name', table_name,
        'column_name', column_name,
        'legacy_id', id
    )
FROM public.schema_catalog
ON CONFLICT (tenant_id, asset_id) DO UPDATE SET
    content_version = CASE
        WHEN target.source_hash <> EXCLUDED.source_hash
        THEN target.content_version + 1
        ELSE target.content_version
    END,
    title = EXCLUDED.title,
    description = EXCLUDED.description,
    aliases = EXCLUDED.aliases,
    source_uri = EXCLUDED.source_uri,
    source_hash = EXCLUDED.source_hash,
    sensitivity = EXCLUDED.sensitivity,
    payload = EXCLUDED.payload,
    updated_at = CASE
        WHEN target.source_hash <> EXCLUDED.source_hash THEN now()
        ELSE target.updated_at
    END;

INSERT INTO semantic_catalog.semantic_assets AS target (
    tenant_id, asset_id, asset_type, title, description, aliases,
    schema_version, content_version, source_uri, source_hash,
    trust_level, sensitivity, status, payload
)
SELECT
    'default',
    'metric:' || lower(metric_name),
    'METRIC',
    metric_name,
    description,
    ARRAY[]::TEXT[],
    1,
    1,
    'db://public/metric_definitions/' || id::text,
    encode(sha256(convert_to(
        concat_ws('|', metric_name, description, formula, example_sql),
        'UTF8'
    )), 'hex'),
    'HIGH',
    'INTERNAL',
    'PUBLISHED',
    jsonb_build_object(
        'formula', formula,
        'example_sql', example_sql,
        'legacy_id', id
    )
FROM public.metric_definitions
ON CONFLICT (tenant_id, asset_id) DO UPDATE SET
    content_version = CASE
        WHEN target.source_hash <> EXCLUDED.source_hash
        THEN target.content_version + 1
        ELSE target.content_version
    END,
    title = EXCLUDED.title,
    description = EXCLUDED.description,
    source_uri = EXCLUDED.source_uri,
    source_hash = EXCLUDED.source_hash,
    payload = EXCLUDED.payload,
    updated_at = CASE
        WHEN target.source_hash <> EXCLUDED.source_hash THEN now()
        ELSE target.updated_at
    END;

INSERT INTO semantic_catalog.semantic_assets AS target (
    tenant_id, asset_id, asset_type, title, description, aliases,
    schema_version, content_version, source_uri, source_hash,
    trust_level, sensitivity, status, payload
)
SELECT
    'default',
    'example:' || id::text,
    'QUERY_EXAMPLE',
    question,
    '已验证的只读 SQL 示例',
    tags,
    1,
    1,
    'db://public/query_examples/' || id::text,
    encode(sha256(convert_to(
        concat_ws('|', question, sql_text, array_to_string(tags, ',')),
        'UTF8'
    )), 'hex'),
    'HIGH',
    'INTERNAL',
    'PUBLISHED',
    jsonb_build_object('sql_text', sql_text, 'legacy_id', id)
FROM public.query_examples
ON CONFLICT (tenant_id, asset_id) DO UPDATE SET
    content_version = CASE
        WHEN target.source_hash <> EXCLUDED.source_hash
        THEN target.content_version + 1
        ELSE target.content_version
    END,
    title = EXCLUDED.title,
    aliases = EXCLUDED.aliases,
    source_uri = EXCLUDED.source_uri,
    source_hash = EXCLUDED.source_hash,
    payload = EXCLUDED.payload,
    updated_at = CASE
        WHEN target.source_hash <> EXCLUDED.source_hash THEN now()
        ELSE target.updated_at
    END;

-- Import physical foreign keys as confirmed, high-trust join relations.
WITH fk_columns AS (
    SELECT
        constraint_row.oid AS constraint_oid,
        constraint_row.conname,
        source_ns.nspname AS source_schema,
        source_table.relname AS source_table,
        source_attribute.attname AS source_column,
        target_ns.nspname AS target_schema,
        target_table.relname AS target_table,
        target_attribute.attname AS target_column
    FROM pg_constraint AS constraint_row
    JOIN pg_class AS source_table ON source_table.oid = constraint_row.conrelid
    JOIN pg_namespace AS source_ns ON source_ns.oid = source_table.relnamespace
    JOIN pg_class AS target_table ON target_table.oid = constraint_row.confrelid
    JOIN pg_namespace AS target_ns ON target_ns.oid = target_table.relnamespace
    JOIN LATERAL unnest(constraint_row.conkey) WITH ORDINALITY
        AS source_key(attnum, position) ON true
    JOIN LATERAL unnest(constraint_row.confkey) WITH ORDINALITY
        AS target_key(attnum, position) ON target_key.position = source_key.position
    JOIN pg_attribute AS source_attribute
        ON source_attribute.attrelid = source_table.oid
       AND source_attribute.attnum = source_key.attnum
    JOIN pg_attribute AS target_attribute
        ON target_attribute.attrelid = target_table.oid
       AND target_attribute.attnum = target_key.attnum
    WHERE constraint_row.contype = 'f'
      AND source_ns.nspname = 'public'
      AND target_ns.nspname = 'public'
), relations AS (
    SELECT
        'fk:' || source_schema || '.' || source_table || '.' || source_column
            || '->' || target_schema || '.' || target_table || '.' || target_column
            AS relation_id,
        'column:' || source_schema || '.' || source_table || '.' || source_column
            AS left_asset_id,
        'column:' || target_schema || '.' || target_table || '.' || target_column
            AS right_asset_id,
        format('%I.%I.%I = %I.%I.%I',
            source_schema, source_table, source_column,
            target_schema, target_table, target_column) AS join_expression,
        'db://pg_constraint/' || constraint_oid::text || '/' || conname AS source_uri
    FROM fk_columns
)
INSERT INTO semantic_catalog.schema_relations AS target (
    tenant_id, relation_id, left_asset_id, right_asset_id,
    relation_type, relation_status, join_expression, schema_version,
    source_uri, source_hash, trust_level, sensitivity, metadata
)
SELECT
    'default', relation_id, left_asset_id, right_asset_id,
    'FOREIGN_KEY', 'CONFIRMED', join_expression, 1,
    source_uri,
    encode(sha256(convert_to(
        concat_ws('|', relation_id, join_expression, source_uri), 'UTF8'
    )), 'hex'),
    'HIGH', 'INTERNAL', '{}'::JSONB
FROM relations
WHERE EXISTS (
    SELECT 1 FROM semantic_catalog.semantic_assets asset
    WHERE asset.tenant_id = 'default' AND asset.asset_id = relations.left_asset_id
)
AND EXISTS (
    SELECT 1 FROM semantic_catalog.semantic_assets asset
    WHERE asset.tenant_id = 'default' AND asset.asset_id = relations.right_asset_id
)
ON CONFLICT (tenant_id, relation_id) DO UPDATE SET
    left_asset_id = EXCLUDED.left_asset_id,
    right_asset_id = EXCLUDED.right_asset_id,
    join_expression = EXCLUDED.join_expression,
    source_uri = EXCLUDED.source_uri,
    source_hash = EXCLUDED.source_hash,
    updated_at = CASE
        WHEN target.source_hash <> EXCLUDED.source_hash
        THEN now()
        ELSE target.updated_at
    END;

-- Curated low-cardinality values only. No business-table scan is performed.
WITH curated_values(table_name, column_name, canonical_value, aliases) AS (
    VALUES
        ('orders', 'status', 'DRAFT', ARRAY['草稿','草稿订单']::TEXT[]),
        ('orders', 'status', 'UNPAID', ARRAY['待支付','未支付']::TEXT[]),
        ('orders', 'status', 'PAID', ARRAY['已支付','付款成功']::TEXT[]),
        ('orders', 'status', 'SHIPPED', ARRAY['已发货']::TEXT[]),
        ('orders', 'status', 'CANCELLED', ARRAY['已取消','取消']::TEXT[]),
        ('refunds', 'status', 'PENDING', ARRAY['退款处理中','待退款']::TEXT[]),
        ('refunds', 'status', 'SUCCESS', ARRAY['退款成功','已退款']::TEXT[]),
        ('refunds', 'status', 'FAILED', ARRAY['退款失败']::TEXT[])
)
INSERT INTO semantic_catalog.value_dictionary AS target (
    tenant_id, value_id, column_asset_id, canonical_value, aliases,
    content_version, source_uri, source_hash, trust_level,
    sensitivity, status, metadata
)
SELECT
    'default',
    'value:public.' || table_name || '.' || column_name || ':' || canonical_value,
    'column:public.' || table_name || '.' || column_name,
    canonical_value,
    aliases,
    1,
    'contract://public/' || table_name || '/' || column_name,
    encode(sha256(convert_to(
        concat_ws('|', table_name, column_name, canonical_value,
                  array_to_string(aliases, ',')),
        'UTF8'
    )), 'hex'),
    'HIGH',
    'INTERNAL',
    'PUBLISHED',
    jsonb_build_object('curated', true, 'source', 'business_contract')
FROM curated_values
ON CONFLICT (tenant_id, value_id) DO UPDATE SET
    content_version = CASE
        WHEN target.source_hash <> EXCLUDED.source_hash
        THEN target.content_version + 1
        ELSE target.content_version
    END,
    aliases = EXCLUDED.aliases,
    source_uri = EXCLUDED.source_uri,
    source_hash = EXCLUDED.source_hash,
    status = EXCLUDED.status,
    metadata = EXCLUDED.metadata,
    updated_at = CASE
        WHEN target.source_hash <> EXCLUDED.source_hash THEN now()
        ELSE target.updated_at
    END;

-- The Agent reads published semantic knowledge through its existing read-only
-- database identity. Asset publication remains an administrator-only migration.
GRANT USAGE ON SCHEMA semantic_catalog TO copilot_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA semantic_catalog TO copilot_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA semantic_catalog
    GRANT SELECT ON TABLES TO copilot_readonly;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON ALL TABLES IN SCHEMA semantic_catalog FROM copilot_readonly;

REVOKE ALL ON SCHEMA semantic_catalog FROM copilot_agent_state;
REVOKE ALL ON ALL TABLES IN SCHEMA semantic_catalog FROM copilot_agent_state;

COMMIT;
