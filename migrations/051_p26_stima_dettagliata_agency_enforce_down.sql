-- Down for 051. Removes only what 051 created.
--
-- Drops this slice's trigger and function and returns the column to nullable.
-- It does NOT blank the values - that belongs to 050, which refuses, and to
-- 049, which drops the column outright.
--
-- The function is dropped by name and is one this slice introduced, so nothing
-- else can be relying on it: 051 is the only file that creates
-- `stima_dettagliata_agency_integrity`, and no other trigger references it.
--
-- Down files bracket themselves; the runner owns no transaction here.

BEGIN;

DROP TRIGGER IF EXISTS trg_stima_dettagliata_agency_integrity ON stime_dettagliate;
DROP FUNCTION IF EXISTS stima_dettagliata_agency_integrity();

ALTER TABLE stime_dettagliate
    ALTER COLUMN agency_id DROP NOT NULL;

DELETE FROM schema_migrations
 WHERE version = '051_p26_stima_dettagliata_agency_enforce';

COMMIT;
