-- STIMA360 ZONA-3 (C1): verifica ESPLICITA della posizione di un IMMOBILE
-- INDIPENDENTE (villa, casa indipendente, negozio o locale autonomo: una
-- `properties` senza edificio collegato), esattamente come la 102 per gli
-- edifici.
--
-- Additiva. Nessun backfill: le coordinate gia' presenti in `properties`
-- restano NON verificate (position_verified_at NULL) e nessuna coordinata
-- cambia. Nessun dato immobiliare viene letto o riscritto.
--
--   1. `properties.position_verified_at` (TIMESTAMPTZ) e
--      `properties.position_verified_by_user_id` (operator_users.id, ON DELETE
--      SET NULL come `deleted_by_user_id` della 085 e la 102): l'ULTIMA
--      verifica, chi e quando. Nessuno storico (stessa decisione D4 di T4).
--
--   2. SOLO gli immobili senza edificio: un'unita' che appartiene a una
--      palazzina (`building_id` NOT NULL) e' posizionata ATTRAVERSO il suo
--      edificio (102) e non ha mai una verifica propria (niente marker doppi).
--      Se un immobile verificato viene collegato a un edificio, la sua
--      verifica decade da sola (la posizione e' quella dell'edificio).
--
--   3. `properties_position_verification()` - la sola porta della verifica,
--      stesso modello di sicurezza della 102:
--
--      * la verifica si scrive SOLO dentro la transazione che ha acceso il
--        segnale `stima360.property_position_confirm` = id dell'immobile
--        (il modulo di conferma di C2, contesto autenticato). La data e'
--        quella del SERVER (now()); l'operatore deve esserci; le coordinate
--        devono esserci ed essere nell'intervallo; l'immobile deve essere
--        senza edificio. Il segnale vale per UNA riga e si spegne appena usato;
--      * senza segnale nessuno scrive i due campi (POSITION_VERIFICATION_RESERVED):
--        ne' un INSERT (nemmeno con il segnale: un immobile non nasce mai
--        verificato), ne' una PATCH generica, ne' un'importazione, ne' uno
--        script. Unica eccezione: il SET NULL dell'operatore cancellato
--        (azione della FK);
--      * coordinate cambiate DAVVERO (valore numerico diverso) fuori dalla
--        conferma -> la verifica decade. Un cambio d'indirizzo, di prezzo o di
--        qualunque altro campo non la tocca: il trigger ascolta SOLO le
--        colonne che contano, i normali salvataggi non lo incontrano.
--
-- Stesso schema del segnale di transazione della 100 e della 102. Nessuna
-- cancellazione, nessun dato delle unita', del censimento o delle stime
-- toccato; `buildings` e la 102 restano identiche.
--
-- Transaction ownership: the runner owns the UP transaction (version >= 027),
-- so this file carries no BEGIN/COMMIT. See
-- docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.

-- ===========================================================================
-- 1. colonne e vincoli
-- ===========================================================================
ALTER TABLE properties ADD COLUMN IF NOT EXISTS position_verified_at TIMESTAMPTZ;
ALTER TABLE properties ADD COLUMN IF NOT EXISTS position_verified_by_user_id BIGINT;

DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'properties_position_verified_by_user_fk'
                      AND conrelid = 'properties'::regclass) THEN
        ALTER TABLE properties ADD CONSTRAINT properties_position_verified_by_user_fk
            FOREIGN KEY (position_verified_by_user_id) REFERENCES operator_users(id) ON DELETE SET NULL;
    END IF;
    -- una verifica senza coordinate non esiste; una verifica su un'unita' di palazzina
    -- neppure (la posizione e' dell'edificio); un operatore senza verifica neppure
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'properties_position_verified_chk'
                      AND conrelid = 'properties'::regclass) THEN
        ALTER TABLE properties ADD CONSTRAINT properties_position_verified_chk CHECK (
            (position_verified_at IS NULL OR (latitude IS NOT NULL AND longitude IS NOT NULL AND building_id IS NULL))
            AND (position_verified_by_user_id IS NULL OR position_verified_at IS NOT NULL));
    END IF;
END
$do$;

-- ===========================================================================
-- 2. la porta della verifica
-- ===========================================================================
CREATE OR REPLACE FUNCTION properties_position_verification() RETURNS trigger AS $fn$
DECLARE
    v_conferma BOOLEAN := coalesce(current_setting('stima360.property_position_confirm', true), '') = NEW.id::text;
BEGIN
    IF v_conferma THEN
        -- il segnale vale per UNA riga, UNA volta
        PERFORM set_config('stima360.property_position_confirm', '', true);
    END IF;

    IF TG_OP = 'INSERT' THEN
        -- creare un immobile non lo verifica mai, nemmeno con la conferma
        IF NEW.position_verified_at IS NOT NULL OR NEW.position_verified_by_user_id IS NOT NULL THEN
            RAISE EXCEPTION 'POSITION_VERIFICATION_RESERVED: a property is never created verified';
        END IF;
        RETURN NEW;
    END IF;

    IF v_conferma THEN
        IF NEW.building_id IS NOT NULL THEN
            RAISE EXCEPTION 'POSITION_NOT_INDEPENDENT: property % belongs to building %, its position is the building''s',
                NEW.id, NEW.building_id;
        END IF;
        IF NEW.latitude IS NULL OR NEW.longitude IS NULL
           OR NEW.latitude < -90 OR NEW.latitude > 90 OR NEW.longitude < -180 OR NEW.longitude > 180
           OR (NEW.latitude = 0 AND NEW.longitude = 0) THEN
            RAISE EXCEPTION 'POSITION_INVALID: property % has no valid coordinates to verify', NEW.id;
        END IF;
        IF NEW.position_verified_by_user_id IS NULL THEN
            RAISE EXCEPTION 'POSITION_VERIFICATION_RESERVED: the verifying operator is required';
        END IF;
        NEW.position_verified_at := now();
        RETURN NEW;
    END IF;

    -- senza conferma i campi della verifica non si scrivono; l'unica eccezione e'
    -- l'azione SET NULL della FK quando l'operatore viene cancellato
    IF NEW.position_verified_at IS DISTINCT FROM OLD.position_verified_at
       OR (NEW.position_verified_by_user_id IS DISTINCT FROM OLD.position_verified_by_user_id
           AND NEW.position_verified_by_user_id IS NOT NULL) THEN
        RAISE EXCEPTION 'POSITION_VERIFICATION_RESERVED: property % position can be verified only by confirmation', NEW.id;
    END IF;

    -- coordinate cambiate davvero fuori dalla conferma, oppure immobile collegato a un
    -- edificio (la posizione diventa quella dell'edificio): la verifica decade
    IF NEW.latitude IS DISTINCT FROM OLD.latitude OR NEW.longitude IS DISTINCT FROM OLD.longitude
       OR (NEW.building_id IS NOT NULL AND NEW.building_id IS DISTINCT FROM OLD.building_id) THEN
        NEW.position_verified_at := NULL;
        NEW.position_verified_by_user_id := NULL;
    END IF;
    RETURN NEW;
END
$fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_properties_position_verification ON properties;
CREATE TRIGGER trg_properties_position_verification
    BEFORE INSERT OR UPDATE OF latitude, longitude, building_id, position_verified_at, position_verified_by_user_id
    ON properties
    FOR EACH ROW EXECUTE FUNCTION properties_position_verification();
