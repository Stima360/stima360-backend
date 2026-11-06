-- WHATSAPP COEXISTENCE (fase C) - il numero WhatsApp Business personale
-- collegato alla Cloud API senza lasciare l'app sul telefono.
--
-- Con Coexistence Meta invia, oltre ai messaggi ricevuti, anche:
--   * le ECO dei messaggi che il titolare scrive dall'app WhatsApp Business
--     (`smb_message_echoes`);
--   * lo STORICO delle chat che il titolare accetta di condividere
--     (`history`, fino a sei mesi, a blocchi);
--   * la RUBRICA dell'app (`smb_app_state_sync`): aggiunte e rimozioni.
--
-- Che cosa aggiunge questa migrazione, senza toccare nulla di esistente:
--
--   whatsapp_inbound_messages.direction  'in' (dal cliente, come oggi) o 'out'
--       (dall'azienda, scritto sull'app). La conversazione resta (agenzia,
--       numero ricevente, numero del cliente): `sender_phone` e' SEMPRE il
--       numero del cliente, in entrambe le direzioni.
--   whatsapp_inbound_messages.origin     'webhook' (ricevuto in diretta, come
--       oggi), 'app_echo' (eco di un messaggio scritto dall'app), 'history'
--       (dallo storico condiviso). Le righe gia' presenti diventano
--       ('in', 'webhook'): nessun cambiamento di significato.
--       Direzione e origine sono immutabili come il resto del messaggio.
--   whatsapp_app_contacts   la rubrica dell'app WhatsApp Business di una
--       casella, come la manda Meta: una riga per numero, con nome e stato
--       (presente / rimosso). NON sono contatti del CRM: diventano contatti
--       solo con «Importa rubrica» (fase B), voce per voce, riservati a chi
--       importa.
--   whatsapp_coexistence_onboardings  il registro dei collegamenti tramite la
--       procedura ufficiale di Meta (Embedded Signup): chi l'ha avviato, per
--       quale WABA e quale numero, l'esito dei passi verso Meta e la conferma
--       letta da Meta. Nessun token: quello della procedura si usa una volta e
--       non si conserva.
--
-- La transazione e' del runner.

ALTER TABLE whatsapp_inbound_messages
    ADD COLUMN direction VARCHAR(3)  NOT NULL DEFAULT 'in',
    ADD COLUMN origin    VARCHAR(10) NOT NULL DEFAULT 'webhook';

ALTER TABLE whatsapp_inbound_messages
    ADD CONSTRAINT whatsapp_inbound_messages_direction_chk CHECK (direction IN ('in', 'out')),
    ADD CONSTRAINT whatsapp_inbound_messages_origin_chk CHECK (origin IN ('webhook', 'app_echo', 'history')),
    -- Un'eco e' sempre un messaggio dell'azienda; un messaggio dell'azienda
    -- arriva solo come eco o dallo storico, mai come ricezione in diretta.
    ADD CONSTRAINT whatsapp_inbound_messages_direction_origin_chk CHECK (
        (direction = 'in' AND origin IN ('webhook', 'history'))
        OR (direction = 'out' AND origin IN ('app_echo', 'history')));

CREATE INDEX idx_whatsapp_inbound_agency_sender_time
    ON whatsapp_inbound_messages (agency_id, phone_number_id, sender_phone,
                                  (COALESCE(provider_timestamp, received_at)) DESC)
    WHERE agency_id IS NOT NULL;

-- La guardia di sempre (118), con direzione e origine fra i campi immutabili.
CREATE OR REPLACE FUNCTION whatsapp_inbound_messages_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_agency BIGINT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'whatsapp inbound message % cannot be deleted', OLD.id;
    END IF;
    IF TG_OP = 'UPDATE' AND (
        NEW.provider_message_id IS DISTINCT FROM OLD.provider_message_id
        OR NEW.phone_number_id IS DISTINCT FROM OLD.phone_number_id
        OR NEW.display_phone_number IS DISTINCT FROM OLD.display_phone_number
        OR NEW.agency_id IS DISTINCT FROM OLD.agency_id
        OR NEW.routing_status IS DISTINCT FROM OLD.routing_status
        OR NEW.routing_reason IS DISTINCT FROM OLD.routing_reason
        OR NEW.sender_phone IS DISTINCT FROM OLD.sender_phone
        OR NEW.sender_profile_name IS DISTINCT FROM OLD.sender_profile_name
        OR NEW.message_type IS DISTINCT FROM OLD.message_type
        OR NEW.text_body IS DISTINCT FROM OLD.text_body
        OR NEW.metadata IS DISTINCT FROM OLD.metadata
        OR NEW.provider_timestamp IS DISTINCT FROM OLD.provider_timestamp
        OR NEW.received_at IS DISTINCT FROM OLD.received_at
        OR NEW.direction IS DISTINCT FROM OLD.direction
        OR NEW.origin IS DISTINCT FROM OLD.origin) THEN
        RAISE EXCEPTION 'whatsapp inbound message %: only contact and property links may change', OLD.id;
    END IF;
    -- L'immobile e' SEMPRE della stessa agenzia del messaggio.
    IF NEW.property_id IS NOT NULL THEN
        SELECT agency_id INTO v_agency FROM properties WHERE id = NEW.property_id;
        IF v_agency IS DISTINCT FROM NEW.agency_id THEN
            RAISE EXCEPTION 'whatsapp inbound message: property % belongs to another agency', NEW.property_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TABLE whatsapp_app_contacts (
    id                BIGSERIAL    PRIMARY KEY,
    agency_id         BIGINT       NOT NULL REFERENCES agencies(id),
    phone_number_id   TEXT         NOT NULL,
    contact_phone     TEXT         NOT NULL,
    full_name         TEXT,
    first_name        TEXT,
    state             VARCHAR(10)  NOT NULL DEFAULT 'present',
    meta_changed_at   TIMESTAMPTZ,
    first_synced_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_synced_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT whatsapp_app_contacts_unq UNIQUE (agency_id, phone_number_id, contact_phone),
    CONSTRAINT whatsapp_app_contacts_phone_number_chk CHECK (phone_number_id ~ '^[0-9]{5,20}$'),
    CONSTRAINT whatsapp_app_contacts_phone_chk CHECK (contact_phone ~ '^[0-9]{6,15}$'),
    CONSTRAINT whatsapp_app_contacts_state_chk CHECK (state IN ('present', 'removed'))
);

CREATE INDEX idx_whatsapp_app_contacts_agency_inbox ON whatsapp_app_contacts (agency_id, phone_number_id, state);

-- Una riga non cambia agenzia, casella o numero: si aggiornano solo nome e stato.
CREATE FUNCTION whatsapp_app_contacts_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND (
        NEW.agency_id IS DISTINCT FROM OLD.agency_id
        OR NEW.phone_number_id IS DISTINCT FROM OLD.phone_number_id
        OR NEW.contact_phone IS DISTINCT FROM OLD.contact_phone
        OR NEW.first_synced_at IS DISTINCT FROM OLD.first_synced_at) THEN
        RAISE EXCEPTION 'whatsapp app contact %: only name and state may change', OLD.id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_whatsapp_app_contacts_guard
    BEFORE UPDATE ON whatsapp_app_contacts
    FOR EACH ROW EXECUTE FUNCTION whatsapp_app_contacts_guard();

CREATE TABLE whatsapp_coexistence_onboardings (
    id                       BIGSERIAL    PRIMARY KEY,
    agency_id                BIGINT       NOT NULL REFERENCES agencies(id),
    requested_by_user_id     BIGINT       NOT NULL REFERENCES operator_users(id),
    waba_id                  TEXT         NOT NULL,
    phone_number_id          TEXT         NOT NULL,
    business_id              TEXT,
    display_phone_number     TEXT,
    verified_name            TEXT,
    -- requested: la procedura Meta e' finita e il CRM ha fatto i suoi passi;
    -- meta_confirmed: Meta conferma il numero sul WABA e in Coexistence;
    -- failed: un passo e' stato rifiutato (vedi `steps` ed `error_code`).
    status                   VARCHAR(16)  NOT NULL DEFAULT 'requested',
    steps                    JSONB        NOT NULL DEFAULT '{}'::jsonb,
    meta_details             JSONB        NOT NULL DEFAULT '{}'::jsonb,
    error_code               VARCHAR(60),
    meta_confirmed_at        TIMESTAMPTZ,
    contacts_sync_requested_at TIMESTAMPTZ,
    history_sync_requested_at  TIMESTAMPTZ,
    created_at               TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT whatsapp_coexistence_waba_chk CHECK (waba_id ~ '^[0-9]{5,25}$'),
    CONSTRAINT whatsapp_coexistence_phone_chk CHECK (phone_number_id ~ '^[0-9]{5,20}$'),
    CONSTRAINT whatsapp_coexistence_business_chk CHECK (business_id IS NULL OR business_id ~ '^[0-9]{5,25}$'),
    CONSTRAINT whatsapp_coexistence_status_chk CHECK (status IN ('requested', 'meta_confirmed', 'failed')),
    CONSTRAINT whatsapp_coexistence_confirmed_chk CHECK ((status = 'meta_confirmed') = (meta_confirmed_at IS NOT NULL)),
    CONSTRAINT whatsapp_coexistence_steps_chk CHECK (jsonb_typeof(steps) = 'object'),
    CONSTRAINT whatsapp_coexistence_details_chk CHECK (jsonb_typeof(meta_details) = 'object')
);

CREATE INDEX idx_whatsapp_coexistence_agency ON whatsapp_coexistence_onboardings (agency_id, created_at DESC);
CREATE INDEX idx_whatsapp_coexistence_phone ON whatsapp_coexistence_onboardings (phone_number_id, created_at DESC);

-- L'autore della procedura e' un operatore della STESSA agenzia.
CREATE FUNCTION whatsapp_coexistence_onboardings_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'whatsapp coexistence onboarding % cannot be deleted', OLD.id;
    END IF;
    IF TG_OP = 'UPDATE' AND (
        NEW.agency_id IS DISTINCT FROM OLD.agency_id
        OR NEW.requested_by_user_id IS DISTINCT FROM OLD.requested_by_user_id
        OR NEW.waba_id IS DISTINCT FROM OLD.waba_id
        OR NEW.phone_number_id IS DISTINCT FROM OLD.phone_number_id
        OR NEW.created_at IS DISTINCT FROM OLD.created_at) THEN
        RAISE EXCEPTION 'whatsapp coexistence onboarding %: identity fields cannot change', OLD.id;
    END IF;
    IF TG_OP = 'INSERT' AND NOT EXISTS (
        SELECT 1 FROM agency_memberships m
        WHERE m.operator_user_id = NEW.requested_by_user_id AND m.agency_id = NEW.agency_id) THEN
        RAISE EXCEPTION 'whatsapp coexistence onboarding: operator % is not a member of agency %',
            NEW.requested_by_user_id, NEW.agency_id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_whatsapp_coexistence_onboardings_guard
    BEFORE INSERT OR UPDATE OR DELETE ON whatsapp_coexistence_onboardings
    FOR EACH ROW EXECUTE FUNCTION whatsapp_coexistence_onboardings_guard();
