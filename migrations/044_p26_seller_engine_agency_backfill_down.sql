-- Rollback of the P26-6A seller engine backfill.
--
-- This migration is IRREVERSIBLE, and this file refuses rather than pretending
-- otherwise.
--
-- Why it cannot be undone:
--
-- 044 set seller_timeline_events.agency_id and followup_actions.agency_id on
-- every row that had none, deriving each value from that row's own references.
-- It left no marker distinguishing a row it touched from one written afterwards,
-- and by design it could not: any such marker would itself be a column this
-- staging exists to avoid.
--
-- Since P26-6A the runtime stamps an agency on every new timeline event and
-- follow-up action, from the same derivation, into the same column, with the
-- same kind of value. A backfilled historical row and one written a minute ago
-- are identical in every respect this schema records.
--
-- So a "reversal" would have to guess. Clearing the column would strip the
-- tenancy from every event and action recorded since the backfill, including
-- everything the public estimation funnel has written. Clearing it for "the
-- historical ones" is not expressible.
--
-- A down that silently loses data is worse than a down that refuses. P26-0
-- section 7.3 requires the file to exist and to fail loudly, which is what it
-- does.
--
-- Recovery path: restore from the snapshot taken immediately before 044, as
-- specified in docs/P26_BACKUP_RESTORE_TEST.md. Take that snapshot between 043
-- and 044 precisely because this window is the one with no cheaper way back.
--
-- To remove the columns entirely rather than undo the data, roll back 045 and
-- then 043, accepting that the backfilled values go with the columns.
--
-- This file brackets its own transaction because it is executed manually rather
-- than by scripts/p26_migrate.py, which has no down command.

BEGIN;

DO $do$
BEGIN
    RAISE EXCEPTION
        'Rollback 044 refused: the seller engine agency backfill is irreversible. '
        'It left no way to tell a row it backfilled from one the runtime has '
        'written since, so clearing agency_id would discard the tenancy of every '
        'timeline event and follow-up action recorded after the backfill. '
        'Recover by restoring the snapshot taken immediately before 044, per '
        'docs/P26_BACKUP_RESTORE_TEST.md. To drop the columns entirely, roll '
        'back 045 and then 043 instead.';
END
$do$;

COMMIT;
