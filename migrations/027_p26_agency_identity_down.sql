-- Rollback of the P26-1 agency and operator identity foundation.
--
-- 027 is purely additive, so its rollback is complete and safe on its own
-- terms. It removes exactly the objects 027 introduced:
--
--   1. table operator_sessions   (and its two indexes)
--   2. table agency_memberships  (and its two indexes, two partial uniques)
--   3. table operator_users
--   4. table agencies
--
-- The drop order is the reverse of the foreign key direction. No CASCADE is
-- used anywhere: a CASCADE would silently remove whatever came to depend on
-- these tables, and the whole point of the guard below is that such a
-- dependency must stop the rollback rather than be swept away.
--
-- What this rollback destroys, and it is not recoverable from here: every
-- operator account, every membership and every live session. There is no
-- backup of them inside this file. Restore from the pre-migration snapshot if
-- those rows matter.

BEGIN;

-- ---------------------------------------------------------------------------
-- Guard 1 - a later CORE scoping migration must not still be in place.
--
-- Once 028 has added contacts.agency_id REFERENCES agencies(id), dropping
-- agencies would fail on the foreign key. That failure is correct but
-- cryptic, so it is anticipated here with an actionable message. Same
-- philosophy as the guard in 026_p26_baseline_down.sql.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE dependent TEXT;
BEGIN
    SELECT string_agg(table_name, ', ' ORDER BY table_name)
      INTO dependent
      FROM information_schema.columns
     WHERE table_schema = 'public'
       AND column_name = 'agency_id'
       AND table_name IN ('contacts', 'leads', 'activities', 'tasks');

    IF dependent IS NOT NULL THEN
        RAISE EXCEPTION
            'Rollback 027 refused: agency_id is still present on %. Roll back '
            'the CORE scoping migrations (030, 029, 028) first, or restore '
            'from the pre-migration snapshot.',
            dependent;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- Guard 2 - the rollback operator must identify themselves.
--
-- schema_migrations_rollback_consistency requires a non-blank operator
-- whenever rolled_back_at is set. Reading it from a run-time setting keeps the
-- identity a deliberate act by the person running the rollback, and never a
-- value inherited from the shared administrative login.
--
-- Set it first:
--     SET p26.rollback_operator = 'firstname.lastname';
-- ---------------------------------------------------------------------------
DO $do$
DECLARE rollback_operator TEXT;
BEGIN
    rollback_operator := BTRIM(COALESCE(current_setting('p26.rollback_operator', true), ''));

    IF rollback_operator = '' THEN
        RAISE EXCEPTION
            'Rollback 027 refused: no operator identity. Run '
            'SET p26.rollback_operator = ''firstname.lastname''; first, so the '
            'ledger records who performed this rollback.';
    END IF;

    -- The ledger is append-only. A rollback is stamped onto the existing row,
    -- never removed: a rolled-back version is consumed, not forgotten.
    UPDATE schema_migrations
       SET rolled_back_at          = NOW(),
           rolled_back_by_operator = rollback_operator
     WHERE version = '027_p26_agency_identity'
       AND rolled_back_at IS NULL;
END
$do$;

-- ---------------------------------------------------------------------------
-- Drop, in foreign-key-reverse order.
--
-- Indexes belonging to a dropped table go with it; they are not dropped
-- separately.
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS operator_sessions;

DROP TABLE IF EXISTS agency_memberships;

DROP TABLE IF EXISTS operator_users;

DROP TABLE IF EXISTS agencies;

COMMIT;
