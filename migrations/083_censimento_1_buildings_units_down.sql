-- Down for 083. Toglie edifici, accessori e le colonne del censimento da
-- `properties`.
--
-- RIFIUTA SE PERDEREBBE DATI DEL CENSIMENTO: non solo righe di `buildings` o
-- `property_accessories`, ma anche i valori scritti sugli IMMOBILI (storici
-- compresi): collegamento a un edificio o a un'unita' principale, scala,
-- interno, identificativi e categoria catastale, record di censimento,
-- indirizzo ereditato, chiave di idempotenza. Stessa regola delle down di
-- 072, 076, 077, 078, 080, 081 e 082. Nulla viene cancellato in silenzio.

BEGIN;

DO $$
DECLARE
    v_edifici   INTEGER := 0;
    v_accessori INTEGER := 0;
    v_immobili  INTEGER := 0;
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'buildings') THEN
        EXECUTE 'SELECT count(*) FROM buildings' INTO v_edifici;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'property_accessories') THEN
        EXECUTE 'SELECT count(*) FROM property_accessories' INTO v_accessori;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'properties'
                  AND column_name = 'record_kind') THEN
        EXECUTE $q$SELECT count(*) FROM properties
                    WHERE building_id IS NOT NULL OR parent_property_id IS NOT NULL
                       OR whole_building OR staircase IS NOT NULL OR internal_number IS NOT NULL
                       OR cadastral_municipality_code IS NOT NULL OR cadastral_section IS NOT NULL
                       OR cadastral_sheet IS NOT NULL OR cadastral_parcel IS NOT NULL
                       OR cadastral_subunit IS NOT NULL OR cadastral_category IS NOT NULL
                       OR record_kind <> 'crm' OR address_inherited
                       OR client_request_id IS NOT NULL$q$ INTO v_immobili;
    END IF;
    IF v_edifici > 0 OR v_accessori > 0 OR v_immobili > 0 THEN
        RAISE EXCEPTION
            'CENSIMENTO-1 083 down: % building(s), % accessory(ies) and % propert(y/ies) carry census data that would be lost. Nothing has been changed.',
            v_edifici, v_accessori, v_immobili;
    END IF;
END
$$;

DROP TRIGGER IF EXISTS trg_property_accessories_client_request_immutable ON property_accessories;
DROP TRIGGER IF EXISTS trg_buildings_client_request_immutable ON buildings;
DROP TRIGGER IF EXISTS trg_properties_client_request_immutable ON properties;
DROP TRIGGER IF EXISTS trg_buildings_agency_guard ON buildings;
DROP TRIGGER IF EXISTS trg_properties_census_guard ON properties;
DROP TRIGGER IF EXISTS trg_properties_links_integrity ON properties;
DROP TRIGGER IF EXISTS trg_properties_cadastral_normalize ON properties;
DROP TRIGGER IF EXISTS trg_buildings_cadastral_normalize ON buildings;
DROP FUNCTION IF EXISTS client_request_immutable();
DROP FUNCTION IF EXISTS buildings_agency_guard();
DROP FUNCTION IF EXISTS properties_census_guard();
DROP FUNCTION IF EXISTS properties_links_integrity();
DROP FUNCTION IF EXISTS censimento_lock_nodes(BIGINT[]);
DROP FUNCTION IF EXISTS censimento_require_read_committed(TEXT);
DROP FUNCTION IF EXISTS properties_cadastral_normalize();
DROP FUNCTION IF EXISTS buildings_cadastral_normalize();
DROP FUNCTION IF EXISTS cadastral_norm(TEXT);

DROP TABLE IF EXISTS property_accessories;

DROP INDEX IF EXISTS uq_properties_cadastral_identity;
DROP INDEX IF EXISTS uq_properties_client_request;
DROP INDEX IF EXISTS idx_properties_record_kind;
DROP INDEX IF EXISTS idx_properties_parent;
DROP INDEX IF EXISTS idx_properties_building;

ALTER TABLE properties DROP CONSTRAINT IF EXISTS properties_client_request_chk;
ALTER TABLE properties DROP CONSTRAINT IF EXISTS properties_belfiore_chk;
ALTER TABLE properties DROP CONSTRAINT IF EXISTS properties_cadastral_category_chk;
ALTER TABLE properties DROP CONSTRAINT IF EXISTS properties_whole_building_chk;
ALTER TABLE properties DROP CONSTRAINT IF EXISTS properties_parent_not_self_chk;
ALTER TABLE properties DROP CONSTRAINT IF EXISTS properties_record_kind_chk;
ALTER TABLE properties DROP CONSTRAINT IF EXISTS properties_parent_property_id_fkey;
ALTER TABLE properties DROP CONSTRAINT IF EXISTS properties_building_id_fkey;

ALTER TABLE properties DROP COLUMN IF EXISTS client_request_fingerprint;
ALTER TABLE properties DROP COLUMN IF EXISTS client_request_id;
ALTER TABLE properties DROP COLUMN IF EXISTS address_inherited;
ALTER TABLE properties DROP COLUMN IF EXISTS record_kind;
ALTER TABLE properties DROP COLUMN IF EXISTS cadastral_category;
ALTER TABLE properties DROP COLUMN IF EXISTS cadastral_subunit;
ALTER TABLE properties DROP COLUMN IF EXISTS cadastral_parcel;
ALTER TABLE properties DROP COLUMN IF EXISTS cadastral_sheet;
ALTER TABLE properties DROP COLUMN IF EXISTS cadastral_section;
ALTER TABLE properties DROP COLUMN IF EXISTS cadastral_municipality_code;
ALTER TABLE properties DROP COLUMN IF EXISTS internal_number;
ALTER TABLE properties DROP COLUMN IF EXISTS staircase;
ALTER TABLE properties DROP COLUMN IF EXISTS whole_building;
ALTER TABLE properties DROP COLUMN IF EXISTS parent_property_id;
ALTER TABLE properties DROP COLUMN IF EXISTS building_id;

DROP TABLE IF EXISTS buildings;

COMMIT;
