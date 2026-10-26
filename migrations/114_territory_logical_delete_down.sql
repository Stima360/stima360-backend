-- Down for 114. Toglie il marcatore dell'eliminazione logica dei territori:
-- i territori eliminati tornano nell'elenco operativo. Nessun altro dato.

BEGIN;

ALTER TABLE network_territories DROP COLUMN IF EXISTS deleted_by_user_id;
ALTER TABLE network_territories DROP COLUMN IF EXISTS deleted_at;

COMMIT;
