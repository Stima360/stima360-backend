-- CESTINO-CONTATTI-1 (FASE F): Cestino dei CONTATTI, sullo stesso impianto
-- del Cestino Immobili (085 + 086). Additiva, nessun backfill, nessuna riga
-- esistente cambia valore.
--
--   1. `contacts.deleted_at` / `deleted_by_user_id` / `deleted_reason`
--      (NULLABLE), stessi motivi e stessa coerenza di `properties` (085). Un
--      contatto e' "nel Cestino" quando `deleted_at` e' valorizzato. L'id resta
--      lo stesso, nessuna relazione viene toccata. Il Cestino NON e' l'archivio
--      (`status = 'archived'`), ne' l'anonimizzazione.
--      Nessun indice UNIQUE su `contacts` (email e telefono ammettono
--      doppioni da sempre): il ripristino non ha conflitti di unicita' da
--      gestire nel database; i possibili doppioni li segnala il servizio.
--
--   2. `record_lifecycle_events` (085, append-only): ammette anche
--      `entity_type = 'contact'`. Solo il CHECK si allarga; le righe esistenti
--      restano valide.
--
--   3. Guardie nel database: un contatto nel Cestino e' CONGELATO.
--      * `cestino_contatti_guard(colonna)`: nessun NUOVO riferimento operativo
--        verso un contatto nel Cestino (INSERT, o UPDATE che CAMBIA la
--        colonna). Tabelle: leads, property_contacts, buy_requests,
--        owner_accounts, property_sale_sellers, acquisitions
--        (owner_contact_id), appointments, property_visits, contact_roles. Le
--        relazioni gia' esistenti restano intatte e modificabili come prima.
--        La lettura del contatto e' FOR KEY SHARE: un collegamento concorrente
--        attende lo spostamento nel Cestino (che blocca la riga FOR UPDATE) e
--        poi lo vede.
--      * `cestino_contatti_reopen_guard(colonna, stato, aperti...)`: un
--        processo CHIUSO di un contatto nel Cestino non si riapre (lead,
--        richiesta d'acquisto, acquisizione, appuntamento, accesso al
--        portale proprietario). Il resto delle righe storiche resta
--        modificabile (es. sincronizzazioni tecniche).
--        Fuori, di proposito: i REGISTRI (consent_events - una revoca del
--        consenso deve poter arrivare sempre -, communication_messages - il
--        dispatcher sopprime -, seller_timeline_events, followup_actions,
--        next_best_actions, seller_revival_suppressions, record_lifecycle_events),
--        attivita' e compiti (storico; i flussi di sistema li scrivono anche per
--        contatti gia' collegati, l'operatore e' fermato nel servizio), le
--        iscrizioni ai percorsi (il motore le fa nascere `stopped` per un
--        contatto non attivo: il Cestino conta come non attivo) e i controlli
--        delle automazioni (il Cestino stesso li mette in pausa).
--      * `cestino_contatti_freeze()` su `contacts`: la riga nel Cestino non
--        cambia. Ammessi: trash (deleted_at da NULL a valore), restore (da
--        valore a NULL), l'azione della FK `deleted_by_user_id` ON DELETE SET
--        NULL e la PROIEZIONE DEL CONSENSO (consent.repository.project: una
--        revoca da link di disiscrizione vale anche per un contatto nel
--        Cestino).
--      Il rifiuto porta il codice nel messaggio (`CONTACT_IN_TRASH: ...`);
--      `core.database.core_cursor` lo traduce nel 409 CONTACT_IN_TRASH.
--
-- Le funzioni leggono `deleted_at` via to_jsonb (stesso idioma della 086).
--
-- Transaction ownership: the runner owns the UP transaction (version >= 027),
-- so this file carries no BEGIN/COMMIT. See
-- docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.

-- ===========================================================================
-- 1. contacts: stato Cestino
-- ===========================================================================
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS deleted_by_user_id BIGINT;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS deleted_reason VARCHAR(30);

DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'contacts_deleted_by_user_fk'
                      AND conrelid = 'contacts'::regclass) THEN
        ALTER TABLE contacts ADD CONSTRAINT contacts_deleted_by_user_fk
            FOREIGN KEY (deleted_by_user_id) REFERENCES operator_users(id) ON DELETE SET NULL;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'contacts_deleted_reason_chk'
                      AND conrelid = 'contacts'::regclass) THEN
        ALTER TABLE contacts ADD CONSTRAINT contacts_deleted_reason_chk CHECK (
            deleted_reason IS NULL OR deleted_reason IN (
                'created_by_mistake', 'duplicate', 'invalid_data', 'test_record', 'other'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'contacts_deleted_state_chk'
                      AND conrelid = 'contacts'::regclass) THEN
        ALTER TABLE contacts ADD CONSTRAINT contacts_deleted_state_chk CHECK (
            (deleted_at IS NULL AND deleted_by_user_id IS NULL AND deleted_reason IS NULL)
            OR (deleted_at IS NOT NULL AND deleted_reason IS NOT NULL));
    END IF;
END
$do$;

CREATE INDEX IF NOT EXISTS idx_contacts_trash
    ON contacts (agency_id, deleted_at DESC)
    WHERE deleted_at IS NOT NULL;

-- ===========================================================================
-- 2. registro del ciclo di vita: anche i contatti
-- ===========================================================================
ALTER TABLE record_lifecycle_events DROP CONSTRAINT IF EXISTS record_lifecycle_events_entity_chk;
ALTER TABLE record_lifecycle_events ADD CONSTRAINT record_lifecycle_events_entity_chk
    CHECK (entity_type IN ('property', 'contact'));

-- ===========================================================================
-- 3. guardie
-- ===========================================================================
CREATE OR REPLACE FUNCTION cestino_contatti_guard() RETURNS trigger AS $fn$
DECLARE
    v_colonna TEXT := TG_ARGV[0];
    v_nuovo   BIGINT;
    v_nel_cestino BOOLEAN;
BEGIN
    v_nuovo := NULLIF(to_jsonb(NEW) ->> v_colonna, '')::BIGINT;
    IF v_nuovo IS NULL THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'UPDATE'
       AND NULLIF(to_jsonb(OLD) ->> v_colonna, '')::BIGINT IS NOT DISTINCT FROM v_nuovo THEN
        RETURN NEW;                                   -- relazione esistente: intatta
    END IF;
    -- FOR KEY SHARE: attende uno spostamento nel Cestino in corso (FOR UPDATE)
    -- e rilegge la riga aggiornata; nessun collegamento passa "in mezzo".
    SELECT (to_jsonb(c) ->> 'deleted_at') IS NOT NULL INTO v_nel_cestino
      FROM contacts c WHERE c.id = v_nuovo FOR KEY SHARE;
    IF v_nel_cestino THEN
        RAISE EXCEPTION 'CONTACT_IN_TRASH: contact % is in the trash (new %.% reference refused)',
            v_nuovo, TG_TABLE_NAME, v_colonna;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

-- Un processo CHIUSO di un contatto nel Cestino non si riapre (lead, richiesta
-- d'acquisto, acquisizione, accesso al portale). TG_ARGV: colonna del
-- contatto, colonna di stato, poi gli stati "aperti" della tabella.
CREATE OR REPLACE FUNCTION cestino_contatti_reopen_guard() RETURNS trigger AS $fn$
DECLARE
    v_colonna   TEXT := TG_ARGV[0];
    v_stato     TEXT := TG_ARGV[1];
    v_aperti    TEXT[] := TG_ARGV[2:];
    v_contatto  BIGINT;
    v_nel_cestino BOOLEAN;
BEGIN
    IF NOT ((to_jsonb(NEW) ->> v_stato) = ANY (v_aperti))
       OR (to_jsonb(OLD) ->> v_stato) = ANY (v_aperti) THEN
        RETURN NEW;                                   -- non e' una riapertura
    END IF;
    v_contatto := NULLIF(to_jsonb(NEW) ->> v_colonna, '')::BIGINT;
    IF v_contatto IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT (to_jsonb(c) ->> 'deleted_at') IS NOT NULL INTO v_nel_cestino
      FROM contacts c WHERE c.id = v_contatto FOR KEY SHARE;
    IF v_nel_cestino THEN
        RAISE EXCEPTION 'CONTACT_IN_TRASH: contact % is in the trash (%.% cannot reopen)',
            v_contatto, TG_TABLE_NAME, v_stato;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION cestino_contatti_freeze() RETURNS trigger AS $fn$
DECLARE
    v_ammessi TEXT[] := ARRAY['deleted_by_user_id', 'updated_at',
                              'marketing_consent', 'marketing_consent_at', 'marketing_revoked_at',
                              'marketing_consent_source', 'marketing_consent_notice_id',
                              'privacy_terms_accepted', 'privacy_terms_accepted_at', 'privacy_terms_revoked_at',
                              'privacy_terms_source', 'privacy_terms_notice_id'];
BEGIN
    IF (to_jsonb(OLD) ->> 'deleted_at') IS NOT NULL
       AND (to_jsonb(NEW) ->> 'deleted_at') IS NOT NULL
       AND (to_jsonb(NEW) - v_ammessi) IS DISTINCT FROM (to_jsonb(OLD) - v_ammessi) THEN
        RAISE EXCEPTION 'CONTACT_IN_TRASH: contact % is in the trash and cannot be modified', OLD.id;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

DO $do$
DECLARE
    v_diretti TEXT[][] := ARRAY[
        ['leads', 'contact_id'], ['property_contacts', 'contact_id'], ['buy_requests', 'contact_id'],
        ['owner_accounts', 'contact_id'], ['property_sale_sellers', 'contact_id'],
        ['acquisitions', 'owner_contact_id'], ['appointments', 'contact_id'],
        ['property_visits', 'contact_id'], ['contact_roles', 'contact_id']];
    v_riaperture TEXT[][] := ARRAY[
        ['leads', 'contact_id', 'status', 'open,paused'],
        ['buy_requests', 'contact_id', 'status', 'draft,active,paused'],
        ['acquisitions', 'owner_contact_id', 'status',
         'appointment_set,inspection_done,valuation_presented,mandate_negotiation'],
        ['appointments', 'contact_id', 'status', 'requested,scheduled,confirmed'],
        ['owner_accounts', 'contact_id', 'status', 'invited,active']];
    i INTEGER;
    v_trigger TEXT;
BEGIN
    FOR i IN 1 .. array_length(v_diretti, 1) LOOP
        CONTINUE WHEN to_regclass('public.' || v_diretti[i][1]) IS NULL;
        v_trigger := 'trg_' || v_diretti[i][1] || '_contact_trash_guard';
        IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = v_trigger
                          AND tgrelid = ('public.' || v_diretti[i][1])::regclass) THEN
            EXECUTE format('CREATE TRIGGER %I BEFORE INSERT OR UPDATE OF %I ON %I '
                           'FOR EACH ROW EXECUTE FUNCTION cestino_contatti_guard(%L)',
                           v_trigger, v_diretti[i][2], v_diretti[i][1], v_diretti[i][2]);
        END IF;
    END LOOP;
    -- riaperture: tabella, colonna del contatto, colonna di stato, stati aperti
    FOR i IN 1 .. array_length(v_riaperture, 1) LOOP
        CONTINUE WHEN to_regclass('public.' || v_riaperture[i][1]) IS NULL;
        v_trigger := 'trg_' || v_riaperture[i][1] || '_contact_trash_reopen';
        IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = v_trigger
                          AND tgrelid = ('public.' || v_riaperture[i][1])::regclass) THEN
            EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OF %I ON %I '
                           'FOR EACH ROW EXECUTE FUNCTION cestino_contatti_reopen_guard(%s)',
                           v_trigger, v_riaperture[i][3], v_riaperture[i][1],
                           (SELECT string_agg(quote_literal(x), ', ')
                              FROM unnest(ARRAY[v_riaperture[i][2], v_riaperture[i][3]]
                                          || string_to_array(v_riaperture[i][4], ',')) AS x));
        END IF;
    END LOOP;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_contacts_trash_freeze'
                      AND tgrelid = 'public.contacts'::regclass) THEN
        CREATE TRIGGER trg_contacts_trash_freeze
            BEFORE UPDATE ON contacts
            FOR EACH ROW EXECUTE FUNCTION cestino_contatti_freeze();
    END IF;
END
$do$;

-- ===========================================================================
-- 4. Sonda finale
-- ===========================================================================
DO $do$
DECLARE
    v_mancanti TEXT;
BEGIN
    IF (SELECT count(*) FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'contacts' AND is_nullable = 'YES'
           AND column_name IN ('deleted_at', 'deleted_by_user_id', 'deleted_reason')) <> 3 THEN
        RAISE EXCEPTION 'CESTINO-CONTATTI 090: contacts.deleted_* missing or NOT NULL';
    END IF;
    IF EXISTS (SELECT 1 FROM contacts WHERE deleted_at IS NOT NULL) THEN
        RAISE EXCEPTION 'CESTINO-CONTATTI 090: a pre-existing contact was moved to the trash by the migration';
    END IF;
    SELECT string_agg(t, ', ') INTO v_mancanti
      FROM unnest(ARRAY['leads', 'property_contacts', 'buy_requests', 'owner_accounts', 'property_sale_sellers',
                        'acquisitions', 'appointments', 'property_visits', 'contact_roles']) AS t
     WHERE to_regclass('public.' || t) IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM pg_trigger
                        WHERE tgname = 'trg_' || t || '_contact_trash_guard'
                          AND tgrelid = ('public.' || t)::regclass);
    IF v_mancanti IS NOT NULL THEN
        RAISE EXCEPTION 'CESTINO-CONTATTI 090: guardie mancanti su %', v_mancanti;
    END IF;
    SELECT string_agg(t, ', ') INTO v_mancanti
      FROM unnest(ARRAY['leads', 'buy_requests', 'acquisitions', 'appointments', 'owner_accounts']) AS t
     WHERE to_regclass('public.' || t) IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM pg_trigger
                        WHERE tgname = 'trg_' || t || '_contact_trash_reopen'
                          AND tgrelid = ('public.' || t)::regclass);
    IF v_mancanti IS NOT NULL THEN
        RAISE EXCEPTION 'CESTINO-CONTATTI 090: guardie di riapertura mancanti su %', v_mancanti;
    END IF;
END
$do$;
