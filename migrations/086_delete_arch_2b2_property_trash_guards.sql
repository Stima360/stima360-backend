-- DELETE-ARCH Fase 2B2: guardie del Cestino Immobili nel database.
--
-- Un immobile nel Cestino (`properties.deleted_at` valorizzato, 085) e'
-- CONGELATO: non riceve nuovi riferimenti operativi, da qualunque percorso
-- arrivino (API, cron, import, SQL). Le relazioni gia' esistenti restano
-- intatte e modificabili come prima: le guardie scattano solo su un NUOVO
-- riferimento (INSERT, o UPDATE che CAMBIA la colonna verso un immobile nel
-- Cestino). Nessun dato cambia, nessun backfill.
--
-- Il rifiuto porta il codice nel messaggio: `PROPERTY_IN_TRASH: ...`.
-- `core.database.core_cursor` lo traduce nel 409 PROPERTY_IN_TRASH del
-- dominio; i percorsi applicativi principali rifiutano gia' prima
-- (core/property_trash.py), il database resta la garanzia.
--
--   1. `delete_arch_property_trash_guard(colonna)`: riferimento DIRETTO
--      (`<tabella>.<colonna>` -> properties.id). Tabelle: property_contacts,
--      property_leads, property_documents, property_photos, property_visits,
--      property_accessories, activities, appointments, acquisitions,
--      stima_acquisitions, matches, match_exclusions, property_sales,
--      buy_request_interactions, owner_property_access, owner_publications,
--      owner_feedback, owner_notifications.
--   2. `delete_arch_property_trash_guard_via(colonna, tabella, colonna_immobile)`:
--      riferimento INDIRETTO attraverso un figlio dell'immobile.
--      property_proposals (match_id -> matches.property_id),
--      owner_shared_documents (property_document_id -> property_documents),
--      owner_visit_feedback_publications (property_visit_id -> property_visits).
--   3. `delete_arch_properties_trash_freeze()` su `properties`:
--      * nessuna pertinenza nuova verso un genitore nel Cestino
--        (`parent_property_id`);
--      * la riga nel Cestino non cambia: ammessi solo trash (deleted_at da
--        NULL a valore), restore (da valore a NULL) e l'azione della FK
--        `deleted_by_user_id` ON DELETE SET NULL.
--
-- Fuori, di proposito: il ledger delle comunicazioni (ha una via di scrittura
-- sola, `communication/`), i registri tecnici (property_status_history,
-- property_price_history, match_runs, buy_request_history, owner_audit_log,
-- seller_timeline_events) e `record_lifecycle_events`.
--
-- Le funzioni leggono `deleted_at` via to_jsonb: restano eseguibili anche se
-- la 085 venisse tolta (down) prima della 086, senza rompere le scritture.
--
-- Transaction ownership: the runner owns the UP transaction (version >= 027),
-- so this file carries no BEGIN/COMMIT. See
-- docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.

CREATE OR REPLACE FUNCTION delete_arch_property_trash_guard() RETURNS trigger AS $fn$
DECLARE
    v_colonna TEXT := TG_ARGV[0];
    v_nuovo   BIGINT;
BEGIN
    v_nuovo := NULLIF(to_jsonb(NEW) ->> v_colonna, '')::BIGINT;
    IF v_nuovo IS NULL THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'UPDATE'
       AND NULLIF(to_jsonb(OLD) ->> v_colonna, '')::BIGINT IS NOT DISTINCT FROM v_nuovo THEN
        RETURN NEW;                                   -- relazione esistente: intatta
    END IF;
    IF EXISTS (SELECT 1 FROM properties p WHERE p.id = v_nuovo AND (to_jsonb(p) ->> 'deleted_at') IS NOT NULL) THEN
        RAISE EXCEPTION 'PROPERTY_IN_TRASH: property % is in the trash (new %.% reference refused)',
            v_nuovo, TG_TABLE_NAME, v_colonna;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION delete_arch_property_trash_guard_via() RETURNS trigger AS $fn$
DECLARE
    v_colonna   TEXT := TG_ARGV[0];
    v_tabella   TEXT := TG_ARGV[1];
    v_col_imm   TEXT := TG_ARGV[2];
    v_figlio    BIGINT;
    v_immobile  BIGINT;
BEGIN
    v_figlio := NULLIF(to_jsonb(NEW) ->> v_colonna, '')::BIGINT;
    IF v_figlio IS NULL THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'UPDATE'
       AND NULLIF(to_jsonb(OLD) ->> v_colonna, '')::BIGINT IS NOT DISTINCT FROM v_figlio THEN
        RETURN NEW;
    END IF;
    EXECUTE format('SELECT %I FROM %I WHERE id = $1', v_col_imm, v_tabella) INTO v_immobile USING v_figlio;
    IF v_immobile IS NOT NULL
       AND EXISTS (SELECT 1 FROM properties p WHERE p.id = v_immobile AND (to_jsonb(p) ->> 'deleted_at') IS NOT NULL) THEN
        RAISE EXCEPTION 'PROPERTY_IN_TRASH: property % is in the trash (new %.% reference refused)',
            v_immobile, TG_TABLE_NAME, v_colonna;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION delete_arch_properties_trash_freeze() RETURNS trigger AS $fn$
BEGIN
    IF NEW.parent_property_id IS NOT NULL
       AND (TG_OP = 'INSERT' OR NEW.parent_property_id IS DISTINCT FROM OLD.parent_property_id)
       AND EXISTS (SELECT 1 FROM properties p WHERE p.id = NEW.parent_property_id AND (to_jsonb(p) ->> 'deleted_at') IS NOT NULL) THEN
        RAISE EXCEPTION 'PROPERTY_IN_TRASH: property % is in the trash (new pertinenza refused)',
            NEW.parent_property_id;
    END IF;
    IF TG_OP = 'UPDATE' AND (to_jsonb(OLD) ->> 'deleted_at') IS NOT NULL
       AND (to_jsonb(NEW) ->> 'deleted_at') IS NOT NULL
       AND (to_jsonb(NEW) - 'deleted_by_user_id' - 'updated_at')
           IS DISTINCT FROM (to_jsonb(OLD) - 'deleted_by_user_id' - 'updated_at') THEN
        RAISE EXCEPTION 'PROPERTY_IN_TRASH: property % is in the trash and cannot be modified', OLD.id;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

DO $do$
DECLARE
    v_diretti TEXT[][] := ARRAY[
        ['property_contacts', 'property_id'], ['property_leads', 'property_id'],
        ['property_documents', 'property_id'], ['property_photos', 'property_id'],
        ['property_visits', 'property_id'], ['property_accessories', 'property_id'],
        ['activities', 'property_id'], ['appointments', 'property_id'],
        ['acquisitions', 'property_id'], ['stima_acquisitions', 'property_id'],
        ['matches', 'property_id'], ['match_exclusions', 'property_id'],
        ['property_sales', 'property_id'], ['buy_request_interactions', 'property_id'],
        ['owner_property_access', 'property_id'], ['owner_publications', 'property_id'],
        ['owner_feedback', 'property_id'], ['owner_notifications', 'property_id']];
    v_indiretti TEXT[][] := ARRAY[
        ['property_proposals', 'match_id', 'matches', 'property_id'],
        ['owner_shared_documents', 'property_document_id', 'property_documents', 'property_id'],
        ['owner_visit_feedback_publications', 'property_visit_id', 'property_visits', 'property_id']];
    i INTEGER;
    v_trigger TEXT;
BEGIN
    FOR i IN 1 .. array_length(v_diretti, 1) LOOP
        CONTINUE WHEN to_regclass('public.' || v_diretti[i][1]) IS NULL;
        v_trigger := 'trg_' || v_diretti[i][1] || '_property_trash_guard';
        IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = v_trigger
                          AND tgrelid = ('public.' || v_diretti[i][1])::regclass) THEN
            EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE OF %I ON %I '
                           'FOR EACH ROW EXECUTE FUNCTION delete_arch_property_trash_guard(%L)',
                           v_trigger, v_diretti[i][2], v_diretti[i][1], v_diretti[i][2]);
        END IF;
    END LOOP;
    FOR i IN 1 .. array_length(v_indiretti, 1) LOOP
        CONTINUE WHEN to_regclass('public.' || v_indiretti[i][1]) IS NULL;
        v_trigger := 'trg_' || v_indiretti[i][1] || '_property_trash_guard';
        IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = v_trigger
                          AND tgrelid = ('public.' || v_indiretti[i][1])::regclass) THEN
            EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE OF %I ON %I '
                           'FOR EACH ROW EXECUTE FUNCTION delete_arch_property_trash_guard_via(%L, %L, %L)',
                           v_trigger, v_indiretti[i][2], v_indiretti[i][1],
                           v_indiretti[i][2], v_indiretti[i][3], v_indiretti[i][4]);
        END IF;
    END LOOP;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_properties_trash_freeze'
                      AND tgrelid = 'public.properties'::regclass) THEN
        CREATE TRIGGER trg_properties_trash_freeze
            BEFORE INSERT OR UPDATE ON properties
            FOR EACH ROW EXECUTE FUNCTION delete_arch_properties_trash_freeze();
    END IF;
END
$do$;

-- Sonda finale: ogni tabella presente ha la sua guardia.
DO $do$
DECLARE
    v_mancanti TEXT;
BEGIN
    SELECT string_agg(t, ', ') INTO v_mancanti
      FROM unnest(ARRAY['property_contacts', 'property_leads', 'property_documents', 'property_photos',
                        'property_visits', 'property_accessories', 'activities', 'appointments',
                        'acquisitions', 'stima_acquisitions', 'matches', 'match_exclusions',
                        'property_sales', 'buy_request_interactions', 'owner_property_access',
                        'owner_publications', 'owner_feedback', 'owner_notifications',
                        'property_proposals', 'owner_shared_documents',
                        'owner_visit_feedback_publications']) AS t
     WHERE to_regclass('public.' || t) IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM pg_trigger
                        WHERE tgname = 'trg_' || t || '_property_trash_guard'
                          AND tgrelid = ('public.' || t)::regclass);
    IF v_mancanti IS NOT NULL THEN
        RAISE EXCEPTION 'DELETE-ARCH 086: guardie mancanti su %', v_mancanti;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_properties_trash_freeze') THEN
        RAISE EXCEPTION 'DELETE-ARCH 086: trg_properties_trash_freeze mancante';
    END IF;
END
$do$;
