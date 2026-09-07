-- Rollback of P26-2B STIMA root ownership, part 3 of 3.
--
-- Genuinely reversible, unlike the rollbacks of 029 and 032. Those refuse
-- because they wrote data and left no way to tell which rows they touched.
-- 033 wrote no data at all - it added a constraint and redefined two functions
-- - so every one of its effects can be lifted exactly, and no agency value is
-- lost in the process.
--
-- Order is the exact reverse of the up migration:
--
--   1. the lead_stime trigger      (it depends on the function)
--   2. the lead_stime function
--   3. core_agency_integrity(), restored to 030's definition
--   4. DROP NOT NULL on stime.agency_id
--
-- Deliberately NOT touched:
--
--   * stime.agency_id itself, and every value in it - those belong to 031 and
--     032. Dropping the column or clearing it would discard the backfill and,
--     worse, the ownership of every estimation written since.
--   * the activities and tasks triggers from 030. They reference
--     core_agency_integrity() by name and keep working against the restored
--     definition, so dropping and recreating them here would be churn.
--   * lead_stime's shape: 033 added no column, so there is none to remove.
--
-- Step 3 is the one that needs care. Restoring "a" function would leave the
-- database in a state that is neither 033 nor 030; what is restored below is
-- 030's body verbatim, including the ORDER BY / LIMIT 1 link walk and the
-- bounded Default Agency fallback that 033 removed. Those constructs are the
-- pre-033 behaviour, and a rollback that quietly kept 033's stricter version
-- would be a different migration wearing this one's name. A test compares this
-- body against 030's actual text so the two cannot drift apart.
--
-- No CASCADE anywhere. If something has come to depend on these objects, that
-- must fail loudly rather than be swept away silently.
--
-- This file brackets its own transaction because it is executed manually
-- rather than by scripts/p26_migrate.py, which has no down command.

BEGIN;

-- 1. The lead_stime trigger, before the function it calls.
DROP TRIGGER IF EXISTS trg_lead_stime_agency_coherence ON lead_stime;

-- 2. The lead_stime coherence function, once nothing calls it.
DROP FUNCTION IF EXISTS lead_stime_agency_coherence();

-- ---------------------------------------------------------------------------
-- 3. core_agency_integrity(), restored to 030's definition verbatim.
--
-- Reproduced rather than referenced because SQL has no way to say "the previous
-- body". Kept in step with 030 by
-- tests/test_p26_2b_migration_033.py::test_h9_the_restored_function_matches_030_branch_for_branch,
-- which compares this text against 030's own.
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

    IF NEW.stima_id IS NOT NULL THEN
        SELECT l.agency_id INTO a_stima
          FROM lead_stime ls
          JOIN leads l ON l.id = ls.lead_id
         WHERE ls.stima_id = NEW.stima_id
         ORDER BY ls.id
         LIMIT 1;
    END IF;

    -- B. Every resolved pair must agree.
    IF a_lead IS NOT NULL AND a_contact IS NOT NULL AND a_lead <> a_contact THEN
        RAISE EXCEPTION
            'P26-1 agency integrity on %: lead % is in agency %, contact % is in agency %',
            TG_TABLE_NAME, NEW.lead_id, a_lead, NEW.contact_id, a_contact;
    END IF;

    IF a_lead IS NOT NULL AND a_stima IS NOT NULL AND a_lead <> a_stima THEN
        RAISE EXCEPTION
            'P26-1 agency integrity on %: lead % is in agency %, stima % resolves to agency %',
            TG_TABLE_NAME, NEW.lead_id, a_lead, NEW.stima_id, a_stima;
    END IF;

    IF a_contact IS NOT NULL AND a_stima IS NOT NULL AND a_contact <> a_stima THEN
        RAISE EXCEPTION
            'P26-1 agency integrity on %: contact % is in agency %, stima % resolves to agency %',
            TG_TABLE_NAME, NEW.contact_id, a_contact, NEW.stima_id, a_stima;
    END IF;

    resolved := COALESCE(a_lead, a_contact, a_stima);

    -- C. An explicit agency_id is validated, never trusted - and never
    -- overwritten once it agrees.
    IF NEW.agency_id IS NOT NULL THEN
        IF resolved IS NOT NULL AND NEW.agency_id <> resolved THEN
            RAISE EXCEPTION
                'P26-1 agency integrity on %: explicit agency_id % contradicts agency % derived from the row references',
                TG_TABLE_NAME, NEW.agency_id, resolved;
        END IF;
        RETURN NEW;
    END IF;

    -- D. Derive.
    IF resolved IS NOT NULL THEN
        NEW.agency_id := resolved;
        RETURN NEW;
    END IF;

    -- E. Bounded fallback. Reachable by exactly one real flow: main.py calls
    -- followup_service.safe_run_followup(... stima_id=new_id, contact_id=None,
    -- lead_id=None ...) after the public bridge returned skipped, conflict or
    -- error, so the task carries a stima and nothing else, with no lead_stime
    -- row to resolve through. All three conditions are required: a generic
    -- activity or task must never be parked in the Default Agency.
    IF NEW.stima_id IS NOT NULL
       AND NEW.lead_id IS NULL
       AND NEW.contact_id IS NULL THEN
        SELECT id INTO NEW.agency_id
          FROM agencies
         WHERE slug = 'stima360' AND status = 'active';

        IF NEW.agency_id IS NULL THEN
            RAISE EXCEPTION
                'P26-1 agency integrity on %: the Default Agency is missing or inactive, so a stima-only row cannot be placed',
                TG_TABLE_NAME;
        END IF;

        RETURN NEW;
    END IF;

    -- F. Anything else is unresolvable and must not be guessed.
    RAISE EXCEPTION
        'P26-1 agency integrity on %: agency_id could not be resolved from contact_id=%, lead_id=%, stima_id=%',
        TG_TABLE_NAME, NEW.contact_id, NEW.lead_id, NEW.stima_id;
END;
$fn$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- 4. Nullability. Every value written by 032 and by the runtime writer stays
-- exactly where it is; only the constraint is lifted.
-- ---------------------------------------------------------------------------
ALTER TABLE stime
    ALTER COLUMN agency_id DROP NOT NULL;

COMMIT;
