-- Casella «Messaggi WhatsApp»: messaggi letti e non letti, per operatore.
--
-- Additiva, nessun backfill. Una riga per (agenzia, operatore, mittente) con
-- l'ultimo messaggio ricevuto che quell'operatore ha visto: i messaggi della
-- conversazione con id maggiore sono «non letti» per lui. La lettura di un
-- operatore non cambia lo stato degli altri. Il segno avanza soltanto (mai
-- indietro) e indica sempre un messaggio della STESSA agenzia e dello STESSO
-- mittente (trigger). Nessun dato del messaggio viene copiato.
--
-- La transazione e' del runner.

CREATE TABLE whatsapp_inbound_reads (
    agency_id             BIGINT       NOT NULL REFERENCES agencies(id),
    user_id               BIGINT       NOT NULL REFERENCES operator_users(id),
    sender_phone          TEXT         NOT NULL,
    last_read_message_id  BIGINT       NOT NULL REFERENCES whatsapp_inbound_messages(id),
    read_at               TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT whatsapp_inbound_reads_pk PRIMARY KEY (agency_id, user_id, sender_phone),
    CONSTRAINT whatsapp_inbound_reads_sender_chk CHECK (sender_phone ~ '^[0-9]{6,15}$')
);

CREATE FUNCTION whatsapp_inbound_reads_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
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

CREATE TRIGGER trg_whatsapp_inbound_reads_guard
    BEFORE INSERT OR UPDATE ON whatsapp_inbound_reads
    FOR EACH ROW EXECUTE FUNCTION whatsapp_inbound_reads_guard();
