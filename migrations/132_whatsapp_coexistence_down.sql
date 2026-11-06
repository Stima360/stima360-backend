-- Down per 132. RIFIUTA se esistono gia' dati di Coexistence: messaggi
-- dell'azienda o dello storico, rubrica sincronizzata, collegamenti registrati.
-- Toglierli li perderebbe senza rimedio: si conservano, oppure si ripristina da
-- un backup verificato. Senza dati Coexistence la 118 torna com'era.
-- La transazione e' del runner.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM whatsapp_inbound_messages WHERE direction <> 'in' OR origin <> 'webhook') THEN
        RAISE EXCEPTION '132 down refused: Coexistence messages exist (app echoes or history)';
    END IF;
    IF EXISTS (SELECT 1 FROM whatsapp_app_contacts) THEN
        RAISE EXCEPTION '132 down refused: WhatsApp app address book rows exist';
    END IF;
    IF EXISTS (SELECT 1 FROM whatsapp_coexistence_onboardings) THEN
        RAISE EXCEPTION '132 down refused: Coexistence onboardings are recorded';
    END IF;
END;
$$;

DROP TRIGGER IF EXISTS trg_whatsapp_coexistence_onboardings_guard ON whatsapp_coexistence_onboardings;
DROP FUNCTION IF EXISTS whatsapp_coexistence_onboardings_guard();
DROP TABLE IF EXISTS whatsapp_coexistence_onboardings;
DROP TRIGGER IF EXISTS trg_whatsapp_app_contacts_guard ON whatsapp_app_contacts;
DROP FUNCTION IF EXISTS whatsapp_app_contacts_guard();
DROP TABLE IF EXISTS whatsapp_app_contacts;

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
        OR NEW.received_at IS DISTINCT FROM OLD.received_at) THEN
        RAISE EXCEPTION 'whatsapp inbound message %: only contact and property links may change', OLD.id;
    END IF;
    IF NEW.property_id IS NOT NULL THEN
        SELECT agency_id INTO v_agency FROM properties WHERE id = NEW.property_id;
        IF v_agency IS DISTINCT FROM NEW.agency_id THEN
            RAISE EXCEPTION 'whatsapp inbound message: property % belongs to another agency', NEW.property_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP INDEX IF EXISTS idx_whatsapp_inbound_agency_sender_time;
ALTER TABLE whatsapp_inbound_messages
    DROP CONSTRAINT IF EXISTS whatsapp_inbound_messages_direction_origin_chk,
    DROP CONSTRAINT IF EXISTS whatsapp_inbound_messages_origin_chk,
    DROP CONSTRAINT IF EXISTS whatsapp_inbound_messages_direction_chk,
    DROP COLUMN IF EXISTS origin,
    DROP COLUMN IF EXISTS direction;
