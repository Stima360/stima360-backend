-- WHATSAPP DUE CASELLE (fase A) - piu' numeri WhatsApp nella stessa agenzia.
--
-- L'instradamento del numero ricevente (`phone_number_id` -> agenzia) resta
-- nella configurazione del server (`WHATSAPP_INBOUND_ROUTES`, BLOCCO-4A). Qui
-- si aggiunge cio' che la configurazione non puo' dire:
--
--   whatsapp_inboxes       le impostazioni di una casella (un numero ricevente
--       dell'agenzia): etichetta, tipo, automazioni attive o no, accesso
--       (`agency` = come oggi, chiunque veda la casella WhatsApp;
--       `restricted` = solo il titolare e gli operatori autorizzati).
--       Un numero senza riga si comporta come oggi (accesso di agenzia,
--       automazioni attive): il numero 1 non cambia.
--   whatsapp_inbox_access  gli operatori autorizzati su una casella riservata.
--
-- La CONVERSAZIONE diventa (agenzia, numero ricevente, numero del cliente):
-- lo stesso cliente che scrive a due numeri ha due conversazioni distinte,
-- ciascuna con i propri non letti e la propria pausa delle risposte
-- automatiche. Per questo:
--
--   whatsapp_inbound_reads        prende `phone_number_id` nella chiave
--       (riempito dal messaggio gia' indicato dal segno di lettura);
--   whatsapp_auto_reply_pauses    prende `phone_number_id` (riempito con il
--       numero dell'ultimo messaggio di quel cliente nell'agenzia; le pause
--       senza messaggi restano senza numero e valgono per ogni casella).
--
-- Nessun messaggio, evento o decisione viene toccato. La transazione e' del runner.

CREATE TABLE whatsapp_inboxes (
    id                    BIGSERIAL    PRIMARY KEY,
    agency_id             BIGINT       NOT NULL REFERENCES agencies(id),
    phone_number_id       TEXT         NOT NULL,
    display_phone_number  TEXT,
    label                 VARCHAR(60)  NOT NULL,
    kind                  VARCHAR(12)  NOT NULL DEFAULT 'cloud_api',
    automations_enabled   BOOLEAN      NOT NULL DEFAULT TRUE,
    access_mode           VARCHAR(10)  NOT NULL DEFAULT 'agency',
    status                VARCHAR(10)  NOT NULL DEFAULT 'active',
    created_by_user_id    BIGINT       REFERENCES operator_users(id),
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT whatsapp_inboxes_phone_unq UNIQUE (phone_number_id),
    CONSTRAINT whatsapp_inboxes_label_unq UNIQUE (agency_id, label),
    CONSTRAINT whatsapp_inboxes_phone_chk CHECK (phone_number_id ~ '^[0-9]{5,20}$'),
    CONSTRAINT whatsapp_inboxes_label_chk CHECK (BTRIM(label) <> ''),
    CONSTRAINT whatsapp_inboxes_kind_chk CHECK (kind IN ('cloud_api', 'coexistence')),
    CONSTRAINT whatsapp_inboxes_access_chk CHECK (access_mode IN ('agency', 'restricted')),
    CONSTRAINT whatsapp_inboxes_status_chk CHECK (status IN ('active', 'disabled'))
);

CREATE INDEX idx_whatsapp_inboxes_agency ON whatsapp_inboxes (agency_id, id);

CREATE TABLE whatsapp_inbox_access (
    inbox_id            BIGINT       NOT NULL REFERENCES whatsapp_inboxes(id) ON DELETE CASCADE,
    user_id             BIGINT       NOT NULL REFERENCES operator_users(id),
    granted_by_user_id  BIGINT       REFERENCES operator_users(id),
    granted_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT whatsapp_inbox_access_pk PRIMARY KEY (inbox_id, user_id)
);

-- L'operatore autorizzato deve essere membro della STESSA agenzia della casella.
CREATE FUNCTION whatsapp_inbox_access_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_agency BIGINT;
BEGIN
    SELECT agency_id INTO v_agency FROM whatsapp_inboxes WHERE id = NEW.inbox_id;
    IF NOT EXISTS (SELECT 1 FROM agency_memberships m
                    WHERE m.agency_id = v_agency AND m.operator_user_id = NEW.user_id) THEN
        RAISE EXCEPTION 'whatsapp inbox access: user % is not a member of agency %', NEW.user_id, v_agency;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_whatsapp_inbox_access_guard
    BEFORE INSERT OR UPDATE ON whatsapp_inbox_access
    FOR EACH ROW EXECUTE FUNCTION whatsapp_inbox_access_guard();

-- Letti/non letti: per casella.
ALTER TABLE whatsapp_inbound_reads ADD COLUMN phone_number_id TEXT;

CREATE OR REPLACE FUNCTION whatsapp_inbound_reads_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_agency BIGINT;
    v_sender TEXT;
    v_number TEXT;
BEGIN
    IF TG_OP = 'UPDATE' AND (NEW.agency_id IS DISTINCT FROM OLD.agency_id OR NEW.user_id IS DISTINCT FROM OLD.user_id
                             OR NEW.sender_phone IS DISTINCT FROM OLD.sender_phone
                             OR (OLD.phone_number_id IS NOT NULL AND NEW.phone_number_id IS DISTINCT FROM OLD.phone_number_id)
                             OR NEW.last_read_message_id < OLD.last_read_message_id) THEN
        RAISE EXCEPTION 'whatsapp inbound read marker: only forward moves of the same conversation';
    END IF;
    SELECT agency_id, sender_phone, phone_number_id INTO v_agency, v_sender, v_number
      FROM whatsapp_inbound_messages WHERE id = NEW.last_read_message_id;
    IF v_agency IS DISTINCT FROM NEW.agency_id OR v_sender IS DISTINCT FROM NEW.sender_phone
       OR (NEW.phone_number_id IS NOT NULL AND v_number IS DISTINCT FROM NEW.phone_number_id) THEN
        RAISE EXCEPTION 'whatsapp inbound read marker: message % belongs to another conversation',
            NEW.last_read_message_id;
    END IF;
    RETURN NEW;
END;
$$;

UPDATE whatsapp_inbound_reads r SET phone_number_id = m.phone_number_id
  FROM whatsapp_inbound_messages m WHERE m.id = r.last_read_message_id;

ALTER TABLE whatsapp_inbound_reads ALTER COLUMN phone_number_id SET NOT NULL;
ALTER TABLE whatsapp_inbound_reads DROP CONSTRAINT whatsapp_inbound_reads_pk;
ALTER TABLE whatsapp_inbound_reads
    ADD CONSTRAINT whatsapp_inbound_reads_pk PRIMARY KEY (agency_id, user_id, phone_number_id, sender_phone);

-- Pausa delle risposte automatiche: per casella.
ALTER TABLE whatsapp_auto_reply_pauses ADD COLUMN phone_number_id TEXT;

UPDATE whatsapp_auto_reply_pauses p SET phone_number_id = (
    SELECT m.phone_number_id FROM whatsapp_inbound_messages m
     WHERE m.agency_id = p.agency_id AND m.sender_phone = p.sender_phone
     ORDER BY m.received_at DESC, m.id DESC LIMIT 1);

DROP INDEX uq_whatsapp_auto_reply_pauses_open;
CREATE UNIQUE INDEX uq_whatsapp_auto_reply_pauses_open
    ON whatsapp_auto_reply_pauses (agency_id, sender_phone, COALESCE(phone_number_id, '')) WHERE resumed_at IS NULL;
