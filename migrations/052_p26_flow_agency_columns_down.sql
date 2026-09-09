-- Down for 052. Drops only what 052 created, in reverse order.
--
-- Every object named here is one 052 introduced: three indexes, three foreign
-- keys, three columns. Nothing belonging to 008 (the FLOW schema) or to any
-- other slice is touched.
--
-- Safe to run: the columns are nullable and nothing reads them until 054, so
-- removing them restores the pre-052 shape exactly.
--
-- Down files bracket themselves - the runner has no `down` command and owns no
-- transaction here.

BEGIN;

DROP INDEX IF EXISTS idx_flow_suppressions_agency;
DROP INDEX IF EXISTS idx_flow_executions_agency;
DROP INDEX IF EXISTS idx_flow_events_agency;

ALTER TABLE flow_suppressions DROP CONSTRAINT IF EXISTS flow_suppressions_agency_id_fk;
ALTER TABLE flow_executions   DROP CONSTRAINT IF EXISTS flow_executions_agency_id_fk;
ALTER TABLE flow_events       DROP CONSTRAINT IF EXISTS flow_events_agency_id_fk;

ALTER TABLE flow_suppressions DROP COLUMN IF EXISTS agency_id;
ALTER TABLE flow_executions   DROP COLUMN IF EXISTS agency_id;
ALTER TABLE flow_events       DROP COLUMN IF EXISTS agency_id;

DELETE FROM schema_migrations WHERE version = '052_p26_flow_agency_columns';

COMMIT;
