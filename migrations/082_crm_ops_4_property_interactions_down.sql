-- Down for 082. Toglie il legame interazioni/immobile: trigger, indice,
-- FK e colonna `activities.property_id`; il CHECK dei riferimenti torna
-- quello di 001.
--
-- RIFIUTA SE ESISTE ANCHE UNA SOLA ATTIVITA' LEGATA A UN IMMOBILE: toglierla
-- cancellerebbe lo storico commerciale (e una nota senza contatto, lead o
-- stima violerebbe il CHECK ripristinato). Stessa regola delle down di 072,
-- 076, 077, 078, 080 e 081.

BEGIN;

DO $$
DECLARE
    v_n INTEGER := 0;
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'activities'
                  AND column_name = 'property_id') THEN
        EXECUTE 'SELECT count(*) FROM activities WHERE property_id IS NOT NULL' INTO v_n;
    END IF;
    IF v_n > 0 THEN
        RAISE EXCEPTION
            'CRM-OPS-4 082 down: % activit(y/ies) are linked to a property and would lose it. Nothing has been changed.',
            v_n;
    END IF;
END
$$;

DROP TRIGGER IF EXISTS trg_activities_property_history ON activities;
DROP FUNCTION IF EXISTS activities_property_history_guard();
DROP TRIGGER IF EXISTS trg_activities_property_scope ON activities;
DROP FUNCTION IF EXISTS activities_property_scope();
DROP INDEX IF EXISTS idx_activities_agency_property_occurred;
ALTER TABLE activities DROP CONSTRAINT IF EXISTS activities_reference_chk;
ALTER TABLE activities
    ADD CONSTRAINT activities_reference_chk CHECK (
        contact_id IS NOT NULL OR lead_id IS NOT NULL OR stima_id IS NOT NULL
    );
ALTER TABLE activities DROP CONSTRAINT IF EXISTS activities_property_id_fkey;
ALTER TABLE activities DROP COLUMN IF EXISTS property_id;

COMMIT;
