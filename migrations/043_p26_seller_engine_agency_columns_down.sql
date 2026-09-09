-- Rollback of P26-6A's seller engine additive foundation.
--
-- Fully reversible. 043 added exactly six objects - two columns, two foreign
-- keys, two indexes - and this file removes exactly those, in the reverse order
-- of their dependencies.
--
-- Deliberately NOT touched:
--
--   * every existing seller_timeline_events and followup_actions row. 043 wrote
--     no data, so there is none to undo.
--   * seller_intent, which has no stored state and was never referenced.
--   * agencies, and every table owned by P26-1 through P26-5.
--
-- No CASCADE anywhere. If something has come to depend on these objects, that
-- must fail loudly rather than be swept away silently.
--
-- This file brackets its own transaction because it is executed manually rather
-- than by scripts/p26_migrate.py, which has no down command.

BEGIN;

-- 1. The indexes.
DROP INDEX IF EXISTS idx_seller_timeline_events_agency_id;
DROP INDEX IF EXISTS idx_followup_actions_agency_id;

-- 2. The foreign keys, before the columns they constrain.
ALTER TABLE seller_timeline_events DROP CONSTRAINT IF EXISTS seller_timeline_events_agency_id_fk;
ALTER TABLE followup_actions DROP CONSTRAINT IF EXISTS followup_actions_agency_id_fk;

-- 3. The columns.
ALTER TABLE seller_timeline_events DROP COLUMN IF EXISTS agency_id;
ALTER TABLE followup_actions DROP COLUMN IF EXISTS agency_id;

COMMIT;
