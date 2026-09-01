BEGIN;

-- Rollback of the sole P18-B addition: drops only followup_actions.
-- Nothing else - no CORE table, no seller_intelligence table, no other
-- migration's objects - is touched.
DROP TABLE IF EXISTS followup_actions;

COMMIT;
