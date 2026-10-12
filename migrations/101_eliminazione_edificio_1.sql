-- ELIMINAZIONE-EDIFICIO-1: eliminazione CONTROLLATA di un edificio (palazzina)
-- con tutte le sue unita', su richiesta di un operatore e con l'autorizzazione
-- del Super Admin (l'amministratore di piattaforma, `is_platform_admin`).
--
-- Additiva. Nessun backfill, nessuna riga esistente cambia valore.
--
--   1. `building_deletion_requests`: la richiesta e il suo esito (in attesa,
--      approvata, rifiutata, ritirata), chi chiede, chi decide e quando,
--      l'istantanea delle unita' coinvolte (id e se erano gia' nel Cestino),
--      le unita' spostate nel Cestino DA QUELLA approvazione, e poi l'eventuale
--      ripristino o cancellazione definitiva. `building_id` NON ha FK: la
--      richiesta e' storia e sopravvive alla cancellazione definitiva
--      dell'edificio (come `record_lifecycle_events.entity_id`). Coerenza di
--      agenzia ed esistenza verificate dal trigger all'inserimento.
--      Una sola richiesta in attesa per edificio, un solo gruppo «nel Cestino
--      da un'approvazione» per edificio (indici unici parziali). Le righe non
--      si cancellano mai e cambiano solo lungo le transizioni ammesse.
--
--   2. `record_lifecycle_events.action` ammette anche gli eventi della
--      richiesta (`deletion_requested`, `deletion_withdrawn`,
--      `deletion_approved`, `deletion_rejected`) e la cancellazione definitiva
--      (`purge`).
--
--   3. `cestino_edifici_freeze()` (091): un edificio con unita' collegate puo'
--      entrare nel Cestino SOLO se TUTTE le sue unita' sono gia' nel Cestino E
--      esiste per lui un'approvazione attiva (richiesta `approved`, non
--      ripristinata ne' cancellata). Le unita' non perdono mai `building_id`:
--      nessun orfano. Un edificio vuoto resta come prima. Il resto della 091
--      (riga congelata nel Cestino) e' invariato.
--
--   4. `eliminazione_edificio_unit_restore_guard()` su `properties`: un'unita'
--      non esce dal Cestino da sola mentre il suo edificio e' nel Cestino
--      (`BUILDING_IN_TRASH: ...`, che `core.database.core_cursor` traduce nel
--      409 BUILDING_IN_TRASH). Si ripristina prima l'edificio.
--
-- Nessuna cancellazione fisica qui: la cancellazione definitiva la decide il
-- Super Admin, dal Cestino, con seconda conferma e controllo delle dipendenze
-- (property/building_deletion.py).
--
-- Transaction ownership: the runner owns the UP transaction (version >= 027),
-- so this file carries no BEGIN/COMMIT. See
-- docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.

-- ===========================================================================
-- 1. le richieste di eliminazione
-- ===========================================================================
CREATE TABLE IF NOT EXISTS building_deletion_requests (
    id                        BIGSERIAL    PRIMARY KEY,
    agency_id                 BIGINT       NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    building_id               BIGINT       NOT NULL,
    building_label            TEXT         NOT NULL,
    status                    VARCHAR(12)  NOT NULL DEFAULT 'pending',
    reason_code               VARCHAR(30)  NOT NULL,
    request_note              TEXT,
    requested_by_user_id      BIGINT       REFERENCES operator_users(id) ON DELETE SET NULL,
    requested_by_role         VARCHAR(30),
    requested_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    units_snapshot            JSONB        NOT NULL DEFAULT '[]'::jsonb,
    units_fingerprint         VARCHAR(64)  NOT NULL,
    decided_by_user_id        BIGINT       REFERENCES operator_users(id) ON DELETE SET NULL,
    decided_at                TIMESTAMPTZ,
    decision_note             TEXT,
    trashed_unit_ids          BIGINT[],
    already_trashed_unit_ids  BIGINT[],
    restored_by_user_id       BIGINT       REFERENCES operator_users(id) ON DELETE SET NULL,
    restored_at               TIMESTAMPTZ,
    purged_by_user_id         BIGINT       REFERENCES operator_users(id) ON DELETE SET NULL,
    purged_at                 TIMESTAMPTZ,
    updated_at                TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT building_deletion_requests_status_chk
        CHECK (status IN ('pending', 'approved', 'rejected', 'withdrawn')),
    CONSTRAINT building_deletion_requests_reason_chk
        CHECK (reason_code IN ('created_by_mistake', 'duplicate', 'invalid_data', 'test_record', 'other')),
    CONSTRAINT building_deletion_requests_notes_chk
        CHECK ((request_note IS NULL OR char_length(request_note) <= 500)
               AND (decision_note IS NULL OR char_length(decision_note) <= 500)),
    CONSTRAINT building_deletion_requests_label_chk CHECK (btrim(building_label) <> ''),
    CONSTRAINT building_deletion_requests_snapshot_chk CHECK (jsonb_typeof(units_snapshot) = 'array'),
    CONSTRAINT building_deletion_requests_fingerprint_chk CHECK (units_fingerprint ~ '^[0-9a-f]{64}$'),
    CONSTRAINT building_deletion_requests_decision_chk
        CHECK ((status = 'pending') = (decided_at IS NULL)),
    CONSTRAINT building_deletion_requests_reject_note_chk
        CHECK (status <> 'rejected' OR btrim(coalesce(decision_note, '')) <> ''),
    CONSTRAINT building_deletion_requests_approved_chk
        CHECK ((status = 'approved') = (trashed_unit_ids IS NOT NULL AND already_trashed_unit_ids IS NOT NULL)),
    CONSTRAINT building_deletion_requests_outcome_chk
        CHECK ((restored_at IS NULL AND purged_at IS NULL)
               OR (status = 'approved' AND (restored_at IS NULL) <> (purged_at IS NULL)))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_building_deletion_requests_pending
    ON building_deletion_requests (building_id) WHERE status = 'pending';
CREATE UNIQUE INDEX IF NOT EXISTS uq_building_deletion_requests_in_trash
    ON building_deletion_requests (building_id)
    WHERE status = 'approved' AND restored_at IS NULL AND purged_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_building_deletion_requests_agency
    ON building_deletion_requests (agency_id, status, requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_building_deletion_requests_status
    ON building_deletion_requests (status, requested_at DESC);

CREATE OR REPLACE FUNCTION building_deletion_requests_guard() RETURNS trigger AS $fn$
DECLARE
    v_trovato BOOLEAN;
    v_nel_cestino BOOLEAN;
    v_fissi TEXT[] := ARRAY['id', 'agency_id', 'building_id', 'building_label', 'reason_code', 'request_note',
                            'requested_by_user_id', 'requested_by_role', 'requested_at', 'units_snapshot',
                            'units_fingerprint'];
    v_decisione TEXT[] := ARRAY['status', 'decided_by_user_id', 'decided_at', 'decision_note',
                                'trashed_unit_ids', 'already_trashed_unit_ids'];
    v_vecchio JSONB;
    v_nuovo JSONB;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'ELIMINAZIONE-EDIFICIO 101: building_deletion_requests is history (DELETE refused, id=%)', OLD.id;
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'pending' OR NEW.decided_at IS NOT NULL OR NEW.restored_at IS NOT NULL
           OR NEW.purged_at IS NOT NULL OR NEW.trashed_unit_ids IS NOT NULL THEN
            RAISE EXCEPTION 'ELIMINAZIONE-EDIFICIO 101: a deletion request is born pending';
        END IF;
        SELECT TRUE, (to_jsonb(b) ->> 'deleted_at') IS NOT NULL INTO v_trovato, v_nel_cestino
          FROM buildings b WHERE b.id = NEW.building_id AND b.agency_id = NEW.agency_id FOR SHARE;
        IF v_trovato IS NULL THEN
            RAISE EXCEPTION 'ELIMINAZIONE-EDIFICIO 101: building % does not belong to agency %',
                NEW.building_id, NEW.agency_id;
        END IF;
        IF v_nel_cestino THEN
            RAISE EXCEPTION 'BUILDING_IN_TRASH: building % is in the trash (deletion request refused)', NEW.building_id;
        END IF;
        RETURN NEW;
    END IF;
    -- UPDATE: i dati della richiesta non cambiano mai
    IF (SELECT jsonb_object_agg(k, to_jsonb(OLD) -> k) FROM unnest(v_fissi) k)
       IS DISTINCT FROM (SELECT jsonb_object_agg(k, to_jsonb(NEW) -> k) FROM unnest(v_fissi) k) THEN
        RAISE EXCEPTION 'ELIMINAZIONE-EDIFICIO 101: request % data are immutable', OLD.id;
    END IF;
    v_vecchio := to_jsonb(OLD) - 'updated_at';
    v_nuovo := to_jsonb(NEW) - 'updated_at';
    IF OLD.status = 'pending' THEN
        -- una sola decisione: approvata, rifiutata o ritirata; nient'altro cambia
        IF NEW.status = 'pending' AND v_nuovo IS DISTINCT FROM v_vecchio THEN
            RAISE EXCEPTION 'ELIMINAZIONE-EDIFICIO 101: a pending request changes only by its decision (id=%)', OLD.id;
        END IF;
        IF NEW.restored_at IS NOT NULL OR NEW.purged_at IS NOT NULL THEN
            RAISE EXCEPTION 'ELIMINAZIONE-EDIFICIO 101: request % is not approved', OLD.id;
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status IN ('rejected', 'withdrawn') THEN
        IF v_nuovo IS DISTINCT FROM v_vecchio THEN
            RAISE EXCEPTION 'ELIMINAZIONE-EDIFICIO 101: request % is closed (%)', OLD.id, OLD.status;
        END IF;
        RETURN NEW;
    END IF;
    -- approvata: la decisione resta; poi al piu' UN esito (ripristino o cancellazione definitiva)
    IF (SELECT jsonb_object_agg(k, v_vecchio -> k) FROM unnest(v_decisione) k)
       IS DISTINCT FROM (SELECT jsonb_object_agg(k, v_nuovo -> k) FROM unnest(v_decisione) k) THEN
        RAISE EXCEPTION 'ELIMINAZIONE-EDIFICIO 101: the decision of request % is final', OLD.id;
    END IF;
    IF (OLD.restored_at IS NOT NULL OR OLD.purged_at IS NOT NULL) AND v_nuovo IS DISTINCT FROM v_vecchio THEN
        RAISE EXCEPTION 'ELIMINAZIONE-EDIFICIO 101: request % already has its outcome', OLD.id;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_building_deletion_requests_guard'
                      AND tgrelid = 'public.building_deletion_requests'::regclass) THEN
        CREATE TRIGGER trg_building_deletion_requests_guard
            BEFORE INSERT OR UPDATE OR DELETE ON building_deletion_requests
            FOR EACH ROW EXECUTE FUNCTION building_deletion_requests_guard();
    END IF;
END
$do$;

-- ===========================================================================
-- 2. registro del ciclo di vita: eventi della richiesta e cancellazione definitiva
-- ===========================================================================
ALTER TABLE record_lifecycle_events DROP CONSTRAINT IF EXISTS record_lifecycle_events_action_chk;
ALTER TABLE record_lifecycle_events ADD CONSTRAINT record_lifecycle_events_action_chk
    CHECK (action IN ('trash', 'restore', 'purge', 'deletion_requested', 'deletion_withdrawn',
                      'deletion_approved', 'deletion_rejected'));

-- ===========================================================================
-- 3. edificio nel Cestino: con unita' solo da un'approvazione, e solo con tutte le unita' gia' nel Cestino
-- ===========================================================================
CREATE OR REPLACE FUNCTION cestino_edifici_freeze() RETURNS trigger AS $fn$
DECLARE
    v_ammessi TEXT[] := ARRAY['deleted_by_user_id', 'updated_at'];
BEGIN
    IF (to_jsonb(OLD) ->> 'deleted_at') IS NULL AND (to_jsonb(NEW) ->> 'deleted_at') IS NOT NULL THEN
        -- un'unita' fuori dal Cestino blocca sempre
        IF EXISTS (SELECT 1 FROM properties p WHERE p.building_id = OLD.id
                      AND (to_jsonb(p) ->> 'deleted_at') IS NULL) THEN
            RAISE EXCEPTION 'BUILDING_HAS_UNITS: building % has linked units and cannot go to the trash', OLD.id;
        END IF;
        -- ELIMINAZIONE-EDIFICIO-1 (101): unita' tutte nel Cestino, ma solo con un'approvazione attiva
        IF EXISTS (SELECT 1 FROM properties p WHERE p.building_id = OLD.id)
           AND NOT EXISTS (SELECT 1 FROM building_deletion_requests r
                            WHERE r.building_id = OLD.id AND r.agency_id = OLD.agency_id
                              AND r.status = 'approved' AND r.restored_at IS NULL AND r.purged_at IS NULL) THEN
            RAISE EXCEPTION 'BUILDING_HAS_UNITS: building % has linked units and cannot go to the trash', OLD.id;
        END IF;
    END IF;
    -- nel Cestino: congelato (invariato dalla 091)
    IF (to_jsonb(OLD) ->> 'deleted_at') IS NOT NULL
       AND (to_jsonb(NEW) ->> 'deleted_at') IS NOT NULL
       AND (to_jsonb(NEW) - v_ammessi) IS DISTINCT FROM (to_jsonb(OLD) - v_ammessi) THEN
        RAISE EXCEPTION 'BUILDING_IN_TRASH: building % is in the trash and cannot be modified', OLD.id;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

-- ===========================================================================
-- 4. un'unita' non esce dal Cestino da sola mentre il suo edificio e' nel Cestino
-- ===========================================================================
CREATE OR REPLACE FUNCTION eliminazione_edificio_unit_restore_guard() RETURNS trigger AS $fn$
DECLARE
    v_nel_cestino BOOLEAN;
BEGIN
    IF (to_jsonb(OLD) ->> 'deleted_at') IS NOT NULL AND (to_jsonb(NEW) ->> 'deleted_at') IS NULL
       AND NEW.building_id IS NOT NULL THEN
        SELECT (to_jsonb(b) ->> 'deleted_at') IS NOT NULL INTO v_nel_cestino
          FROM buildings b WHERE b.id = NEW.building_id FOR SHARE;
        IF v_nel_cestino THEN
            RAISE EXCEPTION 'BUILDING_IN_TRASH: building % is in the trash (unit % cannot leave the trash on its own)',
                NEW.building_id, NEW.id;
        END IF;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_properties_unit_restore_guard'
                      AND tgrelid = 'public.properties'::regclass) THEN
        CREATE TRIGGER trg_properties_unit_restore_guard
            BEFORE UPDATE ON properties
            FOR EACH ROW EXECUTE FUNCTION eliminazione_edificio_unit_restore_guard();
    END IF;
END
$do$;

-- ===========================================================================
-- 5. Sonda finale
-- ===========================================================================
DO $do$
BEGIN
    IF to_regclass('public.building_deletion_requests') IS NULL THEN
        RAISE EXCEPTION 'ELIMINAZIONE-EDIFICIO 101: building_deletion_requests mancante';
    END IF;
    IF (SELECT count(*) FROM pg_trigger
         WHERE tgname IN ('trg_building_deletion_requests_guard', 'trg_properties_unit_restore_guard',
                          'trg_buildings_trash_freeze', 'trg_properties_building_trash_guard')) <> 4 THEN
        RAISE EXCEPTION 'ELIMINAZIONE-EDIFICIO 101: guardie mancanti';
    END IF;
    IF EXISTS (SELECT 1 FROM building_deletion_requests) THEN
        RAISE EXCEPTION 'ELIMINAZIONE-EDIFICIO 101: righe inattese nella tabella appena creata';
    END IF;
END
$do$;
