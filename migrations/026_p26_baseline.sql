-- P26-0 baseline and migration ledger foundation.
--
-- Additive, transactional, idempotent. This migration introduces schema
-- infrastructure only. It touches no application table, writes no application
-- row, and registers nothing for migrations 001-025.
--
-- Migrations 001-025 are deliberately NOT recorded in schema_migrations. Their
-- historical application state was never demonstrable (no ledger ever existed,
-- and 010/011 versus 014/015 are mutually exclusive per environment), so
-- recording them would convert an unverified assumption into a system fact.
-- Their absence is the honest representation and is enforced structurally by
-- the schema_migrations_no_pre_baseline constraint below.
--
-- The baseline row requires certified values supplied by the runner through
-- run-time settings. current_setting() without a fallback raises when a
-- setting is absent, so this migration cannot be applied with a placeholder or
-- an invented fingerprint.

BEGIN;

-- ---------------------------------------------------------------------------
-- Forward-only migration ledger, starting at version 026.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_migrations (
    version                 TEXT        PRIMARY KEY,
    filename                TEXT        NOT NULL,
    checksum_up             CHAR(64)    NOT NULL,
    checksum_down           CHAR(64),
    down_available          BOOLEAN     NOT NULL DEFAULT FALSE,
    transactional           BOOLEAN     NOT NULL DEFAULT TRUE,
    applied_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    applied_by_db_user      TEXT        NOT NULL DEFAULT CURRENT_USER,
    applied_by_operator     TEXT        NOT NULL,
    database_name           TEXT        NOT NULL DEFAULT current_database(),
    execution_ms            INTEGER,
    rolled_back_at          TIMESTAMPTZ,
    rolled_back_by_operator TEXT,
    notes                   TEXT,

    -- Structural gate: versions below 026 can never be registered.
    -- Expressed purely as a regular expression rather than a substring cast,
    -- because PostgreSQL does not guarantee evaluation order between CHECK
    -- constraints and a cast on a malformed version would raise instead of
    -- rejecting cleanly.
    --   026-029 -> 0 2[6-9]
    --   030-099 -> 0 [3-9][0-9]
    --   100-999 -> [1-9][0-9][0-9]
    CONSTRAINT schema_migrations_no_pre_baseline CHECK (
        version ~ '^(0(2[6-9]|[3-9][0-9])|[1-9][0-9]{2})_[a-z0-9_]+$'
    ),

    CONSTRAINT schema_migrations_operator_present CHECK (
        length(btrim(applied_by_operator)) > 0
    ),

    CONSTRAINT schema_migrations_down_consistency CHECK (
        (down_available AND checksum_down IS NOT NULL)
        OR (NOT down_available AND checksum_down IS NULL)
    ),

    CONSTRAINT schema_migrations_rollback_consistency CHECK (
        (rolled_back_at IS NULL AND rolled_back_by_operator IS NULL)
        OR (rolled_back_at IS NOT NULL
            AND rolled_back_by_operator IS NOT NULL
            AND length(btrim(rolled_back_by_operator)) > 0)
    )
);

CREATE INDEX IF NOT EXISTS idx_schema_migrations_applied_at
    ON schema_migrations (applied_at);

-- ---------------------------------------------------------------------------
-- Baseline certificate. Exactly one row per database.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_baseline (
    id                    SMALLINT    PRIMARY KEY DEFAULT 1,
    baseline_version      TEXT        NOT NULL,
    schema_fingerprint    CHAR(64)    NOT NULL,
    snapshot_artifact     TEXT        NOT NULL,
    certified_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    certified_by_operator TEXT        NOT NULL,
    database_name         TEXT        NOT NULL DEFAULT current_database(),
    pre_baseline_tracked  BOOLEAN     NOT NULL DEFAULT FALSE,
    notes                 TEXT        NOT NULL,

    CONSTRAINT schema_baseline_singleton CHECK (id = 1),

    -- The baseline can never claim to have tracked the pre-baseline history.
    CONSTRAINT schema_baseline_not_retroactive CHECK (
        pre_baseline_tracked = FALSE
    ),

    -- A placeholder or invented fingerprint cannot satisfy this.
    CONSTRAINT schema_baseline_fingerprint_shape CHECK (
        schema_fingerprint ~ '^[0-9a-f]{64}$'
    ),

    CONSTRAINT schema_baseline_operator_present CHECK (
        length(btrim(certified_by_operator)) > 0
    ),

    CONSTRAINT schema_baseline_artifact_present CHECK (
        length(btrim(snapshot_artifact)) > 0
    )
);

-- ---------------------------------------------------------------------------
-- Cross-environment guard.
--
-- database_name cannot be enforced with a CHECK constraint because
-- current_database() is not immutable. A trigger is the correct mechanism: a
-- TEST dump restored onto production, or the reverse, becomes immediately
-- detectable instead of silently authoritative.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION schema_ledger_guard() RETURNS trigger AS $fn$
BEGIN
    IF NEW.database_name <> current_database() THEN
        RAISE EXCEPTION
            'P26 ledger guard: row declares database % but the session is on %',
            NEW.database_name, current_database();
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

-- PostgreSQL has no CREATE TRIGGER IF NOT EXISTS. The catalogue is consulted
-- directly instead. tgisinternal is excluded so constraint-backed internal
-- triggers cannot produce a false positive.
DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE t.tgname = 'trg_schema_migrations_guard'
          AND c.relname = 'schema_migrations'
          AND n.nspname = 'public'
          AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_schema_migrations_guard
            BEFORE INSERT OR UPDATE ON schema_migrations
            FOR EACH ROW EXECUTE FUNCTION schema_ledger_guard();
    END IF;
END
$do$;

DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE t.tgname = 'trg_schema_baseline_guard'
          AND c.relname = 'schema_baseline'
          AND n.nspname = 'public'
          AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_schema_baseline_guard
            BEFORE INSERT OR UPDATE ON schema_baseline
            FOR EACH ROW EXECUTE FUNCTION schema_ledger_guard();
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- The baseline row.
--
-- Values come from run-time settings supplied by the runner. current_setting()
-- is called without its missing_ok argument on purpose: an unset value raises
-- and aborts the whole transaction, so this migration cannot be applied
-- without a real, certified fingerprint.
--
-- The WHERE NOT EXISTS clause keeps re-execution a no-op.
-- ---------------------------------------------------------------------------
INSERT INTO schema_baseline (
    id,
    baseline_version,
    schema_fingerprint,
    snapshot_artifact,
    certified_by_operator,
    pre_baseline_tracked,
    notes
)
SELECT
    1,
    current_setting('p26.baseline_version'),
    current_setting('p26.schema_fingerprint'),
    current_setting('p26.snapshot_artifact'),
    current_setting('p26.certified_by_operator'),
    FALSE,
    'Migrations 001-025 are pre-baseline and deliberately untracked: no ledger '
    'existed while they were applied, and 010/011 versus 014/015 are mutually '
    'exclusive per environment, so their application state was never '
    'demonstrable. The forward-only ledger starts at 026.'
WHERE NOT EXISTS (SELECT 1 FROM schema_baseline);

COMMIT;
