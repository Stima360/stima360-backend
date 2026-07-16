BEGIN;

ALTER TABLE buy_requests
    ADD COLUMN IF NOT EXISTS next_action_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS next_action_note TEXT,
    ADD COLUMN IF NOT EXISTS finance_review_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS finance_notes TEXT;

CREATE TABLE buy_request_interactions (
    id BIGSERIAL PRIMARY KEY,
    buy_request_id BIGINT NOT NULL REFERENCES buy_requests(id) ON DELETE CASCADE,
    match_id BIGINT REFERENCES matches(id) ON DELETE SET NULL,
    property_id BIGINT REFERENCES properties(id) ON DELETE SET NULL,
    property_visit_id BIGINT REFERENCES property_visits(id) ON DELETE SET NULL,
    interaction_type VARCHAR(30) NOT NULL CHECK (interaction_type IN (
        'proposed','discarded','interested','visit_requested','visit_scheduled','visited','offer_candidate','other'
    )),
    reason_code VARCHAR(50),
    notes TEXT,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by VARCHAR(200),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (match_id IS NOT NULL OR property_id IS NOT NULL)
);

CREATE TABLE buy_request_task_links (
    id BIGSERIAL PRIMARY KEY,
    buy_request_id BIGINT NOT NULL REFERENCES buy_requests(id) ON DELETE CASCADE,
    task_id BIGINT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (buy_request_id, task_id)
);

CREATE TABLE buy_request_history (
    id BIGSERIAL PRIMARY KEY,
    buy_request_id BIGINT NOT NULL REFERENCES buy_requests(id) ON DELETE CASCADE,
    event_type VARCHAR(40) NOT NULL CHECK (event_type IN (
        'request_created','request_updated','status_changed','finance_updated','next_action_updated',
        'match_proposed','match_discarded','match_interested','visit_requested','visit_scheduled','visited',
        'offer_candidate','task_created','task_linked','task_unlinked','note'
    )),
    match_id BIGINT REFERENCES matches(id) ON DELETE SET NULL,
    property_id BIGINT REFERENCES properties(id) ON DELETE SET NULL,
    task_id BIGINT REFERENCES tasks(id) ON DELETE SET NULL,
    reason_code VARCHAR(50),
    description TEXT,
    old_value JSONB,
    new_value JSONB,
    created_by VARCHAR(200),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_buy_requests_next_action ON buy_requests(next_action_at) WHERE archived_at IS NULL;
CREATE INDEX idx_buy_interactions_request ON buy_request_interactions(buy_request_id, occurred_at DESC);
CREATE INDEX idx_buy_interactions_match ON buy_request_interactions(match_id);
CREATE INDEX idx_buy_interactions_property ON buy_request_interactions(property_id);
CREATE INDEX idx_buy_task_links_request ON buy_request_task_links(buy_request_id);
CREATE INDEX idx_buy_history_request ON buy_request_history(buy_request_id, created_at DESC);

COMMIT;
