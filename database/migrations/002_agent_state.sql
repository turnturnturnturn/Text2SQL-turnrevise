CREATE SCHEMA IF NOT EXISTS agent_state;

CREATE TABLE IF NOT EXISTS agent_state.conversations (
    id VARCHAR(128) PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES public.app_users(id) ON DELETE CASCADE,
    user_snapshot JSONB NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    summary TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (id, user_id)
);

CREATE TABLE IF NOT EXISTS agent_state.messages (
    id BIGSERIAL PRIMARY KEY,
    conversation_id VARCHAR(128) NOT NULL
        REFERENCES agent_state.conversations(id) ON DELETE CASCADE,
    sequence_no INTEGER NOT NULL CHECK (sequence_no >= 0),
    role VARCHAR(24) NOT NULL,
    content TEXT NOT NULL,
    message_payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, sequence_no)
);

CREATE TABLE IF NOT EXISTS agent_state.agent_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id VARCHAR(128) REFERENCES agent_state.conversations(id) ON DELETE SET NULL,
    user_id UUID NOT NULL REFERENCES public.app_users(id) ON DELETE CASCADE,
    request_id VARCHAR(128) NOT NULL UNIQUE,
    status VARCHAR(24) NOT NULL CHECK (status IN
        ('RECEIVED','CONTEXT_READY','MODEL_RUNNING','TOOL_RUNNING','VERIFYING',
         'COMPLETED','FAILED','CANCELLED')),
    model_name VARCHAR(160),
    retrieval_mode VARCHAR(32),
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    failure_type VARCHAR(64),
    failure_detail TEXT,
    metadata JSONB NOT NULL DEFAULT '{}',
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_state.run_steps (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES agent_state.agent_runs(id) ON DELETE CASCADE,
    sequence_no INTEGER NOT NULL CHECK (sequence_no >= 0),
    stage VARCHAR(32) NOT NULL,
    status VARCHAR(24) NOT NULL,
    tool_name VARCHAR(128),
    input_summary JSONB NOT NULL DEFAULT '{}',
    output_summary JSONB NOT NULL DEFAULT '{}',
    duration_ms BIGINT CHECK (duration_ms IS NULL OR duration_ms >= 0),
    error_type VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, sequence_no)
);

CREATE TABLE IF NOT EXISTS agent_state.memories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES public.app_users(id) ON DELETE CASCADE,
    memory_type VARCHAR(32) NOT NULL CHECK (memory_type IN
        ('USER_PREFERENCE','BUSINESS_TERM','VERIFIED_QUERY','TOOL_PATTERN')),
    status VARCHAR(16) NOT NULL DEFAULT 'candidate' CHECK (status IN
        ('candidate','confirmed','rejected')),
    content TEXT NOT NULL,
    normalized_content TEXT NOT NULL,
    source VARCHAR(32) NOT NULL DEFAULT 'explicit_instruction',
    source_ref VARCHAR(160),
    tool_name VARCHAR(128),
    tool_args JSONB,
    success BOOLEAN,
    embedding REAL[],
    metadata JSONB NOT NULL DEFAULT '{}',
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    last_used_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, memory_type, normalized_content)
);

CREATE TABLE IF NOT EXISTS agent_state.memory_events (
    id BIGSERIAL PRIMARY KEY,
    memory_id UUID REFERENCES agent_state.memories(id) ON DELETE SET NULL,
    user_id UUID NOT NULL REFERENCES public.app_users(id) ON DELETE CASCADE,
    event_type VARCHAR(24) NOT NULL CHECK (event_type IN
        ('created','confirmed','rejected','deleted','recalled','expired')),
    details JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_conversations_user_updated
    ON agent_state.conversations(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_messages_conversation_sequence
    ON agent_state.messages(conversation_id, sequence_no);
CREATE INDEX IF NOT EXISTS idx_agent_runs_user_started
    ON agent_state.agent_runs(user_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_run_steps_run_sequence
    ON agent_state.run_steps(run_id, sequence_no);
CREATE INDEX IF NOT EXISTS idx_agent_memories_user_status_updated
    ON agent_state.memories(user_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_memories_expiry
    ON agent_state.memories(expires_at) WHERE expires_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_agent_memory_events_memory_created
    ON agent_state.memory_events(memory_id, created_at DESC);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'copilot_agent_state') THEN
        CREATE ROLE copilot_agent_state LOGIN PASSWORD 'copilot_agent_state_dev';
    END IF;
END $$;

GRANT CONNECT ON DATABASE enterprise_copilot TO copilot_agent_state;
GRANT USAGE ON SCHEMA agent_state TO copilot_agent_state;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA agent_state
    TO copilot_agent_state;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA agent_state TO copilot_agent_state;
ALTER DEFAULT PRIVILEGES IN SCHEMA agent_state
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO copilot_agent_state;
ALTER DEFAULT PRIVILEGES IN SCHEMA agent_state
    GRANT USAGE, SELECT ON SEQUENCES TO copilot_agent_state;
REVOKE ALL ON SCHEMA public FROM copilot_agent_state;
GRANT USAGE ON SCHEMA public TO copilot_agent_state;
GRANT SELECT (id) ON public.app_users TO copilot_agent_state;
