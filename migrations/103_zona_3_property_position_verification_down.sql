-- Down for 103. Toglie la verifica della posizione degli immobili indipendenti
-- (trigger, funzione, vincoli, colonne). Le coordinate restano come sono;
-- `buildings` e la 102 non vengono toccate.
--
-- Si FERMA, senza modifiche, se esiste anche una sola verifica registrata:
-- toglierla cancellerebbe chi e quando ha confermato una posizione.

BEGIN;

DO $do$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public'
                  AND table_name = 'properties' AND column_name = 'position_verified_at')
       AND EXISTS (SELECT 1 FROM properties WHERE position_verified_at IS NOT NULL
                      OR position_verified_by_user_id IS NOT NULL) THEN
        RAISE EXCEPTION '103 down: esistono posizioni verificate; nessuna modifica eseguita';
    END IF;
END
$do$;

DROP TRIGGER IF EXISTS trg_properties_position_verification ON properties;
DROP FUNCTION IF EXISTS properties_position_verification();
ALTER TABLE properties DROP CONSTRAINT IF EXISTS properties_position_verified_chk;
ALTER TABLE properties DROP CONSTRAINT IF EXISTS properties_position_verified_by_user_fk;
ALTER TABLE properties DROP COLUMN IF EXISTS position_verified_by_user_id;
ALTER TABLE properties DROP COLUMN IF EXISTS position_verified_at;

COMMIT;
