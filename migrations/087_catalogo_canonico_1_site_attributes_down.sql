-- Down for 087. Toglie la provenienza dal sito, le colonne nuove e riporta il
-- CHECK dei tipi di accessorio a quello della 083.
--
-- ATTENZIONE: e' distruttiva per i dati scritti DOPO la 087 (valori del sito
-- nelle colonne nuove, provenienze, quantita'). Se esistono accessori dei
-- tipi nuovi (taverna, balcone, piscina, posto_moto, posto_bici) il CHECK
-- della 083 non si puo' ripristinare: il down si FERMA e non cancella nulla.

BEGIN;

DO $do$
BEGIN
    IF EXISTS (SELECT 1 FROM property_accessories
                WHERE kind IN ('taverna', 'balcone', 'piscina', 'posto_moto', 'posto_bici')) THEN
        RAISE EXCEPTION '087 down: esistono accessori dei tipi introdotti dalla 087; nessuna modifica eseguita';
    END IF;
END
$do$;

DROP TABLE IF EXISTS property_site_sources;
DROP FUNCTION IF EXISTS property_site_sources_scope();

ALTER TABLE property_accessories DROP CONSTRAINT IF EXISTS property_accessories_kind_chk;
ALTER TABLE property_accessories ADD CONSTRAINT property_accessories_kind_chk CHECK (kind IN
    ('cantina', 'soffitta', 'posto_auto', 'giardino', 'terrazzo', 'box', 'deposito', 'altro'));
ALTER TABLE property_accessories DROP CONSTRAINT IF EXISTS property_accessories_quantity_chk;
ALTER TABLE property_accessories DROP CONSTRAINT IF EXISTS property_accessories_source_chk;
ALTER TABLE property_accessories DROP COLUMN IF EXISTS quantity;
ALTER TABLE property_accessories DROP COLUMN IF EXISTS source;

ALTER TABLE properties DROP CONSTRAINT IF EXISTS properties_condo_fees_chk;
ALTER TABLE properties
    DROP COLUMN IF EXISTS sea_position,
    DROP COLUMN IF EXISTS sea_distance,
    DROP COLUMN IF EXISTS sea_band,
    DROP COLUMN IF EXISTS sea_barrier,
    DROP COLUMN IF EXISTS sea_view,
    DROP COLUMN IF EXISTS sea_view_detail,
    DROP COLUMN IF EXISTS heating,
    DROP COLUMN IF EXISTS air_conditioning,
    DROP COLUMN IF EXISTS air_conditioning_type,
    DROP COLUMN IF EXISTS exposure,
    DROP COLUMN IF EXISTS furnishing,
    DROP COLUMN IF EXISTS condo_fees,
    DROP COLUMN IF EXISTS other_features;

COMMIT;
