-- Down for 081. Toglie CRM-OPS-3: il trigger sull'incarico, la colonna
-- `properties.acquisition_id` (con FK e UNIQUE), `acquisition_events`,
-- `acquisitions` e le loro funzioni.
--
-- RIFIUTA SE ESISTE ANCHE UNA SOLA ACQUISIZIONE: toglierla cancellerebbe
-- il percorso commerciale, lo storico e l'origine degli incarichi generati
-- (stessa regola delle down di 072, 076, 077, 078 e 080). Gli appuntamenti
-- creati dalle acquisizioni restano nell'Agenda: questa down non li tocca.

BEGIN;

DO $$
DECLARE
    v_n INTEGER := 0;
BEGIN
    IF to_regclass('public.acquisitions') IS NOT NULL THEN
        EXECUTE 'SELECT count(*) FROM acquisitions' INTO v_n;
    END IF;
    IF v_n > 0 THEN
        RAISE EXCEPTION
            'CRM-OPS-3 081 down: % acquisition(s) exist and would be lost, with the origin of their mandates. Nothing has been changed.',
            v_n;
    END IF;
END
$$;

DROP TRIGGER IF EXISTS trg_properties_mandate_origin ON properties;
DROP FUNCTION IF EXISTS properties_mandate_origin_guard();
ALTER TABLE properties DROP CONSTRAINT IF EXISTS properties_acquisition_same_agency_fk;
ALTER TABLE properties DROP CONSTRAINT IF EXISTS properties_acquisition_unq;
ALTER TABLE properties DROP COLUMN IF EXISTS acquisition_id;
DROP TABLE IF EXISTS acquisition_events;
DROP TABLE IF EXISTS acquisitions;
DROP FUNCTION IF EXISTS acquisitions_guard();
DROP FUNCTION IF EXISTS acquisitions_refuse_delete();
DROP FUNCTION IF EXISTS acquisition_events_append_only();

COMMIT;
