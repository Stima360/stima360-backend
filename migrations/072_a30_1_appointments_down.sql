-- Down for 072. Toglie l'Agenda CRM: `appointments`, `appointment_events`, i
-- loro trigger e le loro funzioni.
--
-- RIFIUTA SE `appointments` CONTIENE ANCHE UNA SOLA RIGA. Dal momento in cui
-- l'Agenda e' in uso, quelle righe sono l'unico posto in cui esistono gli
-- appuntamenti: perderle non e' reversibile. La via d'uscita e' esportarle e
-- svuotare le tabelle deliberatamente, e solo allora rieseguire questa down.
-- Svuotarle richiede di disattivare a mano i trigger che rifiutano la DELETE
-- (`trg_appointments_refuse_delete`, `trg_appointment_events_append_only`):
-- e' voluto, perche' nessun percorso applicativo deve poterlo fare. I soli
-- dati di prova (`a30_test`) si tolgono invece con `a30_test_purge(run_id)`.
--
-- NON rimuove l'estensione `btree_gist`: e' un oggetto di database, altri
-- vincoli futuri possono usarla, e lasciarla non ha effetti sul resto dello
-- schema. Toglierla e' una decisione separata.
--
-- NON tocca `stima_inspections`, `property_visits`, `stime`,
-- `stime_dettagliate`, ne' la funzione `lmc15_assert_operator_may_act` (e'
-- della 070 e resta sua).

BEGIN;

DO $$
DECLARE
    v_n INTEGER := 0;
BEGIN
    IF to_regclass('public.appointments') IS NOT NULL THEN
        SELECT count(*) INTO v_n FROM appointments;
    END IF;
    IF v_n > 0 THEN
        RAISE EXCEPTION
            'A30-1 072 down: % appointment(s) would be destroyed, and no other table holds them. Nothing has been changed. Export appointments and appointment_events and empty them deliberately first.',
            v_n;
    END IF;
END
$$;

DROP TABLE IF EXISTS appointment_events;
DROP TABLE IF EXISTS appointments;

DROP FUNCTION IF EXISTS a30_test_purge(TEXT);
DROP FUNCTION IF EXISTS appointment_events_append_only();
DROP FUNCTION IF EXISTS appointments_refuse_delete();
DROP FUNCTION IF EXISTS appointments_guard();
DROP FUNCTION IF EXISTS a30_is_certified_test_database();

DELETE FROM schema_migrations WHERE version = '072_a30_1_appointments';

COMMIT;
