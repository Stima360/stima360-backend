-- P26-2B STIMA root ownership, part 3 of 3: structural enforcement.
--
--   031  nullable column         (applied)
--   032  controlled backfill     (applied)
--   033  NOT NULL + integrity    <- this file
--
-- Three changes, and they belong in one migration because each is only safe
-- once the others hold:
--
--   1. stime.agency_id becomes NOT NULL
--   2. core_agency_integrity() resolves a stima directly from stime.agency_id
--   3. lead_stime gets a trigger refusing a cross-agency link
--
-- WHAT THIS DELETES, AND WHY THAT IS THE POINT
--
-- Migration 030 could not read stime.agency_id: the column did not exist yet.
-- So it resolved a stima's agency the only way then available -
--
--     SELECT l.agency_id FROM lead_stime ls JOIN leads l ON l.id = ls.lead_id
--      WHERE ls.stima_id = NEW.stima_id ORDER BY ls.id LIMIT 1
--
-- - which is a *choice among candidates*. lead_stime's unique key is
-- (lead_id, stima_id), so a stima may carry several links; if two pointed at
-- different agencies, that query returned whichever was inserted first and the
-- trigger accepted it. The row's tenant was decided by insertion order.
--
-- 030 also carried a bounded Default Agency fallback for a stima-only row, for
-- the same reason: with no readable agency on the stima, nothing else could
-- place such a row.
--
-- Both are removed here. A stima now *is* its agency, so
--
--     SELECT agency_id INTO a_stima FROM stime WHERE id = NEW.stima_id;
--
-- is total, exact and unordered. There is nothing left to choose between and
-- nothing left to fall back to. The absences are the deliverable.
--
-- FORWARD-ONLY
--
-- 030 is not edited. This file replaces the function body with CREATE OR
-- REPLACE, and 033_p26_stima_agency_enforce_down.sql restores 030's version
-- verbatim. Rewriting an applied migration would leave every environment that
-- already ran it disagreeing with its own checksum.
--
-- REVERSIBLE
--
-- Unlike 029 and 032, this migration writes no data - it only constrains and
-- redefines. Its down file therefore genuinely reverses it and does not refuse.
--
-- Transaction ownership: this file opens no transaction and commits nothing.
-- scripts/p26_migrate.py executes the body, writes the schema_migrations row
-- through register(), and commits both together.
-- See docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.

-- ---------------------------------------------------------------------------
-- 1. stime.agency_id becomes NOT NULL.
--
-- This is the proof that 032 reached every row. No DEFAULT is added: a default
-- would let a future insert without an agency look correct, which is precisely
-- the proof this constraint exists to provide.
--
-- SET NOT NULL is a no-op when the constraint is already present, so the file
-- stays re-runnable if the runner dies between this body and register().
-- ---------------------------------------------------------------------------
ALTER TABLE stime
    ALTER COLUMN agency_id SET NOT NULL;

-- 1b. Verify the column that is actually there, rather than trusting the
-- statement above to have meant what it said.
DO $do$
DECLARE
    v_null    text;
    v_default text;
BEGIN
    SELECT is_nullable, column_default
      INTO v_null, v_default
      FROM information_schema.columns
     WHERE table_schema = 'public'
       AND table_name   = 'stime'
       AND column_name  = 'agency_id';

    IF v_null IS NULL THEN
        RAISE EXCEPTION
            'P26-2B 033: stime.agency_id is missing; apply 031 and 032 first';
    END IF;

    IF v_null <> 'NO' THEN
        RAISE EXCEPTION
            'P26-2B 033: stime.agency_id is still nullable after the alter';
    END IF;

    IF v_default IS NOT NULL THEN
        RAISE EXCEPTION
            'P26-2B 033: stime.agency_id must carry no column default, found %',
            v_default;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- 2. core_agency_integrity(), evolved.
--
-- Branch for branch this is 030's function with section A's stima lookup
-- replaced and section E deleted. Everything else - pairwise agreement, the
-- treatment of an explicit agency_id, the final refusal - is unchanged, because
-- nothing about those depended on how a stima was resolved.
--
-- The triggers created by 030 on activities and tasks continue to reference
-- this function by name and are not recreated here.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core_agency_integrity() RETURNS trigger AS $fn$
DECLARE
    a_lead    BIGINT;
    a_contact BIGINT;
    a_stima   BIGINT;
    resolved  BIGINT;
BEGIN
    -- A. Resolve every available reference.
    IF NEW.lead_id IS NOT NULL THEN
        SELECT agency_id INTO a_lead FROM leads WHERE id = NEW.lead_id;
    END IF;

    IF NEW.contact_id IS NOT NULL THEN
        SELECT agency_id INTO a_contact FROM contacts WHERE id = NEW.contact_id;
    END IF;

    -- The stima owns its agency outright since 031/032. No join, no ordering,
    -- no row limit: one row, one answer.
    IF NEW.stima_id IS NOT NULL THEN
        SELECT agency_id INTO a_stima
          FROM stime
         WHERE id = NEW.stima_id;

        -- Unreachable while the foreign key holds and agency_id is NOT NULL,
        -- which is exactly why it raises rather than being omitted: a branch
        -- that cannot fire costs nothing, whereas a missing one would let a
        -- NULL fall through and place the row by accident.
        IF a_stima IS NULL THEN
            RAISE EXCEPTION
                'P26-2B agency integrity on %: stima % does not exist or carries no agency',
                TG_TABLE_NAME, NEW.stima_id;
        END IF;
    END IF;

    -- B. Every resolved pair must agree.
    IF a_lead IS NOT NULL AND a_contact IS NOT NULL AND a_lead <> a_contact THEN
        RAISE EXCEPTION
            'P26-2B agency integrity on %: lead % is in agency %, contact % is in agency %',
            TG_TABLE_NAME, NEW.lead_id, a_lead, NEW.contact_id, a_contact;
    END IF;

    IF a_lead IS NOT NULL AND a_stima IS NOT NULL AND a_lead <> a_stima THEN
        RAISE EXCEPTION
            'P26-2B agency integrity on %: lead % is in agency %, stima % is in agency %',
            TG_TABLE_NAME, NEW.lead_id, a_lead, NEW.stima_id, a_stima;
    END IF;

    IF a_contact IS NOT NULL AND a_stima IS NOT NULL AND a_contact <> a_stima THEN
        RAISE EXCEPTION
            'P26-2B agency integrity on %: contact % is in agency %, stima % is in agency %',
            TG_TABLE_NAME, NEW.contact_id, a_contact, NEW.stima_id, a_stima;
    END IF;

    resolved := COALESCE(a_lead, a_contact, a_stima);

    -- C. An explicit agency_id is validated, never trusted - and never
    -- overwritten once it agrees.
    IF NEW.agency_id IS NOT NULL THEN
        IF resolved IS NOT NULL AND NEW.agency_id <> resolved THEN
            RAISE EXCEPTION
                'P26-2B agency integrity on %: explicit agency_id % contradicts agency % derived from the row references',
                TG_TABLE_NAME, NEW.agency_id, resolved;
        END IF;
        RETURN NEW;
    END IF;

    -- D. Derive.
    IF resolved IS NOT NULL THEN
        NEW.agency_id := resolved;
        RETURN NEW;
    END IF;

    -- E. Anything else is unresolvable and must not be guessed.
    --
    -- 030's bounded Default Agency fallback stood here. It existed only because
    -- a stima-only row had no readable agency; now it has one, so the fallback
    -- is not merely unnecessary but wrong - it could only fire on a shape this
    -- function can already resolve, or on one it must refuse.
    RAISE EXCEPTION
        'P26-2B agency integrity on %: agency_id could not be resolved from contact_id=%, lead_id=%, stima_id=%',
        TG_TABLE_NAME, NEW.contact_id, NEW.lead_id, NEW.stima_id;
END;
$fn$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- 3. lead_stime cross-agency protection.
--
-- lead_stime is CHILD-DERIVED in the P26-2A ownership matrix, so it gets no
-- agency_id column of its own: the link has no independent tenancy, it merely
-- must not join two. A physical column would be a third copy of the same fact
-- and a third thing to keep consistent.
--
-- What it gets instead is a trigger that reads both sides and refuses anything
-- but a match. Both reads are by primary key - no ordering, no limit, no
-- fallback - and each of the three failures is explicit:
--
--   * the lead does not resolve to an agency
--   * the stima does not resolve to an agency
--   * the two disagree
--
-- UPDATE OF lead_id, stima_id matters as much as INSERT: without it, a link
-- could be created legitimately and then repointed at another agency's stima,
-- which is the same breach one statement later.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION lead_stime_agency_coherence() RETURNS trigger AS $fn$
DECLARE
    a_lead  BIGINT;
    a_stima BIGINT;
BEGIN
    SELECT agency_id INTO a_lead
      FROM leads
     WHERE id = NEW.lead_id;

    IF a_lead IS NULL THEN
        RAISE EXCEPTION
            'P26-2B lead_stime coherence: lead % does not exist or carries no agency',
            NEW.lead_id;
    END IF;

    SELECT agency_id INTO a_stima
      FROM stime
     WHERE id = NEW.stima_id;

    IF a_stima IS NULL THEN
        RAISE EXCEPTION
            'P26-2B lead_stime coherence: stima % does not exist or carries no agency',
            NEW.stima_id;
    END IF;

    IF a_lead <> a_stima THEN
        RAISE EXCEPTION
            'P26-2B lead_stime coherence: lead % is in agency %, stima % is in agency %; a link may not span two agencies',
            NEW.lead_id, a_lead, NEW.stima_id, a_stima;
    END IF;

    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

-- PostgreSQL has no CREATE TRIGGER IF NOT EXISTS, so the catalogue is consulted
-- directly - the pattern 026 established and 030 reused. tgisinternal is
-- excluded so a constraint-backed internal trigger cannot produce a false
-- positive.
DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE t.tgname = 'trg_lead_stime_agency_coherence'
          AND c.relname = 'lead_stime'
          AND n.nspname = 'public'
          AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_lead_stime_agency_coherence
            BEFORE INSERT OR UPDATE OF lead_id, stima_id
            ON lead_stime
            FOR EACH ROW EXECUTE FUNCTION lead_stime_agency_coherence();
    END IF;
END
$do$;
