-- Rollback of the P26-6B intelligence backfill.
--
-- This migration is IRREVERSIBLE, and this file refuses rather than pretending
-- otherwise.
--
-- Why it cannot be undone:
--
-- 047 set property_watches.agency_id and next_best_actions.agency_id on every
-- row that had none, deriving each value from that row's own provenance. It
-- left no marker distinguishing a row it touched from one written afterwards,
-- and by design it could not: any such marker would itself be a column this
-- staging exists to avoid.
--
-- Since P26-6B the runtime stamps an agency on every new watch and on every row
-- a next-best-action refresh writes, from the same derivation, into the same
-- column, with the same kind of value. A backfilled row and one written a
-- minute ago are identical in every respect this schema records.
--
-- So a "reversal" would have to guess. Clearing the columns would strip the
-- tenancy from every watch opened and every action generated since the
-- backfill. Clearing it for "the historical ones" is not expressible.
--
-- A down that silently loses data is worse than a down that refuses. P26-0
-- section 7.3 requires the file to exist and to fail loudly, which is what it
-- does.
--
-- Recovery path: restore from the snapshot taken immediately before 047, as
-- specified in docs/P26_BACKUP_RESTORE_TEST.md. Take that snapshot between 046
-- and 047 precisely because this window is the one with no cheaper way back.
--
-- To remove the columns entirely rather than undo the data, roll back 048 and
-- then 046, accepting that the backfilled values go with the columns.
--
-- This file brackets its own transaction because it is executed manually rather
-- than by scripts/p26_migrate.py, which has no down command.

BEGIN;

DO $do$
BEGIN
    RAISE EXCEPTION
        'Rollback 047 refused: the intelligence agency backfill is irreversible. '
        'It left no way to tell a row it backfilled from one the runtime has '
        'written since, so clearing agency_id would discard the tenancy of every '
        'property watch and next best action created after the backfill. '
        'Recover by restoring the snapshot taken immediately before 047, per '
        'docs/P26_BACKUP_RESTORE_TEST.md. To drop the columns entirely, roll '
        'back 048 and then 046 instead.';
END
$do$;

COMMIT;
