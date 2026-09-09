-- Down for 055. Restores 054's function bodies verbatim and removes the one
-- trigger 055 added.
--
-- 055 created no columns, constraints or indexes: it replaced three function
-- bodies and added a fourth function with its trigger. So the down is the
-- exact inverse - put the three bodies back as 054 wrote them, and drop the
-- action-record guard.
--
-- The three bodies below are copied from 054 and must stay byte-identical to
-- it. They are reproduced here rather than referenced because a down file
-- cannot re-run another migration, and because the point of the file is to
-- leave the database in exactly the state 054 left it in.
--
-- Nothing from 052, 053 or 054 is dropped: the columns, the NOT NULL, the
-- three triggers and their names all survive this.
--
-- Down files bracket themselves; the runner owns no transaction here.

BEGIN;

-- --- 054's flow_events body -------------------------------------------------
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

-- --- 054's flow_executions body ---------------------------------------------
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

-- --- 054's flow_suppressions body -------------------------------------------
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

-- --- the guard 055 added ----------------------------------------------------
DROP TRIGGER IF EXISTS trg_flow_action_record_agency_integrity ON flow_action_records;
DROP FUNCTION IF EXISTS flow_action_record_agency_integrity();

DELETE FROM schema_migrations WHERE version = '055_p26_flow_agency_immutability';

COMMIT;
