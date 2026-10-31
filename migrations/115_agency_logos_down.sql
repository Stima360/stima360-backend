-- Down for 115. Rifiuta di perdere loghi caricati: se ce ne sono, conservare la
-- tabella (e' additiva, il codice precedente la ignora) o ripristinare da un
-- backup verificato. I dati agenzia in `agencies.settings` non sono toccati.
-- La transazione e' del runner.
LOCK TABLE agency_logos IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM agency_logos) THEN
        RAISE EXCEPTION 'Agency logos exist: preserve the table and restore from a verified backup';
    END IF;
END $$;
DROP TABLE agency_logos;
