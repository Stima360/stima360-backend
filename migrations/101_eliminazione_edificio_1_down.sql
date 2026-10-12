-- Down for 101. Toglie le richieste di eliminazione degli edifici, la guardia
-- del ripristino delle unita' e riporta `cestino_edifici_freeze()` e il CHECK
-- delle azioni del registro al testo della 091 / 085.
--
-- Si FERMA, senza modifiche, se:
--   * esiste anche una sola richiesta di eliminazione (e' storia: andrebbe persa);
--   * il registro contiene azioni introdotte dalla 101 (append-only: non si tolgono);
--   * un edificio nel Cestino ha unita' collegate (stato ammesso solo dalla 101).

BEGIN;

DO $do$
BEGIN
    IF to_regclass('public.building_deletion_requests') IS NOT NULL
       AND EXISTS (SELECT 1 FROM building_deletion_requests) THEN
        RAISE EXCEPTION '101 down: esistono richieste di eliminazione degli edifici; nessuna modifica eseguita';
    END IF;
    IF EXISTS (SELECT 1 FROM record_lifecycle_events WHERE action NOT IN ('trash', 'restore')) THEN
        RAISE EXCEPTION '101 down: il registro contiene eventi della 101; nessuna modifica eseguita';
    END IF;
    IF EXISTS (SELECT 1 FROM buildings b WHERE (to_jsonb(b) ->> 'deleted_at') IS NOT NULL
                  AND EXISTS (SELECT 1 FROM properties p WHERE p.building_id = b.id)) THEN
        RAISE EXCEPTION '101 down: un edificio nel Cestino ha unita'' collegate; nessuna modifica eseguita';
    END IF;
END
$do$;

DROP TRIGGER IF EXISTS trg_properties_unit_restore_guard ON properties;
DROP FUNCTION IF EXISTS eliminazione_edificio_unit_restore_guard();

-- il testo della 091
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

ALTER TABLE record_lifecycle_events DROP CONSTRAINT IF EXISTS record_lifecycle_events_action_chk;
ALTER TABLE record_lifecycle_events ADD CONSTRAINT record_lifecycle_events_action_chk
    CHECK (action IN ('trash', 'restore'));

DROP TABLE IF EXISTS building_deletion_requests;
DROP FUNCTION IF EXISTS building_deletion_requests_guard();

COMMIT;
