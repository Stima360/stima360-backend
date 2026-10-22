-- 110 - RACCOLTA DIGITALE DEL CONSENSO (C3): link personale, pagina pubblica,
-- double opt-in dell'email.
--
-- Additiva. Nessuna riga esistente viene riscritta, nessun consenso viene
-- creato qui: un consenso nasce SOLO quando l'interessato lo esprime sulla
-- pagina (o conferma l'email), e passa dal percorso di scrittura di sempre
-- (`consent_events` + proiezione per canale della 109).
--
--   1. `consent_links`: il link personale che un operatore genera per UN
--      contatto. Si conserva solo l'impronta SHA-256 del token, mai il token.
--      Raccoglie consensi per 30 giorni (`expires_at`); dopo, o se sostituito
--      da un link nuovo (`superseded_at`) o disattivato (`disabled_at`), serve
--      SOLO per revocare. Un solo link "aperto" per contatto.
--   2. `consent_submissions`: ogni invio della pagina (o la casella email del
--      funnel di stima360.it) con la sua prova: canali scelti e revocati,
--      informativa mostrata con la sua impronta, istante, IP cifrato, browser.
--      L'email resta `pending` finche' l'interessato non conferma (double
--      opt-in, 7 giorni). SMS, WhatsApp e telefonate scelti qui sono solo
--      DICHIARATI (`phone_channels_declared`): non diventano consenso finche'
--      il numero non sara' verificato.
--   3. `consent_page_rate_limits`: contatore a finestra fissa contro gli abusi.
--   4. `communication_messages`: il codice `consent_confirmation`, l'email di
--      servizio che porta il link di conferma.
--
-- Nessun BEGIN/COMMIT: la transazione e' del runner (scripts/p26_migrate.py).

-- ---------------------------------------------------------------------------
-- 1. consent_links
-- ---------------------------------------------------------------------------
CREATE TABLE consent_links (
    id                  BIGSERIAL    PRIMARY KEY,
    agency_id           BIGINT       NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,
    contact_id          BIGINT       NOT NULL,
    token_hash          CHAR(64)     NOT NULL UNIQUE,
    created_by_user_id  BIGINT       NOT NULL,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    expires_at          TIMESTAMPTZ  NOT NULL,
    superseded_at       TIMESTAMPTZ,
    disabled_at         TIMESTAMPTZ,
    disabled_by_user_id BIGINT,
    CONSTRAINT consent_links_contact_fk FOREIGN KEY (agency_id, contact_id)
        REFERENCES contacts (agency_id, id) ON DELETE CASCADE,
    CONSTRAINT consent_links_hash_chk CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT consent_links_expiry_chk CHECK (expires_at > created_at),
    CONSTRAINT consent_links_disabled_chk
        CHECK ((disabled_at IS NULL) = (disabled_by_user_id IS NULL))
);

CREATE UNIQUE INDEX uq_consent_links_open_per_contact
    ON consent_links (contact_id) WHERE superseded_at IS NULL AND disabled_at IS NULL;
CREATE INDEX idx_consent_links_contact ON consent_links (contact_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- 2. consent_submissions
-- ---------------------------------------------------------------------------
CREATE TABLE consent_submissions (
    id                      BIGSERIAL    PRIMARY KEY,
    agency_id               BIGINT       NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,
    contact_id              BIGINT       NOT NULL,
    link_id                 BIGINT       REFERENCES consent_links(id) ON DELETE CASCADE,
    stima_id                INTEGER      REFERENCES stime(id) ON DELETE SET NULL,
    origin                  VARCHAR(20)  NOT NULL,
    notice_id               BIGINT       REFERENCES consent_notices(id) ON DELETE RESTRICT,
    notice_content_hash     CHAR(64),
    channels_requested      TEXT[]       NOT NULL DEFAULT '{}',
    channels_revoked        TEXT[]       NOT NULL DEFAULT '{}',
    phone_channels_declared TEXT[]       NOT NULL DEFAULT '{}',
    phone_number_snapshot   VARCHAR(50),
    submitted_at            TIMESTAMPTZ  NOT NULL,
    ip_hash                 CHAR(64),
    user_agent              VARCHAR(300),
    email_status            VARCHAR(20)  NOT NULL DEFAULT 'none',
    email_address           VARCHAR(320),
    confirm_token_hash      CHAR(64)     UNIQUE,
    confirm_expires_at      TIMESTAMPTZ,
    confirmed_at            TIMESTAMPTZ,
    confirm_ip_hash         CHAR(64),
    confirm_user_agent      VARCHAR(300),
    confirmation_message_id BIGINT,
    consent_event_id        BIGINT       REFERENCES consent_events(id) ON DELETE SET NULL,
    idempotency_key         VARCHAR(200) UNIQUE,
    created_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT consent_submissions_contact_fk FOREIGN KEY (agency_id, contact_id)
        REFERENCES contacts (agency_id, id) ON DELETE CASCADE,
    CONSTRAINT consent_submissions_origin_chk CHECK (origin IN ('consent_page', 'public_stima')),
    CONSTRAINT consent_submissions_channels_chk CHECK (
        channels_requested <@ ARRAY['email', 'sms', 'whatsapp', 'phone']::text[]
        AND channels_revoked <@ ARRAY['email', 'sms', 'whatsapp', 'phone']::text[]
        AND NOT (channels_requested && channels_revoked)
        AND (cardinality(channels_requested) + cardinality(channels_revoked)) > 0),
    -- I canali telefonici scelti sono DICHIARATI, mai concessi da qui.
    CONSTRAINT consent_submissions_phone_chk CHECK (
        phone_channels_declared <@ ARRAY['sms', 'whatsapp', 'phone']::text[]
        AND phone_channels_declared <@ channels_requested
        AND (cardinality(phone_channels_declared) = 0 OR phone_number_snapshot IS NOT NULL)),
    -- Una scelta di canali cita sempre l'informativa mostrata.
    CONSTRAINT consent_submissions_notice_chk CHECK (
        cardinality(channels_requested) = 0
        OR (notice_id IS NOT NULL AND notice_content_hash ~ '^[0-9a-f]{64}$')),
    -- Il funnel di stima360.it chiede solo l'email.
    CONSTRAINT consent_submissions_public_stima_chk CHECK (
        origin <> 'public_stima'
        OR (channels_requested = ARRAY['email']::text[] AND cardinality(channels_revoked) = 0)),
    CONSTRAINT consent_submissions_email_status_chk
        CHECK (email_status IN ('none', 'pending', 'confirmed', 'expired', 'cancelled')),
    -- L'email e' nel double opt-in se e solo se e' fra i canali scelti.
    CONSTRAINT consent_submissions_email_matrix_chk CHECK (
        (email_status = 'none'
            AND NOT ('email' = ANY (channels_requested))
            AND email_address IS NULL AND confirm_token_hash IS NULL
            AND confirm_expires_at IS NULL AND confirmed_at IS NULL)
     OR (email_status <> 'none'
            AND 'email' = ANY (channels_requested)
            AND email_address IS NOT NULL
            AND confirm_token_hash ~ '^[0-9a-f]{64}$'
            AND confirm_expires_at > submitted_at
            AND ((email_status = 'confirmed') = (confirmed_at IS NOT NULL)))),
    CONSTRAINT consent_submissions_hash_chk CHECK (
        (ip_hash IS NULL OR ip_hash ~ '^[0-9a-f]{64}$')
        AND (confirm_ip_hash IS NULL OR confirm_ip_hash ~ '^[0-9a-f]{64}$'))
);

CREATE INDEX idx_consent_submissions_contact ON consent_submissions (contact_id, submitted_at DESC);
CREATE INDEX idx_consent_submissions_link ON consent_submissions (link_id);

-- ---------------------------------------------------------------------------
-- La prova non si riscrive: aggiornamenti solo nelle transizioni previste,
-- cancellazioni solo quando sparisce il contatto (purga fisica).
-- ---------------------------------------------------------------------------
CREATE FUNCTION consent_links_guard() RETURNS trigger AS $fn$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF pg_trigger_depth() > 1
           AND NOT EXISTS (SELECT 1 FROM contacts WHERE id = OLD.contact_id AND agency_id = OLD.agency_id) THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION '110: consent_links is a register: DELETE is refused (id=%)', OLD.id;
    END IF;
    IF (to_jsonb(OLD) - ARRAY['superseded_at', 'disabled_at', 'disabled_by_user_id'])
       IS DISTINCT FROM (to_jsonb(NEW) - ARRAY['superseded_at', 'disabled_at', 'disabled_by_user_id'])
       OR (OLD.superseded_at IS NOT NULL AND NEW.superseded_at IS DISTINCT FROM OLD.superseded_at)
       OR (OLD.disabled_at IS NOT NULL AND NEW.disabled_at IS DISTINCT FROM OLD.disabled_at) THEN
        RAISE EXCEPTION '110: consent_links id=% may only be superseded or disabled, once', OLD.id;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

CREATE TRIGGER trg_consent_links_guard
    BEFORE UPDATE OR DELETE ON consent_links
    FOR EACH ROW EXECUTE FUNCTION consent_links_guard();
ALTER TABLE consent_links ENABLE ALWAYS TRIGGER trg_consent_links_guard;

CREATE FUNCTION consent_submissions_guard() RETURNS trigger AS $fn$
DECLARE
    v_mobili CONSTANT text[] := ARRAY['email_status', 'confirmed_at', 'confirm_ip_hash',
                                      'confirm_user_agent', 'confirmation_message_id',
                                      'consent_event_id', 'stima_id'];
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF pg_trigger_depth() > 1
           AND (NOT EXISTS (SELECT 1 FROM contacts WHERE id = OLD.contact_id AND agency_id = OLD.agency_id)
                OR (OLD.link_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM consent_links WHERE id = OLD.link_id))) THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION '110: consent_submissions is a register: DELETE is refused (id=%)', OLD.id;
    END IF;
    IF (to_jsonb(OLD) - v_mobili) IS DISTINCT FROM (to_jsonb(NEW) - v_mobili) THEN
        RAISE EXCEPTION '110: consent_submissions id=%: the proof cannot be rewritten', OLD.id;
    END IF;
    IF NEW.email_status IS DISTINCT FROM OLD.email_status
       AND NOT (OLD.email_status = 'pending' AND NEW.email_status IN ('confirmed', 'expired', 'cancelled')) THEN
        RAISE EXCEPTION '110: consent_submissions id=%: email_status % -> % is not allowed',
            OLD.id, OLD.email_status, NEW.email_status;
    END IF;
    IF (OLD.confirmed_at IS NOT NULL AND NEW.confirmed_at IS DISTINCT FROM OLD.confirmed_at)
       OR (OLD.confirm_ip_hash IS NOT NULL AND NEW.confirm_ip_hash IS DISTINCT FROM OLD.confirm_ip_hash)
       OR (OLD.confirm_user_agent IS NOT NULL AND NEW.confirm_user_agent IS DISTINCT FROM OLD.confirm_user_agent)
       OR (OLD.confirmation_message_id IS NOT NULL
           AND NEW.confirmation_message_id IS DISTINCT FROM OLD.confirmation_message_id)
       OR (NEW.consent_event_id IS NOT NULL AND OLD.consent_event_id IS NOT NULL
           AND NEW.consent_event_id <> OLD.consent_event_id)
       OR (NEW.stima_id IS NOT NULL AND NEW.stima_id IS DISTINCT FROM OLD.stima_id) THEN
        RAISE EXCEPTION '110: consent_submissions id=%: a recorded value cannot change', OLD.id;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

CREATE TRIGGER trg_consent_submissions_guard
    BEFORE UPDATE OR DELETE ON consent_submissions
    FOR EACH ROW EXECUTE FUNCTION consent_submissions_guard();
ALTER TABLE consent_submissions ENABLE ALWAYS TRIGGER trg_consent_submissions_guard;

-- ---------------------------------------------------------------------------
-- 3. consent_page_rate_limits
-- ---------------------------------------------------------------------------
CREATE TABLE consent_page_rate_limits (
    bucket        VARCHAR(120) NOT NULL,
    window_start  TIMESTAMPTZ  NOT NULL,
    request_count INTEGER      NOT NULL DEFAULT 1,
    PRIMARY KEY (bucket, window_start),
    CONSTRAINT consent_page_rate_limits_count_chk CHECK (request_count >= 1)
);

-- ---------------------------------------------------------------------------
-- 4. communication_messages: l'email di conferma (servizio)
-- ---------------------------------------------------------------------------
ALTER TABLE communication_messages DROP CONSTRAINT communication_messages_reason_code_chk;
ALTER TABLE communication_messages ADD CONSTRAINT communication_messages_reason_code_chk
    CHECK (reason_code IN ('stima_pdf','operator_manual','operator_reply','owner_login_link','appointment_reminder',
        'consent_confirmation',
        'm1','m2','m3','m4','m5','m6','m7','m8','m9','m10',
        'm11','m12','m13','m14','m15','m16','m17','m18','m19','m20'));
