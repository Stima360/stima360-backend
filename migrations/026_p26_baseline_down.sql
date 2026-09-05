-- Rollback of the P26-0 baseline and migration ledger foundation.
--
-- 026 is purely additive infrastructure, so its rollback is complete and safe.
-- It removes exactly the five objects 026 introduced:
--
--   1. trigger trg_schema_migrations_guard
--   2. trigger trg_schema_baseline_guard
--   3. function schema_ledger_guard()
--   4. table   schema_migrations
--   5. table   schema_baseline
--
-- No application table is referenced. No application row is read, changed, or
-- removed. There is no DELETE against ledger rows: the two ledger tables are
-- removed whole, which is the only situation in which ledger history may
-- legitimately disappear, because the ledger itself is being uninstalled.
--
-- Applying this after later P26 migrations have registered themselves would
-- discard their ledger history while leaving their schema changes in place.
-- The guard below refuses exactly that.

BEGIN;

DO $do$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'schema_migrations'
    ) AND EXISTS (
        SELECT 1 FROM schema_migrations
    ) THEN
        RAISE EXCEPTION
            'Rollback 026 refused: schema_migrations holds % registered '
            'migration(s). Removing the ledger now would discard their history '
            'while their schema changes remain applied. Roll those migrations '
            'back first, or restore from the pre-migration snapshot.',
            (SELECT count(*) FROM schema_migrations);
    END IF;
END
$do$;

DROP TRIGGER IF EXISTS trg_schema_migrations_guard ON schema_migrations;
DROP TRIGGER IF EXISTS trg_schema_baseline_guard ON schema_baseline;

DROP TABLE IF EXISTS schema_migrations;
DROP TABLE IF EXISTS schema_baseline;

DROP FUNCTION IF EXISTS schema_ledger_guard();

COMMIT;
