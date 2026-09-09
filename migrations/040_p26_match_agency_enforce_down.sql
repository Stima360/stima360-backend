-- Rollback of P26-2E MATCH agency integrity.
--
-- Genuinely reversible. 040 wrote no data - it added one function and four
-- triggers - so every effect can be lifted exactly and nothing is lost. That
-- is why this file reverses rather than refusing, unlike the rollbacks of 029,
-- 032 and 038, which undid backfills they could no longer identify.
--
-- Order is the exact reverse of the up migration:
--
--   1. the four triggers   (they depend on the function)
--   2. the function
--
-- Deliberately NOT touched:
--
--   * every MATCH row. 040 changed no data, so there is none to restore.
--   * matches, match_runs, match_requirement_results, match_exclusions,
--     match_refresh_history and match_feedback keep their shape: 040 added no
--     column to any of them, so there is none to drop.
--   * buy_requests.agency_id and properties.agency_id, which belong to the BUY
--     and PROPERTY slices and are the roots MATCH derives from.
--
-- No CASCADE anywhere. If something has come to depend on these objects, that
-- must fail loudly rather than be swept away silently.
--
-- This file brackets its own transaction because it is executed manually
-- rather than by scripts/p26_migrate.py, which has no down command.

BEGIN;

-- 1. The triggers, before the function they call.
DROP TRIGGER IF EXISTS trg_matches_agency_integrity ON matches;
DROP TRIGGER IF EXISTS trg_match_runs_agency_integrity ON match_runs;
DROP TRIGGER IF EXISTS trg_match_exclusions_agency_integrity ON match_exclusions;
DROP TRIGGER IF EXISTS trg_match_refresh_history_agency_integrity ON match_refresh_history;

-- 2. The function, once nothing calls it.
DROP FUNCTION IF EXISTS match_agency_integrity();

COMMIT;
