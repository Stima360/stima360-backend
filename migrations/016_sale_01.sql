BEGIN;

CREATE TABLE property_sales (
    id BIGSERIAL PRIMARY KEY,
    property_id BIGINT NOT NULL REFERENCES properties(id) ON DELETE RESTRICT,
    buy_request_id BIGINT NOT NULL REFERENCES buy_requests(id) ON DELETE RESTRICT,
    proposal_id BIGINT NOT NULL REFERENCES property_proposals(id) ON DELETE RESTRICT,
    sale_price NUMERIC(14,2) NOT NULL CHECK (sale_price > 0),
    status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN (
        'pending','completed','cancelled'
    )),
    notes TEXT,
    idempotency_key UUID NOT NULL UNIQUE,
    created_by VARCHAR(200) NOT NULL,
    completed_by VARCHAR(200),
    cancelled_by VARCHAR(200),
    completed_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX uq_property_sales_proposal_active
    ON property_sales(proposal_id)
    WHERE status IN ('pending','completed');
CREATE UNIQUE INDEX uq_property_sales_property_active
    ON property_sales(property_id)
    WHERE status IN ('pending','completed');
CREATE INDEX idx_property_sales_buy_request
    ON property_sales(buy_request_id);
CREATE INDEX idx_property_sales_status
    ON property_sales(status, created_at DESC);

CREATE TABLE property_sale_sellers (
    id BIGSERIAL PRIMARY KEY,
    sale_id BIGINT NOT NULL REFERENCES property_sales(id) ON DELETE CASCADE,
    contact_id BIGINT NOT NULL REFERENCES contacts(id) ON DELETE RESTRICT,
    role VARCHAR(30) NOT NULL CHECK (role IN ('owner','seller')),
    ownership_share NUMERIC(5,2) CHECK (ownership_share BETWEEN 0 AND 100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(sale_id, contact_id, role)
);

ALTER TABLE buy_request_history
    DROP CONSTRAINT IF EXISTS buy_request_history_event_type_check;
ALTER TABLE buy_request_history
    ADD CONSTRAINT buy_request_history_event_type_check CHECK (event_type IN (
        'request_created','request_updated','status_changed','finance_updated','next_action_updated',
        'match_proposed','match_discarded','match_interested','visit_requested','visit_scheduled','visited',
        'offer_candidate','task_created','task_linked','task_unlinked','note',
        'proposal_created','proposal_updated','proposal_status_changed',
        'sale_completed'
    ));

COMMIT;
