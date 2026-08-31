BEGIN;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM buy_request_history WHERE event_type = 'sale_completed') THEN
        RAISE EXCEPTION 'cannot run 016 down migration: buy_request_history contains sale_completed events';
    END IF;
END $$;

ALTER TABLE buy_request_history
    DROP CONSTRAINT IF EXISTS buy_request_history_event_type_check;
ALTER TABLE buy_request_history
    ADD CONSTRAINT buy_request_history_event_type_check CHECK (event_type IN (
        'request_created','request_updated','status_changed','finance_updated','next_action_updated',
        'match_proposed','match_discarded','match_interested','visit_requested','visit_scheduled','visited',
        'offer_candidate','task_created','task_linked','task_unlinked','note',
        'proposal_created','proposal_updated','proposal_status_changed'
    ));

DROP TABLE IF EXISTS property_sale_sellers;
DROP TABLE IF EXISTS property_sales;

COMMIT;
