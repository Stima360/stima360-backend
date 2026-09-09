-- P26-2E MATCH agency integrity: enforcement only.
--
-- MATCH is CHILD-DERIVED. No table below gains an agency_id column, and that is
-- the central decision of this slice rather than an omission.
--
-- A match has no tenancy of its own. It is a statement about two rows that do:
--
--     matches.buy_request_id -> buy_requests.agency_id
--     matches.property_id    -> properties.agency_id
--
-- and the whole rule is that those two must be equal. A match between agency
-- A's buyer and agency B's property is not a mis-scoped row - it is a row that
-- must never exist, because it asserts a relationship across a tenant boundary.
--
-- A physical agency_id here would be a third copy of a fact already recorded
-- twice, and the failure mode of three copies is that they disagree. So the
-- agency is derived, every time, from the roots.
--
-- WHICH TABLES GET A TRIGGER
--
--   matches                    two roots + latest_run_id   -> guarded
--   match_runs                 two optional roots          -> guarded
--   match_exclusions           two roots                   -> guarded
--   match_refresh_history      parent + two run references -> guarded
--   match_requirement_results  match_run_id only           -> NOT guarded
--   match_feedback             match_id only               -> NOT guarded
--
-- The last two are excluded for a structural reason, not a preference: each has
-- exactly one foreign key, to a parent this file already guards. A row that
-- names only one other row cannot straddle two agencies, so a trigger there
-- could never fire while costing a write on every insert.
--
-- PAIR, NOT MERELY AGENCY
--
-- `latest_run_id`, `previous_run_id` and `new_run_id` need more than a
-- same-agency check. A match_run carries its own buy_request_id and
-- property_id, so a run from the *same tenant but a different pair* would pass
-- an agency comparison and still be the wrong run - silently attributing one
-- pair's score history to another. The guards below therefore compare the pair.
--
-- match_runs allows either root to be NULL (the historical CHECK requires at
-- least one), because a batch run legitimately covers one side only. Each
-- reference is validated when present; NULL is not treated as agreement.
--
-- Transaction ownership: the runner owns the UP transaction, so this file
-- carries no BEGIN/COMMIT. See docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.
--
-- Reversible: 040 writes no data. It adds one function and four triggers, and
-- its down file removes exactly those.

-- ---------------------------------------------------------------------------
-- 1. Prechecks. The migration refuses on inconsistent history and repairs
--    nothing: a cross-agency match cannot be corrected automatically, because
--    there is no way to know which of the two roots was the mistake.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_bad BIGINT;
BEGIN
    SELECT count(*) INTO v_bad
      FROM matches m
      JOIN buy_requests b ON b.id = m.buy_request_id
      JOIN properties  p ON p.id = m.property_id
     WHERE b.agency_id <> p.agency_id;
    IF v_bad <> 0 THEN
        RAISE EXCEPTION
            'P26-2E 040 refused: % match(es) join a buy request and a property in different agencies', v_bad;
    END IF;

    SELECT count(*) INTO v_bad
      FROM match_runs r
      JOIN buy_requests b ON b.id = r.buy_request_id
      JOIN properties  p ON p.id = r.property_id
     WHERE b.agency_id <> p.agency_id;
    IF v_bad <> 0 THEN
        RAISE EXCEPTION
            'P26-2E 040 refused: % match run(s) name a buy request and a property in different agencies', v_bad;
    END IF;

    SELECT count(*) INTO v_bad
      FROM match_exclusions e
      JOIN buy_requests b ON b.id = e.buy_request_id
      JOIN properties  p ON p.id = e.property_id
     WHERE b.agency_id <> p.agency_id;
    IF v_bad <> 0 THEN
        RAISE EXCEPTION
            'P26-2E 040 refused: % exclusion(s) span two agencies', v_bad;
    END IF;

    -- latest_run_id must belong to this match's own pair, not merely to the
    -- same tenant.
    SELECT count(*) INTO v_bad
      FROM matches m
      JOIN match_runs r ON r.id = m.latest_run_id
     WHERE (r.buy_request_id IS NOT NULL AND r.buy_request_id <> m.buy_request_id)
        OR (r.property_id    IS NOT NULL AND r.property_id    <> m.property_id);
    IF v_bad <> 0 THEN
        RAISE EXCEPTION
            'P26-2E 040 refused: % match(es) point at a latest run belonging to a different pair', v_bad;
    END IF;

    SELECT count(*) INTO v_bad
      FROM match_refresh_history h
      JOIN matches m ON m.id = h.match_id
      JOIN match_runs r ON r.id = h.previous_run_id
     WHERE (r.buy_request_id IS NOT NULL AND r.buy_request_id <> m.buy_request_id)
        OR (r.property_id    IS NOT NULL AND r.property_id    <> m.property_id);
    IF v_bad <> 0 THEN
        RAISE EXCEPTION
            'P26-2E 040 refused: % refresh history row(s) cite a previous run from a different pair', v_bad;
    END IF;

    SELECT count(*) INTO v_bad
      FROM match_refresh_history h
      JOIN matches m ON m.id = h.match_id
      JOIN match_runs r ON r.id = h.new_run_id
     WHERE (r.buy_request_id IS NOT NULL AND r.buy_request_id <> m.buy_request_id)
        OR (r.property_id    IS NOT NULL AND r.property_id    <> m.property_id);
    IF v_bad <> 0 THEN
        RAISE EXCEPTION
            'P26-2E 040 refused: % refresh history row(s) cite a new run from a different pair', v_bad;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- 2. The integrity function, shared by the four triggers.
--
-- One function rather than four: every rule it enforces is the same rule -
-- a MATCH row may not name rows from two agencies - and splitting it would put
-- that one idea in four places.
-- ---------------------------------------------------------------------------
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
        -- legitimately covers one side only, and that case is simply unconstrained.
        IF a_buy IS NOT NULL AND a_property IS NOT NULL AND a_buy <> a_property THEN
            RAISE EXCEPTION
                'P26-2E match integrity on %: buy request % is in agency %, property % is in agency %',
                TG_TABLE_NAME, NEW.buy_request_id, a_buy, NEW.property_id, a_property;
        END IF;
    END IF;

    -- matches.latest_run_id must be a run for this same pair.
    IF TG_TABLE_NAME = 'matches' AND NEW.latest_run_id IS NOT NULL THEN
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

-- ---------------------------------------------------------------------------
-- 3. Triggers.
--
-- BEFORE INSERT OR UPDATE OF the reference columns. The UPDATE branch closes
-- the hole an insert-only form leaves: creating a legitimate match and then
-- repointing it at another agency's property is the same breach, one statement
-- later. Naming the columns keeps ordinary updates (score, status, timestamps)
-- free of trigger cost.
--
-- PostgreSQL has no CREATE TRIGGER IF NOT EXISTS, so the catalogue is consulted
-- directly - the pattern 026 established. tgisinternal is excluded so a
-- constraint-backed internal trigger cannot produce a false positive.
--
-- match_requirement_results and match_feedback are deliberately absent.
-- ---------------------------------------------------------------------------
DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE t.tgname = 'trg_matches_agency_integrity'
          AND c.relname = 'matches' AND n.nspname = 'public' AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_matches_agency_integrity
            BEFORE INSERT OR UPDATE OF buy_request_id, property_id, latest_run_id
            ON matches
            FOR EACH ROW EXECUTE FUNCTION match_agency_integrity();
    END IF;
END
$do$;

DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE t.tgname = 'trg_match_runs_agency_integrity'
          AND c.relname = 'match_runs' AND n.nspname = 'public' AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_match_runs_agency_integrity
            BEFORE INSERT OR UPDATE OF buy_request_id, property_id
            ON match_runs
            FOR EACH ROW EXECUTE FUNCTION match_agency_integrity();
    END IF;
END
$do$;

DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE t.tgname = 'trg_match_exclusions_agency_integrity'
          AND c.relname = 'match_exclusions' AND n.nspname = 'public' AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_match_exclusions_agency_integrity
            BEFORE INSERT OR UPDATE OF buy_request_id, property_id
            ON match_exclusions
            FOR EACH ROW EXECUTE FUNCTION match_agency_integrity();
    END IF;
END
$do$;

DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE t.tgname = 'trg_match_refresh_history_agency_integrity'
          AND c.relname = 'match_refresh_history' AND n.nspname = 'public' AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_match_refresh_history_agency_integrity
            BEFORE INSERT OR UPDATE OF match_id, previous_run_id, new_run_id
            ON match_refresh_history
            FOR EACH ROW EXECUTE FUNCTION match_agency_integrity();
    END IF;
END
$do$;
