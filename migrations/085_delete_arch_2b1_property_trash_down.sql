-- Down for 085. Toglie il Cestino Immobili: registro, indice ricreato,
-- vincoli e colonne `deleted_*`; riporta `uq_properties_cadastral_identity`
-- e `uq_properties_client_request` alla definizione della 083.
--
-- RIFIUTA SE ESISTE ANCHE UN SOLO IMMOBILE NEL CESTINO O UN SOLO EVENTO NEL
-- REGISTRO: un immobile nel Cestino tornerebbe vivo in silenzio (e gli indici
-- della 083 potrebbero non ricrearsi su identita' catastali o client_request_id
-- ora duplicate), e il registro append-only andrebbe perso. Stessa regola
-- delle down di 072, 076-084.

BEGIN;

DO $$
DECLARE
    v_cestino INTEGER := 0;
    v_eventi  INTEGER := 0;
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'properties'
                  AND column_name = 'deleted_at') THEN
        EXECUTE 'SELECT count(*) FROM properties WHERE deleted_at IS NOT NULL' INTO v_cestino;
    END IF;
    IF to_regclass('public.record_lifecycle_events') IS NOT NULL THEN
        EXECUTE 'SELECT count(*) FROM record_lifecycle_events' INTO v_eventi;
    END IF;
    IF v_cestino > 0 OR v_eventi > 0 THEN
        RAISE EXCEPTION
            'DELETE-ARCH 085 down: % property(ies) in the trash and % lifecycle event(s). Nothing has been changed.',
            v_cestino, v_eventi;
    END IF;
END
$$;

DROP TRIGGER IF EXISTS trg_record_lifecycle_events_append_only ON record_lifecycle_events;
DROP TABLE IF EXISTS record_lifecycle_events;
DROP FUNCTION IF EXISTS record_lifecycle_events_append_only();

DROP INDEX IF EXISTS uq_properties_cadastral_identity;
CREATE UNIQUE INDEX uq_properties_cadastral_identity
    ON properties (agency_id, cadastral_municipality_code, cadastral_section,
                   cadastral_sheet, cadastral_parcel, cadastral_subunit)
    WHERE cadastral_municipality_code IS NOT NULL AND cadastral_section IS NOT NULL
      AND cadastral_sheet IS NOT NULL AND cadastral_parcel IS NOT NULL
      AND cadastral_subunit IS NOT NULL;

DROP INDEX IF EXISTS uq_properties_client_request;
CREATE UNIQUE INDEX uq_properties_client_request
    ON properties (agency_id, client_request_id) WHERE client_request_id IS NOT NULL;

DROP INDEX IF EXISTS idx_properties_trash;
ALTER TABLE properties DROP CONSTRAINT IF EXISTS properties_deleted_state_chk;
ALTER TABLE properties DROP CONSTRAINT IF EXISTS properties_deleted_reason_chk;
ALTER TABLE properties DROP CONSTRAINT IF EXISTS properties_deleted_by_user_fk;
ALTER TABLE properties DROP COLUMN IF EXISTS deleted_reason;
ALTER TABLE properties DROP COLUMN IF EXISTS deleted_by_user_id;
ALTER TABLE properties DROP COLUMN IF EXISTS deleted_at;

COMMIT;
