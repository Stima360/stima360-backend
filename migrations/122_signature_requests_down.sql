-- BLOCCO-6B - rollback della 122. Rifiuta se esistono richieste di firma:
-- richieste, esiti e documenti ricevuti si conservano e si ripristinano da un
-- backup verificato.
--
-- La transazione e' del runner.

LOCK TABLE signature_request_events, signature_requests IN ACCESS EXCLUSIVE MODE;

DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM signature_requests) OR EXISTS (SELECT 1 FROM signature_request_events) THEN
        RAISE EXCEPTION 'Signature requests exist: preserve the tables and restore from a verified backup';
    END IF;
END $$;

DROP TABLE signature_request_events;
DROP TABLE signature_requests;
DROP FUNCTION signature_request_events_append_only();
DROP FUNCTION signature_requests_guard();
