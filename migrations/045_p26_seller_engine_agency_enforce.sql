-- P26-6A SELLER ENGINE, part 3 of 3: structural enforcement.
--
--   043  nullable structure    (applied)
--   044  controlled backfill   (applied)
--   045  NOT NULL + integrity  <- this file
--
-- Two things happen: the column becomes NOT NULL on both tables, and a trigger
-- on each refuses a row whose references do not agree with its own agency.
--
-- TWO FUNCTIONS, NOT ONE
--
-- Migration 040 installed a single trigger function across four tables and
-- wrote a condition that mixed the table test with a field only one of them had:
--
--     IF TG_TABLE_NAME = 'matches' AND NEW.latest_run_id IS NOT NULL THEN
--
-- PL/pgSQL resolves a record field when it prepares the expression, not only
-- when the preceding conjunct is true, so every INSERT on the other three tables
-- failed. 041 fixed it by nesting.
--
-- Here the two tables differ in their fourth reference - the timeline has
-- property_id, the actions have task_id - so a shared function would need
-- exactly the kind of branching that went wrong. Two functions remove the
-- possibility by construction rather than by care: neither ever sees a record
-- shape it was not written for.
--
-- WHAT EACH TRIGGER ENFORCES
--
-- Every reference that is not NULL must belong to the same agency as the row.
-- A NULL reference is not a disagreement: ON DELETE SET NULL means a parent may
-- legitimately be gone, and the row keeps the tenancy it was written with -
-- which is the whole reason 043 gave these tables a physical column.
--
-- BEFORE INSERT OR UPDATE OF the references and agency_id. The UPDATE branch is
-- not optional: writing a legitimate row and then repointing it at another
-- agency's contact is the same breach, one statement later.
--
-- Transaction ownership: the runner owns the UP transaction, so this file
-- carries no BEGIN/COMMIT. Reversible - it writes no data.

-- ---------------------------------------------------------------------------
-- 1. Prechecks. Refuse on inconsistent history; repair nothing.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_bad BIGINT;
BEGIN
    SELECT count(*) INTO v_bad FROM seller_timeline_events WHERE agency_id IS NULL;
    IF v_bad <> 0 THEN
        RAISE EXCEPTION
            'P26-6A 045 refused: % timeline event(s) still carry no agency; apply 044 first', v_bad;
    END IF;

    SELECT count(*) INTO v_bad FROM followup_actions WHERE agency_id IS NULL;
    IF v_bad <> 0 THEN
        RAISE EXCEPTION
            'P26-6A 045 refused: % follow-up action(s) still carry no agency; apply 044 first', v_bad;
    END IF;

    SELECT count(*) INTO v_bad
      FROM seller_timeline_events e
      LEFT JOIN contacts   c ON c.id = e.contact_id
      LEFT JOIN leads      l ON l.id = e.lead_id
      LEFT JOIN stime      s ON s.id = e.stima_id
      LEFT JOIN properties p ON p.id = e.property_id
     WHERE (c.id IS NOT NULL AND c.agency_id <> e.agency_id)
        OR (l.id IS NOT NULL AND l.agency_id <> e.agency_id)
        OR (s.id IS NOT NULL AND s.agency_id <> e.agency_id)
        OR (p.id IS NOT NULL AND p.agency_id <> e.agency_id);
    IF v_bad <> 0 THEN
        RAISE EXCEPTION
            'P26-6A 045 refused: % timeline event(s) reference a row from another agency', v_bad;
    END IF;

    SELECT count(*) INTO v_bad
      FROM followup_actions a
      LEFT JOIN contacts c ON c.id = a.contact_id
      LEFT JOIN leads    l ON l.id = a.lead_id
      LEFT JOIN stime    s ON s.id = a.stima_id
      LEFT JOIN tasks    t ON t.id = a.task_id
     WHERE (c.id IS NOT NULL AND c.agency_id <> a.agency_id)
        OR (l.id IS NOT NULL AND l.agency_id <> a.agency_id)
        OR (s.id IS NOT NULL AND s.agency_id <> a.agency_id)
        OR (t.id IS NOT NULL AND t.agency_id <> a.agency_id);
    IF v_bad <> 0 THEN
        RAISE EXCEPTION
            'P26-6A 045 refused: % follow-up action(s) reference a row from another agency', v_bad;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- 2. NOT NULL. The proof that 044 reached every row; no DEFAULT is added,
--    because a default would make a future ownerless insert look correct and
--    destroy exactly that proof.
-- ---------------------------------------------------------------------------
ALTER TABLE seller_timeline_events
    ALTER COLUMN agency_id SET NOT NULL;

ALTER TABLE followup_actions
    ALTER COLUMN agency_id SET NOT NULL;

-- ---------------------------------------------------------------------------
-- 3. seller_timeline_events integrity.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION seller_timeline_event_agency_integrity() RETURNS trigger AS $fn$
DECLARE
    a_ref BIGINT;
BEGIN
    IF NEW.contact_id IS NOT NULL THEN
        SELECT agency_id INTO a_ref FROM contacts WHERE id = NEW.contact_id;
        IF a_ref IS NULL OR a_ref <> NEW.agency_id THEN
            RAISE EXCEPTION
                'P26-6A timeline integrity: contact % is not in agency %',
                NEW.contact_id, NEW.agency_id;
        END IF;
    END IF;

    IF NEW.lead_id IS NOT NULL THEN
        SELECT agency_id INTO a_ref FROM leads WHERE id = NEW.lead_id;
        IF a_ref IS NULL OR a_ref <> NEW.agency_id THEN
            RAISE EXCEPTION
                'P26-6A timeline integrity: lead % is not in agency %',
                NEW.lead_id, NEW.agency_id;
        END IF;
    END IF;

    IF NEW.stima_id IS NOT NULL THEN
        SELECT agency_id INTO a_ref FROM stime WHERE id = NEW.stima_id;
        IF a_ref IS NULL OR a_ref <> NEW.agency_id THEN
            RAISE EXCEPTION
                'P26-6A timeline integrity: stima % is not in agency %',
                NEW.stima_id, NEW.agency_id;
        END IF;
    END IF;

    IF NEW.property_id IS NOT NULL THEN
        SELECT agency_id INTO a_ref FROM properties WHERE id = NEW.property_id;
        IF a_ref IS NULL OR a_ref <> NEW.agency_id THEN
            RAISE EXCEPTION
                'P26-6A timeline integrity: property % is not in agency %',
                NEW.property_id, NEW.agency_id;
        END IF;
    END IF;

    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- 4. followup_actions integrity.
--
-- The same shape, with tasks in place of properties. Written out rather than
-- shared: see the header for why one function across both would reintroduce
-- 040's failure mode.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION followup_action_agency_integrity() RETURNS trigger AS $fn$
DECLARE
    a_ref BIGINT;
BEGIN
    IF NEW.contact_id IS NOT NULL THEN
        SELECT agency_id INTO a_ref FROM contacts WHERE id = NEW.contact_id;
        IF a_ref IS NULL OR a_ref <> NEW.agency_id THEN
            RAISE EXCEPTION
                'P26-6A follow-up integrity: contact % is not in agency %',
                NEW.contact_id, NEW.agency_id;
        END IF;
    END IF;

    IF NEW.lead_id IS NOT NULL THEN
        SELECT agency_id INTO a_ref FROM leads WHERE id = NEW.lead_id;
        IF a_ref IS NULL OR a_ref <> NEW.agency_id THEN
            RAISE EXCEPTION
                'P26-6A follow-up integrity: lead % is not in agency %',
                NEW.lead_id, NEW.agency_id;
        END IF;
    END IF;

    IF NEW.stima_id IS NOT NULL THEN
        SELECT agency_id INTO a_ref FROM stime WHERE id = NEW.stima_id;
        IF a_ref IS NULL OR a_ref <> NEW.agency_id THEN
            RAISE EXCEPTION
                'P26-6A follow-up integrity: stima % is not in agency %',
                NEW.stima_id, NEW.agency_id;
        END IF;
    END IF;

    IF NEW.task_id IS NOT NULL THEN
        SELECT agency_id INTO a_ref FROM tasks WHERE id = NEW.task_id;
        IF a_ref IS NULL OR a_ref <> NEW.agency_id THEN
            RAISE EXCEPTION
                'P26-6A follow-up integrity: task % is not in agency %',
                NEW.task_id, NEW.agency_id;
        END IF;
    END IF;

    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- 5. Triggers.
--
-- PostgreSQL has no CREATE TRIGGER IF NOT EXISTS, so the catalogue is consulted
-- directly - the pattern 026 established. tgisinternal is excluded so a
-- constraint-backed internal trigger cannot produce a false positive.
-- ---------------------------------------------------------------------------
DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE t.tgname = 'trg_seller_timeline_events_agency_integrity'
          AND c.relname = 'seller_timeline_events' AND n.nspname = 'public'
          AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_seller_timeline_events_agency_integrity
            BEFORE INSERT OR UPDATE OF agency_id, contact_id, lead_id, stima_id, property_id
            ON seller_timeline_events
            FOR EACH ROW EXECUTE FUNCTION seller_timeline_event_agency_integrity();
    END IF;
END
$do$;

DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE t.tgname = 'trg_followup_actions_agency_integrity'
          AND c.relname = 'followup_actions' AND n.nspname = 'public'
          AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_followup_actions_agency_integrity
            BEFORE INSERT OR UPDATE OF agency_id, contact_id, lead_id, stima_id, task_id
            ON followup_actions
            FOR EACH ROW EXECUTE FUNCTION followup_action_agency_integrity();
    END IF;
END
$do$;
