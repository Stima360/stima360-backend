-- SITE-IMPORT-1 down: rifiuta se il registro contiene importazioni.
-- Senza il registro il sincronizzatore non saprebbe piu' cosa ha gia' importato
-- e ricreerebbe record cancellati di proposito dagli operatori.
LOCK TABLE site_import_records IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM site_import_records) THEN
        RAISE EXCEPTION 'Cannot rollback a populated site import ledger';
    END IF;
END $$;
DROP TABLE site_import_records;
