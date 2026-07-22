BEGIN;

DROP INDEX IF EXISTS idx_match_feedback_match;
DROP INDEX IF EXISTS idx_match_refresh_history_match;
DROP INDEX IF EXISTS idx_matches_stale_since;
DROP INDEX IF EXISTS idx_matches_review_required;
DROP INDEX IF EXISTS idx_matches_freshness_status;

DROP TABLE IF EXISTS match_feedback;
DROP TABLE IF EXISTS match_refresh_history;

ALTER TABLE matches DROP CONSTRAINT IF EXISTS matches_freshness_status_check;
UPDATE matches SET commercial_status='visit_requested' WHERE commercial_status='visit_scheduled';
UPDATE matches SET commercial_status='visited' WHERE commercial_status='offer_candidate';
ALTER TABLE matches DROP CONSTRAINT IF EXISTS matches_commercial_status_check;
ALTER TABLE matches ADD CONSTRAINT matches_commercial_status_check
CHECK (commercial_status IN ('new','to_review','approved','rejected','suggested','interested','visit_requested','visited','archived'));
ALTER TABLE matches
    DROP COLUMN IF EXISTS review_required,
    DROP COLUMN IF EXISTS property_version_at_calculation,
    DROP COLUMN IF EXISTS buy_version_at_calculation,
    DROP COLUMN IF EXISTS recalculation_error,
    DROP COLUMN IF EXISTS last_failed_run_at,
    DROP COLUMN IF EXISTS last_successful_run_at,
    DROP COLUMN IF EXISTS stale_since,
    DROP COLUMN IF EXISTS stale_reason,
    DROP COLUMN IF EXISTS freshness_status;

COMMIT;
