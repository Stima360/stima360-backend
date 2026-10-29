-- Down for 117. Rifiuta di perdere documenti: se esistono PDF di valutazioni,
-- conservare la tabella (e' additiva, il codice precedente la ignora) o
-- ripristinare da un backup verificato. Nessun'altra tabella e' toccata.
-- La transazione e' del runner.
LOCK TABLE property_valuation_pdfs IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM property_valuation_pdfs) THEN
        RAISE EXCEPTION 'Valuation PDFs exist: preserve the table and restore from a verified backup';
    END IF;
END $$;
DROP TABLE property_valuation_pdfs;
DROP FUNCTION property_valuation_pdfs_guard();
