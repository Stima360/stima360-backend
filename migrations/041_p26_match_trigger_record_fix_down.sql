-- Rollback of the P26-2E trigger record hotfix.
--
-- Restores match_agency_integrity() to 040's definition verbatim, including the
-- conjoined condition
--
--     IF TG_TABLE_NAME = 'matches' AND NEW.latest_run_id IS NOT NULL THEN
--
-- that 041 exists to correct. That is deliberate and it is what "reversible"
-- has to mean here: a rollback which quietly kept 041's body would be a
-- different migration wearing this one's name, and the database would end up in
-- a state that is neither 040 nor 041.
--
-- Be aware of what rolling back restores. With 040's body in place, every
-- INSERT and UPDATE on match_runs fails with
--
--     record "new" has no field "latest_run_id"
--
-- so match calculation stops working. Roll this back only to reproduce that
-- failure or to step the schema backwards past 041 on the way to 040's own
-- rollback - not as a way to disable the hotfix while continuing to use MATCH.
--
-- Deliberately NOT touched:
--
--   * the four triggers from 040. They reference the function by name and pick
--     up whichever body is installed, so dropping and recreating them here
--     would be churn and would briefly leave the tables unguarded.
--   * every row in every MATCH table. 041 wrote no data.
--   * 040 itself, which stays applied and keeps its checksum.
--
-- This file brackets its own transaction because it is executed manually rather
-- than by scripts/p26_migrate.py, which has no down command.

BEGIN;

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

COMMIT;
