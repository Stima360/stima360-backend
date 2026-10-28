-- Down for 116. Rifiuta di perdere valutazioni: se ce ne sono, conservare la
-- tabella (e' additiva, il codice precedente la ignora) o ripristinare da un
-- backup verificato. Nessun'altra tabella e' toccata.
-- La transazione e' del runner.
LOCK TABLE property_valuations IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM property_valuations) THEN
        RAISE EXCEPTION 'Property valuations exist: preserve the table and restore from a verified backup';
    END IF;
END $$;
DROP TABLE property_valuations;
DROP FUNCTION property_valuations_guard();
