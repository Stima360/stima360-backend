BEGIN;

CREATE TABLE flow_rules (
    id BIGSERIAL PRIMARY KEY,
    code VARCHAR(30) NOT NULL UNIQUE,
    code_version INTEGER NOT NULL CHECK (code_version >= 1),
    name VARCHAR(200) NOT NULL,
    description TEXT,
    event_type VARCHAR(100) NOT NULL,
    entity_type VARCHAR(50) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    priority VARCHAR(20) NOT NULL DEFAULT 'normal' CHECK (priority IN ('low','normal','high','urgent')),
    cooldown_minutes INTEGER NOT NULL DEFAULT 0 CHECK (cooldown_minutes BETWEEN 0 AND 43200),
    parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
    default_parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
    allowed_parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_simulation_at TIMESTAMPTZ,
    last_simulation_status VARCHAR(20) NOT NULL DEFAULT 'never_run' CHECK (last_simulation_status IN ('never_run','success','failed','outdated')),
    last_simulation_execution_id BIGINT,
    last_simulation_parameters_hash VARCHAR(64),
    last_simulation_rule_version INTEGER,
    activated_at TIMESTAMPTZ,
    activated_by VARCHAR(200),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    archived_at TIMESTAMPTZ
);

CREATE TABLE flow_events (
    id BIGSERIAL PRIMARY KEY,
    event_type VARCHAR(100) NOT NULL,
    entity_type VARCHAR(50) NOT NULL,
    entity_id BIGINT NOT NULL,
    source_module VARCHAR(30) NOT NULL CHECK (source_module IN ('core','property','buy','match','flow')),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    deduplication_key VARCHAR(250) NOT NULL UNIQUE,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status VARCHAR(20) NOT NULL DEFAULT 'received' CHECK (status IN ('received','processed','ignored','failed')),
    error_message TEXT
);

CREATE TABLE flow_executions (
    id BIGSERIAL PRIMARY KEY,
    event_id BIGINT REFERENCES flow_events(id) ON DELETE SET NULL,
    rule_id BIGINT NOT NULL REFERENCES flow_rules(id) ON DELETE RESTRICT,
    entity_type VARCHAR(50) NOT NULL,
    entity_id BIGINT NOT NULL,
    execution_mode VARCHAR(20) NOT NULL CHECK (execution_mode IN ('simulation','live')),
    status VARCHAR(30) NOT NULL CHECK (status IN ('matched','not_matched','executed','skipped','failed')),
    conditions_result JSONB NOT NULL DEFAULT '{}'::jsonb,
    actions_result JSONB NOT NULL DEFAULT '{}'::jsonb,
    rule_version INTEGER NOT NULL,
    parameters_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    parameters_hash VARCHAR(64) NOT NULL,
    error_message TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count BETWEEN 0 AND 3),
    max_retry INTEGER NOT NULL DEFAULT 3 CHECK (max_retry = 3),
    retry_of_execution_id BIGINT REFERENCES flow_executions(id) ON DELETE SET NULL,
    last_retry_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE flow_action_records (
    id BIGSERIAL PRIMARY KEY,
    execution_id BIGINT NOT NULL REFERENCES flow_executions(id) ON DELETE CASCADE,
    action_type VARCHAR(50) NOT NULL CHECK (action_type IN ('create_core_task','create_core_activity','mark_for_review','log_only')),
    target_module VARCHAR(30) NOT NULL,
    target_entity_type VARCHAR(50),
    target_entity_id BIGINT,
    idempotency_key VARCHAR(300) NOT NULL UNIQUE,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','completed','failed','skipped')),
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE flow_suppressions (
    id BIGSERIAL PRIMARY KEY,
    rule_id BIGINT NOT NULL REFERENCES flow_rules(id) ON DELETE CASCADE,
    entity_type VARCHAR(50) NOT NULL,
    entity_id BIGINT NOT NULL,
    reason TEXT NOT NULL,
    expires_at TIMESTAMPTZ,
    created_by VARCHAR(200),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(rule_id, entity_type, entity_id)
);

CREATE INDEX idx_flow_rules_active ON flow_rules(is_active) WHERE archived_at IS NULL;
CREATE INDEX idx_flow_events_status_received ON flow_events(status, received_at DESC);
CREATE INDEX idx_flow_events_entity ON flow_events(entity_type, entity_id);
CREATE INDEX idx_flow_executions_status_created ON flow_executions(status, created_at DESC);
CREATE INDEX idx_flow_executions_rule_entity ON flow_executions(rule_id, entity_type, entity_id);
CREATE INDEX idx_flow_action_execution ON flow_action_records(execution_id);
CREATE INDEX idx_flow_suppressions_active ON flow_suppressions(rule_id, entity_type, entity_id, expires_at);

COMMIT;
