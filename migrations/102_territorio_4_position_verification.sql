-- STIMA360 TERRITORIO T4: verifica ESPLICITA della posizione di un edificio.
--
-- Additiva. Nessun backfill: le coordinate gia' presenti restano NON
-- verificate (position_verified_at NULL) e nessuna coordinata cambia.
--
--   1. `buildings.position_verified_at` (TIMESTAMPTZ) e
--      `buildings.position_verified_by_user_id` (operator_users.id, ON DELETE
--      SET NULL come `deleted_by_user_id` della 091): l'ULTIMA verifica, chi e
--      quando. Nessuno storico (decisione D4).
--
--   2. `buildings_position_verification()` - la sola porta della verifica:
--
--      * la verifica si scrive SOLO dentro la transazione che ha acceso il
--        segnale `stima360.building_position_confirm` = id dell'edificio
--        (property/building_position.py::confirm_position, contesto
--        autenticato). La data e' quella del SERVER (now()); l'operatore deve
--        esserci; le coordinate devono esserci ed essere nell'intervallo.
--        Il segnale vale per UNA riga e si spegne appena usato;
--      * senza segnale nessuno scrive i due campi (POSITION_VERIFICATION_RESERVED):
--        ne' un INSERT, ne' una PATCH generica, ne' uno script. Unica
--        eccezione: il SET NULL dell'operatore cancellato (azione della FK);
--      * coordinate cambiate DAVVERO (valore numerico diverso) fuori dalla
--        conferma -> la verifica decade (D2). Un cambio d'indirizzo non la
--        tocca.
--
-- Stesso schema del segnale di transazione della 100 (provenienze del
-- censimento). Nessuna cancellazione, nessun dato del censimento, delle unita'
-- o delle stime toccato.
--
-- Transaction ownership: the runner owns the UP transaction (version >= 027),
-- so this file carries no BEGIN/COMMIT. See
-- docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.

-- ===========================================================================
-- 1. colonne e vincoli
-- ===========================================================================
ALTER TABLE buildings ADD COLUMN IF NOT EXISTS position_verified_at TIMESTAMPTZ;
ALTER TABLE buildings ADD COLUMN IF NOT EXISTS position_verified_by_user_id BIGINT;

DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'buildings_position_verified_by_user_fk'
                      AND conrelid = 'buildings'::regclass) THEN
        ALTER TABLE buildings ADD CONSTRAINT buildings_position_verified_by_user_fk
            FOREIGN KEY (position_verified_by_user_id) REFERENCES operator_users(id) ON DELETE SET NULL;
    END IF;
    -- una verifica senza coordinate non esiste; un operatore senza verifica neppure
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'buildings_position_verified_chk'
                      AND conrelid = 'buildings'::regclass) THEN
        ALTER TABLE buildings ADD CONSTRAINT buildings_position_verified_chk CHECK (
            (position_verified_at IS NULL OR (latitude IS NOT NULL AND longitude IS NOT NULL))
            AND (position_verified_by_user_id IS NULL OR position_verified_at IS NOT NULL));
    END IF;
END
$do$;

-- ===========================================================================
-- 2. la porta della verifica
-- ===========================================================================
CREATE OR REPLACE FUNCTION buildings_position_verification() RETURNS trigger AS $fn$
DECLARE
    v_conferma BOOLEAN := coalesce(current_setting('stima360.building_position_confirm', true), '') = NEW.id::text;
BEGIN
    IF v_conferma THEN
        -- il segnale vale per UNA riga, UNA volta
        PERFORM set_config('stima360.building_position_confirm', '', true);
    END IF;

    IF TG_OP = 'INSERT' THEN
        -- creare un edificio non lo verifica mai, nemmeno con la conferma
        IF NEW.position_verified_at IS NOT NULL OR NEW.position_verified_by_user_id IS NOT NULL THEN
            RAISE EXCEPTION 'POSITION_VERIFICATION_RESERVED: a building is never created verified';
        END IF;
        RETURN NEW;
    END IF;

    IF v_conferma THEN
        IF NEW.latitude IS NULL OR NEW.longitude IS NULL
           OR NEW.latitude < -90 OR NEW.latitude > 90 OR NEW.longitude < -180 OR NEW.longitude > 180
           OR (NEW.latitude = 0 AND NEW.longitude = 0) THEN
            RAISE EXCEPTION 'POSITION_INVALID: building % has no valid coordinates to verify', NEW.id;
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
        RAISE EXCEPTION 'POSITION_VERIFICATION_RESERVED: building % position can be verified only by confirmation', NEW.id;
    END IF;

    -- coordinate cambiate davvero fuori dalla conferma: la verifica decade
    IF NEW.latitude IS DISTINCT FROM OLD.latitude OR NEW.longitude IS DISTINCT FROM OLD.longitude THEN
        NEW.position_verified_at := NULL;
        NEW.position_verified_by_user_id := NULL;
    END IF;
    RETURN NEW;
END
$fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_buildings_position_verification ON buildings;
CREATE TRIGGER trg_buildings_position_verification
    BEFORE INSERT OR UPDATE OF latitude, longitude, position_verified_at, position_verified_by_user_id
    ON buildings
    FOR EACH ROW EXECUTE FUNCTION buildings_position_verification();
