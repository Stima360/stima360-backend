-- Rientro della 123. RIFIUTA se esistono dati del modo d'invio: decisioni o
-- impostazioni 'send', messaggi 'auto_reply', invii registrati, collegamenti
-- dedicati. Conservarli o ripristinare da un backup verificato.
--
-- La transazione e' del runner.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM whatsapp_auto_reply_dispatches)
       OR EXISTS (SELECT 1 FROM whatsapp_auto_reply_decisions WHERE mode = 'send')
       OR EXISTS (SELECT 1 FROM whatsapp_auto_reply_settings WHERE mode = 'send')
       OR EXISTS (SELECT 1 FROM communication_messages WHERE reason_code = 'auto_reply')
       OR EXISTS (SELECT 1 FROM property_visit_booking_links WHERE parent_link_id IS NOT NULL) THEN
        RAISE EXCEPTION '123 down refused: send-mode data exists';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION property_visit_booking_links_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_agency BIGINT;
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
        OR (OLD.status = 'disabled' AND NEW.status <> 'disabled')) THEN
        RAISE EXCEPTION 'property visit booking link %: only disabling is allowed', OLD.id;
    END IF;
    SELECT agency_id INTO v_agency FROM properties WHERE id = NEW.property_id;
    IF v_agency IS DISTINCT FROM NEW.agency_id THEN
        RAISE EXCEPTION 'property visit booking link: property % belongs to another agency', NEW.property_id;
    END IF;
    RETURN NEW;
END;
$$;

DROP INDEX idx_property_visit_booking_links_parent;
ALTER TABLE property_visit_booking_links
    DROP CONSTRAINT property_visit_booking_links_issued_chk,
    DROP CONSTRAINT property_visit_booking_links_issued_unq,
    DROP COLUMN issued_contact_id,
    DROP COLUMN issued_decision_id,
    DROP COLUMN parent_link_id;

DROP TRIGGER trg_whatsapp_auto_reply_dispatches_guard ON whatsapp_auto_reply_dispatches;
DROP FUNCTION whatsapp_auto_reply_dispatches_guard();
DROP TABLE whatsapp_auto_reply_dispatches;

ALTER TABLE communication_messages DROP CONSTRAINT communication_messages_reason_code_chk;
ALTER TABLE communication_messages ADD CONSTRAINT communication_messages_reason_code_chk
    CHECK (reason_code IN ('stima_pdf','operator_manual','operator_reply','owner_login_link','appointment_reminder',
        'consent_confirmation',
        'm1','m2','m3','m4','m5','m6','m7','m8','m9','m10',
        'm11','m12','m13','m14','m15','m16','m17','m18','m19','m20'));

ALTER TABLE whatsapp_auto_reply_decisions DROP CONSTRAINT whatsapp_auto_reply_decisions_mode_chk;
ALTER TABLE whatsapp_auto_reply_decisions ADD CONSTRAINT whatsapp_auto_reply_decisions_mode_chk
    CHECK (mode = 'simulate');

ALTER TABLE whatsapp_auto_reply_settings DROP CONSTRAINT whatsapp_auto_reply_settings_mode_chk;
ALTER TABLE whatsapp_auto_reply_settings ADD CONSTRAINT whatsapp_auto_reply_settings_mode_chk
    CHECK (mode IN ('off', 'simulate'));
