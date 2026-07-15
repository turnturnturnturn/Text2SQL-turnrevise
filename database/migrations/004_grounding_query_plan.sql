BEGIN;

-- Phase A inherited table-level rows only where the legacy catalog happened
-- to define one. Build a complete parent table layer from curated columns;
-- this is catalog metadata, not a scan of business data.
WITH tables AS (
    SELECT
        table_name,
        string_agg(description, '；' ORDER BY column_name)
            FILTER (WHERE column_name IS NOT NULL) AS descriptions,
        array_agg(DISTINCT alias ORDER BY alias)
            FILTER (WHERE alias IS NOT NULL) AS aliases
    FROM public.schema_catalog
    LEFT JOIN LATERAL unnest(aliases) AS alias ON true
    GROUP BY table_name
)
INSERT INTO semantic_catalog.semantic_assets AS target (
    tenant_id, asset_id, asset_type, title, description, aliases,
    schema_version, content_version, source_uri, source_hash,
    trust_level, sensitivity, status, payload
)
SELECT
    'default',
    'table:public.' || table_name,
    'TABLE',
    table_name,
    COALESCE(descriptions, table_name || ' table'),
    COALESCE(aliases, ARRAY[]::TEXT[]),
    1,
    1,
    'contract://public/' || table_name,
    encode(sha256(convert_to(
        concat_ws('|', table_name, COALESCE(descriptions, ''),
                  array_to_string(COALESCE(aliases, ARRAY[]::TEXT[]), ',')),
        'UTF8'
    )), 'hex'),
    'HIGH',
    'INTERNAL',
    'PUBLISHED',
    jsonb_build_object('database_schema', 'public', 'table_name', table_name)
FROM tables
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
    payload = EXCLUDED.payload,
    status = EXCLUDED.status,
    updated_at = CASE
        WHEN target.source_hash <> EXCLUDED.source_hash
          OR target.status <> EXCLUDED.status THEN now()
        ELSE target.updated_at
    END;

-- Fill physical columns missing from the curated legacy catalog. This reads
-- information_schema only and never samples business values.
INSERT INTO semantic_catalog.semantic_assets (
    tenant_id, asset_id, asset_type, title, description, aliases,
    schema_version, content_version, source_uri, source_hash,
    trust_level, sensitivity, status, payload
)
SELECT
    'default',
    'column:public.' || table_name || '.' || column_name,
    'COLUMN',
    table_name || '.' || column_name,
    'Physical column ' || table_name || '.' || column_name
        || ' (' || data_type || ', nullable=' || is_nullable || ')',
    ARRAY[]::TEXT[],
    1,
    1,
    'db://information_schema/public/' || table_name || '/' || column_name,
    encode(sha256(convert_to(
        concat_ws('|', table_name, column_name, data_type, is_nullable),
        'UTF8'
    )), 'hex'),
    'MEDIUM',
    CASE WHEN column_name IN ('phone','email')
         THEN 'RESTRICTED' ELSE 'INTERNAL' END,
    'PUBLISHED',
    jsonb_build_object(
        'database_schema', 'public',
        'table_name', table_name,
        'column_name', column_name,
        'data_type', data_type,
        'nullable', is_nullable = 'YES',
        'physical_only', true
    )
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name IN ('customers','products','orders','order_items','refunds')
ON CONFLICT (tenant_id, asset_id) DO NOTHING;

-- Schema Graph v2 metadata. Physical foreign keys are confirmed evidence;
-- candidate relations remain non-executable regardless of these fields.
ALTER TABLE semantic_catalog.schema_relations
    ADD COLUMN IF NOT EXISTS left_nullable BOOLEAN,
    ADD COLUMN IF NOT EXISTS right_unique BOOLEAN,
    ADD COLUMN IF NOT EXISTS historical_success_count BIGINT NOT NULL DEFAULT 0
        CHECK (historical_success_count >= 0),
    ADD COLUMN IF NOT EXISTS historical_failure_count BIGINT NOT NULL DEFAULT 0
        CHECK (historical_failure_count >= 0),
    ADD COLUMN IF NOT EXISTS last_verified_at TIMESTAMPTZ;

UPDATE semantic_catalog.schema_relations
SET cardinality = COALESCE(cardinality, 'MANY_TO_ONE'),
    left_nullable = COALESCE(left_nullable, false),
    right_unique = COALESCE(right_unique, true),
    last_verified_at = COALESCE(last_verified_at, now())
WHERE relation_type = 'FOREIGN_KEY'
  AND relation_status = 'CONFIRMED';

-- The original Phase A seed used FAILED, but the protected business contract
-- uses REJECTED. Preserve provenance by retiring the bad value instead of
-- deleting history, and publish the real contract value.
UPDATE semantic_catalog.value_dictionary
SET status = 'RETIRED',
    content_version = CASE WHEN status = 'RETIRED' THEN content_version
                           ELSE content_version + 1 END,
    metadata = metadata || jsonb_build_object(
        'retired_reason', 'not_in_database_check_constraint',
        'replacement_value_id', 'value:public.refunds.status:REJECTED'
    ),
    updated_at = CASE WHEN status = 'RETIRED' THEN updated_at ELSE now() END
WHERE tenant_id = 'default'
  AND value_id = 'value:public.refunds.status:FAILED';

INSERT INTO semantic_catalog.value_dictionary AS target (
    tenant_id, value_id, column_asset_id, canonical_value, aliases,
    content_version, source_uri, source_hash, trust_level,
    sensitivity, status, metadata
)
VALUES (
    'default',
    'value:public.refunds.status:REJECTED',
    'column:public.refunds.status',
    'REJECTED',
    ARRAY['退款被拒绝','已拒绝']::TEXT[],
    1,
    'contract://public/refunds/status',
    encode(sha256(convert_to(
        'refunds|status|REJECTED|退款被拒绝,已拒绝', 'UTF8'
    )), 'hex'),
    'HIGH',
    'INTERNAL',
    'PUBLISHED',
    jsonb_build_object('curated', true, 'source', 'database_check_constraint')
)
ON CONFLICT (tenant_id, value_id) DO UPDATE SET
    content_version = CASE
        WHEN target.source_hash <> EXCLUDED.source_hash
        THEN target.content_version + 1
        ELSE target.content_version
    END,
    column_asset_id = EXCLUDED.column_asset_id,
    canonical_value = EXCLUDED.canonical_value,
    aliases = EXCLUDED.aliases,
    source_uri = EXCLUDED.source_uri,
    source_hash = EXCLUDED.source_hash,
    trust_level = EXCLUDED.trust_level,
    sensitivity = EXCLUDED.sensitivity,
    status = EXCLUDED.status,
    metadata = EXCLUDED.metadata,
    updated_at = CASE
        WHEN target.source_hash <> EXCLUDED.source_hash
          OR target.status <> EXCLUDED.status THEN now()
        ELSE target.updated_at
    END;

CREATE TABLE IF NOT EXISTS agent_state.query_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES agent_state.agent_runs(id) ON DELETE CASCADE,
    sequence_no INTEGER NOT NULL CHECK (sequence_no >= 0),
    plan_version INTEGER NOT NULL DEFAULT 1 CHECK (plan_version > 0),
    plan_hash CHAR(64) NOT NULL CHECK (plan_hash ~ '^[0-9a-f]{64}$'),
    validation_status VARCHAR(24) NOT NULL CHECK (validation_status IN
        ('VALID','NEEDS_CLARIFICATION','BLOCKED')),
    plan_json JSONB NOT NULL,
    grounding_snapshot JSONB NOT NULL,
    evidence_ids TEXT[] NOT NULL DEFAULT '{}',
    validation_errors JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, sequence_no),
    UNIQUE (run_id, plan_hash)
);

CREATE INDEX IF NOT EXISTS idx_query_plans_run_created
    ON agent_state.query_plans(run_id, created_at DESC);

GRANT SELECT, INSERT, UPDATE, DELETE ON agent_state.query_plans
    TO copilot_agent_state;

REVOKE ALL ON agent_state.query_plans FROM copilot_readonly;

COMMIT;
