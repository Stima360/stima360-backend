-- Down per 128. Rifiuta se esistono firmatari: sono prove di richieste di firma, si conservano
-- (additivi, il codice precedente li ignora). La transazione e' del runner.
DO $do$
BEGIN
    IF EXISTS (SELECT 1 FROM signature_request_signers) THEN
        RAISE EXCEPTION 'signature_request_signers has rows: keep the table or restore from a verified backup';
    END IF;
END
$do$;
DROP TRIGGER IF EXISTS trg_signature_request_signers_guard ON signature_request_signers;
DROP FUNCTION IF EXISTS signature_request_signers_guard();
DROP TABLE IF EXISTS signature_request_signers;
