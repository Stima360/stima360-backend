BEGIN;
DROP TABLE IF EXISTS buy_request_history;
DROP TABLE IF EXISTS buy_request_task_links;
DROP TABLE IF EXISTS buy_request_interactions;
DROP INDEX IF EXISTS idx_buy_requests_next_action;
ALTER TABLE buy_requests
    DROP COLUMN IF EXISTS finance_notes,
    DROP COLUMN IF EXISTS finance_review_at,
    DROP COLUMN IF EXISTS next_action_note,
    DROP COLUMN IF EXISTS next_action_at;
COMMIT;
