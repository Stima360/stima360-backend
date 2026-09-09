-- Rollback of P26-6B's intelligence additive foundation.
--
-- Fully reversible. 046 added exactly six objects - two columns, two foreign
-- keys, two indexes - and this file removes exactly those, in the reverse order
-- of their dependencies.
--
-- Deliberately NOT touched:
--
--   * every property_watches and next_best_actions row. 046 wrote no data.
--   * the five derived tables it never referenced:
--     property_watch_observations, invisible_sale_opportunities,
--     invisible_sale_candidates, invisible_sale_events and
--     seller_revival_suppressions.
--   * agencies, and every object owned by P26-1 through P26-6A.
--
-- No CASCADE anywhere. If something has come to depend on these objects, that
-- must fail loudly rather than be swept away silently.
--
-- This file brackets its own transaction because it is executed manually rather
-- than by scripts/p26_migrate.py, which has no down command.

BEGIN;

-- 1. The indexes.
DROP INDEX IF EXISTS idx_property_watches_agency_id;
DROP INDEX IF EXISTS idx_next_best_actions_agency_id;

-- 2. The foreign keys, before the columns they constrain.
ALTER TABLE property_watches DROP CONSTRAINT IF EXISTS property_watches_agency_id_fk;
ALTER TABLE next_best_actions DROP CONSTRAINT IF EXISTS next_best_actions_agency_id_fk;

-- 3. The columns.
ALTER TABLE property_watches DROP COLUMN IF EXISTS agency_id;
ALTER TABLE next_best_actions DROP COLUMN IF EXISTS agency_id;

COMMIT;
