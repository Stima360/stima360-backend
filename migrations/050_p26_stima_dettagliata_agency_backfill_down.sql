-- Down for 050. Refuses.
--
-- 050 does not create structure; it decides ownership. Reversing it would mean
-- blanking `stime_dettagliate.agency_id` back to NULL, and the values it wrote
-- are indistinguishable from values a writer has set since - the runtime has
-- been stamping the column on every insert since P26-6C shipped.
--
-- So a down that blanked the column would not restore the pre-050 state; it
-- would destroy ownership for every row written after it, and 050 could not
-- re-derive those (a row written after the fact may have no parent to derive
-- from - that is why 049 gave this table a physical column in the first place).
--
-- The reversible half is 049, which drops the column outright. If the intent
-- is to undo this slice entirely, run 051_down, then this file's refusal is
-- moot, then 049_down.
--
-- Down files bracket themselves; the runner owns no transaction here.

BEGIN;

DO $do$
BEGIN
    RAISE EXCEPTION
        'P26-6C 050 is irreversible: it assigned ownership, and the values are now indistinguishable from those written by the runtime since. To remove the column entirely, run 051_down then 049_down.';
END
$do$;

COMMIT;
