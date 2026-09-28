-- 077 - A30-12: PUBLIC BOOKING LINK. Tre tabelle nuove additive per il
-- booking pubblico a link singolo-agente (D1-D10 del gate A30-12B). NON
-- tocca `appointments`, `appointment_events`, `appointment_calendar_sync`,
-- `agent_working_hours`, `agent_availability_exceptions`, `agency_closures`:
-- nessuna colonna aggiunta, nessun vincolo esistente modificato. Il vincolo
-- EXCLUDE di 072, `lock_agents`, `find_conflicts`, e l'enforcement SOFT di
-- 076 per il CRM manuale restano esattamente come sono.
--
-- LE TRE TABELLE
--
--   public_booking_links        un link pubblico, per UN agente singolo
--                                (D2): chi lo apre vede solo la disponibilita'
--                                di quell'agente, con tipo/durata/buffer
--                                fissati dal link (D3), mai scelti dal
--                                client.
--   public_booking_submissions  una riga per ogni tentativo di invio del
--                                form pubblico (D7): la garanzia finale di
--                                idempotenza resta l'indice UNIQUE
--                                (source, source_record_id) su `appointments`
--                                gia' esistente dalla 072 - questa tabella
--                                collega il submission_hash all'appuntamento
--                                che ha prodotto, non lo sostituisce.
--   public_booking_rate_limits  contatori DB-backed a finestra fissa (D9):
--                                un bucket per (link, scope, finestra) e,
--                                quando lo scope lo richiede, per IP-HMAC.
--                                Nessun IP in chiaro in nessuna colonna.
--
-- TOKEN (D6): SOLO l'hash SHA-256 (64 esadecimali) del token opaco vive nel
-- database, mai il valore grezzo. Stesso principio per il submission_token
-- (D7): la colonna `submission_hash` di `public_booking_submissions` e' lo
-- SHA-256 del submission_token, e RIUSATO com'e' come
-- `appointments.source_record_id` con `source='booking_link'` (gia'
-- permesso dal CHECK di 072, mai usato finora).
--
-- IP (D8): `client_ip_hash` e' un HMAC-SHA256(pepper, ip_canonico), mai
-- SHA256(ip) semplice - lo spazio IPv4 e' troppo piccolo e un digest sempre
-- deterministico sarebbe reversibile per dizionario. Il pepper vive in una
-- env var dedicata, mai in questa migration. La colonna e' CHAR(64) come
-- ogni altro digest esadecimale di questo schema; NOT NULL DEFAULT ''
-- quando uno scope non e' per-IP, cosi' il vincolo UNIQUE sotto resta
-- affidabile (NULL <> NULL in Postgres avrebbe reso l'UNIQUE inutile per
-- quelle righe).
--
-- TENANT (D2): stesso pattern di 072/076 - la FK composita
-- `(agency_id, assigned_user_id) REFERENCES agency_memberships
-- (agency_id, operator_user_id)` impone che il link appartenga a un membro
-- DI QUESTA agenzia; che la membership sia ATTIVA lo verifica il trigger di
-- guardia quando il link viene scritto (stessa forma della guardia 072 per
-- `assigned_user_id` e della guardia 076 per gli orari).
--
-- La transazione e la riga in `schema_migrations` le gestisce il runner
-- P26: questo file non apre ne' chiude una transazione.

-- ---------------------------------------------------------------------------
-- public_booking_links
-- ---------------------------------------------------------------------------

CREATE TABLE public_booking_links (
    id                      BIGSERIAL    PRIMARY KEY,
    agency_id               BIGINT       NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,

    -- D2: V1 e' sempre singolo-agente. NOT NULL - nessuna selezione
    -- pubblica dell'agente esiste in V1.
    assigned_user_id        BIGINT       NOT NULL,

    -- D6: SOLO l'hash. secrets.token_urlsafe(32) -> sha256 esadecimale.
    token_hash              CHAR(64)     UNIQUE NOT NULL,

    label                   VARCHAR(200),

    status                  VARCHAR(20)  NOT NULL DEFAULT 'active',

    -- D3: durata/buffer/tipo fissati sul link, mai scelti dal client
    -- pubblico. Stessa lista di valori di `appointments.appointment_type`
    -- (072): un tipo pubblico deve restare uno di quelli che l'Agenda gia'
    -- riconosce, non un vocabolario parallelo.
    appointment_type        VARCHAR(40)  NOT NULL,
    duration_minutes        INTEGER      NOT NULL,
    buffer_before_minutes   INTEGER      NOT NULL DEFAULT 0,
    buffer_after_minutes    INTEGER      NOT NULL DEFAULT 0,

    created_by_user_id      BIGINT       REFERENCES operator_users(id) ON DELETE RESTRICT,

    created_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- Nullable per definizione (D6, D9 del design A30-12A): un link puo'
    -- non scadere mai e non essere mai stato revocato.
    expires_at              TIMESTAMPTZ,
    revoked_at              TIMESTAMPTZ,

    CONSTRAINT public_booking_links_agent_same_agency_fk
        FOREIGN KEY (agency_id, assigned_user_id)
        REFERENCES agency_memberships (agency_id, operator_user_id),

    CONSTRAINT public_booking_links_status_chk
        CHECK (status IN ('active', 'disabled')),

    CONSTRAINT public_booking_links_appointment_type_chk CHECK (
        appointment_type IN (
            'call', 'video_call', 'seller_meeting', 'inspection', 'buyer_visit',
            'valuation_presentation', 'mandate_signing', 'proposal',
            'preliminary_contract', 'notary', 'technical', 'other'
        )
    ),

    CONSTRAINT public_booking_links_duration_chk
        CHECK (duration_minutes > 0 AND duration_minutes <= 1440),

    CONSTRAINT public_booking_links_buffers_chk CHECK (
        buffer_before_minutes >= 0 AND buffer_before_minutes <= 1440
        AND buffer_after_minutes >= 0 AND buffer_after_minutes <= 1440
    )
);

CREATE INDEX idx_public_booking_links_agency
    ON public_booking_links (agency_id, assigned_user_id);

CREATE INDEX idx_public_booking_links_token_hash
    ON public_booking_links (token_hash);

-- ---------------------------------------------------------------------------
-- public_booking_submissions
-- ---------------------------------------------------------------------------

CREATE TABLE public_booking_submissions (
    id                  BIGSERIAL    PRIMARY KEY,
    link_id             BIGINT       NOT NULL REFERENCES public_booking_links(id) ON DELETE RESTRICT,

    -- D7: SHA-256 del submission_token generato dalla GET, mai il token
    -- grezzo. Riusato com'e' come appointments.source_record_id.
    submission_hash     CHAR(64)     UNIQUE NOT NULL,

    -- Nullable finche' la creazione dell'appuntamento non e' riuscita
    -- (status='pending'/'failed'); valorizzato solo a 'succeeded'.
    appointment_id      BIGINT       REFERENCES appointments(id) ON DELETE RESTRICT,

    -- D8: HMAC-SHA256(pepper, ip), mai l'IP in chiaro. Nessun default vuoto
    -- qui: ogni submission ha un client, a differenza dei bucket di rate
    -- limit sotto che possono essere per-solo-link.
    client_ip_hash      CHAR(64)     NOT NULL,

    -- D7 "payload differente => rifiuto deterministico": SHA-256 esadecimale
    -- del payload canonico (start_at, nome, telefono, email normalizzati) al
    -- momento di QUESTA submission_hash. Un retry con lo stesso
    -- submission_token ricalcola la stessa impronta e la confronta: uguale
    -- => stesso esito idempotente (nessun evento duplicato, garantito
    -- comunque dall'UNIQUE su appointments.source_record_id); diversa =>
    -- rifiuto, mai una scrittura silenziosa su dati diversi.
    payload_fingerprint  CHAR(64)     NOT NULL,

    status               VARCHAR(20) NOT NULL DEFAULT 'pending',

    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    completed_at         TIMESTAMPTZ,

    CONSTRAINT public_booking_submissions_status_chk
        CHECK (status IN ('pending', 'succeeded', 'failed'))
);

CREATE INDEX idx_public_booking_submissions_link
    ON public_booking_submissions (link_id);

-- ---------------------------------------------------------------------------
-- public_booking_rate_limits (D9) - contatori a finestra fissa, DB-backed.
--
-- Uno scope per ciascuno dei quattro budget richiesti: GET e POST hanno
-- budget separati, e ciascuno ha una variante per-link e una variante
-- per-(link+IP). `client_ip_hash` e' '' (mai NULL) per le varianti
-- solo-link, cosi' l'UNIQUE sotto - la base dell'incremento atomico
-- INSERT ... ON CONFLICT ... DO UPDATE - resta affidabile: NULL <> NULL in
-- Postgres avrebbe reso l'UNIQUE inutile per quelle righe.
-- ---------------------------------------------------------------------------

CREATE TABLE public_booking_rate_limits (
    id                BIGSERIAL    PRIMARY KEY,
    link_id           BIGINT       NOT NULL REFERENCES public_booking_links(id) ON DELETE RESTRICT,
    scope             VARCHAR(20)  NOT NULL,
    client_ip_hash    CHAR(64)     NOT NULL DEFAULT '',
    window_start      TIMESTAMPTZ  NOT NULL,
    request_count     INTEGER      NOT NULL DEFAULT 0,

    CONSTRAINT public_booking_rate_limits_scope_chk
        CHECK (scope IN ('get_link', 'get_ip', 'post_link', 'post_ip')),

    CONSTRAINT public_booking_rate_limits_unique
        UNIQUE (link_id, scope, client_ip_hash, window_start)
);

CREATE INDEX idx_public_booking_rate_limits_lookup
    ON public_booking_rate_limits (link_id, scope, window_start);

-- ---------------------------------------------------------------------------
-- Guardie: `updated_at` e membership ATTIVA quando il link viene scritto.
-- Stessa forma delle guardie 072/076.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public_booking_links_guard() RETURNS trigger AS $fn$
DECLARE
    v_is_ins BOOLEAN := (TG_OP = 'INSERT');
BEGIN
    NEW.updated_at := NOW();
    IF v_is_ins OR NEW.assigned_user_id IS DISTINCT FROM OLD.assigned_user_id
       OR NEW.agency_id IS DISTINCT FROM OLD.agency_id THEN
        IF NOT EXISTS (
            SELECT 1 FROM agency_memberships
             WHERE agency_id = NEW.agency_id
               AND operator_user_id = NEW.assigned_user_id
               AND status = 'active') THEN
            RAISE EXCEPTION 'A30-12: operator % has no active membership in agency %',
                NEW.assigned_user_id, NEW.agency_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

CREATE TRIGGER trg_public_booking_links_guard
    BEFORE INSERT OR UPDATE ON public_booking_links
    FOR EACH ROW EXECUTE FUNCTION public_booking_links_guard();
