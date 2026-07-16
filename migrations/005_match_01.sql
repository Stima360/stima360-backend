BEGIN;

CREATE TABLE match_runs (
    id BIGSERIAL PRIMARY KEY,
    run_type VARCHAR(40) NOT NULL CHECK (run_type IN ('single','buy_to_all_properties','property_to_all_buyers','manual_refresh')),
    buy_request_id BIGINT REFERENCES buy_requests(id) ON DELETE CASCADE,
    property_id BIGINT REFERENCES properties(id) ON DELETE CASCADE,
    algorithm_version VARCHAR(40) NOT NULL,
    criteria_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    property_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(20) NOT NULL DEFAULT 'completed' CHECK (status IN ('running','completed','failed')),
    error_message TEXT,
    created_by VARCHAR(200),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (buy_request_id IS NOT NULL OR property_id IS NOT NULL)
);

CREATE TABLE matches (
    id BIGSERIAL PRIMARY KEY,
    buy_request_id BIGINT NOT NULL REFERENCES buy_requests(id) ON DELETE CASCADE,
    property_id BIGINT NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    latest_run_id BIGINT REFERENCES match_runs(id) ON DELETE SET NULL,
    compatibility_status VARCHAR(30) NOT NULL CHECK (compatibility_status IN ('compatible','exception','incompatible')),
    score_total NUMERIC(5,2) NOT NULL CHECK (score_total BETWEEN 0 AND 100),
    score_location NUMERIC(5,2) NOT NULL DEFAULT 0,
    score_budget NUMERIC(5,2) NOT NULL DEFAULT 0,
    score_typology NUMERIC(5,2) NOT NULL DEFAULT 0,
    score_dimensions NUMERIC(5,2) NOT NULL DEFAULT 0,
    score_rooms NUMERIC(5,2) NOT NULL DEFAULT 0,
    score_features NUMERIC(5,2) NOT NULL DEFAULT 0,
    score_condition NUMERIC(5,2) NOT NULL DEFAULT 0,
    match_class VARCHAR(30) NOT NULL CHECK (match_class IN ('excellent','strong','good','possible','weak','poor','incompatible')),
    hard_fail_count INTEGER NOT NULL DEFAULT 0 CHECK (hard_fail_count >= 0),
    warning_count INTEGER NOT NULL DEFAULT 0 CHECK (warning_count >= 0),
    strengths JSONB NOT NULL DEFAULT '[]'::jsonb,
    warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    blocking_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
    commercial_status VARCHAR(30) NOT NULL DEFAULT 'new' CHECK (commercial_status IN ('new','to_review','approved','rejected','suggested','interested','visit_requested','visited','archived')),
    priority VARCHAR(20) NOT NULL DEFAULT 'normal' CHECK (priority IN ('low','normal','high','urgent')),
    is_manual_override BOOLEAN NOT NULL DEFAULT FALSE,
    manual_score NUMERIC(5,2) CHECK (manual_score BETWEEN 0 AND 100),
    manual_reason TEXT,
    assigned_to VARCHAR(200),
    algorithm_version VARCHAR(40) NOT NULL,
    first_matched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_calculated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_reviewed_at TIMESTAMPTZ,
    archived_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (buy_request_id, property_id)
);

CREATE TABLE match_requirement_results (
    id BIGSERIAL PRIMARY KEY,
    match_run_id BIGINT NOT NULL REFERENCES match_runs(id) ON DELETE CASCADE,
    criterion_code VARCHAR(100) NOT NULL,
    criterion_group VARCHAR(50) NOT NULL,
    criterion_type VARCHAR(20) NOT NULL CHECK (criterion_type IN ('hard','soft','preference','informational')),
    requested_value JSONB,
    property_value JSONB,
    weight NUMERIC(7,3) NOT NULL DEFAULT 0,
    result VARCHAR(30) NOT NULL CHECK (result IN ('matched','partially_matched','not_matched','not_available','not_applicable')),
    score NUMERIC(5,2) NOT NULL DEFAULT 0,
    penalty NUMERIC(5,2) NOT NULL DEFAULT 0,
    is_blocking BOOLEAN NOT NULL DEFAULT FALSE,
    explanation TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE match_exclusions (
    id BIGSERIAL PRIMARY KEY,
    buy_request_id BIGINT NOT NULL REFERENCES buy_requests(id) ON DELETE CASCADE,
    property_id BIGINT NOT NULL REFERENCES properties(id) ON DELETE CASCADE,
    exclusion_type VARCHAR(30) NOT NULL DEFAULT 'agent_decision' CHECK (exclusion_type IN ('permanent','temporary','already_rejected','duplicate','agent_decision','buyer_decision')),
    reason TEXT,
    expires_at TIMESTAMPTZ,
    created_by VARCHAR(200),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (buy_request_id, property_id)
);

CREATE INDEX idx_match_runs_buy ON match_runs(buy_request_id, created_at DESC);
CREATE INDEX idx_match_runs_property ON match_runs(property_id, created_at DESC);
CREATE INDEX idx_matches_buy_score ON matches(buy_request_id, score_total DESC);
CREATE INDEX idx_matches_property_score ON matches(property_id, score_total DESC);
CREATE INDEX idx_matches_status ON matches(commercial_status, match_class);
CREATE INDEX idx_match_results_run ON match_requirement_results(match_run_id, criterion_group);
CREATE INDEX idx_match_exclusions_pair ON match_exclusions(buy_request_id, property_id);

COMMIT;
