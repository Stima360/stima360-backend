-- CENSIMENTO-2: composizione dichiarata degli edifici e provenienza dei dati
-- delle unita' (superficie, categoria catastale).
--
-- Additiva. Dieci colonne NULLABLE, nessun default, NESSUN backfill: nessuna
-- riga esistente cambia valore. Le categorie gia' presenti restano con
-- provenienza «non indicata» (NULL, decisione D8).
--
-- Transaction ownership: the runner owns the UP transaction (version >= 027),
-- so this file carries no BEGIN/COMMIT. See
-- docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.
--
-- properties
--   surface_source             provenienza di `surface_sqm`
--   cadastral_category_source  provenienza di `cadastral_category`
--       'estimated' | 'declared' | 'verified'; NULL = non indicata.
--       Una provenienza senza il dato non esiste (CHECK).
--       'verified' solo con una FONTE CATASTALE EFFETTIVAMENTE CONSULTATA E
--       REGISTRATA, non per la sola compilazione dei campi:
--         * l'identita' catastale dell'unita' (comune catastale, foglio,
--           particella, subalterno; la sezione e' facoltativa);
--         * la fonte: tipo di documento (cadastral_evidence_type: visura,
--           planimetria, atto, altro) e data della consultazione
--           (cadastral_evidence_date); riferimento facoltativo
--           (cadastral_evidence_ref, es. protocollo). Nessun allegato
--           obbligatorio;
--         * l'identita' a cui la fonte si riferisce
--           (cadastral_evidence_identity), scritta dal trigger al momento
--           della verifica.
--   La provenienza vale per QUEL valore. Se un aggiornamento cambia il valore
--   SENZA riscrivere nella stessa istruzione la sua provenienza (nuova
--   conferma, anche con lo stesso valore di prima), la provenienza torna
--   «non indicata». Se cambiano i riferimenti catastali che identificano
--   l'unita', una «verificata» non riconfermata nella stessa istruzione torna
--   «non indicata» (da rivalutare e riconfermare); le provenienze stimate o
--   dichiarate non dipendono dalla verifica e restano. La fonte registrata
--   resta come storico: la sua identita' diversa da quella attuale dice
--   «verifica da riconfermare». Le modifiche ad altri campi non toccano nulla.
--   La conferma si riconosce dal trigger BEFORE UPDATE OF <colonna della
--   provenienza>, che scatta solo quando la colonna e' nella lista SET
--   dell'istruzione; l'ordine alfabetico dei trigger della stessa riga lo fa
--   girare prima di quello che decide (trg_properties_z_census_source), che
--   gira a sua volta dopo la normalizzazione catastale della 083 (un valore
--   rimandato identico non conta come cambiato). Mai promossa, mai dedotta.
--
-- buildings: composizione dichiarata (D7), NULL = non nota («Non so»), mai 0
--   units_declared_residential  abitazioni
--   units_declared_commercial   negozi, uffici, locali commerciali
--   units_declared_other        altre unita' principali (box e cantine
--                               autonomi non pertinenza, terreni, ...)
--   units_declared_pertinenze   pertinenze con subalterno proprio
--   Coerenza: con le quattro note e il totale `units_declared` noto, il totale
--   e' la loro somma. Bozza sempre salvabile: una sola classe non nota (o il
--   totale non noto) e il controllo di somma non si applica.

ALTER TABLE properties
  ADD COLUMN IF NOT EXISTS surface_source VARCHAR(10),
  ADD COLUMN IF NOT EXISTS cadastral_category_source VARCHAR(10),
  ADD COLUMN IF NOT EXISTS cadastral_evidence_type VARCHAR(20),
  ADD COLUMN IF NOT EXISTS cadastral_evidence_date DATE,
  ADD COLUMN IF NOT EXISTS cadastral_evidence_ref VARCHAR(120),
  ADD COLUMN IF NOT EXISTS cadastral_evidence_identity TEXT;

-- L'identita' catastale come chiave confrontabile (valori gia' normalizzati
-- dalla 083). Sezione non conosciuta e nessuna sezione coincidono: e' facoltativa.
CREATE OR REPLACE FUNCTION census_identity_key(belfiore TEXT, sezione TEXT, foglio TEXT, particella TEXT, sub TEXT)
RETURNS TEXT AS $fn$
    SELECT CASE WHEN nullif(btrim(belfiore), '') IS NULL OR nullif(btrim(foglio), '') IS NULL
                  OR nullif(btrim(particella), '') IS NULL OR nullif(btrim(sub), '') IS NULL THEN NULL
                ELSE btrim(belfiore) || '|' || coalesce(btrim(sezione), '') || '|' || btrim(foglio) || '|'
                     || btrim(particella) || '|' || btrim(sub) END
$fn$ LANGUAGE sql IMMUTABLE;

ALTER TABLE properties DROP CONSTRAINT IF EXISTS properties_cadastral_evidence_type_chk;
ALTER TABLE properties ADD CONSTRAINT properties_cadastral_evidence_type_chk CHECK (
    cadastral_evidence_type IS NULL OR cadastral_evidence_type IN ('visura', 'planimetria', 'atto', 'altro'));

ALTER TABLE properties DROP CONSTRAINT IF EXISTS properties_surface_source_chk;
ALTER TABLE properties ADD CONSTRAINT properties_surface_source_chk CHECK (
    surface_source IS NULL
    OR (surface_source IN ('estimated', 'declared', 'verified') AND surface_sqm IS NOT NULL));

ALTER TABLE properties DROP CONSTRAINT IF EXISTS properties_category_source_chk;
ALTER TABLE properties ADD CONSTRAINT properties_category_source_chk CHECK (
    cadastral_category_source IS NULL
    OR (cadastral_category_source IN ('estimated', 'declared', 'verified') AND cadastral_category IS NOT NULL));

ALTER TABLE properties DROP CONSTRAINT IF EXISTS properties_verified_source_evidence_chk;
ALTER TABLE properties ADD CONSTRAINT properties_verified_source_evidence_chk CHECK (
    (surface_source IS DISTINCT FROM 'verified' AND cadastral_category_source IS DISTINCT FROM 'verified')
    OR (census_identity_key(cadastral_municipality_code, cadastral_section, cadastral_sheet, cadastral_parcel,
                            cadastral_subunit) IS NOT NULL
        AND cadastral_evidence_type IS NOT NULL
        AND cadastral_evidence_date IS NOT NULL
        AND cadastral_evidence_identity = census_identity_key(cadastral_municipality_code, cadastral_section,
                                                              cadastral_sheet, cadastral_parcel, cadastral_subunit)));

-- La conferma esplicita della provenienza nell'istruzione corrente, per riga:
-- un'impostazione locale alla transazione con l'id della riga, letta e
-- azzerata subito dal trigger che decide (stessa riga, stessa istruzione).
CREATE OR REPLACE FUNCTION properties_census_source_mark() RETURNS trigger AS $fn$
BEGIN
    PERFORM set_config('stima360.census_source_' || TG_ARGV[0], NEW.id::text, true);
    RETURN NEW;
END
$fn$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION properties_census_source_follow() RETURNS trigger AS $fn$
DECLARE
    superficie_confermata BOOLEAN := coalesce(current_setting('stima360.census_source_surface', true), '') = NEW.id::text;
    categoria_confermata  BOOLEAN := coalesce(current_setting('stima360.census_source_category', true), '') = NEW.id::text;
    identita_nuova TEXT := census_identity_key(NEW.cadastral_municipality_code, NEW.cadastral_section,
                                               NEW.cadastral_sheet, NEW.cadastral_parcel, NEW.cadastral_subunit);
    identita_cambiata BOOLEAN := identita_nuova IS DISTINCT FROM census_identity_key(
        OLD.cadastral_municipality_code, OLD.cadastral_section, OLD.cadastral_sheet, OLD.cadastral_parcel,
        OLD.cadastral_subunit);
BEGIN
    PERFORM set_config('stima360.census_source_surface', '', true);
    PERFORM set_config('stima360.census_source_category', '', true);
    -- il valore cambia senza nuova conferma: la sua provenienza torna «non indicata»
    IF NEW.surface_sqm IS DISTINCT FROM OLD.surface_sqm AND NOT superficie_confermata THEN
        NEW.surface_source := NULL;
    END IF;
    IF NEW.cadastral_category IS DISTINCT FROM OLD.cadastral_category AND NOT categoria_confermata THEN
        NEW.cadastral_category_source := NULL;
    END IF;
    -- cambia l'identita' catastale: solo una «verificata» non riconfermata va rivalutata;
    -- stimata e dichiarata non dipendono dalla verifica e restano
    IF identita_cambiata THEN
        IF NEW.surface_source = 'verified' AND NOT superficie_confermata THEN
            NEW.surface_source := NULL;
        END IF;
        IF NEW.cadastral_category_source = 'verified' AND NOT categoria_confermata THEN
            NEW.cadastral_category_source := NULL;
        END IF;
    END IF;
    -- una verifica (ri)confermata in questa istruzione si lega all'identita' attuale
    IF (NEW.surface_source = 'verified' AND superficie_confermata)
       OR (NEW.cadastral_category_source = 'verified' AND categoria_confermata) THEN
        NEW.cadastral_evidence_identity := identita_nuova;
    END IF;
    RETURN NEW;
END
$fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_properties_y1_census_surface_source ON properties;
CREATE TRIGGER trg_properties_y1_census_surface_source
    BEFORE UPDATE OF surface_source ON properties
    FOR EACH ROW EXECUTE FUNCTION properties_census_source_mark('surface');

DROP TRIGGER IF EXISTS trg_properties_y2_census_category_source ON properties;
CREATE TRIGGER trg_properties_y2_census_category_source
    BEFORE UPDATE OF cadastral_category_source ON properties
    FOR EACH ROW EXECUTE FUNCTION properties_census_source_mark('category');

DROP TRIGGER IF EXISTS trg_properties_z_census_source ON properties;
CREATE TRIGGER trg_properties_z_census_source
    BEFORE UPDATE OF surface_sqm, surface_source, cadastral_category, cadastral_category_source,
                     cadastral_municipality_code, cadastral_section, cadastral_sheet, cadastral_parcel,
                     cadastral_subunit
    ON properties
    FOR EACH ROW EXECUTE FUNCTION properties_census_source_follow();

ALTER TABLE buildings
  ADD COLUMN IF NOT EXISTS units_declared_residential INTEGER,
  ADD COLUMN IF NOT EXISTS units_declared_commercial INTEGER,
  ADD COLUMN IF NOT EXISTS units_declared_other INTEGER,
  ADD COLUMN IF NOT EXISTS units_declared_pertinenze INTEGER;

ALTER TABLE buildings DROP CONSTRAINT IF EXISTS buildings_composition_nonneg_chk;
ALTER TABLE buildings ADD CONSTRAINT buildings_composition_nonneg_chk CHECK (
    (units_declared_residential IS NULL OR units_declared_residential >= 0)
    AND (units_declared_commercial IS NULL OR units_declared_commercial >= 0)
    AND (units_declared_other IS NULL OR units_declared_other >= 0)
    AND (units_declared_pertinenze IS NULL OR units_declared_pertinenze >= 0));

ALTER TABLE buildings DROP CONSTRAINT IF EXISTS buildings_composition_total_chk;
ALTER TABLE buildings ADD CONSTRAINT buildings_composition_total_chk CHECK (
    units_declared IS NULL
    OR units_declared_residential IS NULL OR units_declared_commercial IS NULL
    OR units_declared_other IS NULL OR units_declared_pertinenze IS NULL
    OR units_declared = units_declared_residential + units_declared_commercial
                        + units_declared_other + units_declared_pertinenze);
