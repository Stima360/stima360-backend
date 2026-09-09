-- P26-6C FLOW addendum: the tenant of a FLOW row is immutable after INSERT.
--
-- 052/053/054 are applied and are NOT touched by this file. 054 gave the three
-- ROOT-OWNED tables a NOT NULL agency_id and three per-table triggers; this
-- migration replaces the bodies of those same three functions and adds one
-- more for the derived table. Forward-only, as the ledger requires.
--
-- WHAT 054 MISSED
--
-- 054 validates the CHILD side of every link, at the moment the child is
-- written:
--
--     an execution must agree with the event it came from
--     an execution must agree with the execution it retries
--
-- Nothing validated the PARENT side. A BEFORE trigger on `flow_executions`
-- fires when a flow_executions row is written - not when the event it points
-- at is rewritten. So:
--
--     UPDATE flow_events SET agency_id = <other agency> WHERE id = <parent>;
--
-- was accepted, and every execution already attached to that event was left
-- pointing at a parent in a different tenant. The invariant 054 spends three
-- checks establishing at write time could be invalidated afterwards by a
-- single statement that 054 never looked at. A live hostile probe on TEST
-- confirmed it: CHILD_AGENCY_CHANGE_BLOCKED passed with SQLSTATE P0001,
-- PARENT_EVENT_AGENCY_CHANGE_BLOCKED did not.
--
-- The same hole exists one link over: an execution that is the PARENT of a
-- retry, and - through `execution_id` - every `flow_action_records` row, whose
-- tenancy is nothing but a join to its execution. Moving one execution between
-- agencies silently moved all of its action records with it.
--
-- WHY IMMUTABILITY RATHER THAN RE-VALIDATING CHILDREN
--
-- The alternative is a trigger on the parent that re-checks its children on
-- every UPDATE. Three reasons not to:
--
--   1. Nothing in this system transfers a FLOW row between tenants. No route,
--      no service, no repository function writes `agency_id` on these tables
--      after the row exists; the only writer that ever set it is 053's
--      backfill, which moved NULL to a value once. CORE already treats
--      `agency_id` as server-owned (`SERVER_OWNED_COLUMNS`) and refuses a
--      payload that supplies it. This is the same rule, restated where a
--      direct write cannot avoid it.
--
--   2. Re-validation is a scan of the children on every parent UPDATE.
--      Immutability is a comparison of two scalars.
--
--   3. It closes a race that re-validation leaves open. Under READ COMMITTED,
--      a child INSERT reads the parent's agency and a concurrent parent UPDATE
--      can commit between that read and the child's commit - so re-validation
--      would need `SELECT ... FOR SHARE` on the parent to be correct. If the
--      parent's agency can never change, there is nothing for the child to
--      race against, and the existing child-side checks become sufficient
--      rather than merely necessary.
--
-- WHAT STAYS ALLOWED
--
-- Only a genuine tenant change is refused. An UPDATE that writes the same
-- agency back, or does not mention the column at all, passes - which the
-- runtime depends on, because `execute_live` issues
--
--     UPDATE flow_executions SET status='executed', ... WHERE id=%s
--
-- and that rewrites the whole row through this trigger on every live
-- execution.
--
-- WHY `IS DISTINCT FROM` AND NOT `<>`
--
-- Not because `<>` would block a same-agency UPDATE: `a <> a` is false, so
-- that case passes under either operator. The difference is NULL. `<>` yields
-- NULL when either side is NULL, an IF on NULL does not take its branch, and
-- the guard would silently let the row through.
--
-- Today that is unreachable: the NULL check above raises before this line, and
-- 054 made the column NOT NULL, so neither side can be NULL. But `054_down`
-- drops that NOT NULL, and a guard whose correctness depends on a constraint
-- another migration can remove is a guard with a hidden precondition.
-- `IS DISTINCT FROM` is three-valued-logic-safe on its own terms and needs no
-- such assumption.
--
-- Every check 054 established is preserved verbatim: the NULL guards, the
-- event agreement, the retry agreement. This file only adds.
--
-- THE DERIVED TABLE IS DIFFERENT, AND IS NOT MADE IMMUTABLE
--
-- `flow_action_records` has no `agency_id`; its tenant is whatever its
-- `execution_id` resolves to. That column is NOT immutable by design - the
-- recovery path in `execute_live` deliberately re-points an existing action
-- record at the new execution when a previous attempt failed after the record
-- was written:
--
--     UPDATE flow_action_records SET execution_id=%s, ... WHERE id=%s
--
-- Forbidding that would break recovery. So the guard for this table is not
-- immutability but agreement: a re-point may happen, and it must land inside
-- the same agency it came from. An INSERT is unconstrained, because a new row
-- does not move anywhere - it simply acquires the tenant of the execution it
-- names.
--
-- Transaction ownership: the runner owns the UP transaction, so this file
-- carries no BEGIN/COMMIT.

-- ---------------------------------------------------------------------------
-- 0. Preconditions: 054 must be in place, or this file has nothing to replace.
--
-- pg_trigger.tgtype flags, from PostgreSQL's pg_trigger.h:
--
--     ROW = 1, BEFORE = 2, INSERT = 4, DELETE = 8, UPDATE = 16,
--     TRUNCATE = 32, INSTEAD = 64
--
-- BEFORE is a bit that is SET. AFTER is the absence of both BEFORE and
-- INSTEAD - there is no AFTER bit. So `BEFORE INSERT OR UPDATE FOR EACH ROW`
-- is 1|2|4|16 = 23, and the assertion for BEFORE is `& 2 <> 0`, matching what
-- 051 and 054 already do.
--
-- Each trigger is also checked to be attached to the expected table AND to
-- call the expected function, and to be enabled. Matching on `tgname` alone
-- would accept a trigger of the right name pointing at some other function,
-- or one disabled with ALTER TABLE ... DISABLE TRIGGER, which is a guard that
-- exists and does nothing.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_pair       text[];
    v_tgtype     smallint;
    v_tgenabled  "char";
    v_function   text;
BEGIN
    FOREACH v_pair SLICE 1 IN ARRAY ARRAY[
        ARRAY['trg_flow_event_agency_integrity','flow_events','flow_event_agency_integrity'],
        ARRAY['trg_flow_execution_agency_integrity','flow_executions','flow_execution_agency_integrity'],
        ARRAY['trg_flow_suppression_agency_integrity','flow_suppressions','flow_suppression_agency_integrity']
    ]
    LOOP
        SELECT t.tgtype, t.tgenabled, p.proname
          INTO v_tgtype, v_tgenabled, v_function
          FROM pg_trigger t
          JOIN pg_proc p ON p.oid = t.tgfoid
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE t.tgname  = v_pair[1]
           AND t.tgrelid = ('public.' || v_pair[2])::regclass
           AND n.nspname = 'public'
           AND NOT t.tgisinternal;

        IF v_tgtype IS NULL THEN
            RAISE EXCEPTION
                'P26-6C 055: % is missing on %, or does not call a function in schema public; 054 has not been applied',
                v_pair[1], v_pair[2];
        END IF;
        IF v_function <> v_pair[3] THEN
            RAISE EXCEPTION
                'P26-6C 055: % on % calls %, expected %',
                v_pair[1], v_pair[2], v_function, v_pair[3];
        END IF;
        IF v_tgenabled <> 'O' THEN
            RAISE EXCEPTION
                'P26-6C 055: % on % is not enabled (tgenabled=%)',
                v_pair[1], v_pair[2], v_tgenabled;
        END IF;
        IF (v_tgtype & 1) = 0 THEN
            RAISE EXCEPTION 'P26-6C 055: % must be FOR EACH ROW', v_pair[1];
        END IF;
        IF (v_tgtype & 2) = 0 OR (v_tgtype & 64) <> 0 THEN
            RAISE EXCEPTION 'P26-6C 055: % must fire BEFORE, not AFTER or INSTEAD OF', v_pair[1];
        END IF;
        IF (v_tgtype & 4) = 0 OR (v_tgtype & 16) = 0 THEN
            RAISE EXCEPTION
                'P26-6C 055: % must cover both INSERT and UPDATE', v_pair[1];
        END IF;
    END LOOP;
END
$do$;

-- ---------------------------------------------------------------------------
-- 1. flow_events.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION flow_event_agency_integrity()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $fn$
BEGIN
    IF NEW.agency_id IS NULL THEN
        RAISE EXCEPTION 'flow_events.agency_id cannot be NULL (id=%)', NEW.id;
    END IF;

    IF TG_OP = 'UPDATE' AND NEW.agency_id IS DISTINCT FROM OLD.agency_id THEN
        RAISE EXCEPTION
            'flow_events.agency_id is immutable: id=% cannot move from agency % to agency %',
            OLD.id, OLD.agency_id, NEW.agency_id;
    END IF;

    RETURN NEW;
END
$fn$;

-- ---------------------------------------------------------------------------
-- 2. flow_executions.
--
-- The immutability check comes before the link checks so that an UPDATE which
-- changes the tenant is refused for the reason it is actually wrong, with a
-- message that names the guard. The link checks still run afterwards: an
-- UPDATE can leave `agency_id` alone and repoint `event_id` or
-- `retry_of_execution_id` at a foreign row, which is a different attack and
-- was already refused by 054.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION flow_execution_agency_integrity()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_event_agency BIGINT;
    v_retry_agency BIGINT;
BEGIN
    IF NEW.agency_id IS NULL THEN
        RAISE EXCEPTION 'flow_executions.agency_id cannot be NULL (id=%)', NEW.id;
    END IF;

    IF TG_OP = 'UPDATE' AND NEW.agency_id IS DISTINCT FROM OLD.agency_id THEN
        RAISE EXCEPTION
            'flow_executions.agency_id is immutable: id=% cannot move from agency % to agency %',
            OLD.id, OLD.agency_id, NEW.agency_id;
    END IF;

    IF NEW.event_id IS NOT NULL THEN
        SELECT agency_id INTO v_event_agency FROM flow_events WHERE id = NEW.event_id;
        IF v_event_agency IS NULL THEN
            RAISE EXCEPTION
                'flow_executions.event_id=% does not resolve to an event with an agency',
                NEW.event_id;
        END IF;
        IF v_event_agency <> NEW.agency_id THEN
            RAISE EXCEPTION
                'flow_executions.agency_id=% does not match event %''s agency %',
                NEW.agency_id, NEW.event_id, v_event_agency;
        END IF;
    END IF;

    IF NEW.retry_of_execution_id IS NOT NULL THEN
        SELECT agency_id INTO v_retry_agency
          FROM flow_executions WHERE id = NEW.retry_of_execution_id;
        IF v_retry_agency IS NULL THEN
            RAISE EXCEPTION
                'flow_executions.retry_of_execution_id=% does not resolve to an execution with an agency',
                NEW.retry_of_execution_id;
        END IF;
        IF v_retry_agency <> NEW.agency_id THEN
            RAISE EXCEPTION
                'flow_executions.agency_id=% does not match retried execution %''s agency %',
                NEW.agency_id, NEW.retry_of_execution_id, v_retry_agency;
        END IF;
    END IF;

    RETURN NEW;
END
$fn$;

-- ---------------------------------------------------------------------------
-- 3. flow_suppressions.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION flow_suppression_agency_integrity()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $fn$
BEGIN
    IF NEW.agency_id IS NULL THEN
        RAISE EXCEPTION 'flow_suppressions.agency_id cannot be NULL (id=%)', NEW.id;
    END IF;

    IF TG_OP = 'UPDATE' AND NEW.agency_id IS DISTINCT FROM OLD.agency_id THEN
        RAISE EXCEPTION
            'flow_suppressions.agency_id is immutable: id=% cannot move from agency % to agency %',
            OLD.id, OLD.agency_id, NEW.agency_id;
    END IF;

    RETURN NEW;
END
$fn$;

-- ---------------------------------------------------------------------------
-- 4. flow_action_records: a re-point may not cross agencies.
--
-- The one derived table in this slice. It has no column of its own, so there
-- is nothing here to make immutable - and `execution_id` must stay mutable,
-- because recovery re-points it. What must not happen is a re-point that
-- changes which tenant the row belongs to.
--
-- Only UPDATE is checked. An INSERT names its execution for the first time and
-- moves nothing.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION flow_action_record_agency_integrity()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_old_agency BIGINT;
    v_new_agency BIGINT;
BEGIN
    IF NEW.execution_id IS DISTINCT FROM OLD.execution_id THEN
        SELECT agency_id INTO v_old_agency
          FROM flow_executions WHERE id = OLD.execution_id;
        SELECT agency_id INTO v_new_agency
          FROM flow_executions WHERE id = NEW.execution_id;

        IF v_new_agency IS NULL THEN
            RAISE EXCEPTION
                'flow_action_records.execution_id=% does not resolve to an execution with an agency',
                NEW.execution_id;
        END IF;
        IF v_old_agency IS DISTINCT FROM v_new_agency THEN
            RAISE EXCEPTION
                'flow_action_records.execution_id=% would move record % from agency % to agency %',
                NEW.execution_id, OLD.id, v_old_agency, v_new_agency;
        END IF;
    END IF;

    RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS trg_flow_action_record_agency_integrity ON flow_action_records;
CREATE TRIGGER trg_flow_action_record_agency_integrity
    BEFORE UPDATE ON flow_action_records
    FOR EACH ROW EXECUTE FUNCTION flow_action_record_agency_integrity();

-- ---------------------------------------------------------------------------
-- 5. Proof, read back from the catalogue.
--
-- Each function is resolved by schema, name, exact signature (no arguments)
-- and trigger return type - not by `proname` alone, which would match an
-- overload or a same-named function in another schema and let this block
-- certify the wrong object.
--
-- Then every trigger is re-checked for table, function, enablement and the
-- operations it covers, so the four guards this file leaves behind are the
-- four it intended.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_name text;
    v_body text;
BEGIN
    FOREACH v_name IN ARRAY ARRAY[
        'flow_event_agency_integrity',
        'flow_execution_agency_integrity',
        'flow_suppression_agency_integrity',
        'flow_action_record_agency_integrity'
    ]
    LOOP
        SELECT p.prosrc INTO v_body
          FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public'
           AND p.proname = v_name
           AND p.pronargs = 0
           AND p.prorettype = 'pg_catalog.trigger'::regtype;

        IF v_body IS NULL THEN
            RAISE EXCEPTION
                'P26-6C 055: public.%() returning trigger is missing after replacement', v_name;
        END IF;

        IF v_name = 'flow_action_record_agency_integrity' THEN
            IF position('NEW.execution_id IS DISTINCT FROM OLD.execution_id' in v_body) = 0 THEN
                RAISE EXCEPTION 'P26-6C 055: % does not guard a cross-agency repoint', v_name;
            END IF;
        ELSE
            IF position('IS DISTINCT FROM OLD.agency_id' in v_body) = 0 THEN
                RAISE EXCEPTION 'P26-6C 055: % does not enforce agency immutability', v_name;
            END IF;
            IF position('agency_id IS NULL' in v_body) = 0 THEN
                RAISE EXCEPTION 'P26-6C 055: % lost its NULL guard', v_name;
            END IF;
        END IF;
    END LOOP;

    SELECT p.prosrc INTO v_body
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'public' AND p.proname = 'flow_execution_agency_integrity'
       AND p.pronargs = 0 AND p.prorettype = 'pg_catalog.trigger'::regtype;
    IF position('NEW.event_id' in v_body) = 0
       OR position('NEW.retry_of_execution_id' in v_body) = 0 THEN
        RAISE EXCEPTION
            'P26-6C 055: flow_execution_agency_integrity lost one of 054''s link checks';
    END IF;
END
$do$;

DO $do$
DECLARE
    v_row       text[];
    v_tgtype    smallint;
    v_tgenabled "char";
    v_function  text;
    v_wanted    smallint;
BEGIN
    -- name, table, function, required operation bits (INSERT=4, UPDATE=16)
    FOREACH v_row SLICE 1 IN ARRAY ARRAY[
        ARRAY['trg_flow_event_agency_integrity','flow_events','flow_event_agency_integrity','20'],
        ARRAY['trg_flow_execution_agency_integrity','flow_executions','flow_execution_agency_integrity','20'],
        ARRAY['trg_flow_suppression_agency_integrity','flow_suppressions','flow_suppression_agency_integrity','20'],
        ARRAY['trg_flow_action_record_agency_integrity','flow_action_records','flow_action_record_agency_integrity','16']
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
            RAISE EXCEPTION 'P26-6C 055: % is not installed on %', v_row[1], v_row[2];
        END IF;
        IF v_function <> v_row[3] THEN
            RAISE EXCEPTION
                'P26-6C 055: % on % calls %, expected %',
                v_row[1], v_row[2], v_function, v_row[3];
        END IF;
        IF v_tgenabled <> 'O' THEN
            RAISE EXCEPTION
                'P26-6C 055: % on % is not enabled (tgenabled=%)',
                v_row[1], v_row[2], v_tgenabled;
        END IF;
        IF (v_tgtype & 1) = 0 THEN
            RAISE EXCEPTION 'P26-6C 055: % must be FOR EACH ROW', v_row[1];
        END IF;
        IF (v_tgtype & 2) = 0 OR (v_tgtype & 64) <> 0 THEN
            RAISE EXCEPTION 'P26-6C 055: % must fire BEFORE', v_row[1];
        END IF;
        IF (v_tgtype & v_wanted) <> v_wanted THEN
            RAISE EXCEPTION
                'P26-6C 055: % does not cover the required operations (tgtype=%, wanted=%)',
                v_row[1], v_tgtype, v_wanted;
        END IF;
        -- The action-record guard must NOT fire on INSERT: a new row names its
        -- execution for the first time and moves nothing.
        IF v_row[1] = 'trg_flow_action_record_agency_integrity'
           AND (v_tgtype & 4) <> 0 THEN
            RAISE EXCEPTION
                'P26-6C 055: % must not fire on INSERT', v_row[1];
        END IF;
    END LOOP;
END
$do$;
