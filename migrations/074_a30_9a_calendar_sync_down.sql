-- Down for 074. Toglie SOLO gli oggetti della 074: le tre tabelle della
-- sincronizzazione calendario, i loro trigger e le loro funzioni.
--
-- RIFIUTA SE ESISTE UNA CONNESSIONE O UNA RIGA DI SYNC. Dal momento in cui la
-- sincronizzazione e' in uso, `appointment_calendar_sync` e' l'unico posto che
-- sa quale evento remoto appartiene a quale appuntamento, e
-- `calendar_connections` l'unico che tiene i token (cifrati): perderli lascia
-- eventi remoti orfani e costringe ogni operatore a ricollegarsi. Gli state
-- OAuth sono effimeri e non bloccano.
--
-- NON tocca `appointments`, ne' le colonne `google_*` della 072.

BEGIN;

DO $$
DECLARE
    v_conn INTEGER := 0;
    v_sync INTEGER := 0;
BEGIN
    IF to_regclass('public.calendar_connections') IS NOT NULL THEN
        SELECT count(*) INTO v_conn FROM calendar_connections;
    END IF;
    IF to_regclass('public.appointment_calendar_sync') IS NOT NULL THEN
        SELECT count(*) INTO v_sync FROM appointment_calendar_sync;
    END IF;
    IF v_conn > 0 OR v_sync > 0 THEN
        RAISE EXCEPTION
            'A30-9A 074 down: % calendar connection(s) and % sync row(s) would be destroyed. Nothing has been changed. Disconnect and export them deliberately first.',
            v_conn, v_sync;
    END IF;
END
$$;

DROP TABLE IF EXISTS appointment_calendar_sync;
DROP TABLE IF EXISTS calendar_oauth_states;
DROP TABLE IF EXISTS calendar_connections;

DROP FUNCTION IF EXISTS appointment_calendar_sync_guard();
DROP FUNCTION IF EXISTS calendar_connections_guard();

DELETE FROM schema_migrations WHERE version = '074_a30_9a_calendar_sync';

COMMIT;
