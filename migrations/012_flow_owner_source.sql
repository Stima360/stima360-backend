-- FLOW - P8.3A
-- Estende esclusivamente la allowlist source_module di flow_events con OWNER.

BEGIN;

ALTER TABLE flow_events
DROP CONSTRAINT flow_events_source_module_check;

ALTER TABLE flow_events
ADD CONSTRAINT flow_events_source_module_check
CHECK (source_module IN ('core','property','buy','match','flow','owner'));

COMMIT;
