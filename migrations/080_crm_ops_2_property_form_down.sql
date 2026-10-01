-- Down for 080. Toglie CRM-OPS-2: la FK composita, l'indice e le colonne
-- `properties.region` e `properties.assigned_agent_id`.
--
-- RIFIUTA SE ANCHE UN SOLO IMMOBILE HA UNA DELLE DUE COLONNE VALORIZZATA:
-- stessa regola delle down di 072, 076, 077 e 078. Togliere la colonna
-- cancellerebbe un dato inserito dall'operatore. `assigned_to` (l'istantanea
-- testuale) e le altre colonne della 002 non sono toccate.

BEGIN;

DO $$
DECLARE
    v_n INTEGER := 0;
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'properties'
                  AND column_name = 'region')
       AND EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'properties'
                      AND column_name = 'assigned_agent_id') THEN
        EXECUTE 'SELECT count(*) FROM properties
                  WHERE region IS NOT NULL OR assigned_agent_id IS NOT NULL'
           INTO v_n;
    END IF;
    IF v_n > 0 THEN
        RAISE EXCEPTION
            'CRM-OPS-2 080 down: % properties row(s) carry region or assigned_agent_id and would lose it. Nothing has been changed.',
            v_n;
    END IF;
END
$$;

ALTER TABLE properties DROP CONSTRAINT IF EXISTS properties_agent_same_agency_fk;
DROP INDEX IF EXISTS idx_properties_assigned_agent;
ALTER TABLE properties DROP COLUMN IF EXISTS assigned_agent_id;
ALTER TABLE properties DROP COLUMN IF EXISTS region;

COMMIT;
