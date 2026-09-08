-- Rollback of P26-2C PROPERTY root ownership, part 2 of 3.
--
-- This migration is IRREVERSIBLE, and this file refuses rather than pretending
-- otherwise.
--
-- Why it cannot be undone:
--
-- 035 set properties.agency_id on every property that had none, deriving each
-- value from recorded provenance or safe Default Agency fallback.
-- It left no marker distinguishing a row it touched from a row written afterwards,
-- and by design it could not: any such marker would itself be a column this
-- staging exists to avoid.
--
-- Since P26-2C2B the property create writer stamps agency_id on every new
-- property. A backfilled historical row and a row written by the runtime writer
-- are identical in every respect this schema records.
--
-- So a "reversal" would have to guess. Setting agency_id back to NULL for every
-- row would strip the ownership from every property created since the backfill.
-- Setting it back for "the historical ones" is not expressible.
--
-- A down that silently loses data is worse than a down that refuses. P26-0
-- section 7.3 requires the file to exist and to fail loudly, which is what it
-- does.
--
-- Recovery path: restore required from pre-035 database backup, as
-- specified in docs/P26_BACKUP_RESTORE_TEST.md. Take that snapshot between 034
-- and 035 precisely because this window is the one with no cheaper way back.
--
-- If the intent is to remove the column entirely rather than to undo the data,
-- roll back 034 instead, accepting that the backfilled values are dropped with
-- the column.
--
-- This file brackets its own transaction because it is executed manually rather
-- than by scripts/p26_migrate.py, which has no down command.

BEGIN;

DO $do$
BEGIN
    RAISE EXCEPTION
        'Rollback 035 refused: the PROPERTY agency backfill is irreversible. '
        'Restore required from pre-035 database backup per docs/P26_BACKUP_RESTORE_TEST.md. '
        'Setting agency_id back to NULL would destroy tenancy on records written since. '
        'To remove the column entirely, roll back 034 instead, accepting that backfilled '
        'values are lost with it.';
END
$do$;

COMMIT;
