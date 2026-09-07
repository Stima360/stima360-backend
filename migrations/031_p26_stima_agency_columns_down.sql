-- Rollback of P26-2B's STIMA additive foundation.
--
-- Fully reversible. 031 added exactly three objects and this file removes
-- exactly those three, in the reverse order of their dependencies:
--
--   1. the index
--   2. the foreign key
--   3. the column
--
-- Deliberately NOT touched:
--
--   * every existing stime column and every existing row - 031 wrote no data,
--     so there is no data to undo
--   * lead_stime and stime_dettagliate, which 031 never referenced
--   * agencies, and the P26-1 tables
--
-- No CASCADE anywhere. If something has come to depend on these objects, that
-- must fail loudly rather than be swept away silently.
--
-- This file brackets its own transaction because it is executed manually
-- rather than by scripts/p26_migrate.py, which has no down command.

BEGIN;

-- 1. The index.
DROP INDEX IF EXISTS idx_stime_agency_id;

-- 2. The foreign key, before the column it constrains.
ALTER TABLE stime DROP CONSTRAINT IF EXISTS stime_agency_id_fk;

-- 3. The column.
ALTER TABLE stime DROP COLUMN IF EXISTS agency_id;

COMMIT;
