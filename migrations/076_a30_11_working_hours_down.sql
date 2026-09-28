-- Down for 076. Toglie A30-11: `agent_working_hours`,
-- `agent_availability_exceptions`, `agency_closures`, le loro funzioni di
-- guardia e i loro trigger.
--
-- RIFIUTA SE UNA QUALUNQUE DELLE TRE TABELLE CONTIENE ANCHE UNA SOLA RIGA:
-- stessa regola della down di 072. Se l'agenda o l'availability_check
-- dipendono gia' da questi orari, perderli non e' reversibile senza
-- esportarli prima.
--
-- NON rimuove l'estensione `btree_gist`: era gia' installata dalla 072 e
-- resta di sua competenza.
--
-- NON tocca `appointments`, `appointment_events`,
-- `appointment_calendar_sync`, `agencies`, `agency_memberships`: A30-11 non
-- le ha mai toccate all'andata, quindi il ritorno non le tocca.

BEGIN;

DO $$
DECLARE
    v_n INTEGER := 0;
    v_tot INTEGER := 0;
BEGIN
    IF to_regclass('public.agent_working_hours') IS NOT NULL THEN
        SELECT count(*) INTO v_n FROM agent_working_hours;
        v_tot := v_tot + v_n;
    END IF;
    IF to_regclass('public.agent_availability_exceptions') IS NOT NULL THEN
        SELECT count(*) INTO v_n FROM agent_availability_exceptions;
        v_tot := v_tot + v_n;
    END IF;
    IF to_regclass('public.agency_closures') IS NOT NULL THEN
        SELECT count(*) INTO v_n FROM agency_closures;
        v_tot := v_tot + v_n;
    END IF;
    IF v_tot > 0 THEN
        RAISE EXCEPTION
            'A30-11 076 down: % row(s) across agent_working_hours/agent_availability_exceptions/agency_closures would be destroyed. Nothing has been changed. Export and empty them deliberately first.',
            v_tot;
    END IF;
END
$$;

DROP TABLE IF EXISTS agent_working_hours;
DROP TABLE IF EXISTS agent_availability_exceptions;
DROP TABLE IF EXISTS agency_closures;

DROP FUNCTION IF EXISTS agent_working_hours_guard();
DROP FUNCTION IF EXISTS agent_availability_exceptions_guard();
DROP FUNCTION IF EXISTS agency_closures_guard();

DELETE FROM schema_migrations WHERE version = '076_a30_11_working_hours';

COMMIT;
