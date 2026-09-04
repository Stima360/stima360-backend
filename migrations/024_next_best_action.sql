BEGIN;

-- P23 Next Best Action - materialized single-winner table.
--
-- One row per (subject_type, subject_id): the UNIQUE constraint below is
-- the DB-level enforcement of the P23 core requirement ("una sola azione
-- primaria per opportunita"), not just an application-layer convention.
-- next_best_action/service.py fully replaces the set on every refresh
-- (UPSERT the current winners, DELETE anything no longer winning) - there
-- is deliberately no status/history column: this table is a materialized
-- "current answer" cache over signals that already live in
-- seller_intent, followup, property_watch (P22 invisible sale), match and
-- flow (P17-P22), never a second source of truth for any of them.
--
-- contact_id/lead_id/stima_id are nullable with ON DELETE SET NULL, same
-- rationale as migrations/017_seller_intelligence_01.sql and
-- migrations/020_followup_actions.sql: this table is a derived read model,
-- not a live operational record other modules depend on, so losing a
-- reference (but not the row) on a source-table delete is acceptable.
CREATE TABLE IF NOT EXISTS next_best_actions (
    id BIGSERIAL PRIMARY KEY,
    subject_type VARCHAR(30) NOT NULL,
    subject_id BIGINT NOT NULL,
    contact_id BIGINT REFERENCES contacts(id) ON DELETE SET NULL,
    lead_id BIGINT REFERENCES leads(id) ON DELETE SET NULL,
    stima_id INTEGER REFERENCES stime(id) ON DELETE SET NULL,
    action_type VARCHAR(50) NOT NULL,
    priority VARCHAR(20) NOT NULL,
    reason VARCHAR(300) NOT NULL,
    source_signal VARCHAR(50) NOT NULL,
    cta_route VARCHAR(50),
    cta_params JSONB NOT NULL DEFAULT '[]'::jsonb,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT next_best_actions_subject_unq UNIQUE (subject_type, subject_id)
);

CREATE INDEX IF NOT EXISTS idx_next_best_actions_priority
    ON next_best_actions (priority, generated_at DESC, subject_id ASC);

COMMIT;
