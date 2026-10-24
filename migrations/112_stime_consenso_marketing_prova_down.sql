-- Down for 112. Toglie le due colonne della prova (e i loro vincoli).
-- ATTENZIONE: cancella le prove registrate con le richieste dopo la 112.

BEGIN;

ALTER TABLE stime DROP CONSTRAINT IF EXISTS stime_consenso_marketing_privacy_sha256_chk;
ALTER TABLE stime DROP CONSTRAINT IF EXISTS stime_consenso_marketing_testo_chk;
ALTER TABLE stime DROP COLUMN IF EXISTS consenso_marketing_privacy_sha256;
ALTER TABLE stime DROP COLUMN IF EXISTS consenso_marketing_testo;

COMMIT;
