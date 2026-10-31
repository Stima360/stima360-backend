-- BLOCCO-6A - rollback della 121. Rifiuta se esistono fogli visita: i
-- documenti si conservano e si ripristinano da un backup verificato.
--
-- La transazione e' del runner.

LOCK TABLE visit_sheets IN ACCESS EXCLUSIVE MODE;

DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM visit_sheets) THEN
        RAISE EXCEPTION 'Visit sheets exist: preserve the table and restore from a verified backup';
    END IF;
END $$;

DROP TABLE visit_sheets;
DROP FUNCTION visit_sheets_guard();
