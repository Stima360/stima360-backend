BEGIN;

-- P17 Seller Intelligence Foundation.
-- Additive, isolated table: no ALTER on any existing table, and no SQL
-- constraint requiring "at least one reference" to be present. The four
-- FKs below use ON DELETE SET NULL; a NOT-NULL-reference constraint would
-- block a future legitimate CORE delete once it nulled out the last
-- remaining reference on a row here. The "at least one reference at
-- creation time" rule is enforced by the application layer only
-- (seller_intelligence.service.record_event), never by the database.
CREATE TABLE IF NOT EXISTS seller_timeline_events (
    id BIGSERIAL PRIMARY KEY,
    contact_id BIGINT REFERENCES contacts(id) ON DELETE SET NULL,
    lead_id BIGINT REFERENCES leads(id) ON DELETE SET NULL,
    stima_id INTEGER REFERENCES stime(id) ON DELETE SET NULL,
    property_id BIGINT REFERENCES properties(id) ON DELETE SET NULL,
    event_type VARCHAR(50) NOT NULL,
    event_source VARCHAR(30),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key VARCHAR(255),
    created_by VARCHAR(200),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Deterministic dedup for events with a natural identity (e.g. one
-- "stima_richiesta" per stima_id). Partial index: rows with a NULL key
-- (manual/free-form events without natural dedup) are never deduplicated.
CREATE UNIQUE INDEX IF NOT EXISTS idx_seller_timeline_events_idempotency_key
    ON seller_timeline_events (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_seller_timeline_events_contact_id
    ON seller_timeline_events (contact_id);
CREATE INDEX IF NOT EXISTS idx_seller_timeline_events_lead_id
    ON seller_timeline_events (lead_id);
CREATE INDEX IF NOT EXISTS idx_seller_timeline_events_stima_id
    ON seller_timeline_events (stima_id);
CREATE INDEX IF NOT EXISTS idx_seller_timeline_events_property_id
    ON seller_timeline_events (property_id);
CREATE INDEX IF NOT EXISTS idx_seller_timeline_events_occurred_at
    ON seller_timeline_events (occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_seller_timeline_events_event_type
    ON seller_timeline_events (event_type);

COMMIT;
