BEGIN;

-- P18-B Automated Follow-up Engine - foundation only.
-- Additive, isolated table: no ALTER on any existing table. This is the
-- DB-level idempotency gate for the follow-up engine (mirrors the role of
-- flow_action_records.idempotency_key for the existing FLOW module, at a
-- smaller scale): one row per rule execution attempt, keyed by a
-- deterministic idempotency_key, so the same trigger can never create two
-- CORE tasks even under a retried/duplicated call.
--
-- All four references are nullable with ON DELETE SET NULL, same rationale
-- as migrations/017_seller_intelligence_01.sql: a NOT-NULL-reference
-- constraint would block a future legitimate CORE delete once it nulled out
-- the last remaining reference on a row here. This table is a log of what
-- the follow-up engine did, not a live operational record CORE depends on,
-- so losing a reference (but not the row) on a CORE delete is acceptable
-- and expected.
--
-- No status enum via SQL CHECK on purpose: 'pending'/'completed'/'failed'
-- are the only values the application writes today, but a CHECK here would
-- require a migration every time a new status is needed later (e.g. a
-- future 'skipped' or 'superseded' status for P18-D/P23). The application
-- layer (followup/service.py, followup/repository.py) is the single writer
-- and enforces the vocabulary in Python, exactly like P17 does for
-- seller_timeline_events.event_type.
-- idempotency_key carries a plain UNIQUE (not a partial index): it is
-- NOT NULL on every row here (unlike seller_timeline_events.idempotency_key,
-- which tolerates NULL for manual/free-form events) - every follow-up
-- action attempt has a deterministic key by construction (see
-- followup/service.py::run_followup), so a straightforward UNIQUE gives a
-- real DB-level guarantee with no extra WHERE clause needed. Declared
-- inline as a column constraint (not a separate ALTER TABLE), since this
-- migration only ever creates this one new table.
CREATE TABLE IF NOT EXISTS followup_actions (
    id BIGSERIAL PRIMARY KEY,
    rule_code VARCHAR(50) NOT NULL,
    trigger_type VARCHAR(20) NOT NULL,
    contact_id BIGINT REFERENCES contacts(id) ON DELETE SET NULL,
    lead_id BIGINT REFERENCES leads(id) ON DELETE SET NULL,
    stima_id INTEGER REFERENCES stime(id) ON DELETE SET NULL,
    idempotency_key VARCHAR(300) NOT NULL UNIQUE,
    task_id BIGINT REFERENCES tasks(id) ON DELETE SET NULL,
    status VARCHAR(20) NOT NULL,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_followup_actions_rule_code
    ON followup_actions (rule_code);
CREATE INDEX IF NOT EXISTS idx_followup_actions_contact_id
    ON followup_actions (contact_id);
CREATE INDEX IF NOT EXISTS idx_followup_actions_lead_id
    ON followup_actions (lead_id);
CREATE INDEX IF NOT EXISTS idx_followup_actions_stima_id
    ON followup_actions (stima_id);
CREATE INDEX IF NOT EXISTS idx_followup_actions_task_id
    ON followup_actions (task_id);
CREATE INDEX IF NOT EXISTS idx_followup_actions_status
    ON followup_actions (status);
CREATE INDEX IF NOT EXISTS idx_followup_actions_created_at
    ON followup_actions (created_at DESC);

COMMIT;
