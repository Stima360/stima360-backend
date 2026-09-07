-- Rollback of P26-1 CORE agency scoping, part 1 of 3.
--
-- 028 is purely additive, so its rollback is complete: it removes exactly the
-- four indexes and ten columns 028 introduced, and nothing else.
--
-- What this destroys, and it is not recoverable from here: any agency
-- assignment, creator attribution or agency stamp written into contacts,
-- leads, activities or tasks since 028 was applied. Immediately after 028 that
-- is nothing at all - every column is NULL - which is precisely why the 028
-- rollback window is the cheap one. Once 029 has backfilled, rolling back this
-- far discards the backfill too; restore from the pre-029 snapshot instead.
--
-- Order: indexes first, then columns. An index depends on the column it
-- covers, so dropping the column first would force PostgreSQL to remove the
-- index implicitly - the same outcome, but by side effect rather than by
-- statement, and a rollback should say exactly what it removes.
--
-- No CASCADE anywhere. If something has come to depend on these columns -
-- 030's composite foreign keys, most obviously - this must fail loudly rather
-- than quietly demolish it. Roll 030 back first.
--
-- This file keeps its own transaction because it is executed manually rather
-- than by scripts/p26_migrate.py, which has no down command. Under psql each
-- statement would otherwise autocommit on its own, leaving a half-reverted
-- schema if a later statement failed.

BEGIN;

DROP INDEX IF EXISTS idx_contacts_agency_id;
DROP INDEX IF EXISTS idx_leads_agency_id;
DROP INDEX IF EXISTS idx_activities_agency_id;
DROP INDEX IF EXISTS idx_tasks_agency_id;

ALTER TABLE contacts
    DROP COLUMN IF EXISTS created_by_user_id,
    DROP COLUMN IF EXISTS assigned_agent_id,
    DROP COLUMN IF EXISTS agency_id;

ALTER TABLE leads
    DROP COLUMN IF EXISTS created_by_user_id,
    DROP COLUMN IF EXISTS assigned_agent_id,
    DROP COLUMN IF EXISTS agency_id;

ALTER TABLE activities
    DROP COLUMN IF EXISTS created_by_user_id,
    DROP COLUMN IF EXISTS agency_id;

ALTER TABLE tasks
    DROP COLUMN IF EXISTS created_by_user_id,
    DROP COLUMN IF EXISTS agency_id;

COMMIT;
