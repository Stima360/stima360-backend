-- Down for 091. Toglie il Cestino degli edifici: guardie, colonne, e riporta
-- il CHECK del registro a immobili e contatti (090).
--
-- Si FERMA se un edificio e' nel Cestino o se il registro contiene eventi di
-- edifici (append-only: non si riscrive). Nessuna modifica eseguita in quel caso.

BEGIN;

DO $do$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'buildings' AND column_name = 'deleted_at')
       AND EXISTS (SELECT 1 FROM buildings WHERE deleted_at IS NOT NULL) THEN
        RAISE EXCEPTION '091 down: esistono edifici nel Cestino; nessuna modifica eseguita';
    END IF;
    IF EXISTS (SELECT 1 FROM record_lifecycle_events WHERE entity_type = 'building') THEN
        RAISE EXCEPTION '091 down: il registro contiene eventi di edifici; nessuna modifica eseguita';
    END IF;
END
$do$;

DROP TRIGGER IF EXISTS trg_properties_building_trash_guard ON properties;
DROP TRIGGER IF EXISTS trg_buildings_trash_freeze ON buildings;
DROP FUNCTION IF EXISTS cestino_edifici_unit_guard();
DROP FUNCTION IF EXISTS cestino_edifici_freeze();

ALTER TABLE record_lifecycle_events DROP CONSTRAINT IF EXISTS record_lifecycle_events_entity_chk;
ALTER TABLE record_lifecycle_events ADD CONSTRAINT record_lifecycle_events_entity_chk
    CHECK (entity_type IN ('property', 'contact'));

DROP INDEX IF EXISTS idx_buildings_trash;
ALTER TABLE buildings DROP CONSTRAINT IF EXISTS buildings_deleted_state_chk;
ALTER TABLE buildings DROP CONSTRAINT IF EXISTS buildings_deleted_reason_chk;
ALTER TABLE buildings DROP CONSTRAINT IF EXISTS buildings_deleted_by_user_fk;
ALTER TABLE buildings DROP COLUMN IF EXISTS deleted_reason;
ALTER TABLE buildings DROP COLUMN IF EXISTS deleted_by_user_id;
ALTER TABLE buildings DROP COLUMN IF EXISTS deleted_at;

COMMIT;
