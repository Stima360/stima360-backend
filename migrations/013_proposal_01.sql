BEGIN;

CREATE TABLE property_proposals (
    id BIGSERIAL PRIMARY KEY,
    match_id BIGINT NOT NULL REFERENCES matches(id) ON DELETE RESTRICT,
    amount NUMERIC(14,2) NOT NULL CHECK (amount > 0),
    expires_at TIMESTAMPTZ NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK (status IN (
        'draft','submitted','accepted','rejected','expired','withdrawn'
    )),
    notes TEXT,
    idempotency_key UUID NOT NULL UNIQUE,
    created_by VARCHAR(200) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_property_proposals_match
    ON property_proposals(match_id, created_at DESC);
CREATE INDEX idx_property_proposals_status_expiry
    ON property_proposals(status, expires_at);
CREATE UNIQUE INDEX uq_property_proposals_open_match
    ON property_proposals(match_id)
    WHERE status IN ('draft','submitted');

ALTER TABLE buy_request_history
    DROP CONSTRAINT IF EXISTS buy_request_history_event_type_check;
ALTER TABLE buy_request_history
    ADD CONSTRAINT buy_request_history_event_type_check CHECK (event_type IN (
        'request_created','request_updated','status_changed','finance_updated','next_action_updated',
        'match_proposed','match_discarded','match_interested','visit_requested','visit_scheduled','visited',
        'offer_candidate','task_created','task_linked','task_unlinked','note',
        'proposal_created','proposal_updated','proposal_status_changed'
    ));

COMMIT;
