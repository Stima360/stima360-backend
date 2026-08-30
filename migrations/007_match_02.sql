BEGIN;

ALTER TABLE matches
    ADD COLUMN IF NOT EXISTS freshness_status VARCHAR(30) NOT NULL DEFAULT 'fresh',
    ADD COLUMN IF NOT EXISTS stale_reason TEXT,
    ADD COLUMN IF NOT EXISTS stale_since TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_successful_run_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_failed_run_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS recalculation_error TEXT,
    ADD COLUMN IF NOT EXISTS buy_version_at_calculation TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS property_version_at_calculation TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS review_required BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE matches DROP CONSTRAINT IF EXISTS matches_commercial_status_check;
ALTER TABLE matches ADD CONSTRAINT matches_commercial_status_check
CHECK (commercial_status IN (
    'new','to_review','approved','rejected','suggested','interested',
    'visit_requested','visit_scheduled','visited','offer_candidate','archived'
));

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'matches_freshness_status_check'
    ) THEN
        ALTER TABLE matches ADD CONSTRAINT matches_freshness_status_check
        CHECK (freshness_status IN ('fresh','stale','recalculating','failed','excluded'));
    END IF;
END $$;

UPDATE matches m
SET
    freshness_status = CASE
        WHEN EXISTS (
            SELECT 1 FROM match_exclusions e
            WHERE e.buy_request_id=m.buy_request_id
              AND e.property_id=m.property_id
              AND (e.expires_at IS NULL OR e.expires_at>NOW())
        ) THEN 'excluded'
        ELSE 'fresh'
    END,
    last_successful_run_at = COALESCE(last_successful_run_at,last_calculated_at),
    buy_version_at_calculation = COALESCE(buy_version_at_calculation,m.last_calculated_at,m.created_at),
    property_version_at_calculation = COALESCE(property_version_at_calculation,m.last_calculated_at,m.created_at)
FROM buy_requests b, properties p
WHERE b.id=m.buy_request_id AND p.id=m.property_id;

CREATE TABLE IF NOT EXISTS match_refresh_history (
    id BIGSERIAL PRIMARY KEY,
    match_id BIGINT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    previous_run_id BIGINT REFERENCES match_runs(id) ON DELETE SET NULL,
    new_run_id BIGINT REFERENCES match_runs(id) ON DELETE SET NULL,
    previous_score NUMERIC(5,2),
    new_score NUMERIC(5,2),
    previous_class VARCHAR(30),
    new_class VARCHAR(30),
    previous_compatibility_status VARCHAR(30),
    new_compatibility_status VARCHAR(30),
    trigger_source VARCHAR(20) NOT NULL DEFAULT 'manual'
        CHECK (trigger_source IN ('manual','buy','property','system')),
    trigger_reason TEXT,
    changed_fields JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS match_feedback (
    id BIGSERIAL PRIMARY KEY,
    match_id BIGINT NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    source VARCHAR(20) NOT NULL CHECK (source IN ('agent','buyer')),
    feedback_type VARCHAR(20) NOT NULL CHECK (feedback_type IN ('positive','neutral','negative')),
    reason_code VARCHAR(40) CHECK (reason_code IS NULL OR reason_code IN (
        'price','location','size','condition','floor','elevator','parking','outdoor_space','not_available','other'
    )),
    notes TEXT,
    created_by VARCHAR(200),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_matches_freshness_status ON matches(freshness_status);
CREATE INDEX IF NOT EXISTS idx_matches_review_required ON matches(review_required) WHERE archived_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_matches_stale_since ON matches(stale_since) WHERE freshness_status='stale';
CREATE INDEX IF NOT EXISTS idx_match_refresh_history_match ON match_refresh_history(match_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_match_feedback_match ON match_feedback(match_id,created_at DESC);

COMMIT;
