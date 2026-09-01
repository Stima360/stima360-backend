BEGIN;

-- Rollback della sola aggiunta 018: rimuove esclusivamente le due colonne
-- che questa migration ha introdotto su stime. Nessun'altra colonna,
-- tabella o riga viene toccata.
ALTER TABLE stime
    DROP COLUMN IF EXISTS consenso_marketing_at;

ALTER TABLE stime
    DROP COLUMN IF EXISTS consenso_marketing;

COMMIT;
