BEGIN;

-- P24 Database Revival - daily-batch + cooldown tracker.
--
-- Additive, isolated table: no ALTER on any existing table. Same design
-- philosophy as migrations/024_next_best_action.sql - a materialized state
-- table, not an audit/history log: one row per contact, reused (never
-- duplicated) across cooldown cycles via a conditional UPSERT in
-- database_revival/repository.py (ON CONFLICT (contact_id) DO UPDATE ...
-- WHERE seller_revival_suppressions.expires_at <= NOW()).
--
-- created_at doubles as "entered today's revival batch" (compared against
-- CURRENT_DATE) and as the start of the 90-day cooldown; expires_at is
-- created_at + 90 days, computed once by the application at write time.
-- No status/snapshot/idempotency_key column: eligibility is recomputed
-- live on every read (database_revival/eligibility.py), never cached here,
-- and UNIQUE(contact_id) is the real DB-level idempotency guarantee.
--
-- contact_id is NOT NULL ON DELETE CASCADE (the row only means something
-- in relation to a contact; if the contact is deleted, the cooldown is
-- moot). lead_id is nullable ON DELETE SET NULL - same rationale as
-- migrations/017_seller_intelligence_01.sql and
-- migrations/020_followup_actions.sql: this table is a derived read
-- model, not a live operational record other modules depend on.
CREATE TABLE IF NOT EXISTS seller_revival_suppressions (
    id BIGSERIAL PRIMARY KEY,
    contact_id BIGINT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    lead_id BIGINT REFERENCES leads(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT seller_revival_suppressions_contact_unq UNIQUE (contact_id)
);

CREATE INDEX IF NOT EXISTS idx_seller_revival_suppressions_created_at
    ON seller_revival_suppressions (created_at);
CREATE INDEX IF NOT EXISTS idx_seller_revival_suppressions_expires_at
    ON seller_revival_suppressions (expires_at);

COMMIT;
