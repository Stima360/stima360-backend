-- Runner-owned transaction. Refuse data loss; use a verified DB backup or
-- keep the additive schema when rolling application code back.
-- Block concurrent inserts before checking emptiness, in this same transaction.
LOCK TABLE stima_pdf_artifacts IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM stima_pdf_artifacts) THEN
        RAISE EXCEPTION 'Private PDFs exist: preserve the table and restore from a verified backup';
    END IF;
END $$;
DROP TABLE stima_pdf_artifacts;
DROP FUNCTION enforce_stima_pdf_agency();
