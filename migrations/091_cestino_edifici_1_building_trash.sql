-- CESTINO-EDIFICI-1 (FASE G): Cestino degli EDIFICI (palazzine contenitore),
-- sullo stesso impianto del Cestino Immobili (085/086) e Contatti (090).
-- Additiva, nessun backfill, nessuna riga esistente cambia valore.
--
--   1. `buildings.deleted_at` / `deleted_by_user_id` / `deleted_reason`
--      (NULLABLE), stessi motivi e stessa coerenza di `properties` (085) e
--      `contacts` (090). Un edificio e' "nel Cestino" quando `deleted_at` e'
--      valorizzato. L'id resta lo stesso. La palazzina contenitore resta
--      distinta dall'immobile «intero stabile» (`properties.whole_building`),
--      che e' un immobile e ha il suo Cestino.
--      `uq_buildings_client_request` resta su TUTTE le righe: un retry della
--      creazione con la chiave di un edificio nel Cestino non crea un doppione
--      (il servizio risponde 409 BUILDING_IN_TRASH) e il ripristino non ha
--      conflitti di unicita' possibili.
--
--   2. `record_lifecycle_events` ammette anche `entity_type = 'building'`.
--
--   3. Guardie:
--      * `cestino_edifici_unit_guard()` su `properties` (INSERT, o UPDATE che
--        CAMBIA `building_id`): nessuna unita' nuova verso un edificio nel
--        Cestino. L'edificio si legge FOR SHARE, lo stesso lock del trigger
--        della 083 (`properties_links_integrity`): uno spostamento nel Cestino
--        concorrente (FOR UPDATE, o la sua UPDATE) e un collegamento si
--        serializzano, e chi arriva dopo vede lo stato dell'altro.
--      * `cestino_edifici_freeze()` su `buildings`:
--          - lo spostamento nel Cestino (deleted_at da NULL a valore) e'
--            rifiutato se QUALUNQUE riga di `properties` punta all'edificio
--            (attive, archiviate, pertinenze, «intero stabile», unita' nel
--            Cestino Immobili): devono poter essere ripristinate nel loro
--            edificio. Nessuna unita' viene scollegata o spostata;
--          - la riga nel Cestino non cambia (ammessi: ripristino, l'azione
--            della FK `deleted_by_user_id` ON DELETE SET NULL, `updated_at`).
--      Il rifiuto porta il codice nel messaggio (`BUILDING_IN_TRASH: ...`,
--      `BUILDING_HAS_UNITS: ...`); `core.database.core_cursor` traduce il
--      primo nel 409 BUILDING_IN_TRASH.
--
-- Le funzioni leggono `deleted_at` via to_jsonb (stesso idioma della 086/090).
--
-- Transaction ownership: the runner owns the UP transaction (version >= 027),
-- so this file carries no BEGIN/COMMIT. See
-- docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.

-- ===========================================================================
-- 1. buildings: stato Cestino
-- ===========================================================================
ALTER TABLE buildings ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE buildings ADD COLUMN IF NOT EXISTS deleted_by_user_id BIGINT;
ALTER TABLE buildings ADD COLUMN IF NOT EXISTS deleted_reason VARCHAR(30);

DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'buildings_deleted_by_user_fk'
                      AND conrelid = 'buildings'::regclass) THEN
        ALTER TABLE buildings ADD CONSTRAINT buildings_deleted_by_user_fk
            FOREIGN KEY (deleted_by_user_id) REFERENCES operator_users(id) ON DELETE SET NULL;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'buildings_deleted_reason_chk'
                      AND conrelid = 'buildings'::regclass) THEN
        ALTER TABLE buildings ADD CONSTRAINT buildings_deleted_reason_chk CHECK (
            deleted_reason IS NULL OR deleted_reason IN (
                'created_by_mistake', 'duplicate', 'invalid_data', 'test_record', 'other'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'buildings_deleted_state_chk'
                      AND conrelid = 'buildings'::regclass) THEN
        ALTER TABLE buildings ADD CONSTRAINT buildings_deleted_state_chk CHECK (
            (deleted_at IS NULL AND deleted_by_user_id IS NULL AND deleted_reason IS NULL)
            OR (deleted_at IS NOT NULL AND deleted_reason IS NOT NULL));
    END IF;
END
$do$;

CREATE INDEX IF NOT EXISTS idx_buildings_trash
    ON buildings (agency_id, deleted_at DESC)
    WHERE deleted_at IS NOT NULL;

-- ===========================================================================
-- 2. registro del ciclo di vita: anche gli edifici
-- ===========================================================================
ALTER TABLE record_lifecycle_events DROP CONSTRAINT IF EXISTS record_lifecycle_events_entity_chk;
ALTER TABLE record_lifecycle_events ADD CONSTRAINT record_lifecycle_events_entity_chk
    CHECK (entity_type IN ('property', 'contact', 'building'));

-- ===========================================================================
-- 3. guardie
-- ===========================================================================
CREATE OR REPLACE FUNCTION cestino_edifici_unit_guard() RETURNS trigger AS $fn$
DECLARE
    v_nel_cestino BOOLEAN;
BEGIN
    IF NEW.building_id IS NULL THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.building_id IS NOT DISTINCT FROM NEW.building_id THEN
        RETURN NEW;                                   -- collegamento esistente: intatto
    END IF;
    SELECT (to_jsonb(b) ->> 'deleted_at') IS NOT NULL INTO v_nel_cestino
      FROM buildings b WHERE b.id = NEW.building_id FOR SHARE;
    IF v_nel_cestino THEN
        RAISE EXCEPTION 'BUILDING_IN_TRASH: building % is in the trash (new unit refused)', NEW.building_id;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION cestino_edifici_freeze() RETURNS trigger AS $fn$
DECLARE
    v_ammessi TEXT[] := ARRAY['deleted_by_user_id', 'updated_at'];
BEGIN
    -- spostamento nel Cestino: solo un edificio senza righe collegate
    IF (to_jsonb(OLD) ->> 'deleted_at') IS NULL AND (to_jsonb(NEW) ->> 'deleted_at') IS NOT NULL
       AND EXISTS (SELECT 1 FROM properties p WHERE p.building_id = OLD.id) THEN
        RAISE EXCEPTION 'BUILDING_HAS_UNITS: building % has linked units and cannot go to the trash', OLD.id;
    END IF;
    -- nel Cestino: congelato
    IF (to_jsonb(OLD) ->> 'deleted_at') IS NOT NULL
       AND (to_jsonb(NEW) ->> 'deleted_at') IS NOT NULL
       AND (to_jsonb(NEW) - v_ammessi) IS DISTINCT FROM (to_jsonb(OLD) - v_ammessi) THEN
        RAISE EXCEPTION 'BUILDING_IN_TRASH: building % is in the trash and cannot be modified', OLD.id;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_properties_building_trash_guard'
                      AND tgrelid = 'public.properties'::regclass) THEN
        CREATE TRIGGER trg_properties_building_trash_guard
            BEFORE INSERT OR UPDATE OF building_id ON properties
            FOR EACH ROW EXECUTE FUNCTION cestino_edifici_unit_guard();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_buildings_trash_freeze'
                      AND tgrelid = 'public.buildings'::regclass) THEN
        CREATE TRIGGER trg_buildings_trash_freeze
            BEFORE UPDATE ON buildings
            FOR EACH ROW EXECUTE FUNCTION cestino_edifici_freeze();
    END IF;
END
$do$;

-- ===========================================================================
-- 4. Sonda finale
-- ===========================================================================
DO $do$
BEGIN
    IF (SELECT count(*) FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'buildings' AND is_nullable = 'YES'
           AND column_name IN ('deleted_at', 'deleted_by_user_id', 'deleted_reason')) <> 3 THEN
        RAISE EXCEPTION 'CESTINO-EDIFICI 091: buildings.deleted_* missing or NOT NULL';
    END IF;
    IF EXISTS (SELECT 1 FROM buildings WHERE deleted_at IS NOT NULL) THEN
        RAISE EXCEPTION 'CESTINO-EDIFICI 091: a pre-existing building was moved to the trash by the migration';
    END IF;
    IF (SELECT count(*) FROM pg_trigger
         WHERE tgname IN ('trg_properties_building_trash_guard', 'trg_buildings_trash_freeze')) <> 2 THEN
        RAISE EXCEPTION 'CESTINO-EDIFICI 091: guardie mancanti';
    END IF;
END
$do$;
