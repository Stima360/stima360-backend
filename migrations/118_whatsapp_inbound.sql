-- BLOCCO-4A - ricezione affidabile dei messaggi WhatsApp nel CRM.
--
-- Due tabelle nuove, additive, nessun backfill. `whatsapp_incoming` (la
-- tabella storica, non creata da nessuna migrazione del repository) NON viene
-- toccata: i vecchi messaggi restano dove sono, non attribuiti.
--
--   whatsapp_inbound_messages   un messaggio ricevuto per riga. Idempotenza
--       sull'identificativo del provider (`provider_message_id`, il `wamid` di
--       Meta): un reinvio dello stesso webhook non crea una seconda riga.
--       L'agenzia e' quella del numero destinatario secondo la configurazione
--       del server; se il numero e' sconosciuto o ambiguo la riga resta SENZA
--       agenzia (`routing_status = 'unrouted'`) e nessuna rotta di tenant la
--       espone. Contatto e immobile: collegati solo nella stessa agenzia
--       (FK composta sui contatti, trigger sugli immobili). Gli allegati non
--       vengono scaricati: si conservano tipo e metadati minimi.
--   whatsapp_inbound_message_links  registro append-only delle associazioni
--       manuali (contatto/immobile, prima e dopo, autore e data dal server).
--   whatsapp_webhook_events     gli eventi che non sono messaggi: aggiornamenti
--       di consegna (`status`) e altri eventi (`other`), idempotenti su una
--       chiave calcolata dal server.
--
-- La transazione e' del runner.

CREATE TABLE whatsapp_inbound_messages (
    id                      BIGSERIAL PRIMARY KEY,
    provider_message_id     TEXT         NOT NULL,
    phone_number_id         TEXT         NOT NULL,
    display_phone_number    TEXT,
    agency_id               BIGINT       REFERENCES agencies(id),
    routing_status          VARCHAR(10)  NOT NULL,
    routing_reason          VARCHAR(30),
    sender_phone            TEXT         NOT NULL,
    sender_profile_name     TEXT,
    message_type            VARCHAR(30)  NOT NULL,
    text_body               TEXT,
    metadata                JSONB        NOT NULL DEFAULT '{}'::jsonb,
    provider_timestamp      TIMESTAMPTZ,
    received_at             TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    contact_id              BIGINT,
    contact_match           VARCHAR(10)  NOT NULL DEFAULT 'none',
    property_id             BIGINT       REFERENCES properties(id),
    linked_by_user_id       BIGINT       REFERENCES operator_users(id),
    linked_at               TIMESTAMPTZ,
    CONSTRAINT whatsapp_inbound_messages_provider_unq UNIQUE (provider_message_id),
    CONSTRAINT whatsapp_inbound_messages_provider_id_chk CHECK (BTRIM(provider_message_id) <> ''),
    CONSTRAINT whatsapp_inbound_messages_sender_chk CHECK (sender_phone ~ '^[0-9]{6,15}$'),
    CONSTRAINT whatsapp_inbound_messages_routing_chk CHECK (
        (routing_status = 'routed' AND agency_id IS NOT NULL AND routing_reason IS NULL)
        OR (routing_status = 'unrouted' AND agency_id IS NULL
            AND routing_reason IN ('unknown_number', 'ambiguous_number', 'agency_not_found'))),
    CONSTRAINT whatsapp_inbound_messages_text_chk CHECK (message_type = 'text' OR text_body IS NULL),
    CONSTRAINT whatsapp_inbound_messages_match_chk CHECK (contact_match IN ('none', 'matched', 'ambiguous', 'manual')),
    CONSTRAINT whatsapp_inbound_messages_contact_chk CHECK (
        (contact_id IS NULL AND contact_match IN ('none', 'ambiguous', 'manual'))
        OR (contact_id IS NOT NULL AND contact_match IN ('matched', 'manual'))),
    CONSTRAINT whatsapp_inbound_messages_unrouted_free_chk CHECK (
        routing_status = 'routed' OR (contact_id IS NULL AND property_id IS NULL AND contact_match = 'none')),
    CONSTRAINT whatsapp_inbound_messages_link_audit_chk CHECK ((linked_by_user_id IS NULL) = (linked_at IS NULL)),
    CONSTRAINT whatsapp_inbound_messages_metadata_chk CHECK (jsonb_typeof(metadata) = 'object'),
    -- Il contatto e' SEMPRE della stessa agenzia del messaggio.
    CONSTRAINT whatsapp_inbound_messages_contact_fk
        FOREIGN KEY (agency_id, contact_id) REFERENCES contacts (agency_id, id)
);

CREATE INDEX idx_whatsapp_inbound_agency_received
    ON whatsapp_inbound_messages (agency_id, received_at DESC) WHERE agency_id IS NOT NULL;
CREATE INDEX idx_whatsapp_inbound_agency_sender
    ON whatsapp_inbound_messages (agency_id, sender_phone, received_at DESC) WHERE agency_id IS NOT NULL;
CREATE INDEX idx_whatsapp_inbound_contact ON whatsapp_inbound_messages (contact_id) WHERE contact_id IS NOT NULL;

CREATE FUNCTION whatsapp_inbound_messages_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
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
        OR NEW.received_at IS DISTINCT FROM OLD.received_at) THEN
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

CREATE TRIGGER trg_whatsapp_inbound_messages_guard
    BEFORE INSERT OR UPDATE OR DELETE ON whatsapp_inbound_messages
    FOR EACH ROW EXECUTE FUNCTION whatsapp_inbound_messages_guard();

CREATE TABLE whatsapp_inbound_message_links (
    id                    BIGSERIAL PRIMARY KEY,
    message_id            BIGINT       NOT NULL REFERENCES whatsapp_inbound_messages(id),
    agency_id             BIGINT       NOT NULL REFERENCES agencies(id),
    previous_contact_id   BIGINT,
    previous_property_id  BIGINT,
    contact_id            BIGINT,
    property_id           BIGINT,
    linked_by_user_id     BIGINT       NOT NULL REFERENCES operator_users(id),
    linked_at             TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_whatsapp_inbound_links_message ON whatsapp_inbound_message_links (message_id, linked_at DESC);

CREATE FUNCTION whatsapp_inbound_message_links_append_only() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'whatsapp inbound message links are append-only';
END;
$$;

CREATE TRIGGER trg_whatsapp_inbound_message_links_append_only
    BEFORE UPDATE OR DELETE ON whatsapp_inbound_message_links
    FOR EACH ROW EXECUTE FUNCTION whatsapp_inbound_message_links_append_only();

CREATE TABLE whatsapp_webhook_events (
    id                   BIGSERIAL PRIMARY KEY,
    event_kind           VARCHAR(10)  NOT NULL CHECK (event_kind IN ('status', 'other')),
    dedupe_key           TEXT         NOT NULL,
    phone_number_id      TEXT,
    agency_id            BIGINT       REFERENCES agencies(id),
    routing_status       VARCHAR(10)  NOT NULL CHECK (routing_status IN ('routed', 'unrouted')),
    provider_message_id  TEXT,
    status               VARCHAR(20),
    provider_timestamp   TIMESTAMPTZ,
    details              JSONB        NOT NULL DEFAULT '{}'::jsonb,
    received_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT whatsapp_webhook_events_dedupe_unq UNIQUE (dedupe_key),
    CONSTRAINT whatsapp_webhook_events_routing_chk CHECK ((routing_status = 'routed') = (agency_id IS NOT NULL)),
    CONSTRAINT whatsapp_webhook_events_status_chk CHECK (event_kind <> 'status' OR status IS NOT NULL),
    CONSTRAINT whatsapp_webhook_events_details_chk CHECK (jsonb_typeof(details) = 'object')
);

CREATE INDEX idx_whatsapp_webhook_events_provider_message
    ON whatsapp_webhook_events (provider_message_id) WHERE provider_message_id IS NOT NULL;

CREATE TRIGGER trg_whatsapp_webhook_events_append_only
    BEFORE UPDATE OR DELETE ON whatsapp_webhook_events
    FOR EACH ROW EXECUTE FUNCTION whatsapp_inbound_message_links_append_only();
