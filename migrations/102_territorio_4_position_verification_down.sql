-- Down for 102. Toglie la verifica della posizione degli edifici (trigger,
-- funzione, vincoli, colonne). Le coordinate restano come sono.
--
-- Si FERMA, senza modifiche, se esiste anche una sola verifica registrata:
-- toglierla cancellerebbe chi e quando ha confermato una posizione.

BEGIN;

DO $do$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public'
                  AND table_name = 'buildings' AND column_name = 'position_verified_at')
       AND EXISTS (SELECT 1 FROM buildings WHERE position_verified_at IS NOT NULL
                      OR position_verified_by_user_id IS NOT NULL) THEN
        RAISE EXCEPTION '102 down: esistono posizioni verificate; nessuna modifica eseguita';
    END IF;
END
$do$;

DROP TRIGGER IF EXISTS trg_buildings_position_verification ON buildings;
DROP FUNCTION IF EXISTS buildings_position_verification();
ALTER TABLE buildings DROP CONSTRAINT IF EXISTS buildings_position_verified_chk;
ALTER TABLE buildings DROP CONSTRAINT IF EXISTS buildings_position_verified_by_user_fk;
ALTER TABLE buildings DROP COLUMN IF EXISTS position_verified_by_user_id;
ALTER TABLE buildings DROP COLUMN IF EXISTS position_verified_at;

COMMIT;
