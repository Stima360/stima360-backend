BEGIN;
DROP TRIGGER IF EXISTS trg_buy_history_agency_integrity ON buy_request_history;
DROP TRIGGER IF EXISTS trg_buy_task_links_agency_integrity ON buy_request_task_links;
DROP TRIGGER IF EXISTS trg_buy_interactions_agency_integrity ON buy_request_interactions;
DROP TRIGGER IF EXISTS trg_buy_requests_agency_integrity ON buy_requests;
DROP FUNCTION IF EXISTS buy_agency_integrity();
ALTER TABLE buy_requests ALTER COLUMN agency_id DROP NOT NULL;
COMMIT;
