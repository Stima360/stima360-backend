-- Down for 089. Toglie la natura di pertinenza (`is_pertinenza`,
-- `pertinenza_kind`).
--
-- Si FERMA se una scheda e' marcata pertinenza o porta un tipo di pertinenza:
-- e' l'unica traccia della natura di una pertinenza non collegata (senza,
-- tornerebbe a contare come unita' principale) e della distinzione box /
-- posto auto. Nessuna modifica eseguita in quel caso.

BEGIN;

DO $do$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'properties' AND column_name = 'is_pertinenza')
       AND EXISTS (SELECT 1 FROM properties WHERE is_pertinenza OR pertinenza_kind IS NOT NULL) THEN
        RAISE EXCEPTION '089 down: esistono schede marcate come pertinenza; nessuna modifica eseguita';
    END IF;
END
$do$;

DROP INDEX IF EXISTS idx_properties_building_pertinenze;
ALTER TABLE properties DROP CONSTRAINT IF EXISTS properties_pertinenza_kind_chk;
ALTER TABLE properties DROP COLUMN IF EXISTS pertinenza_kind;
ALTER TABLE properties DROP COLUMN IF EXISTS is_pertinenza;

COMMIT;
