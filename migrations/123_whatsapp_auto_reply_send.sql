-- BLOCCO-4D/5B - motore delle risposte automatiche WhatsApp: percorso d'invio
-- reale, SPENTO per impostazione predefinita, e collegamenti di prenotazione
-- DEDICATI per le risposte inviate.
--
-- Additiva: nessun dato esistente cambia significato e nessuna riga viene
-- riscritta. Si allargano tre insiemi chiusi e si aggiungono colonne
-- facoltative e una tabella.
--
--   whatsapp_auto_reply_settings.mode   + 'send' (il modo d'invio di
--       un'agenzia; resta 'off' di partenza e vale solo con l'arresto generale
--       `WHATSAPP_AUTO_REPLY_MODE=send` sull'ambiente)
--   whatsapp_auto_reply_decisions.mode  + 'send' (la decisione presa in modo
--       d'invio: il testo e' quello che si accoda, non una simulazione)
--   communication_messages.reason_code  + 'auto_reply' (la risposta del
--       motore nel registro delle comunicazioni: WhatsApp, servizio,
--       automatica; il claim generico non la prende, come `operator_reply`)
--   whatsapp_auto_reply_dispatches      una riga per decisione in modo
--       d'invio: il messaggio accodato e l'attivita' creata per l'operatore.
--       Al piu' UNA per decisione (nessun doppio accodamento).
--   property_visit_booking_links        + collegamento DEDICATO: figlio di un
--       collegamento dell'operatore (`parent_link_id`), emesso per UNA
--       decisione (`issued_decision_id`, unico: un solo collegamento per
--       risposta) e per il suo contatto, sempre con scadenza. Stesso immobile,
--       agente e autorizzazione del padre (trigger). Il token, come per ogni
--       collegamento, esiste solo come impronta.
--
-- La transazione e' del runner.

ALTER TABLE whatsapp_auto_reply_settings DROP CONSTRAINT whatsapp_auto_reply_settings_mode_chk;
ALTER TABLE whatsapp_auto_reply_settings ADD CONSTRAINT whatsapp_auto_reply_settings_mode_chk
    CHECK (mode IN ('off', 'simulate', 'send'));

ALTER TABLE whatsapp_auto_reply_decisions DROP CONSTRAINT whatsapp_auto_reply_decisions_mode_chk;
ALTER TABLE whatsapp_auto_reply_decisions ADD CONSTRAINT whatsapp_auto_reply_decisions_mode_chk
    CHECK (mode IN ('simulate', 'send'));

ALTER TABLE communication_messages DROP CONSTRAINT communication_messages_reason_code_chk;
ALTER TABLE communication_messages ADD CONSTRAINT communication_messages_reason_code_chk
    CHECK (reason_code IN ('stima_pdf','operator_manual','operator_reply','owner_login_link','appointment_reminder',
        'consent_confirmation', 'auto_reply',
        'm1','m2','m3','m4','m5','m6','m7','m8','m9','m10',
        'm11','m12','m13','m14','m15','m16','m17','m18','m19','m20'));

CREATE TABLE whatsapp_auto_reply_dispatches (
    id                         BIGSERIAL    PRIMARY KEY,
    agency_id                  BIGINT       NOT NULL REFERENCES agencies(id),
    decision_id                BIGINT       NOT NULL REFERENCES whatsapp_auto_reply_decisions(id),
    communication_message_id   BIGINT       REFERENCES communication_messages(id),
    task_id                    BIGINT       REFERENCES tasks(id),
    created_at                 TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT whatsapp_auto_reply_dispatches_decision_unq UNIQUE (decision_id),
    CONSTRAINT whatsapp_auto_reply_dispatches_message_unq UNIQUE (communication_message_id),
    CONSTRAINT whatsapp_auto_reply_dispatches_something_chk
        CHECK (communication_message_id IS NOT NULL OR task_id IS NOT NULL)
);

CREATE FUNCTION whatsapp_auto_reply_dispatches_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_agency BIGINT;
    v_mode   TEXT;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'whatsapp auto-reply dispatches are append-only';
    END IF;
    SELECT agency_id, mode INTO v_agency, v_mode FROM whatsapp_auto_reply_decisions WHERE id = NEW.decision_id;
    IF v_agency IS DISTINCT FROM NEW.agency_id THEN
        RAISE EXCEPTION 'whatsapp auto-reply dispatch: decision % belongs to another agency', NEW.decision_id;
    END IF;
    IF v_mode IS DISTINCT FROM 'send' THEN
        RAISE EXCEPTION 'whatsapp auto-reply dispatch: decision % was not taken in send mode', NEW.decision_id;
    END IF;
    IF NEW.communication_message_id IS NOT NULL THEN
        SELECT agency_id INTO v_agency FROM communication_messages WHERE id = NEW.communication_message_id;
        IF v_agency IS DISTINCT FROM NEW.agency_id THEN
            RAISE EXCEPTION 'whatsapp auto-reply dispatch: message % belongs to another agency',
                NEW.communication_message_id;
        END IF;
    END IF;
    IF NEW.task_id IS NOT NULL THEN
        SELECT agency_id INTO v_agency FROM tasks WHERE id = NEW.task_id;
        IF v_agency IS DISTINCT FROM NEW.agency_id THEN
            RAISE EXCEPTION 'whatsapp auto-reply dispatch: task % belongs to another agency', NEW.task_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_whatsapp_auto_reply_dispatches_guard
    BEFORE INSERT OR UPDATE OR DELETE ON whatsapp_auto_reply_dispatches
    FOR EACH ROW EXECUTE FUNCTION whatsapp_auto_reply_dispatches_guard();

ALTER TABLE property_visit_booking_links
    ADD COLUMN parent_link_id      BIGINT REFERENCES property_visit_booking_links(id),
    ADD COLUMN issued_decision_id  BIGINT REFERENCES whatsapp_auto_reply_decisions(id),
    ADD COLUMN issued_contact_id   BIGINT REFERENCES contacts(id),
    ADD CONSTRAINT property_visit_booking_links_issued_unq UNIQUE (issued_decision_id),
    ADD CONSTRAINT property_visit_booking_links_issued_chk CHECK (
        (parent_link_id IS NULL AND issued_decision_id IS NULL AND issued_contact_id IS NULL)
        OR (parent_link_id IS NOT NULL AND issued_decision_id IS NOT NULL AND issued_contact_id IS NOT NULL
            AND expires_at IS NOT NULL));

CREATE INDEX idx_property_visit_booking_links_parent
    ON property_visit_booking_links (parent_link_id) WHERE parent_link_id IS NOT NULL;

CREATE OR REPLACE FUNCTION property_visit_booking_links_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_agency BIGINT;
    v_parent RECORD;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'property visit booking link % cannot be deleted', OLD.id;
    END IF;
    IF TG_OP = 'UPDATE' AND (
        NEW.agency_id IS DISTINCT FROM OLD.agency_id
        OR NEW.property_id IS DISTINCT FROM OLD.property_id
        OR NEW.assigned_user_id IS DISTINCT FROM OLD.assigned_user_id
        OR NEW.token_hash IS DISTINCT FROM OLD.token_hash
        OR NEW.owner_authorization IS DISTINCT FROM OLD.owner_authorization
        OR NEW.created_at IS DISTINCT FROM OLD.created_at
        OR NEW.parent_link_id IS DISTINCT FROM OLD.parent_link_id
        OR NEW.issued_decision_id IS DISTINCT FROM OLD.issued_decision_id
        OR NEW.issued_contact_id IS DISTINCT FROM OLD.issued_contact_id
        OR (OLD.status = 'disabled' AND NEW.status <> 'disabled')) THEN
        RAISE EXCEPTION 'property visit booking link %: only disabling is allowed', OLD.id;
    END IF;
    SELECT agency_id INTO v_agency FROM properties WHERE id = NEW.property_id;
    IF v_agency IS DISTINCT FROM NEW.agency_id THEN
        RAISE EXCEPTION 'property visit booking link: property % belongs to another agency', NEW.property_id;
    END IF;
    IF TG_OP = 'INSERT' AND NEW.parent_link_id IS NOT NULL THEN
        SELECT * INTO v_parent FROM property_visit_booking_links WHERE id = NEW.parent_link_id;
        IF v_parent.id IS NULL OR v_parent.parent_link_id IS NOT NULL
           OR v_parent.agency_id IS DISTINCT FROM NEW.agency_id
           OR v_parent.property_id IS DISTINCT FROM NEW.property_id
           OR v_parent.assigned_user_id IS DISTINCT FROM NEW.assigned_user_id
           OR v_parent.owner_authorization IS DISTINCT FROM NEW.owner_authorization THEN
            RAISE EXCEPTION 'property visit booking link: dedicated link must match its operator link %',
                NEW.parent_link_id;
        END IF;
        IF v_parent.status <> 'active' THEN
            RAISE EXCEPTION 'property visit booking link: operator link % is not active', NEW.parent_link_id;
        END IF;
        SELECT agency_id INTO v_agency FROM contacts WHERE id = NEW.issued_contact_id;
        IF v_agency IS DISTINCT FROM NEW.agency_id THEN
            RAISE EXCEPTION 'property visit booking link: contact % belongs to another agency', NEW.issued_contact_id;
        END IF;
        SELECT agency_id INTO v_agency FROM whatsapp_auto_reply_decisions WHERE id = NEW.issued_decision_id;
        IF v_agency IS DISTINCT FROM NEW.agency_id THEN
            RAISE EXCEPTION 'property visit booking link: decision % belongs to another agency', NEW.issued_decision_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
