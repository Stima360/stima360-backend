-- Rollback of P26-2B STIMA root ownership, part 2 of 3.
--
-- This migration is IRREVERSIBLE, and this file refuses rather than pretending
-- otherwise.
--
-- Why it cannot be undone:
--
-- 032 set stime.agency_id on every estimation that had none, deriving each
-- value from that estimation's own lead. It left no marker distinguishing a row
-- it touched from a row written afterwards, and by design it could not: any
-- such marker would itself be a column this staging exists to avoid.
--
-- The two populations are indistinguishable in a way that matters more here
-- than it did for 029. Since P26-2B2B the public writer stamps agency_id on
-- every new estimation, from the same server-side authority, into the same
-- column, with the same kind of value. A backfilled historical row and a row
-- the runtime wrote five minutes ago are identical in every respect this
-- schema records.
--
-- So a "reversal" would have to guess. Setting agency_id back to NULL for every
-- row would strip the ownership from every estimation taken since the backfill,
-- including all of them written by the live public endpoint. Setting it back
-- for "the historical ones" is not expressible.
--
-- A down that silently loses data is worse than a down that refuses. P26-0
-- section 7.3 requires the file to exist and to fail loudly, which is what it
-- does.
--
-- Recovery path: restore from the snapshot taken immediately before 032, as
-- specified in docs/P26_BACKUP_RESTORE_TEST.md. Take that snapshot between 031
-- and 032 precisely because this window is the one with no cheaper way back.
--
-- If the intent is to remove the column entirely rather than to undo the data,
-- roll back 031 instead, accepting that the backfilled values are dropped with
-- the column.
--
-- This file brackets its own transaction because it is executed manually rather
-- than by scripts/p26_migrate.py, which has no down command.

BEGIN;

DO $do$
BEGIN
    RAISE EXCEPTION
        'Rollback 032 refused: the STIMA agency backfill is irreversible. It '
        'left no way to tell an estimation it backfilled from one the runtime '
        'writer has stamped since, so clearing the column would discard the '
        'ownership of every estimation taken after the backfill. Recover by '
        'restoring the snapshot taken immediately before 032, per '
        'docs/P26_BACKUP_RESTORE_TEST.md. To remove the column entirely, roll '
        'back 031 instead, accepting that the backfilled values go with it.';
END
$do$;

COMMIT;
