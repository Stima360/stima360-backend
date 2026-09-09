-- Down for 053. Refuses, for the same reason 050 does.
--
-- 053 does not create structure; it decides ownership. Blanking the three
-- columns back to NULL would not restore the pre-053 state - it would destroy
-- ownership for every FLOW event, execution and suppression written since, and
-- those cannot be re-derived on demand: an execution's entity may have been
-- archived, and the entity reference is polymorphic with no foreign key
-- holding it in place.
--
-- The reversible half is 052, which drops the columns outright. To undo this
-- slice entirely: 054_down, then 052_down.
--
-- The helper function 053 creates is dropped at the end of 053 itself, so
-- there is nothing of it left here to remove.
--
-- Down files bracket themselves; the runner owns no transaction here.

BEGIN;

DO $do$
BEGIN
    RAISE EXCEPTION
        'P26-6C 053 is irreversible: it assigned ownership, and the values are now indistinguishable from those written by the runtime since. To remove the columns entirely, run 054_down then 052_down.';
END
$do$;

COMMIT;
