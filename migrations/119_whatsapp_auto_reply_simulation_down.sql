-- BLOCCO-4D - rollback della 119. Rifiuta se esistono decisioni o pause
-- registrate: il registro della simulazione si conserva e si ripristina da un
-- backup verificato, non si cancella. Le impostazioni (solo modo e data) si
-- eliminano con le tabelle.
--
-- La transazione e' del runner.

LOCK TABLE whatsapp_auto_reply_decisions, whatsapp_auto_reply_pauses, whatsapp_auto_reply_settings
    IN ACCESS EXCLUSIVE MODE;

DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM whatsapp_auto_reply_decisions)
       OR EXISTS (SELECT 1 FROM whatsapp_auto_reply_pauses) THEN
        RAISE EXCEPTION 'WhatsApp auto-reply records exist: preserve the tables and restore from a verified backup';
    END IF;
END $$;

DROP TABLE whatsapp_auto_reply_decisions;
DROP TABLE whatsapp_auto_reply_pauses;
DROP TABLE whatsapp_auto_reply_settings;
DROP FUNCTION whatsapp_auto_reply_decisions_guard();
