-- DELETE-ARCH Fase 2B1: fondamenta del Cestino IMMOBILI.
--
-- Additiva, nessun backfill, nessuna riga esistente cambia. Architettura M2
-- scelta in Fase 2A (B+C+D): stato sulla tabella, filtri espliciti, guardie;
-- NIENTE rename di `properties`, NIENTE VIEW sostitutive.
--
--   1. `properties.deleted_at` / `deleted_by_user_id` / `deleted_reason`
--      (tutte NULLABLE). Un immobile e' "nel Cestino" quando `deleted_at` e'
--      valorizzato. L'id resta lo stesso, nessun figlio viene toccato.
--      `deleted_by_user_id` -> operator_users ON DELETE SET NULL.
--      Motivi: created_by_mistake, duplicate, invalid_data, test_record, other.
--      Coerenza: fuori dal Cestino i tre campi sono NULL; nel Cestino il
--      motivo c'e' sempre (l'autore puo' diventare NULL se l'operatore viene
--      eliminato, per la FK).
--
--   2. `uq_properties_cadastral_identity` e `uq_properties_client_request`
--      ricreati con `AND deleted_at IS NULL`: un immobile nel Cestino non
--      occupa ne' la sua identita' catastale ne' la sua `client_request_id`
--      (REVIEW 1, R1). Il ripristino verifica i due conflitti PRIMA
--      dell'UPDATE (409 RESTORE_CONFLICT, property/lifecycle.py).
--      NON cambia, di proposito: `properties.code` UNIQUE globale (002), il
--      codice resta occupato anche nel Cestino (D13).
--
--   3. `record_lifecycle_events`: registro minimo, APPEND-ONLY (trigger che
--      rifiuta UPDATE e DELETE). In 2B1 solo entity_type='property' e
--      action IN ('trash', 'restore'); `before_state` contiene i soli campi
--      di stato, mai uno snapshot dell'immobile.
--      `actor_user_id` ON DELETE RESTRICT (come acquisition_events): un
--      registro append-only non puo' essere riscritto da una FK SET NULL.
--
-- Transaction ownership: the runner owns the UP transaction (version >= 027),
-- so this file carries no BEGIN/COMMIT. See
-- docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.

-- ===========================================================================
-- 1. properties: stato Cestino
-- ===========================================================================
ALTER TABLE properties ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE properties ADD COLUMN IF NOT EXISTS deleted_by_user_id BIGINT;
ALTER TABLE properties ADD COLUMN IF NOT EXISTS deleted_reason VARCHAR(30);

DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'properties_deleted_by_user_fk'
                      AND conrelid = 'properties'::regclass) THEN
        ALTER TABLE properties ADD CONSTRAINT properties_deleted_by_user_fk
            FOREIGN KEY (deleted_by_user_id) REFERENCES operator_users(id) ON DELETE SET NULL;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'properties_deleted_reason_chk'
                      AND conrelid = 'properties'::regclass) THEN
        ALTER TABLE properties ADD CONSTRAINT properties_deleted_reason_chk CHECK (
            deleted_reason IS NULL OR deleted_reason IN (
                'created_by_mistake', 'duplicate', 'invalid_data', 'test_record', 'other'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'properties_deleted_state_chk'
                      AND conrelid = 'properties'::regclass) THEN
        ALTER TABLE properties ADD CONSTRAINT properties_deleted_state_chk CHECK (
            (deleted_at IS NULL AND deleted_by_user_id IS NULL AND deleted_reason IS NULL)
            OR (deleted_at IS NOT NULL AND deleted_reason IS NOT NULL));
    END IF;
END
$do$;

-- Il Cestino si legge per agenzia: indice parziale piccolo, nessun costo
-- sulle righe vive.
CREATE INDEX IF NOT EXISTS idx_properties_trash
    ON properties (agency_id, deleted_at DESC)
    WHERE deleted_at IS NOT NULL;

-- ===========================================================================
-- 2. identita' catastale e client_request_id: solo fuori dal Cestino
-- ===========================================================================
-- Stessi nomi degli indici della 083 (property/repository.py e
-- property/census.py traducono le violazioni per nome), stesse colonne e
-- stessi predicati, piu' `deleted_at IS NULL`.
DROP INDEX IF EXISTS uq_properties_client_request;
CREATE UNIQUE INDEX uq_properties_client_request
    ON properties (agency_id, client_request_id)
    WHERE client_request_id IS NOT NULL AND deleted_at IS NULL;

DROP INDEX IF EXISTS uq_properties_cadastral_identity;
CREATE UNIQUE INDEX uq_properties_cadastral_identity
    ON properties (agency_id, cadastral_municipality_code, cadastral_section,
                   cadastral_sheet, cadastral_parcel, cadastral_subunit)
    WHERE cadastral_municipality_code IS NOT NULL AND cadastral_section IS NOT NULL
      AND cadastral_sheet IS NOT NULL AND cadastral_parcel IS NOT NULL
      AND cadastral_subunit IS NOT NULL AND deleted_at IS NULL;

-- ===========================================================================
-- 3. registro del ciclo di vita (minimo, append-only)
-- ===========================================================================
CREATE TABLE IF NOT EXISTS record_lifecycle_events (
    id             BIGSERIAL    PRIMARY KEY,
    agency_id      BIGINT       NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    entity_type    VARCHAR(30)  NOT NULL,
    entity_id      BIGINT       NOT NULL,
    action         VARCHAR(30)  NOT NULL,
    reason_code    VARCHAR(30),
    note           TEXT,
    actor_user_id  BIGINT       REFERENCES operator_users(id) ON DELETE RESTRICT,
    occurred_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    before_state   JSONB        NOT NULL DEFAULT '{}'::jsonb,
    metadata       JSONB        NOT NULL DEFAULT '{}'::jsonb,

    CONSTRAINT record_lifecycle_events_entity_chk CHECK (entity_type IN ('property')),
    CONSTRAINT record_lifecycle_events_action_chk CHECK (action IN ('trash', 'restore')),
    CONSTRAINT record_lifecycle_events_note_chk CHECK (note IS NULL OR char_length(note) <= 500)
);

CREATE INDEX IF NOT EXISTS idx_record_lifecycle_events_entity
    ON record_lifecycle_events (agency_id, entity_type, entity_id, occurred_at DESC);

CREATE OR REPLACE FUNCTION record_lifecycle_events_append_only() RETURNS trigger AS $fn$
BEGIN
    RAISE EXCEPTION 'DELETE-ARCH 2B1: record_lifecycle_events is append-only (% refused, id=%)', TG_OP, OLD.id;
END;
$fn$ LANGUAGE plpgsql;

DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_record_lifecycle_events_append_only'
                      AND tgrelid = 'public.record_lifecycle_events'::regclass) THEN
        CREATE TRIGGER trg_record_lifecycle_events_append_only
            BEFORE UPDATE OR DELETE ON record_lifecycle_events
            FOR EACH ROW EXECUTE FUNCTION record_lifecycle_events_append_only();
    END IF;
END
$do$;

-- ===========================================================================
-- 4. Sonda finale
-- ===========================================================================
DO $do$
BEGIN
    IF (SELECT count(*) FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'properties' AND is_nullable = 'YES'
           AND column_name IN ('deleted_at', 'deleted_by_user_id', 'deleted_reason')) <> 3 THEN
        RAISE EXCEPTION 'DELETE-ARCH 085: properties.deleted_* missing or NOT NULL';
    END IF;
    IF EXISTS (SELECT 1 FROM properties WHERE deleted_at IS NOT NULL) THEN
        RAISE EXCEPTION 'DELETE-ARCH 085: a pre-existing property was moved to the trash by the migration';
    END IF;
    IF (SELECT pg_get_indexdef('uq_properties_cadastral_identity'::regclass)) NOT LIKE '%deleted_at IS NULL%' THEN
        RAISE EXCEPTION 'DELETE-ARCH 085: uq_properties_cadastral_identity not scoped to live rows';
    END IF;
    IF (SELECT pg_get_indexdef('uq_properties_client_request'::regclass)) NOT LIKE '%deleted_at IS NULL%' THEN
        RAISE EXCEPTION 'DELETE-ARCH 085: uq_properties_client_request not scoped to live rows';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_record_lifecycle_events_append_only') THEN
        RAISE EXCEPTION 'DELETE-ARCH 085: record_lifecycle_events is not append-only';
    END IF;
END
$do$;
