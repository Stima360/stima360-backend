-- Down for 049. Drops only what 049 created, in reverse order.
--
-- Safe to run: the column is nullable and nothing reads it until 051, so
-- removing it restores the pre-049 shape exactly. Down files bracket
-- themselves - the runner has no `down` command and owns no transaction here.

BEGIN;

DROP INDEX IF EXISTS idx_stime_dettagliate_agency_id;

ALTER TABLE stime_dettagliate
    DROP CONSTRAINT IF EXISTS stime_dettagliate_agency_id_fk;

ALTER TABLE stime_dettagliate
    DROP COLUMN IF EXISTS agency_id;

DELETE FROM schema_migrations
 WHERE version = '049_p26_stima_dettagliata_agency_columns';

COMMIT;
