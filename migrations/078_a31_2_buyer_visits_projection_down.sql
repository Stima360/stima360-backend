-- Down for 078. Toglie A31-2: il trigger `trg_property_visits_appointment_guard`,
-- la sua funzione e la colonna `property_visits.appointment_id` (con la FK e
-- il vincolo UNIQUE che le appartengono).
--
-- RIFIUTA SE ANCHE UNA SOLA VISITA E' COLLEGATA A UN APPUNTAMENTO: stessa
-- regola delle down di 072, 076 e 077. Togliere la colonna cancellerebbe il
-- solo legame fra la visita e l'appuntamento autorevole, e la riga
-- `property_visits` resterebbe una visita senza fonte.
--
-- NON tocca `appointments`, `appointment_events`, `properties`, le righe di
-- `property_visits` ne' il trigger P26 `trg_property_visits_agency_integrity`:
-- A31-2 non li ha mai modificati all'andata.

BEGIN;

DO $$
DECLARE
    v_n INTEGER := 0;
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'property_visits'
           AND column_name = 'appointment_id') THEN
        EXECUTE 'SELECT count(*) FROM property_visits WHERE appointment_id IS NOT NULL'
           INTO v_n;
    END IF;
    IF v_n > 0 THEN
        RAISE EXCEPTION
            'A31-2 078 down: % property_visits row(s) are projections of an appointment and would lose their link. Nothing has been changed.',
            v_n;
    END IF;
END
$$;

DROP TRIGGER IF EXISTS trg_property_visits_appointment_guard ON property_visits;
DROP FUNCTION IF EXISTS property_visits_appointment_guard();
ALTER TABLE property_visits DROP COLUMN IF EXISTS appointment_id;

DELETE FROM schema_migrations WHERE version = '078_a31_2_buyer_visits_projection';

COMMIT;
