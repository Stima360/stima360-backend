-- Down for 077. Toglie A30-12: `public_booking_links`,
-- `public_booking_submissions`, `public_booking_rate_limits`, la loro
-- funzione di guardia e il suo trigger.
--
-- RIFIUTA SE public_booking_links O public_booking_submissions CONTENGONO
-- ANCHE UNA SOLA RIGA: stessa regola delle down di 072 e 076. I contatori
-- di rate limit in public_booking_rate_limits sono effimeri per natura (si
-- azzerano ad ogni finestra) e non bloccano la down da soli.
--
-- NON tocca `appointments`, `appointment_events`,
-- `appointment_calendar_sync`, `agent_working_hours`,
-- `agent_availability_exceptions`, `agency_closures`, `agencies`,
-- `agency_memberships`, `operator_users`: A30-12 non le ha mai toccate
-- all'andata, quindi il ritorno non le tocca.

BEGIN;

DO $$
DECLARE
    v_n INTEGER := 0;
    v_tot INTEGER := 0;
BEGIN
    IF to_regclass('public.public_booking_links') IS NOT NULL THEN
        SELECT count(*) INTO v_n FROM public_booking_links;
        v_tot := v_tot + v_n;
    END IF;
    IF to_regclass('public.public_booking_submissions') IS NOT NULL THEN
        SELECT count(*) INTO v_n FROM public_booking_submissions;
        v_tot := v_tot + v_n;
    END IF;
    IF v_tot > 0 THEN
        RAISE EXCEPTION
            'A30-12 077 down: % row(s) across public_booking_links/public_booking_submissions would be destroyed. Nothing has been changed. Export and empty them deliberately first.',
            v_tot;
    END IF;
END
$$;

DROP TABLE IF EXISTS public_booking_rate_limits;
DROP TABLE IF EXISTS public_booking_submissions;
DROP TABLE IF EXISTS public_booking_links;

DROP FUNCTION IF EXISTS public_booking_links_guard();

DELETE FROM schema_migrations WHERE version = '077_a30_12_public_booking';

COMMIT;
