-- Down for 100. Toglie la composizione dichiarata degli edifici, la
-- provenienza di superficie e categoria delle unita' e la fonte catastale
-- registrata della verifica.
--
-- Si FERMA se un edificio porta una composizione o un'unita' porta una
-- provenienza: e' l'unica traccia di quei dati e andrebbe persa. Nessuna
-- modifica eseguita in quel caso.

BEGIN;

DO $do$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'properties' AND column_name = 'surface_source')
       AND EXISTS (SELECT 1 FROM properties
                    WHERE surface_source IS NOT NULL OR cadastral_category_source IS NOT NULL
                       OR cadastral_evidence_type IS NOT NULL OR cadastral_evidence_date IS NOT NULL
                       OR cadastral_evidence_ref IS NOT NULL OR cadastral_evidence_identity IS NOT NULL) THEN
        RAISE EXCEPTION '100 down: esistono unita'' con la provenienza dei dati; nessuna modifica eseguita';
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'buildings' AND column_name = 'units_declared_residential')
       AND EXISTS (SELECT 1 FROM buildings
                    WHERE units_declared_residential IS NOT NULL OR units_declared_commercial IS NOT NULL
                       OR units_declared_other IS NOT NULL OR units_declared_pertinenze IS NOT NULL) THEN
        RAISE EXCEPTION '100 down: esistono edifici con la composizione dichiarata; nessuna modifica eseguita';
    END IF;
END
$do$;

DROP TRIGGER IF EXISTS trg_properties_z_census_source ON properties;
DROP TRIGGER IF EXISTS trg_properties_y2_census_category_source ON properties;
DROP TRIGGER IF EXISTS trg_properties_y1_census_surface_source ON properties;
DROP FUNCTION IF EXISTS properties_census_source_follow();
DROP FUNCTION IF EXISTS properties_census_source_mark();
ALTER TABLE properties DROP CONSTRAINT IF EXISTS properties_verified_source_evidence_chk;
ALTER TABLE properties DROP CONSTRAINT IF EXISTS properties_cadastral_evidence_type_chk;
ALTER TABLE properties DROP CONSTRAINT IF EXISTS properties_category_source_chk;
ALTER TABLE properties DROP CONSTRAINT IF EXISTS properties_surface_source_chk;
ALTER TABLE properties DROP COLUMN IF EXISTS cadastral_category_source;
ALTER TABLE properties DROP COLUMN IF EXISTS surface_source;
ALTER TABLE properties DROP COLUMN IF EXISTS cadastral_evidence_identity;
ALTER TABLE properties DROP COLUMN IF EXISTS cadastral_evidence_ref;
ALTER TABLE properties DROP COLUMN IF EXISTS cadastral_evidence_date;
ALTER TABLE properties DROP COLUMN IF EXISTS cadastral_evidence_type;
DROP FUNCTION IF EXISTS census_identity_key(TEXT, TEXT, TEXT, TEXT, TEXT);
ALTER TABLE buildings DROP CONSTRAINT IF EXISTS buildings_composition_total_chk;
ALTER TABLE buildings DROP CONSTRAINT IF EXISTS buildings_composition_nonneg_chk;
ALTER TABLE buildings DROP COLUMN IF EXISTS units_declared_pertinenze;
ALTER TABLE buildings DROP COLUMN IF EXISTS units_declared_other;
ALTER TABLE buildings DROP COLUMN IF EXISTS units_declared_commercial;
ALTER TABLE buildings DROP COLUMN IF EXISTS units_declared_residential;

COMMIT;
