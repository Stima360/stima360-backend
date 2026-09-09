-- P26-2E hotfix: make match_agency_integrity() safe for every table it guards.
--
-- WHAT WENT WRONG
--
-- 040 installed one trigger function on four tables and wrote the latest_run
-- check as a single condition:
--
--     IF TG_TABLE_NAME = 'matches' AND NEW.latest_run_id IS NOT NULL THEN
--
-- That reads as "only look at latest_run_id when the row is a match", but it is
-- not what PL/pgSQL does. A record field is resolved when the expression
-- containing it is *prepared*, not only when the preceding conjunct turns out
-- to be true, so the field lookup happens for whichever table fires the
-- trigger. On match_runs - which has no latest_run_id - every INSERT failed
-- with:
--
--     record "new" has no field "latest_run_id"
--
-- The guard destroyed the writes it existed to protect. It was found by the
-- hostile certification on TEST, on the first INSERT into match_runs.
--
-- THE FIX
--
-- The table test and the field access become separate statements. A nested IF
-- is prepared only when the outer branch is actually entered, so a field is
-- never resolved for a table that does not have it. That is the whole change.
--
-- WHY THE OTHER BRANCHES WERE ALREADY SAFE
--
-- The function was audited in full, not only at the line that failed:
--
--   * `IF TG_TABLE_NAME IN ('matches','match_runs','match_exclusions')` names
--     no field in its condition, and all three of those tables have
--     buy_request_id and property_id. The NEW references inside it are
--     therefore reached only for tables that carry them.
--   * `IF TG_TABLE_NAME = 'match_refresh_history'` likewise names no field in
--     its condition; match_id, previous_run_id and new_run_id are read inside.
--   * The NEW references on the RAISE lines are arguments to a message, inside
--     a branch already entered, not part of a condition.
--
-- So exactly one expression was unsafe, and only that one is restructured here.
--
-- SEMANTICS ARE UNCHANGED
--
-- Every P26-2E invariant from 040 is preserved exactly:
--
--   matches               buy request and property in the same agency
--   match_runs            both roots, when both are present, in the same agency
--   match_exclusions      buy request and property in the same agency
--   matches.latest_run_id a run belonging to this same pair
--   match_refresh_history previous_run_id and new_run_id of this match's pair
--
-- Pair, not merely agency: a run from the same tenant but a different pair
-- would pass an agency comparison and still be the wrong run.
--
-- 040 IS NOT EDITED
--
-- It is applied on TEST and its checksum must stay immutable, so this file
-- replaces the function forward. No trigger is touched: all four continue to
-- reference match_agency_integrity() by name and pick up the new body.
--
-- Transaction ownership: the runner owns the UP transaction, so this file
-- carries no BEGIN/COMMIT. Reversible - it writes no data, and its down file
-- restores 040's definition verbatim.

CREATE OR REPLACE FUNCTION match_agency_integrity() RETURNS trigger AS $fn$
DECLARE
    a_buy      BIGINT;
    a_property BIGINT;
    m_buy      BIGINT;
    m_property BIGINT;
    r_buy      BIGINT;
    r_property BIGINT;
BEGIN
    IF TG_TABLE_NAME IN ('matches', 'match_runs', 'match_exclusions') THEN
        IF NEW.buy_request_id IS NOT NULL THEN
            SELECT agency_id INTO a_buy FROM buy_requests WHERE id = NEW.buy_request_id;
            IF a_buy IS NULL THEN
                RAISE EXCEPTION
                    'P26-2E match integrity on %: buy request % does not exist or carries no agency',
                    TG_TABLE_NAME, NEW.buy_request_id;
            END IF;
        END IF;

        IF NEW.property_id IS NOT NULL THEN
            SELECT agency_id INTO a_property FROM properties WHERE id = NEW.property_id;
            IF a_property IS NULL THEN
                RAISE EXCEPTION
                    'P26-2E match integrity on %: property % does not exist or carries no agency',
                    TG_TABLE_NAME, NEW.property_id;
            END IF;
        END IF;

        -- Both present: they must agree. NULL is not agreement - match_runs
        -- legitimately covers one side only, and that case is unconstrained.
        IF a_buy IS NOT NULL AND a_property IS NOT NULL AND a_buy <> a_property THEN
            RAISE EXCEPTION
                'P26-2E match integrity on %: buy request % is in agency %, property % is in agency %',
                TG_TABLE_NAME, NEW.buy_request_id, a_buy, NEW.property_id, a_property;
        END IF;
    END IF;

    -- matches.latest_run_id must be a run for this same pair.
    --
    -- Nested, not conjoined: this is the correction 041 exists for. See the
    -- header - a single `TG_TABLE_NAME = 'matches' AND NEW.latest_run_id ...`
    -- resolves latest_run_id on every table the function guards.
    IF TG_TABLE_NAME = 'matches' THEN
        IF NEW.latest_run_id IS NOT NULL THEN
            SELECT buy_request_id, property_id INTO r_buy, r_property
              FROM match_runs WHERE id = NEW.latest_run_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'P26-2E match integrity on matches: latest run % does not exist',
                    NEW.latest_run_id;
            END IF;
            IF (r_buy IS NOT NULL AND r_buy <> NEW.buy_request_id)
               OR (r_property IS NOT NULL AND r_property <> NEW.property_id) THEN
                RAISE EXCEPTION
                    'P26-2E match integrity on matches: latest run % belongs to a different pair',
                    NEW.latest_run_id;
            END IF;
        END IF;
    END IF;

    IF TG_TABLE_NAME = 'match_refresh_history' THEN
        SELECT buy_request_id, property_id INTO m_buy, m_property
          FROM matches WHERE id = NEW.match_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION
                'P26-2E match integrity on match_refresh_history: match % does not exist',
                NEW.match_id;
        END IF;

        IF NEW.previous_run_id IS NOT NULL THEN
            SELECT buy_request_id, property_id INTO r_buy, r_property
              FROM match_runs WHERE id = NEW.previous_run_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'P26-2E match integrity on match_refresh_history: previous run % does not exist',
                    NEW.previous_run_id;
            END IF;
            IF (r_buy IS NOT NULL AND r_buy <> m_buy)
               OR (r_property IS NOT NULL AND r_property <> m_property) THEN
                RAISE EXCEPTION
                    'P26-2E match integrity on match_refresh_history: previous run % belongs to a different pair',
                    NEW.previous_run_id;
            END IF;
        END IF;

        IF NEW.new_run_id IS NOT NULL THEN
            SELECT buy_request_id, property_id INTO r_buy, r_property
              FROM match_runs WHERE id = NEW.new_run_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'P26-2E match integrity on match_refresh_history: new run % does not exist',
                    NEW.new_run_id;
            END IF;
            IF (r_buy IS NOT NULL AND r_buy <> m_buy)
               OR (r_property IS NOT NULL AND r_property <> m_property) THEN
                RAISE EXCEPTION
                    'P26-2E match integrity on match_refresh_history: new run % belongs to a different pair',
                    NEW.new_run_id;
            END IF;
        END IF;
    END IF;

    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;
