-- Down for 118. Rifiuta di perdere messaggi, associazioni o eventi ricevuti:
-- se una delle tabelle contiene righe, conservarle (sono additive, il codice
-- precedente le ignora) o ripristinare da un backup verificato.
-- `whatsapp_incoming` non e' toccata. La transazione e' del runner.
LOCK TABLE whatsapp_inbound_messages, whatsapp_inbound_message_links, whatsapp_webhook_events
    IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM whatsapp_inbound_messages)
       OR EXISTS (SELECT 1 FROM whatsapp_inbound_message_links)
       OR EXISTS (SELECT 1 FROM whatsapp_webhook_events) THEN
        RAISE EXCEPTION 'WhatsApp inbound data exist: preserve the tables and restore from a verified backup';
    END IF;
END $$;
DROP TABLE whatsapp_webhook_events;
DROP TABLE whatsapp_inbound_message_links;
DROP TABLE whatsapp_inbound_messages;
DROP FUNCTION whatsapp_inbound_message_links_append_only();
DROP FUNCTION whatsapp_inbound_messages_guard();
