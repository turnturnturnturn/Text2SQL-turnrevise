BEGIN;

ALTER TABLE agent_state.agent_runs
    ADD COLUMN IF NOT EXISTS operation_kind VARCHAR(24) NOT NULL DEFAULT 'READ_QUERY',
    ADD COLUMN IF NOT EXISTS parent_run_id UUID REFERENCES agent_state.agent_runs(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS correlation_id UUID;

UPDATE agent_state.agent_runs SET correlation_id = id WHERE correlation_id IS NULL;
ALTER TABLE agent_state.agent_runs ALTER COLUMN correlation_id SET NOT NULL;

DO $$
DECLARE constraint_name TEXT;
BEGIN
    SELECT conname INTO constraint_name
    FROM pg_constraint
    WHERE conrelid = 'agent_state.agent_runs'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) LIKE '%status%';
    IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE agent_state.agent_runs DROP CONSTRAINT %I', constraint_name);
    END IF;
END $$;

ALTER TABLE agent_state.agent_runs
    DROP CONSTRAINT IF EXISTS agent_runs_operation_kind_check,
    ADD CONSTRAINT agent_runs_status_v2_check CHECK (status IN
        ('RECEIVED','CONTEXT_BUILDING','LINKING','NEEDS_CLARIFICATION','PLANNING',
         'GENERATING','VALIDATING','EXECUTING','CONTEXT_READY','MODEL_RUNNING',
         'TOOL_RUNNING','VERIFYING','COMPLETED','FAILED','CANCELLED')),
    ADD CONSTRAINT agent_runs_operation_kind_check CHECK (operation_kind IN
        ('READ_QUERY','BUSINESS_WRITE','APPROVAL'));

DROP INDEX IF EXISTS agent_state.uq_agent_runs_active_conversation;
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_runs_active_conversation
    ON agent_state.agent_runs(conversation_id)
    WHERE conversation_id IS NOT NULL
      AND status NOT IN ('NEEDS_CLARIFICATION','COMPLETED','FAILED','CANCELLED');
CREATE INDEX IF NOT EXISTS idx_agent_runs_parent
    ON agent_state.agent_runs(parent_run_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_correlation
    ON agent_state.agent_runs(correlation_id, started_at);

ALTER TABLE agent_state.memories
    ADD COLUMN IF NOT EXISTS validity VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
    ADD COLUMN IF NOT EXISTS source_hash CHAR(64),
    ADD COLUMN IF NOT EXISTS conflict_key VARCHAR(160),
    ADD COLUMN IF NOT EXISTS valid_from TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS valid_to TIMESTAMPTZ;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='agent_state.memories'::regclass
          AND conname='memories_validity_check'
    ) THEN
        ALTER TABLE agent_state.memories ADD CONSTRAINT memories_validity_check
            CHECK (validity IN ('ACTIVE','CONFLICTED','SUPERSEDED','INVALID'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_memories_conflict_active
    ON agent_state.memories(user_id, conflict_key, updated_at DESC)
    WHERE status='confirmed' AND validity='ACTIVE' AND conflict_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_memories_one_active_conflict
    ON agent_state.memories(user_id, conflict_key)
    WHERE status='confirmed' AND validity='ACTIVE' AND conflict_key IS NOT NULL;

DO $$
DECLARE constraint_name TEXT;
BEGIN
    SELECT conname INTO constraint_name
    FROM pg_constraint
    WHERE conrelid='agent_state.memory_events'::regclass
      AND contype='c' AND pg_get_constraintdef(oid) LIKE '%event_type%';
    IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE agent_state.memory_events DROP CONSTRAINT %I', constraint_name);
    END IF;
END $$;
ALTER TABLE agent_state.memory_events
    ADD CONSTRAINT memory_events_type_v2_check CHECK (event_type IN
        ('created','confirmed','rejected','deleted','recalled','expired',
         'superseded','invalidated','conflicted'));

CREATE TABLE IF NOT EXISTS agent_state.context_manifests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES agent_state.agent_runs(id) ON DELETE CASCADE,
    sequence_no INTEGER NOT NULL CHECK (sequence_no >= 0),
    policy_version VARCHAR(64) NOT NULL,
    mode VARCHAR(12) NOT NULL CHECK (mode IN ('off','shadow','enforce')),
    total_token_budget INTEGER NOT NULL CHECK (total_token_budget > 0),
    actual_tokens INTEGER NOT NULL CHECK (actual_tokens >= 0),
    manifest_payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, sequence_no)
);

ALTER TABLE agent_state.context_manifests
    ADD COLUMN IF NOT EXISTS id UUID DEFAULT gen_random_uuid(),
    ADD COLUMN IF NOT EXISTS sequence_no INTEGER NOT NULL DEFAULT 0;

DO $$
DECLARE primary_name TEXT;
BEGIN
    SELECT conname INTO primary_name
    FROM pg_constraint
    WHERE conrelid='agent_state.context_manifests'::regclass
      AND contype='p'
      AND pg_get_constraintdef(oid) LIKE '%(run_id)%';
    IF primary_name IS NOT NULL THEN
        EXECUTE format(
            'ALTER TABLE agent_state.context_manifests DROP CONSTRAINT %I',
            primary_name
        );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='agent_state.context_manifests'::regclass AND contype='p'
    ) THEN
        ALTER TABLE agent_state.context_manifests
            ADD CONSTRAINT context_manifests_pkey PRIMARY KEY (id);
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_context_manifests_run_sequence
    ON agent_state.context_manifests(run_id, sequence_no);

CREATE TABLE IF NOT EXISTS agent_state.conversation_states (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id VARCHAR(128) NOT NULL REFERENCES agent_state.conversations(id) ON DELETE CASCADE,
    summary_version INTEGER NOT NULL CHECK (summary_version > 0),
    state_payload JSONB NOT NULL,
    source_message_ids TEXT[] NOT NULL,
    model_id VARCHAR(160) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, summary_version)
);

CREATE TABLE IF NOT EXISTS agent_state.run_artifacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES agent_state.agent_runs(id) ON DELETE CASCADE,
    stage VARCHAR(32) NOT NULL,
    artifact_type VARCHAR(64) NOT NULL,
    content_hash CHAR(64) NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    storage_ref VARCHAR(240) NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_state.run_checkpoints (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES agent_state.agent_runs(id) ON DELETE CASCADE,
    stage VARCHAR(32) NOT NULL,
    artifacts JSONB NOT NULL DEFAULT '[]',
    safe_to_resume BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_state.clarification_requests (
    id UUID PRIMARY KEY,
    parent_run_id UUID NOT NULL UNIQUE REFERENCES agent_state.agent_runs(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES public.app_users(id) ON DELETE CASCADE,
    ambiguity_id VARCHAR(160) NOT NULL,
    question TEXT NOT NULL,
    options JSONB NOT NULL,
    reason VARCHAR(240) NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    answered_at TIMESTAMPTZ,
    selected_option_id VARCHAR(160),
    child_run_id UUID REFERENCES agent_state.agent_runs(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_run_artifacts_run_stage
    ON agent_state.run_artifacts(run_id, stage, created_at);
CREATE INDEX IF NOT EXISTS idx_run_checkpoints_run_created
    ON agent_state.run_checkpoints(run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_clarification_user_expiry
    ON agent_state.clarification_requests(user_id, expires_at DESC);

GRANT SELECT, INSERT, UPDATE, DELETE ON
    agent_state.context_manifests,
    agent_state.conversation_states,
    agent_state.run_artifacts,
    agent_state.run_checkpoints,
    agent_state.clarification_requests
    TO copilot_agent_state;

REVOKE ALL ON
    agent_state.context_manifests,
    agent_state.conversation_states,
    agent_state.run_artifacts,
    agent_state.run_checkpoints,
    agent_state.clarification_requests
    FROM copilot_readonly;

COMMIT;
