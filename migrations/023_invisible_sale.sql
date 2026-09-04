CREATE TABLE IF NOT EXISTS invisible_sale_opportunities (
    id SERIAL PRIMARY KEY,
    watch_id INTEGER NOT NULL REFERENCES property_watches(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN ('ready', 'empty', 'closed')),
    candidate_digest CHAR(64) NOT NULL CHECK (char_length(candidate_digest) = 64),
    current_candidate_count INTEGER NOT NULL CHECK (current_candidate_count >= 0),
    algorithm_version TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    last_evaluated_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (watch_id)
);
CREATE TABLE IF NOT EXISTS invisible_sale_candidates (
    id SERIAL PRIMARY KEY,
    opportunity_id INTEGER NOT NULL REFERENCES invisible_sale_opportunities(id) ON DELETE RESTRICT,
    buy_request_id INTEGER NOT NULL REFERENCES buy_requests(id) ON DELETE RESTRICT,
    score_total NUMERIC(5, 2) NOT NULL CHECK (score_total >= 0 AND score_total <= 100),
    compatibility_status TEXT NOT NULL CHECK (compatibility_status IN ('compatible', 'exception')),
    reason_codes JSONB NOT NULL,
    last_activity_at TIMESTAMPTZ NOT NULL,
    budget_reference NUMERIC(14, 2),
    match_algorithm_version TEXT NOT NULL,
    candidate_digest CHAR(64) NOT NULL CHECK (char_length(candidate_digest) = 64),
    status TEXT NOT NULL CHECK (status IN ('pending_review', 'approved', 'rejected', 'stale')),
    decision_version INTEGER NOT NULL DEFAULT 0 CHECK (decision_version >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (opportunity_id, buy_request_id)
);
CREATE TABLE IF NOT EXISTS invisible_sale_events (
    id SERIAL PRIMARY KEY,
    opportunity_id INTEGER NOT NULL REFERENCES invisible_sale_opportunities(id) ON DELETE RESTRICT,
    candidate_id INTEGER REFERENCES invisible_sale_candidates(id) ON DELETE RESTRICT,
    event_type TEXT NOT NULL CHECK (event_type IN ('discovered', 'refreshed', 'approved', 'rejected', 'stale', 'closed')),
    opportunity_revision INTEGER CHECK (opportunity_revision >= 0),
    decision_version INTEGER CHECK (decision_version >= 0),
    idempotency_key TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_invisible_sale_opportunities_watch ON invisible_sale_opportunities(watch_id);
CREATE INDEX IF NOT EXISTS idx_invisible_sale_candidates_state_buy ON invisible_sale_candidates(opportunity_id, status, buy_request_id);
CREATE INDEX IF NOT EXISTS idx_invisible_sale_events_chronology ON invisible_sale_events(opportunity_id, created_at, id);
