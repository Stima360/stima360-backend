-- Down for 113. Toglie il marcatore dell'eliminazione logica degli operatori:
-- gli operatori eliminati tornano semplici account `disabled`. Nessun altro dato.

BEGIN;

ALTER TABLE operator_users DROP CONSTRAINT IF EXISTS operator_users_deleted_disabled_chk;
ALTER TABLE operator_users DROP COLUMN IF EXISTS deleted_by_user_id;
ALTER TABLE operator_users DROP COLUMN IF EXISTS deleted_at;

COMMIT;
