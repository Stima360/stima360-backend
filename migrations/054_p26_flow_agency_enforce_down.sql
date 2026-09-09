-- Down for 054. Removes only what 054 created.
--
-- Drops this slice's three triggers and their three functions and returns the
-- three columns to nullable. It does NOT blank the values - that belongs to
-- 053, which refuses, and to 052, which drops the columns outright.
--
-- Every function dropped here is one 054 introduced: 054 is the only file that
-- creates them, and no trigger outside this slice references them. Nothing
-- belonging to 008 or to any other FLOW migration is touched.
--
-- Down files bracket themselves; the runner owns no transaction here.

BEGIN;

DROP TRIGGER IF EXISTS trg_flow_suppression_agency_integrity ON flow_suppressions;
DROP TRIGGER IF EXISTS trg_flow_execution_agency_integrity ON flow_executions;
DROP TRIGGER IF EXISTS trg_flow_event_agency_integrity ON flow_events;

DROP FUNCTION IF EXISTS flow_suppression_agency_integrity();
DROP FUNCTION IF EXISTS flow_execution_agency_integrity();
DROP FUNCTION IF EXISTS flow_event_agency_integrity();

ALTER TABLE flow_suppressions ALTER COLUMN agency_id DROP NOT NULL;
ALTER TABLE flow_executions   ALTER COLUMN agency_id DROP NOT NULL;
ALTER TABLE flow_events       ALTER COLUMN agency_id DROP NOT NULL;

DELETE FROM schema_migrations WHERE version = '054_p26_flow_agency_enforce';

COMMIT;
