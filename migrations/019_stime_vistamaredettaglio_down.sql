BEGIN;

-- Rollback della sola aggiunta 019: rimuove esclusivamente la colonna che
-- questa migration ha introdotto su stime. Nessun'altra colonna, tabella
-- o riga viene toccata.
ALTER TABLE stime
    DROP COLUMN IF EXISTS vistamaredettaglio;

COMMIT;
