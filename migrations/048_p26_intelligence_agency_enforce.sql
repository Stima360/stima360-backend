-- P26-6B INTELLIGENCE, part 3 of 3: structural enforcement.
--
--   046  nullable structure    (applied)
--   047  controlled backfill   (applied)
--   048  NOT NULL + integrity  <- this file
--
-- FOUR FUNCTIONS, ONE PER TABLE
--
-- Migration 040 installed a single trigger function across four tables and
-- wrote a condition mixing the table test with a field only one of them had.
-- PL/pgSQL resolves a record field when it prepares the expression, not only
-- when the preceding conjunct is true, so every write on the other three tables
-- failed with `record "new" has no field ...`. 041 fixed it by nesting.
--
-- The four tables guarded here share almost no columns - a watch has stima_id,
-- an action has a polymorphic subject, a candidate has two parents, an event
-- has an optional one - so one shared function would need exactly the branching
-- that went wrong. Four functions remove the possibility by construction rather
-- than by care: none ever sees a record shape it was not written for.
--
-- The one place branching is unavoidable is *inside* the next_best_actions
-- function, on `subject_type`. That is safe for the opposite reason: it is a
-- single rowtype, so every field the branches read exists on every row.
--
-- WHAT EACH TRIGGER ENFORCES
--
--   property_watches           if stima_id is set, its stima is in the same
--                              agency. NULL is not a disagreement: ON DELETE
--                              SET NULL means the estimation may legitimately
--                              be gone, and the watch keeps the tenancy it was
--                              written with - which is why 046 gave it a column.
--
--   next_best_actions          every non-null reference is in the row's agency,
--                              and the subject, when it resolves, agrees too.
--
--   invisible_sale_candidates  the opportunity's agency (through its watch) and
--                              the buy request's agency must match. Two
--                              mandatory parents that can disagree.
--
--   invisible_sale_events      when candidate_id is set, the candidate must
--                              belong to *exactly* this event's opportunity.
--                              Same-agency is not enough - an event citing a
--                              sibling opportunity's candidate would attribute
--                              one opportunity's decision to another, inside a
--                              single tenant, where no agency comparison would
--                              notice. The exact-parent rule implies tenant
--                              compatibility; the reverse is not true.
--
--   seller_revival_suppressions its optional lead must belong to the same
--                              agency as its mandatory contact.
--
-- BEFORE INSERT OR UPDATE OF the references. The UPDATE branch is not optional:
-- writing a legitimate row and then repointing it is the same breach, one
-- statement later.
--
-- Transaction ownership: the runner owns the UP transaction. Reversible - this
-- file writes no data.

-- ---------------------------------------------------------------------------
-- 1. Prechecks. Refuse on inconsistent history; repair nothing.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_bad BIGINT;
BEGIN
    SELECT count(*) INTO v_bad FROM property_watches WHERE agency_id IS NULL;
    IF v_bad <> 0 THEN
        RAISE EXCEPTION
            'P26-6B 048 refused: % property watch(es) still carry no agency; apply 047 first', v_bad;
    END IF;

    SELECT count(*) INTO v_bad FROM next_best_actions WHERE agency_id IS NULL;
    IF v_bad <> 0 THEN
        RAISE EXCEPTION
            'P26-6B 048 refused: % next best action(s) still carry no agency; apply 047 first', v_bad;
    END IF;

    SELECT count(*) INTO v_bad
      FROM property_watches w
      JOIN stime s ON s.id = w.stima_id
     WHERE s.agency_id <> w.agency_id;
    IF v_bad <> 0 THEN
        RAISE EXCEPTION
            'P26-6B 048 refused: % property watch(es) reference an estimation from another agency', v_bad;
    END IF;

    SELECT count(*) INTO v_bad
      FROM next_best_actions n
      LEFT JOIN contacts c ON c.id = n.contact_id
      LEFT JOIN leads    l ON l.id = n.lead_id
      LEFT JOIN stime    s ON s.id = n.stima_id
     WHERE (c.id IS NOT NULL AND c.agency_id <> n.agency_id)
        OR (l.id IS NOT NULL AND l.agency_id <> n.agency_id)
        OR (s.id IS NOT NULL AND s.agency_id <> n.agency_id);
    IF v_bad <> 0 THEN
        RAISE EXCEPTION
            'P26-6B 048 refused: % next best action(s) reference a row from another agency', v_bad;
    END IF;

    SELECT count(*) INTO v_bad
      FROM invisible_sale_candidates ic
      JOIN invisible_sale_opportunities o ON o.id = ic.opportunity_id
      JOIN property_watches w ON w.id = o.watch_id
      JOIN buy_requests b ON b.id = ic.buy_request_id
     WHERE w.agency_id <> b.agency_id;
    IF v_bad <> 0 THEN
        RAISE EXCEPTION
            'P26-6B 048 refused: % invisible sale candidate(s) join an opportunity and a buy request in different agencies', v_bad;
    END IF;

    SELECT count(*) INTO v_bad
      FROM invisible_sale_events e
      JOIN invisible_sale_candidates ic ON ic.id = e.candidate_id
     WHERE ic.opportunity_id <> e.opportunity_id;
    IF v_bad <> 0 THEN
        RAISE EXCEPTION
            'P26-6B 048 refused: % invisible sale event(s) cite a candidate belonging to a different opportunity', v_bad;
    END IF;

    SELECT count(*) INTO v_bad
      FROM seller_revival_suppressions srs
      JOIN contacts c ON c.id = srs.contact_id
      JOIN leads    l ON l.id = srs.lead_id
     WHERE l.agency_id <> c.agency_id;
    IF v_bad <> 0 THEN
        RAISE EXCEPTION
            'P26-6B 048 refused: % suppression(s) name a lead from a different agency than their contact', v_bad;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- 2. NOT NULL. The proof that 047 reached every row; no DEFAULT is added,
--    because a default would make a future ownerless insert look correct and
--    destroy exactly that proof.
-- ---------------------------------------------------------------------------
ALTER TABLE property_watches
    ALTER COLUMN agency_id SET NOT NULL;

ALTER TABLE next_best_actions
    ALTER COLUMN agency_id SET NOT NULL;

-- ---------------------------------------------------------------------------
-- 3. property_watches integrity.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION property_watch_agency_integrity() RETURNS trigger AS $fn$
DECLARE
    a_stima BIGINT;
BEGIN
    IF NEW.stima_id IS NOT NULL THEN
        SELECT agency_id INTO a_stima FROM stime WHERE id = NEW.stima_id;
        IF a_stima IS NULL OR a_stima <> NEW.agency_id THEN
            RAISE EXCEPTION
                'P26-6B watch integrity: estimation % is not in agency %',
                NEW.stima_id, NEW.agency_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- 4. next_best_actions integrity.
--
-- The subject branching is on subject_type only. This is one table, so every
-- field the branches touch exists on every row - the distinction from 040,
-- where the branching spanned four different rowtypes.
--
-- A subject_type this function does not recognise is not an error here: the
-- three direct references are still checked, and 047 is what refuses a row
-- whose type resolves nothing at all.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION next_best_action_agency_integrity() RETURNS trigger AS $fn$
DECLARE
    a_ref     BIGINT;
    a_subject BIGINT;
BEGIN
    IF NEW.contact_id IS NOT NULL THEN
        SELECT agency_id INTO a_ref FROM contacts WHERE id = NEW.contact_id;
        IF a_ref IS NULL OR a_ref <> NEW.agency_id THEN
            RAISE EXCEPTION
                'P26-6B next best action integrity: contact % is not in agency %',
                NEW.contact_id, NEW.agency_id;
        END IF;
    END IF;

    IF NEW.lead_id IS NOT NULL THEN
        SELECT agency_id INTO a_ref FROM leads WHERE id = NEW.lead_id;
        IF a_ref IS NULL OR a_ref <> NEW.agency_id THEN
            RAISE EXCEPTION
                'P26-6B next best action integrity: lead % is not in agency %',
                NEW.lead_id, NEW.agency_id;
        END IF;
    END IF;

    IF NEW.stima_id IS NOT NULL THEN
        SELECT agency_id INTO a_ref FROM stime WHERE id = NEW.stima_id;
        IF a_ref IS NULL OR a_ref <> NEW.agency_id THEN
            RAISE EXCEPTION
                'P26-6B next best action integrity: estimation % is not in agency %',
                NEW.stima_id, NEW.agency_id;
        END IF;
    END IF;

    IF NEW.subject_type = 'lead' THEN
        SELECT agency_id INTO a_subject FROM leads WHERE id = NEW.subject_id;
    ELSIF NEW.subject_type = 'buy_request' THEN
        SELECT agency_id INTO a_subject FROM buy_requests WHERE id = NEW.subject_id;
    ELSIF NEW.subject_type = 'stima' THEN
        SELECT agency_id INTO a_subject FROM stime WHERE id = NEW.subject_id;
    ELSIF NEW.subject_type = 'match' THEN
        SELECT b.agency_id INTO a_subject
          FROM matches m
          JOIN buy_requests b ON b.id = m.buy_request_id
         WHERE m.id = NEW.subject_id;
    ELSE
        a_subject := NULL;
    END IF;

    IF a_subject IS NOT NULL AND a_subject <> NEW.agency_id THEN
        RAISE EXCEPTION
            'P26-6B next best action integrity: subject %:% is in agency %, not %',
            NEW.subject_type, NEW.subject_id, a_subject, NEW.agency_id;
    END IF;

    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- 5. invisible_sale_candidates integrity.
--
-- Two mandatory parents that can disagree. The opportunity's tenancy is reached
-- through its watch, which 046/047 gave a physical agency.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION invisible_sale_candidate_agency_integrity() RETURNS trigger AS $fn$
DECLARE
    a_opportunity BIGINT;
    a_buy         BIGINT;
BEGIN
    SELECT w.agency_id INTO a_opportunity
      FROM invisible_sale_opportunities o
      JOIN property_watches w ON w.id = o.watch_id
     WHERE o.id = NEW.opportunity_id;
    IF NOT FOUND OR a_opportunity IS NULL THEN
        RAISE EXCEPTION
            'P26-6B invisible sale integrity: opportunity % does not exist or derives no agency',
            NEW.opportunity_id;
    END IF;

    SELECT agency_id INTO a_buy FROM buy_requests WHERE id = NEW.buy_request_id;
    IF a_buy IS NULL THEN
        RAISE EXCEPTION
            'P26-6B invisible sale integrity: buy request % does not exist or carries no agency',
            NEW.buy_request_id;
    END IF;

    IF a_opportunity <> a_buy THEN
        RAISE EXCEPTION
            'P26-6B invisible sale integrity: opportunity % is in agency %, buy request % is in agency %',
            NEW.opportunity_id, a_opportunity, NEW.buy_request_id, a_buy;
    END IF;

    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- 6. invisible_sale_events exact-parent integrity.
--
-- Stricter than tenancy on purpose. See the header.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION invisible_sale_event_parent_integrity() RETURNS trigger AS $fn$
DECLARE
    c_opportunity BIGINT;
BEGIN
    IF NEW.candidate_id IS NOT NULL THEN
        SELECT opportunity_id INTO c_opportunity
          FROM invisible_sale_candidates WHERE id = NEW.candidate_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION
                'P26-6B invisible sale integrity: candidate % does not exist',
                NEW.candidate_id;
        END IF;
        IF c_opportunity <> NEW.opportunity_id THEN
            RAISE EXCEPTION
                'P26-6B invisible sale integrity: candidate % belongs to opportunity %, not %',
                NEW.candidate_id, c_opportunity, NEW.opportunity_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- 7. seller_revival_suppressions integrity.
--
-- A fifth function rather than a branch in one of the others: same reason as
-- above, a different rowtype.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION seller_revival_suppression_agency_integrity() RETURNS trigger AS $fn$
DECLARE
    a_contact BIGINT;
    a_lead    BIGINT;
BEGIN
    SELECT agency_id INTO a_contact FROM contacts WHERE id = NEW.contact_id;
    IF a_contact IS NULL THEN
        RAISE EXCEPTION
            'P26-6B suppression integrity: contact % does not exist or carries no agency',
            NEW.contact_id;
    END IF;

    IF NEW.lead_id IS NOT NULL THEN
        SELECT agency_id INTO a_lead FROM leads WHERE id = NEW.lead_id;
        IF a_lead IS NULL OR a_lead <> a_contact THEN
            RAISE EXCEPTION
                'P26-6B suppression integrity: lead % is not in the same agency as contact %',
                NEW.lead_id, NEW.contact_id;
        END IF;
    END IF;

    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- 8. Triggers.
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
        WHERE t.tgname = 'trg_property_watches_agency_integrity'
          AND c.relname = 'property_watches' AND n.nspname = 'public'
          AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_property_watches_agency_integrity
            BEFORE INSERT OR UPDATE OF agency_id, stima_id
            ON property_watches
            FOR EACH ROW EXECUTE FUNCTION property_watch_agency_integrity();
    END IF;
END
$do$;

DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE t.tgname = 'trg_next_best_actions_agency_integrity'
          AND c.relname = 'next_best_actions' AND n.nspname = 'public'
          AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_next_best_actions_agency_integrity
            BEFORE INSERT OR UPDATE OF agency_id, contact_id, lead_id, stima_id, subject_type, subject_id
            ON next_best_actions
            FOR EACH ROW EXECUTE FUNCTION next_best_action_agency_integrity();
    END IF;
END
$do$;

DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE t.tgname = 'trg_invisible_sale_candidates_agency_integrity'
          AND c.relname = 'invisible_sale_candidates' AND n.nspname = 'public'
          AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_invisible_sale_candidates_agency_integrity
            BEFORE INSERT OR UPDATE OF opportunity_id, buy_request_id
            ON invisible_sale_candidates
            FOR EACH ROW EXECUTE FUNCTION invisible_sale_candidate_agency_integrity();
    END IF;
END
$do$;

DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE t.tgname = 'trg_invisible_sale_events_parent_integrity'
          AND c.relname = 'invisible_sale_events' AND n.nspname = 'public'
          AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_invisible_sale_events_parent_integrity
            BEFORE INSERT OR UPDATE OF opportunity_id, candidate_id
            ON invisible_sale_events
            FOR EACH ROW EXECUTE FUNCTION invisible_sale_event_parent_integrity();
    END IF;
END
$do$;

DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE t.tgname = 'trg_seller_revival_suppressions_agency_integrity'
          AND c.relname = 'seller_revival_suppressions' AND n.nspname = 'public'
          AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_seller_revival_suppressions_agency_integrity
            BEFORE INSERT OR UPDATE OF contact_id, lead_id
            ON seller_revival_suppressions
            FOR EACH ROW EXECUTE FUNCTION seller_revival_suppression_agency_integrity();
    END IF;
END
$do$;
