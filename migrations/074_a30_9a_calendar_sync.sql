-- 074 - A30-9A: le FONDAMENTA della sincronizzazione OUTBOUND Agenda ->
-- Google Calendar. Tre tabelle, due funzioni di guardia, due trigger, gli
-- indici. Nessuna rotta, nessuna chiamata a Google, nessun hook nell'Agenda:
-- quelli sono A30-9B.
--
-- ADDITIVA. Non tocca `appointments` ne' `appointment_events`: le colonne
-- `google_*` della 072 restano predisposte e INUTILIZZATE (decisione D2). Ogni
-- UPDATE di `appointments` incrementa `version`: la sincronizzazione non deve
-- mai farlo, quindi il suo stato vive qui, in tabelle proprie.
--
-- LE TRE TABELLE
--
--   calendar_connections       la connessione Google di UN operatore in UNA
--                              agenzia (D1/D10). Il refresh token e' cifrato
--                              dall'applicazione (Fernet, chiave in env): qui
--                              solo ciphertext + id della chiave. MAI access
--                              token, MAI client secret.
--   calendar_oauth_states      l'intento OAuth monouso: solo l'HASH dello
--                              state, il verifier PKCE cifrato, scadenza,
--                              uso singolo, legato ad agenzia + operatore.
--   appointment_calendar_sync  UNA riga per CATENA di appuntamenti (la radice
--                              e i suoi spostamenti): insieme mapping verso
--                              l'evento remoto, stato desiderato (generazioni),
--                              coda di riconciliazione con retry e claim.
--
-- TENANT
--
-- Ogni tabella porta `agency_id` NOT NULL e ogni riferimento e' COMPOSITO con
-- l'agenzia: una connessione appartiene a una membership (agency_id,
-- operator_user_id) di `agency_memberships`; una riga di sync punta a
-- `appointments (agency_id, id)` e a `calendar_connections (agency_id, id)`.
-- Un riferimento di un'altra agenzia e' irrappresentabile. Che la membership
-- sia ATTIVA quando la connessione diventa `connected` lo verifica il trigger
-- (stesso modello del trigger di guardia della 072 per l'agente).
--
-- La transazione e la riga in `schema_migrations` le gestisce il runner P26
-- (convenzione dalla 027): questo file non apre ne' chiude una transazione.

-- ---------------------------------------------------------------------------
-- calendar_connections
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS calendar_connections (
    id                        BIGSERIAL    PRIMARY KEY,
    agency_id                 BIGINT       NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    user_id                   BIGINT       NOT NULL,
    provider                  VARCHAR(20)  NOT NULL DEFAULT 'google',
    -- L'identita' dell'account remoto (il `sub` di Google). NULL finche'
    -- l'OAuth non e' completato; obbligatoria per `connected`.
    provider_subject          VARCHAR(255),
    -- D11: in A30-9 solo il calendario principale dell'account.
    calendar_id               VARCHAR(255) NOT NULL DEFAULT 'primary',
    -- Il refresh token CIFRATO dall'applicazione e l'id della chiave usata.
    refresh_token_ciphertext  BYTEA,
    token_key_id              VARCHAR(32),
    granted_scopes            TEXT[]       NOT NULL DEFAULT '{}',
    status                    VARCHAR(20)  NOT NULL DEFAULT 'connected',
    last_error_code           VARCHAR(64),
    connected_at              TIMESTAMPTZ,
    disconnected_at           TIMESTAMPTZ,
    created_at                TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT calendar_connections_agency_scope_unq UNIQUE (agency_id, id),
    CONSTRAINT calendar_connections_owner_unq UNIQUE (agency_id, user_id, provider),
    CONSTRAINT calendar_connections_membership_fk
        FOREIGN KEY (agency_id, user_id)
        REFERENCES agency_memberships (agency_id, operator_user_id) ON DELETE RESTRICT,
    CONSTRAINT calendar_connections_provider_chk CHECK (provider = 'google'),
    CONSTRAINT calendar_connections_status_chk
        CHECK (status IN ('connected', 'needs_reauth', 'disconnected')),
    CONSTRAINT calendar_connections_token_pair_chk
        CHECK ((refresh_token_ciphertext IS NULL) = (token_key_id IS NULL)),
    CONSTRAINT calendar_connections_key_id_chk
        CHECK (token_key_id IS NULL OR token_key_id ~ '^[A-Za-z0-9_-]{1,32}$'),
    CONSTRAINT calendar_connections_error_code_chk
        CHECK (last_error_code IS NULL OR last_error_code ~ '^[a-z0-9_]{1,64}$'),
    CONSTRAINT calendar_connections_calendar_chk CHECK (BTRIM(calendar_id) <> ''),
    -- `connected`: token, identita' remota e istante di collegamento presenti.
    CONSTRAINT calendar_connections_connected_chk CHECK (
        status <> 'connected' OR (
            refresh_token_ciphertext IS NOT NULL
            AND provider_subject IS NOT NULL
            AND connected_at IS NOT NULL
            AND disconnected_at IS NULL)),
    -- `disconnected`: il token locale e' rimosso (D14).
    CONSTRAINT calendar_connections_disconnected_chk CHECK (
        status <> 'disconnected' OR (
            refresh_token_ciphertext IS NULL AND disconnected_at IS NOT NULL))
);

-- ---------------------------------------------------------------------------
-- calendar_oauth_states
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS calendar_oauth_states (
    id                        BIGSERIAL    PRIMARY KEY,
    agency_id                 BIGINT       NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    user_id                   BIGINT       NOT NULL,
    provider                  VARCHAR(20)  NOT NULL DEFAULT 'google',
    -- SHA-256 esadecimale dello state: lo state in chiaro non si salva mai.
    state_hash                CHAR(64)     NOT NULL,
    code_verifier_ciphertext  BYTEA        NOT NULL,
    token_key_id              VARCHAR(32)  NOT NULL,
    expires_at                TIMESTAMPTZ  NOT NULL,
    used_at                   TIMESTAMPTZ,
    created_at                TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT calendar_oauth_states_hash_unq UNIQUE (state_hash),
    CONSTRAINT calendar_oauth_states_membership_fk
        FOREIGN KEY (agency_id, user_id)
        REFERENCES agency_memberships (agency_id, operator_user_id) ON DELETE CASCADE,
    CONSTRAINT calendar_oauth_states_provider_chk CHECK (provider = 'google'),
    CONSTRAINT calendar_oauth_states_hash_chk CHECK (state_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT calendar_oauth_states_key_id_chk CHECK (token_key_id ~ '^[A-Za-z0-9_-]{1,32}$'),
    -- TTL: nel futuro rispetto alla creazione, e breve (al massimo un'ora).
    CONSTRAINT calendar_oauth_states_ttl_chk CHECK (
        expires_at > created_at AND expires_at <= created_at + INTERVAL '1 hour'),
    CONSTRAINT calendar_oauth_states_used_chk CHECK (used_at IS NULL OR used_at >= created_at)
);

-- ---------------------------------------------------------------------------
-- appointment_calendar_sync
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS appointment_calendar_sync (
    id                        BIGSERIAL    PRIMARY KEY,
    agency_id                 BIGINT       NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    provider                  VARCHAR(20)  NOT NULL DEFAULT 'google',

    -- La CATENA: la radice (mai spostata) e la riga viva attuale. Uno
    -- spostamento (D5) cambia solo `current_appointment_id`.
    chain_root_appointment_id BIGINT       NOT NULL,
    current_appointment_id    BIGINT       NOT NULL,

    -- Il REMOTO ATTUALE: su quale connessione/calendario l'evento vive ORA.
    -- NULL = nessun evento remoto noto. Il remoto DESIDERATO non si salva: lo
    -- deriva il worker dall'agente attuale (D6), cosi' un mark_dirty non
    -- sovrascrive mai il remoto prima che l'evento sia stato spostato.
    remote_connection_id      BIGINT,
    remote_calendar_id        VARCHAR(255),
    -- L'id DETERMINISTICO dell'evento (agenzia + radice), deciso e salvato
    -- alla nascita della riga, prima di qualunque chiamata remota: identico a
    -- ogni retry. Charset base32hex minuscolo 0-9a-v, 5-1024 caratteri.
    remote_event_id           VARCHAR(64)  NOT NULL,

    status                    VARCHAR(24)  NOT NULL DEFAULT 'pending',
    -- Latest-state (D9): ogni mark_dirty incrementa `dirty_generation`; il
    -- worker dichiara `synced` solo se la generazione non e' cambiata mentre
    -- lavorava.
    dirty_generation          BIGINT       NOT NULL DEFAULT 1,
    synced_generation         BIGINT       NOT NULL DEFAULT 0,
    desired_payload_hash      CHAR(64),
    synced_payload_hash       CHAR(64),

    attempt_count             INTEGER      NOT NULL DEFAULT 0,
    next_attempt_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    claim_token               UUID,
    claimed_at                TIMESTAMPTZ,

    last_error_code           VARCHAR(64),
    last_error_detail         VARCHAR(300),

    etag                      VARCHAR(255),
    remote_updated_at         TIMESTAMPTZ,
    last_synced_at            TIMESTAMPTZ,
    created_at                TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT appointment_calendar_sync_chain_unq
        UNIQUE (agency_id, provider, chain_root_appointment_id),
    CONSTRAINT appointment_calendar_sync_current_unq
        UNIQUE (provider, current_appointment_id),
    -- CASCADE: un appuntamento non si cancella mai a runtime (072); lo fa
    -- solo la purge di una corsa di prova, e le sue righe di sync seguono.
    CONSTRAINT appointment_calendar_sync_root_fk
        FOREIGN KEY (agency_id, chain_root_appointment_id)
        REFERENCES appointments (agency_id, id) ON DELETE CASCADE,
    CONSTRAINT appointment_calendar_sync_current_fk
        FOREIGN KEY (agency_id, current_appointment_id)
        REFERENCES appointments (agency_id, id) ON DELETE CASCADE,
    CONSTRAINT appointment_calendar_sync_connection_fk
        FOREIGN KEY (agency_id, remote_connection_id)
        REFERENCES calendar_connections (agency_id, id) ON DELETE RESTRICT,
    CONSTRAINT appointment_calendar_sync_provider_chk CHECK (provider = 'google'),
    CONSTRAINT appointment_calendar_sync_status_chk CHECK (status IN (
        'pending', 'syncing', 'synced', 'retrying', 'failed',
        'needs_reauth', 'waiting_connection', 'detached')),
    CONSTRAINT appointment_calendar_sync_event_id_chk
        CHECK (remote_event_id ~ '^[a-v0-9]{5,64}$'),
    CONSTRAINT appointment_calendar_sync_remote_pair_chk
        CHECK ((remote_connection_id IS NULL) = (remote_calendar_id IS NULL)),
    CONSTRAINT appointment_calendar_sync_generation_chk
        CHECK (synced_generation >= 0 AND synced_generation <= dirty_generation),
    CONSTRAINT appointment_calendar_sync_synced_chk
        CHECK (status <> 'synced' OR synced_generation = dirty_generation),
    CONSTRAINT appointment_calendar_sync_claim_pair_chk
        CHECK ((claim_token IS NULL) = (claimed_at IS NULL)),
    CONSTRAINT appointment_calendar_sync_claim_status_chk
        CHECK ((status = 'syncing') = (claim_token IS NOT NULL)),
    CONSTRAINT appointment_calendar_sync_attempts_chk
        CHECK (attempt_count BETWEEN 0 AND 100),
    CONSTRAINT appointment_calendar_sync_hash_chk CHECK (
        (desired_payload_hash IS NULL OR desired_payload_hash ~ '^[0-9a-f]{64}$')
        AND (synced_payload_hash IS NULL OR synced_payload_hash ~ '^[0-9a-f]{64}$')),
    CONSTRAINT appointment_calendar_sync_error_code_chk
        CHECK (last_error_code IS NULL OR last_error_code ~ '^[a-z0-9_]{1,64}$')
);

-- ---------------------------------------------------------------------------
-- GUARDIE
-- ---------------------------------------------------------------------------

-- Una connessione: identita' immutabile; `connected` solo per una membership
-- ATTIVA (stessa regola che la 072 applica all'agente di un appuntamento).
CREATE OR REPLACE FUNCTION calendar_connections_guard() RETURNS trigger AS $fn$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.agency_id <> OLD.agency_id OR NEW.user_id <> OLD.user_id
           OR NEW.provider <> OLD.provider THEN
            RAISE EXCEPTION 'A30-9A: agency_id, user_id and provider of a calendar connection are immutable (id=%)', OLD.id;
        END IF;
        NEW.updated_at := NOW();
    END IF;
    IF NEW.status = 'connected' AND (TG_OP = 'INSERT' OR OLD.status <> 'connected') THEN
        IF NOT EXISTS (
            SELECT 1 FROM agency_memberships
             WHERE agency_id = NEW.agency_id
               AND operator_user_id = NEW.user_id
               AND status = 'active') THEN
            RAISE EXCEPTION 'A30-9A: operator % has no active membership in agency %',
                NEW.user_id, NEW.agency_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

-- Una riga di sync: agenzia, provider, radice e id evento immutabili; la
-- radice e' davvero una radice; la riga viva discende dalla radice (stessa
-- agenzia) seguendo `rescheduled_from_id`.
CREATE OR REPLACE FUNCTION appointment_calendar_sync_guard() RETURNS trigger AS $fn$
DECLARE
    v_cursor BIGINT;
    v_parent BIGINT;
    v_depth  INTEGER := 0;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.agency_id <> OLD.agency_id OR NEW.provider <> OLD.provider
           OR NEW.chain_root_appointment_id <> OLD.chain_root_appointment_id
           OR NEW.remote_event_id <> OLD.remote_event_id THEN
            RAISE EXCEPTION 'A30-9A: agency_id, provider, chain root and remote event id of a sync row are immutable (id=%)', OLD.id;
        END IF;
        NEW.updated_at := NOW();
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF EXISTS (SELECT 1 FROM appointments
                    WHERE id = NEW.chain_root_appointment_id
                      AND rescheduled_from_id IS NOT NULL) THEN
            RAISE EXCEPTION 'A30-9A: appointment % is not the root of its chain',
                NEW.chain_root_appointment_id;
        END IF;
    END IF;

    IF TG_OP = 'INSERT' OR NEW.current_appointment_id <> OLD.current_appointment_id THEN
        v_cursor := NEW.current_appointment_id;
        WHILE v_cursor <> NEW.chain_root_appointment_id LOOP
            SELECT rescheduled_from_id INTO v_parent
              FROM appointments
             WHERE id = v_cursor AND agency_id = NEW.agency_id;
            v_depth := v_depth + 1;
            IF v_parent IS NULL OR v_depth > 1000 THEN
                RAISE EXCEPTION 'A30-9A: appointment % does not descend from chain root % in agency %',
                    NEW.current_appointment_id, NEW.chain_root_appointment_id, NEW.agency_id;
            END IF;
            v_cursor := v_parent;
        END LOOP;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
         WHERE t.tgname = 'trg_calendar_connections_guard' AND c.relname = 'calendar_connections'
           AND NOT t.tgisinternal) THEN
        CREATE TRIGGER trg_calendar_connections_guard
            BEFORE INSERT OR UPDATE ON calendar_connections
            FOR EACH ROW EXECUTE FUNCTION calendar_connections_guard();
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
         WHERE t.tgname = 'trg_appointment_calendar_sync_guard'
           AND c.relname = 'appointment_calendar_sync' AND NOT t.tgisinternal) THEN
        CREATE TRIGGER trg_appointment_calendar_sync_guard
            BEFORE INSERT OR UPDATE ON appointment_calendar_sync
            FOR EACH ROW EXECUTE FUNCTION appointment_calendar_sync_guard();
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- Indici
-- ---------------------------------------------------------------------------
-- La coda: le righe da lavorare in ordine di scadenza.
CREATE INDEX IF NOT EXISTS idx_appointment_calendar_sync_due
    ON appointment_calendar_sync (next_attempt_at, id)
    WHERE status IN ('pending', 'retrying');
-- I claim da recuperare (lease scaduto).
CREATE INDEX IF NOT EXISTS idx_appointment_calendar_sync_claimed
    ON appointment_calendar_sync (claimed_at) WHERE status = 'syncing';
CREATE INDEX IF NOT EXISTS idx_appointment_calendar_sync_connection
    ON appointment_calendar_sync (remote_connection_id) WHERE remote_connection_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_calendar_connections_user
    ON calendar_connections (agency_id, user_id);
CREATE INDEX IF NOT EXISTS idx_calendar_oauth_states_expires
    ON calendar_oauth_states (expires_at);
