-- P26-6C FLOW, part 3 of 3: constraints and integrity.
--
--   052  nullable structure       (applied)
--   053  controlled backfill      (applied)
--   054  NOT NULL and integrity   <- this file
--
-- SET NOT NULL on all three is the proof 053 reached every row. It is the
-- reason 052 was forbidden a DEFAULT: with one, every row would look correct
-- and these statements would succeed without proving anything.
--
-- THREE INVARIANTS, AND THEY ARE NOT THE SAME ONE
--
-- 1. An execution and the event it came from belong to one agency.
--    `event_id` is nullable, so this is checked only where it is set - but
--    where it is set the two rows describe a single occurrence, and a
--    disagreement would mean one agency's event produced another's execution.
--
-- 2. An execution and the execution it retries belong to one agency.
--    `retry_of_execution_id` is nullable and ON DELETE SET NULL. A retry that
--    crossed agencies would re-run one tenant's automation under another
--    tenant's name. The scoped retry route refuses this at the HTTP layer;
--    this is the second line, where a direct write cannot avoid it.
--
-- 3. An action record belongs to its execution's agency.
--    `flow_action_records` has no column of its own - it derives, because
--    `execution_id` is NOT NULL ON DELETE CASCADE - so there is nothing to
--    compare and no trigger for it. Its tenancy is its execution's by
--    construction, which is the whole reason 052 did not give it a column.
--
-- ONE FUNCTION PER TABLE, NOT A SHARED MULTI-TABLE ONE
--
-- Migration 040 shipped a bug this project pays attention to: PL/pgSQL
-- resolves a record field when it *prepares* the expression, not only when a
-- preceding conjunct is true, so `IF TG_TABLE_NAME = 'x' AND NEW.<field>`
-- fails on every table that lacks that field. Here `flow_events` has no
-- `event_id`, and `flow_suppressions` has neither `event_id` nor
-- `retry_of_execution_id` - a shared function would be exactly that shape and
-- would fail on two of the three tables it was written for. Three functions,
-- each touching only fields its own table has, and no conjunct to get wrong.
--
-- Transaction ownership: the runner owns the UP transaction, so this file
-- carries no BEGIN/COMMIT.

-- ---------------------------------------------------------------------------
-- 1. NOT NULL on all three.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_table     text;
    v_remaining bigint;
BEGIN
    FOREACH v_table IN ARRAY ARRAY['flow_events','flow_executions','flow_suppressions']
    LOOP
        EXECUTE format('SELECT COUNT(*) FROM %I WHERE agency_id IS NULL', v_table)
           INTO v_remaining;
        IF v_remaining > 0 THEN
            RAISE EXCEPTION
                'P26-6C 054: % row(s) in % still have a NULL agency_id; 053 has not completed',
                v_remaining, v_table;
        END IF;
    END LOOP;
END
$do$;

ALTER TABLE flow_events       ALTER COLUMN agency_id SET NOT NULL;
ALTER TABLE flow_executions   ALTER COLUMN agency_id SET NOT NULL;
ALTER TABLE flow_suppressions ALTER COLUMN agency_id SET NOT NULL;

DO $do$
DECLARE
    v_table text;
    v_null  text;
BEGIN
    FOREACH v_table IN ARRAY ARRAY['flow_events','flow_executions','flow_suppressions']
    LOOP
        SELECT is_nullable INTO v_null
          FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = v_table
           AND column_name = 'agency_id';
        IF v_null <> 'NO' THEN
            RAISE EXCEPTION 'P26-6C 054: %.agency_id is still nullable after SET NOT NULL', v_table;
        END IF;
    END LOOP;
END
$do$;

-- ---------------------------------------------------------------------------
-- 2. flow_events.
--
-- Its entity reference is polymorphic with no foreign key and its rule is the
-- platform-global registry, so there is nothing here to check the agency
-- against. The column is the only record of ownership, which is why 052 gave
-- it one, and requiring it is all this trigger can honestly assert.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION flow_event_agency_integrity()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $fn$
BEGIN
    IF NEW.agency_id IS NULL THEN
        RAISE EXCEPTION 'flow_events.agency_id cannot be NULL (id=%)', NEW.id;
    END IF;
    RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS trg_flow_event_agency_integrity ON flow_events;
CREATE TRIGGER trg_flow_event_agency_integrity
    BEFORE INSERT OR UPDATE ON flow_events
    FOR EACH ROW EXECUTE FUNCTION flow_event_agency_integrity();

-- ---------------------------------------------------------------------------
-- 3. flow_executions: the two optional links must not cross agencies.
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

DROP TRIGGER IF EXISTS trg_flow_execution_agency_integrity ON flow_executions;
CREATE TRIGGER trg_flow_execution_agency_integrity
    BEFORE INSERT OR UPDATE ON flow_executions
    FOR EACH ROW EXECUTE FUNCTION flow_execution_agency_integrity();

-- ---------------------------------------------------------------------------
-- 4. flow_suppressions.
--
-- Same shape as flow_events: `rule_id` points at the platform-global registry,
-- which carries no tenant, and the entity reference is polymorphic. Requiring
-- the column is the assertion.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION flow_suppression_agency_integrity()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $fn$
BEGIN
    IF NEW.agency_id IS NULL THEN
        RAISE EXCEPTION 'flow_suppressions.agency_id cannot be NULL (id=%)', NEW.id;
    END IF;
    RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS trg_flow_suppression_agency_integrity ON flow_suppressions;
CREATE TRIGGER trg_flow_suppression_agency_integrity
    BEFORE INSERT OR UPDATE ON flow_suppressions
    FOR EACH ROW EXECUTE FUNCTION flow_suppression_agency_integrity();

-- ---------------------------------------------------------------------------
-- 5. Proof all three triggers are installed and armed for both events.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_pair   text[];
    v_tgtype smallint;
BEGIN
    FOREACH v_pair SLICE 1 IN ARRAY ARRAY[
        ARRAY['trg_flow_event_agency_integrity','flow_events'],
        ARRAY['trg_flow_execution_agency_integrity','flow_executions'],
        ARRAY['trg_flow_suppression_agency_integrity','flow_suppressions']
    ]
    LOOP
        SELECT tgtype INTO v_tgtype
          FROM pg_trigger
         WHERE tgname  = v_pair[1]
           AND tgrelid = ('public.' || v_pair[2])::regclass
           AND NOT tgisinternal;

        IF v_tgtype IS NULL THEN
            RAISE EXCEPTION 'P26-6C 054: % is not installed on %', v_pair[1], v_pair[2];
        END IF;
        -- pg_trigger.tgtype flags: ROW=1, BEFORE=2, INSERT=4, UPDATE=16
        IF (v_tgtype & 1) = 0 THEN
            RAISE EXCEPTION 'P26-6C 054: % must be FOR EACH ROW', v_pair[1];
        END IF;
        IF (v_tgtype & 2) = 0 THEN
            RAISE EXCEPTION 'P26-6C 054: % must fire BEFORE, not AFTER', v_pair[1];
        END IF;
        IF (v_tgtype & 4) = 0 OR (v_tgtype & 16) = 0 THEN
            RAISE EXCEPTION 'P26-6C 054: % must cover both INSERT and UPDATE', v_pair[1];
        END IF;
    END LOOP;
END
$do$;
