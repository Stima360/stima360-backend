-- P27-1 platform audit foundation.
--
-- Additive, idempotent, and the first migration of P27. It creates ONE table
-- and the guard that makes it append-only. It alters no existing table, adds
-- no column to agencies, operator_users or agency_memberships, and seeds no
-- row: there is no such thing as a default administrative act.
--
-- Transaction ownership: the runner owns the UP transaction (version >= 027),
-- so this file carries no BEGIN/COMMIT. See
-- docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.
--
-- WHAT THIS TABLE IS
--
-- The record of what was done to the NETWORK, by whom, and with what outcome.
-- It is not an application log and not an activity feed: nothing in the
-- product reads it to decide anything. It exists to be read later, by a person
-- asking what happened.
--
-- It is deliberately separate from owner_audit_log. That table records what an
-- owner did inside one agency; this one records what the platform did to the
-- agencies. Merging them would mean one table whose rows belong to two
-- different tenancy models, and every future query would have to remember
-- which kind it was looking at.
--
-- DECISION D2: THE WRITER COMMITS ON ITS OWN CONNECTION
--
-- Nothing in this file enforces that - a transaction boundary is not a schema
-- object - but the shape of the table follows from it, so it is recorded here
-- too. `platform_admin/database.py` opens its own connection and commits the
-- audit row independently of the operation it describes. A row therefore
-- survives the failure or rollback of that operation, which is the whole point:
-- the register describes the administrative ATTEMPT.
--
-- That is why `result` exists and why it has three values rather than a
-- boolean. `denied` and `error` are not error handling; they are the rows the
-- register exists to keep.
--
-- DECISION D3: APPEND-ONLY IN THE DATABASE, NOT BY CONVENTION
--
-- A register that the administrator of the platform can rewrite proves very
-- little about what the administrator of the platform did. The trigger below
-- refuses UPDATE and DELETE outright, following the precedent migration 055
-- set for FLOW: a guard in the database, verified by reading the catalogue
-- back at the end of the same migration.
--
-- Corrections are made by writing a NEW, compensating row. The history is
-- never edited.
--
-- WHY TRUNCATE IS COVERED TOO
--
-- TRUNCATE is neither UPDATE nor DELETE, it fires no row-level trigger, and it
-- empties a table in one statement. A guard against DELETE that leaves
-- TRUNCATE open is a guard against the slow way of doing the same thing. It
-- needs a STATEMENT-level trigger, which is why there are two.
--
-- WHAT A SUPERUSER CAN STILL DO
--
-- Drop the trigger and then the rows. This is honest and worth stating: the
-- guard defends the register against the application, against a mistaken
-- script and against an ordinary role, not against someone who owns the
-- database. Defending against that is backup and restore, which P26 already
-- certified (docs/P26_BACKUP_RESTORE_TEST.md).

-- ---------------------------------------------------------------------------
-- 1. The table.
--
-- THERE ARE NO FOREIGN KEYS ON THIS TABLE, AND THAT IS THE DESIGN.
--
-- `actor_user_id` and `target_agency_id` are plain BIGINTs. They hold the id
-- an entity had AT THE TIME OF THE ACT - a historical snapshot - and not a
-- live reference to a row that must still exist.
--
-- This took two wrong turns to reach, and both are recorded because each looks
-- right until it meets the append-only rule.
--
--   1. ON DELETE SET NULL. It reads as the kind thing to do: the register
--      survives, it merely forgets who. It cannot work here. SET NULL is
--      IMPLEMENTED as an UPDATE on this table, and this table refuses UPDATE.
--      Applying the migration to a real PostgreSQL and deleting an operator
--      produced:
--
--          ERROR: platform_audit_log is append-only: UPDATE is refused (id=8)
--          CONTEXT: SQL statement "UPDATE ONLY public.platform_audit_log
--                   SET actor_user_id = NULL WHERE ..."
--
--      The DELETE failed entirely, and the message named the wrong rule.
--
--   2. ON DELETE RESTRICT. It removes the contradiction by removing the
--      UPDATE - but it does so by making the register VETO the deletion of
--      anything it has ever mentioned. An operator who once opened
--      `/api/platform/me` could never be deleted again, and the only way back
--      would be to delete the audit row first, which the same guard forbids.
--      A rule with no exit is not a rule, it is a trap, and it was still sitting
--      in the risk register as "P27-9 must delete the audit rows first" -
--      advice that is impossible to follow by construction.
--
-- The third answer is the one that was there all along: an immutable register
-- must not hold a referential claim over the entities it describes. It records
-- what was true when the act happened. It does not modify itself, it does not
-- delete itself, and it does not stop a parent from being deleted - the three
-- properties together, with no exception, no trigger bypass and no special
-- cleanup path.
--
-- WHAT IS LOST, AND WHY IT COSTS NOTHING HERE
--
-- A join to `operator_users` can now return nothing, and a serial could in
-- principle be reissued to a different person. That is the usual objection to
-- an audit table without foreign keys, and the usual answer applies: the
-- authoritative record of WHO is `actor_label`, frozen as text at write time
-- ('operator:<id>', the format operator_auth/dependencies.audit_actor has
-- produced since P26-5) and NOT NULL on every row. The integer is for joining
-- while the parent lives; the text is the record. A reader who finds no parent
-- has learned something true - that account is gone - rather than losing the
-- row.
--
-- This is also why `target_id` is TEXT and duplicated alongside
-- `target_agency_id`: the same snapshot discipline, applied to every kind of
-- target rather than only to agencies.
--
-- The label carries the operator id and never the email. /me deliberately
-- excludes the email, and an audit trail is not the place to reintroduce a
-- personal datum.
--
-- target_id is TEXT, not BIGINT. It must stay readable after the row it named
-- has gone, and across target types whose keys are not all integers. Same
-- choice as owner_audit_log.entity_id.
--
-- target_agency_id is a separate column rather than a reading of target_id, so
-- "everything ever done to agency X" is one indexed query no matter what kind
-- of object each row targeted. Like the actor id, it is a snapshot: it carries
-- no foreign key and never blocks the agency's own lifecycle.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS platform_audit_log (
    id               BIGSERIAL    PRIMARY KEY,
    actor_user_id    BIGINT,
    actor_label      VARCHAR(120) NOT NULL,
    action           VARCHAR(80)  NOT NULL,
    result           VARCHAR(20)  NOT NULL DEFAULT 'success',
    target_type      VARCHAR(80),
    target_id        VARCHAR(100),
    target_agency_id BIGINT,
    metadata         JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT platform_audit_log_actor_chk CHECK (BTRIM(actor_label) <> ''),

    -- The namespace is enforced, not merely conventional. A row whose action
    -- is 'login' or 'update' tells a later reader nothing about which surface
    -- produced it; 'platform.' says it, in the row itself, forever.
    CONSTRAINT platform_audit_log_action_chk
        CHECK (BTRIM(action) <> '' AND action LIKE 'platform.%'),

    -- The three outcomes, kept identical to platform_admin/enums.AUDIT_RESULTS.
    -- tests/test_p27_1_migration_057.py compares the two lists, so a fourth
    -- value added on one side fails rather than being silently rejected by the
    -- database at the worst possible moment.
    CONSTRAINT platform_audit_log_result_chk
        CHECK (result IN ('success', 'denied', 'error'))
);

-- ---------------------------------------------------------------------------
-- 2. Indexes.
--
-- Three read paths, and no more: "what happened lately", "what happened to
-- this agency", "what did this operator do". Each is the whole reason someone
-- opens this table. An index on `action` is deliberately absent - it has very
-- few distinct values, so it would cost writes and save nothing.
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_platform_audit_created
    ON platform_audit_log (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_platform_audit_target_agency
    ON platform_audit_log (target_agency_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_platform_audit_actor
    ON platform_audit_log (actor_user_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- 3. Append-only: the row guard.
--
-- CREATE OR REPLACE rather than CREATE, so re-running this migration replaces
-- the body instead of failing on an existing name - the same idempotence the
-- CREATE TABLE IF NOT EXISTS above has.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION platform_audit_log_append_only()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $fn$
BEGIN
    RAISE EXCEPTION
        'platform_audit_log is append-only: % is refused (id=%). Record a correction as a new row.',
        TG_OP, OLD.id;
END
$fn$;

DROP TRIGGER IF EXISTS trg_platform_audit_log_append_only ON platform_audit_log;
CREATE TRIGGER trg_platform_audit_log_append_only
    BEFORE UPDATE OR DELETE ON platform_audit_log
    FOR EACH ROW EXECUTE FUNCTION platform_audit_log_append_only();

-- ---------------------------------------------------------------------------
-- 4. Append-only: the statement guard.
--
-- TRUNCATE fires no row-level trigger, so the guard above never sees it. This
-- one is STATEMENT level and has no OLD row to name.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION platform_audit_log_no_truncate()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $fn$
BEGIN
    RAISE EXCEPTION
        'platform_audit_log is append-only: TRUNCATE is refused.';
END
$fn$;

DROP TRIGGER IF EXISTS trg_platform_audit_log_no_truncate ON platform_audit_log;
CREATE TRIGGER trg_platform_audit_log_no_truncate
    BEFORE TRUNCATE ON platform_audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION platform_audit_log_no_truncate();

-- ---------------------------------------------------------------------------
-- 5. Proof, read back from the catalogue.
--
-- The same discipline migration 055 established: the migration does not assume
-- its own statements took effect, it asks the catalogue.
--
-- pg_trigger.tgtype bits, from PostgreSQL's pg_trigger.h:
--     ROW = 1, BEFORE = 2, INSERT = 4, DELETE = 8, UPDATE = 16,
--     TRUNCATE = 32, INSTEAD = 64
-- BEFORE is a set bit; AFTER is the absence of both BEFORE and INSTEAD.
--
-- Each trigger is checked for its table, its function, its enablement and the
-- operations it covers. Matching on tgname alone would accept a trigger of the
-- right name calling some other function, or one left disabled by
-- ALTER TABLE ... DISABLE TRIGGER - a guard that exists and does nothing.
--
-- INSERT is asserted ABSENT on both. A guard that also fired on INSERT would
-- make the table not append-only but unwritable, and the first write would be
-- the one to discover it.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_row       text[];
    v_tgtype    smallint;
    v_tgenabled "char";
    v_function  text;
    v_wanted    smallint;
BEGIN
    -- name, table, function, required operation bits
    -- row guard      : ROW(1) + BEFORE(2) + DELETE(8) + UPDATE(16)  = 27
    -- statement guard: BEFORE(2) + TRUNCATE(32)                     = 34
    FOREACH v_row SLICE 1 IN ARRAY ARRAY[
        ARRAY['trg_platform_audit_log_append_only','platform_audit_log','platform_audit_log_append_only','27'],
        ARRAY['trg_platform_audit_log_no_truncate','platform_audit_log','platform_audit_log_no_truncate','34']
    ]
    LOOP
        v_wanted := v_row[4]::smallint;

        SELECT t.tgtype, t.tgenabled, p.proname
          INTO v_tgtype, v_tgenabled, v_function
          FROM pg_trigger t
          JOIN pg_proc p ON p.oid = t.tgfoid
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE t.tgname  = v_row[1]
           AND t.tgrelid = ('public.' || v_row[2])::regclass
           AND n.nspname = 'public'
           AND NOT t.tgisinternal;

        IF v_tgtype IS NULL THEN
            RAISE EXCEPTION
                'P27-1 057: % is not installed on %, or does not call a function in schema public',
                v_row[1], v_row[2];
        END IF;
        IF v_function <> v_row[3] THEN
            RAISE EXCEPTION
                'P27-1 057: % on % calls %, expected %',
                v_row[1], v_row[2], v_function, v_row[3];
        END IF;
        IF v_tgenabled <> 'O' THEN
            RAISE EXCEPTION
                'P27-1 057: % on % is not enabled (tgenabled=%)',
                v_row[1], v_row[2], v_tgenabled;
        END IF;
        IF (v_tgtype & 2) = 0 OR (v_tgtype & 64) <> 0 THEN
            RAISE EXCEPTION
                'P27-1 057: % must fire BEFORE, not AFTER or INSTEAD OF', v_row[1];
        END IF;
        IF (v_tgtype & v_wanted) <> v_wanted THEN
            RAISE EXCEPTION
                'P27-1 057: % does not cover the required operations (tgtype=%, wanted=%)',
                v_row[1], v_tgtype, v_wanted;
        END IF;
        IF (v_tgtype & 4) <> 0 THEN
            RAISE EXCEPTION
                'P27-1 057: % must not fire on INSERT; the table is append-only, not read-only',
                v_row[1];
        END IF;
    END LOOP;
END
$do$;

-- ---------------------------------------------------------------------------
-- 6. Proof: the table itself, and that it is append-only in both directions.
--
-- An over-broad guard is as bad as a missing one: a trigger that also fired on
-- INSERT would leave a table nothing can write, and the first platform login
-- after deployment would be the thing that discovered it. So the probe below
-- proves BOTH halves - one INSERT is accepted, one UPDATE is refused.
--
-- The probe row is then undone, and the way it is undone matters. DELETE is
-- refused by the guard, correctly. SAVEPOINT is not available: PL/pgSQL cannot
-- execute transaction-control statements. What it does have is the implicit
-- savepoint of a BEGIN ... EXCEPTION block, so the probe raises a private
-- sentinel and catches it one level up: the database work inside is rolled
-- back, and the PL/pgSQL variables - which are memory, not transactional -
-- keep the findings.
--
-- The sentinel is re-raised if it is anything other than the one expected, so
-- a genuine failure inside the probe is never swallowed by the handler that
-- exists to discard the probe row.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_id             BIGINT;
    v_insert_ok      BOOLEAN := FALSE;
    v_update_refused BOOLEAN := FALSE;
    v_sentinel       CONSTANT text := 'P27_1_057_PROBE_ROLLBACK';
BEGIN
    IF to_regclass('public.platform_audit_log') IS NULL THEN
        RAISE EXCEPTION 'P27-1 057: platform_audit_log was not created';
    END IF;

    BEGIN
        INSERT INTO platform_audit_log (actor_label, action, result, metadata)
        VALUES ('anonymous', 'platform.migration.probe', 'success', '{}'::jsonb)
        RETURNING id INTO v_id;

        v_insert_ok := v_id IS NOT NULL;

        BEGIN
            UPDATE platform_audit_log SET result = 'denied' WHERE id = v_id;
        EXCEPTION WHEN OTHERS THEN
            v_update_refused := TRUE;
        END;

        -- Undo the probe row. Not an error: the only way out of this block
        -- that leaves the table as it was found.
        RAISE EXCEPTION USING MESSAGE = v_sentinel;
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM <> v_sentinel THEN
            RAISE;
        END IF;
    END;

    IF NOT v_insert_ok THEN
        RAISE EXCEPTION
            'P27-1 057: platform_audit_log refused a legitimate INSERT; the guard is over-broad';
    END IF;

    IF NOT v_update_refused THEN
        RAISE EXCEPTION
            'P27-1 057: platform_audit_log accepted an UPDATE; the append-only guard is not effective';
    END IF;
END
$do$;
