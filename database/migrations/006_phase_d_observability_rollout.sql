BEGIN;

CREATE TABLE IF NOT EXISTS agent_state.run_trace_events (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES agent_state.agent_runs(id) ON DELETE CASCADE,
    sequence_no INTEGER NOT NULL CHECK (sequence_no > 0),
    event_type VARCHAR(48) NOT NULL,
    attributes JSONB NOT NULL DEFAULT '{}',
    schema_version VARCHAR(32) NOT NULL,
    instrumentation_version VARCHAR(32) NOT NULL,
    sampled BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, sequence_no)
);

CREATE TABLE IF NOT EXISTS agent_state.clarification_resume_tokens (
    token_hash CHAR(64) PRIMARY KEY CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    child_run_id UUID NOT NULL UNIQUE REFERENCES agent_state.agent_runs(id) ON DELETE CASCADE,
    parent_run_id UUID NOT NULL REFERENCES agent_state.agent_runs(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES public.app_users(id) ON DELETE CASCADE,
    tenant_id VARCHAR(80) NOT NULL DEFAULT 'default',
    evidence_hash CHAR(64) NOT NULL CHECK (evidence_hash ~ '^[0-9a-f]{64}$'),
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_state.rollout_policies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    policy_key VARCHAR(120) NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    scope_type VARCHAR(24) NOT NULL CHECK (scope_type IN ('user','tenant','role','route_risk','global')),
    scope_value VARCHAR(240) NOT NULL,
    cohort_percent INTEGER NOT NULL CHECK (cohort_percent BETWEEN 0 AND 100),
    modes JSONB NOT NULL,
    active BOOLEAN NOT NULL DEFAULT true,
    downgrade_reason VARCHAR(240),
    created_by VARCHAR(120) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (policy_key, version)
);

CREATE TABLE IF NOT EXISTS agent_state.rollout_decisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL UNIQUE REFERENCES agent_state.agent_runs(id) ON DELETE CASCADE,
    policy_id UUID NOT NULL REFERENCES agent_state.rollout_policies(id),
    policy_version INTEGER NOT NULL,
    tenant_id VARCHAR(80) NOT NULL DEFAULT 'default',
    cohort VARCHAR(40) NOT NULL,
    bucket SMALLINT NOT NULL CHECK (bucket BETWEEN 0 AND 99),
    modes JSONB NOT NULL,
    reason VARCHAR(240) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO agent_state.rollout_policies
    (policy_key,version,scope_type,scope_value,cohort_percent,modes,active,created_by)
VALUES
    ('global-default',1,'global','*',100,
     '{"evidence_drawer":"shadow","otel":"shadow","clarification_resume":"shadow","grounding":"shadow","context_harness":"shadow"}'::jsonb,
     true,'migration-006')
ON CONFLICT (policy_key,version) DO NOTHING;

CREATE TABLE IF NOT EXISTS agent_state.evaluation_releases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    release_name VARCHAR(120) NOT NULL UNIQUE,
    manifest_hash CHAR(64) NOT NULL CHECK (manifest_hash ~ '^[0-9a-f]{64}$'),
    frozen_config JSONB NOT NULL,
    case_count INTEGER NOT NULL CHECK (case_count > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_state.evaluation_cases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    release_id UUID NOT NULL REFERENCES agent_state.evaluation_releases(id) ON DELETE CASCADE,
    case_id VARCHAR(160) NOT NULL,
    subset VARCHAR(40) NOT NULL,
    case_hash CHAR(64) NOT NULL CHECK (case_hash ~ '^[0-9a-f]{64}$'),
    expected JSONB NOT NULL,
    UNIQUE (release_id, case_id)
);

CREATE TABLE IF NOT EXISTS agent_state.evaluation_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    release_id UUID NOT NULL REFERENCES agent_state.evaluation_releases(id) ON DELETE CASCADE,
    case_id VARCHAR(160) NOT NULL,
    run_id UUID REFERENCES agent_state.agent_runs(id) ON DELETE SET NULL,
    evaluation_path VARCHAR(20) NOT NULL CHECK (evaluation_path IN ('oracle','offline','live_model')),
    status VARCHAR(32) NOT NULL,
    evidence_hash CHAR(64),
    plan_hash CHAR(64),
    failure_stage VARCHAR(48),
    guard_decision VARCHAR(48),
    latency_ms INTEGER CHECK (latency_ms IS NULL OR latency_ms >= 0),
    metrics JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (release_id, case_id, evaluation_path)
);

CREATE INDEX IF NOT EXISTS idx_run_trace_events_run_created
    ON agent_state.run_trace_events(run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_resume_tokens_expiry
    ON agent_state.clarification_resume_tokens(expires_at) WHERE consumed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_rollout_policies_active
    ON agent_state.rollout_policies(active, scope_type, scope_value, version DESC);
CREATE INDEX IF NOT EXISTS idx_rollout_decisions_policy_created
    ON agent_state.rollout_decisions(policy_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_evaluation_results_release_status
    ON agent_state.evaluation_results(release_id, status, failure_stage);

GRANT SELECT, INSERT, UPDATE, DELETE ON
    agent_state.run_trace_events,
    agent_state.clarification_resume_tokens,
    agent_state.rollout_policies,
    agent_state.rollout_decisions,
    agent_state.evaluation_releases,
    agent_state.evaluation_cases,
    agent_state.evaluation_results
    TO copilot_agent_state;
GRANT USAGE, SELECT ON SEQUENCE agent_state.run_trace_events_id_seq
    TO copilot_agent_state;

REVOKE ALL ON
    agent_state.run_trace_events,
    agent_state.clarification_resume_tokens,
    agent_state.rollout_policies,
    agent_state.rollout_decisions,
    agent_state.evaluation_releases,
    agent_state.evaluation_cases,
    agent_state.evaluation_results
    FROM copilot_readonly;
REVOKE ALL ON SEQUENCE agent_state.run_trace_events_id_seq
    FROM copilot_readonly;

COMMIT;
