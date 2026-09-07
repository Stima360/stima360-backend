-- Rollback of P26-1 CORE agency scoping, part 2 of 3.
--
-- This migration is IRREVERSIBLE, and this file refuses rather than pretending
-- otherwise.
--
-- Why it cannot be undone:
--
-- 029 set contacts.agency_id, leads.agency_id, activities.agency_id and
-- tasks.agency_id on every row that had none. It left no marker distinguishing
-- a row it touched from a row written afterwards, and by design it could not:
-- any such marker would itself be a column this staging exists to avoid.
--
-- So a "reversal" would have to guess. Setting agency_id back to NULL for
-- every row would discard the agency of every record created since the
-- backfill - including every public estimation routed to the Default Agency
-- and every record an operator has since assigned. Setting it back for "the
-- legacy ones" is not expressible: after 029 the two populations are
-- indistinguishable.
--
-- A down that silently loses data is worse than a down that refuses. P26-0
-- section 7.3 requires the file to exist and to fail loudly, which is what it
-- does.
--
-- Recovery path: restore from the snapshot taken immediately before 029, as
-- specified in docs/P26_BACKUP_RESTORE_TEST.md. The migration runbook takes
-- that snapshot between 028 and 029 precisely because this window is the one
-- with no cheaper way back.
--
-- This file brackets its own transaction because it is executed manually
-- rather than by scripts/p26_migrate.py, which has no down command.

BEGIN;

DO $do$
BEGIN
    RAISE EXCEPTION
        'Rollback 029 refused: the CORE agency backfill is irreversible. It '
        'left no way to tell a row it backfilled from one written afterwards, '
        'so setting agency_id back to NULL would discard the agency of every '
        'record created since - including public estimations and operator '
        'assignments. Recover by restoring the snapshot taken immediately '
        'before 029, per docs/P26_BACKUP_RESTORE_TEST.md. If you intended to '
        'roll back the CORE columns entirely, roll back 030 and then 028 '
        'instead, accepting that the backfilled values are lost with them.';
END
$do$;

COMMIT;
