-- Rollback of P26-6B intelligence enforcement.
--
-- Genuinely reversible. 048 wrote no data - it added a constraint on two
-- columns, five functions and five triggers - so every effect can be lifted
-- exactly and no value is lost. That is why this file reverses rather than
-- refusing, unlike the rollback of 047, which undid a backfill it can no longer
-- identify.
--
-- Order is the exact reverse of the up migration:
--
--   1. the five triggers   (they depend on their functions)
--   2. the five functions
--   3. DROP NOT NULL on the two columns
--
-- Deliberately NOT touched:
--
--   * every value in property_watches.agency_id and next_best_actions.agency_id.
--     Those belong to 047 and to the runtime that has written since; clearing
--     them would discard real tenancy.
--   * the columns, foreign keys and indexes themselves - they belong to 046.
--   * every row of the five tables the triggers guard.
--   * every P26-1 through P26-6A object.
--
-- No CASCADE anywhere. If something has come to depend on these objects, that
-- must fail loudly rather than be swept away silently.
--
-- This file brackets its own transaction because it is executed manually rather
-- than by scripts/p26_migrate.py, which has no down command.

BEGIN;

-- 1. The triggers, before the functions they call.
DROP TRIGGER IF EXISTS trg_property_watches_agency_integrity ON property_watches;
DROP TRIGGER IF EXISTS trg_next_best_actions_agency_integrity ON next_best_actions;
DROP TRIGGER IF EXISTS trg_invisible_sale_candidates_agency_integrity ON invisible_sale_candidates;
DROP TRIGGER IF EXISTS trg_invisible_sale_events_parent_integrity ON invisible_sale_events;
DROP TRIGGER IF EXISTS trg_seller_revival_suppressions_agency_integrity ON seller_revival_suppressions;

-- 2. The functions, once nothing calls them.
DROP FUNCTION IF EXISTS property_watch_agency_integrity();
DROP FUNCTION IF EXISTS next_best_action_agency_integrity();
DROP FUNCTION IF EXISTS invisible_sale_candidate_agency_integrity();
DROP FUNCTION IF EXISTS invisible_sale_event_parent_integrity();
DROP FUNCTION IF EXISTS seller_revival_suppression_agency_integrity();

-- 3. Nullability. Every value written by 047 and by the runtime stays exactly
-- where it is; only the constraint is lifted.
ALTER TABLE property_watches ALTER COLUMN agency_id DROP NOT NULL;
ALTER TABLE next_best_actions ALTER COLUMN agency_id DROP NOT NULL;

COMMIT;
