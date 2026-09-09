-- Rollback of P26-6A seller engine enforcement.
--
-- Genuinely reversible. 045 wrote no data - it added a constraint on two
-- columns, two functions and two triggers - so every effect can be lifted
-- exactly and no value is lost. That is why this file reverses rather than
-- refusing, unlike the rollback of 044, which undid a backfill it can no longer
-- identify.
--
-- Order is the exact reverse of the up migration:
--
--   1. the two triggers   (they depend on their functions)
--   2. the two functions
--   3. DROP NOT NULL on the two columns
--
-- Deliberately NOT touched:
--
--   * every value in seller_timeline_events.agency_id and
--     followup_actions.agency_id. Those belong to 044 and to the runtime that
--     has written since; clearing them would discard real tenancy.
--   * the columns, foreign keys and indexes themselves - they belong to 043.
--   * every reference table, and every P26-1 through P26-5 object.
--
-- No CASCADE anywhere. If something has come to depend on these objects, that
-- must fail loudly rather than be swept away silently.
--
-- This file brackets its own transaction because it is executed manually rather
-- than by scripts/p26_migrate.py, which has no down command.

BEGIN;

-- 1. The triggers, before the functions they call.
DROP TRIGGER IF EXISTS trg_seller_timeline_events_agency_integrity ON seller_timeline_events;
DROP TRIGGER IF EXISTS trg_followup_actions_agency_integrity ON followup_actions;

-- 2. The functions, once nothing calls them.
DROP FUNCTION IF EXISTS seller_timeline_event_agency_integrity();
DROP FUNCTION IF EXISTS followup_action_agency_integrity();

-- 3. Nullability. Every value written by 044 and by the runtime stays exactly
-- where it is; only the constraint is lifted.
ALTER TABLE seller_timeline_events ALTER COLUMN agency_id DROP NOT NULL;
ALTER TABLE followup_actions ALTER COLUMN agency_id DROP NOT NULL;

COMMIT;
