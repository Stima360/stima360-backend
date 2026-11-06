-- Down per 130. Le impostazioni delle caselle e gli accessi si eliminano (sono
-- configurazione: il codice precedente non le legge). I segni di lettura
-- tornano per (agenzia, operatore, mittente): fra due caselle resta il segno
-- piu' avanzato. Le pause restano per (agenzia, mittente): se la stessa
-- conversazione era in pausa su due caselle resta la piu' vecchia.
-- Nessun messaggio si perde. La transazione e' del runner.

DROP TRIGGER IF EXISTS trg_whatsapp_inbox_access_guard ON whatsapp_inbox_access;
DROP FUNCTION IF EXISTS whatsapp_inbox_access_guard();
DROP TABLE IF EXISTS whatsapp_inbox_access;
DROP TABLE IF EXISTS whatsapp_inboxes;

DROP INDEX IF EXISTS uq_whatsapp_auto_reply_pauses_open;
UPDATE whatsapp_auto_reply_pauses p SET resumed_at = NOW(), resumed_by_user_id = p.paused_by_user_id
 WHERE p.resumed_at IS NULL AND EXISTS (
    SELECT 1 FROM whatsapp_auto_reply_pauses q
     WHERE q.agency_id = p.agency_id AND q.sender_phone = p.sender_phone AND q.resumed_at IS NULL AND q.id < p.id);
ALTER TABLE whatsapp_auto_reply_pauses DROP COLUMN IF EXISTS phone_number_id;
CREATE UNIQUE INDEX uq_whatsapp_auto_reply_pauses_open
    ON whatsapp_auto_reply_pauses (agency_id, sender_phone) WHERE resumed_at IS NULL;

ALTER TABLE whatsapp_inbound_reads DROP CONSTRAINT IF EXISTS whatsapp_inbound_reads_pk;
DELETE FROM whatsapp_inbound_reads r
 WHERE EXISTS (SELECT 1 FROM whatsapp_inbound_reads q
                WHERE q.agency_id = r.agency_id AND q.user_id = r.user_id AND q.sender_phone = r.sender_phone
                  AND (q.last_read_message_id > r.last_read_message_id
                       OR (q.last_read_message_id = r.last_read_message_id AND q.phone_number_id < r.phone_number_id)));
ALTER TABLE whatsapp_inbound_reads DROP COLUMN IF EXISTS phone_number_id;
ALTER TABLE whatsapp_inbound_reads
    ADD CONSTRAINT whatsapp_inbound_reads_pk PRIMARY KEY (agency_id, user_id, sender_phone);

CREATE OR REPLACE FUNCTION whatsapp_inbound_reads_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_agency BIGINT;
    v_sender TEXT;
BEGIN
    IF TG_OP = 'UPDATE' AND (NEW.agency_id IS DISTINCT FROM OLD.agency_id OR NEW.user_id IS DISTINCT FROM OLD.user_id
                             OR NEW.sender_phone IS DISTINCT FROM OLD.sender_phone
                             OR NEW.last_read_message_id < OLD.last_read_message_id) THEN
        RAISE EXCEPTION 'whatsapp inbound read marker: only forward moves of the same conversation';
    END IF;
    SELECT agency_id, sender_phone INTO v_agency, v_sender
      FROM whatsapp_inbound_messages WHERE id = NEW.last_read_message_id;
    IF v_agency IS DISTINCT FROM NEW.agency_id OR v_sender IS DISTINCT FROM NEW.sender_phone THEN
        RAISE EXCEPTION 'whatsapp inbound read marker: message % belongs to another conversation',
            NEW.last_read_message_id;
    END IF;
    RETURN NEW;
END;
$$;
