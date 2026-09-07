-- P26-1 CORE agency scoping, part 2 of 3: controlled legacy backfill.
--
-- Assigns every existing row in contacts, leads, activities and tasks to the
-- real Default Agency. Data only: no structure, no constraint, no index, no
-- trigger, no agent assignment, no invented creator attribution.
--
--   028  nullable structure
--   029  controlled backfill      <- this file
--   030  NOT NULL, composite FKs, agency-aware indexes, integrity trigger
--
-- 029 owns agency_id and nothing else. assigned_agent_id and
-- created_by_user_id stay NULL: legacy rows carry no trustworthy historical
-- operator identity, and inventing one would repeat exactly the error P26-0
-- refused when it declined to retro-register migrations 001-025. An
-- unassigned record is invisible to an agent and visible to an agency owner or
-- admin, which is the documented and intended consequence.
--
-- Transaction ownership: this file opens no transaction and commits nothing.
-- scripts/p26_migrate.py executes this body, writes the schema_migrations row
-- through register(), and commits both together, rolling back on any error.
-- So a failed guard below leaves the database exactly as it was.
-- See docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.
--
-- This is the one irreversible step of the three. Its down file refuses; see
-- 029_p26_core_agency_backfill_down.sql.

-- ---------------------------------------------------------------------------
-- Guard 1 - the Default Agency must resolve to exactly one active row,
-- BEFORE anything is written.
--
-- This guard is not decoration. Each UPDATE below resolves the agency with a
-- scalar subselect, and a subselect that matches nothing returns NULL rather
-- than failing: without this check a missing or suspended Default Agency would
-- quietly set agency_id = NULL on every legacy row, the four statements would
-- report success, and the damage would surface only later as a confusing
-- NOT NULL violation in 030.
--
-- Resolved by slug, never by a numeric id. agencies.slug is UNIQUE, so the
-- count can only be 0 or 1 - it is counted anyway, because a guard that
-- assumes its own precondition proves nothing.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    resolved_agencies INTEGER;
BEGIN
    SELECT count(*) INTO resolved_agencies
      FROM agencies
     WHERE slug = 'stima360' AND status = 'active';

    IF resolved_agencies <> 1 THEN
        RAISE EXCEPTION
            'P26-1 backfill refused: the Default Agency (slug ''stima360'', '
            'status ''active'') resolved to % rows, expected exactly 1. '
            'Apply 027 first, or reactivate the agency. No row was modified.',
            resolved_agencies;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- The backfill.
--
-- WHERE agency_id IS NULL on every statement, so re-execution before the
-- ledger row is written is a zero-row no-op and an agency already assigned to
-- a row is never overwritten. That matters more than "029 runs once" suggests:
-- if the runner dies between this body and register(), the next apply re-runs
-- the whole file.
-- ---------------------------------------------------------------------------
UPDATE contacts
   SET agency_id = (SELECT id FROM agencies WHERE slug = 'stima360' AND status = 'active')
 WHERE agency_id IS NULL;

UPDATE leads
   SET agency_id = (SELECT id FROM agencies WHERE slug = 'stima360' AND status = 'active')
 WHERE agency_id IS NULL;

UPDATE activities
   SET agency_id = (SELECT id FROM agencies WHERE slug = 'stima360' AND status = 'active')
 WHERE agency_id IS NULL;

UPDATE tasks
   SET agency_id = (SELECT id FROM agencies WHERE slug = 'stima360' AND status = 'active')
 WHERE agency_id IS NULL;

-- ---------------------------------------------------------------------------
-- Guard 2 - 029 certifies its own post-condition.
--
-- All four tables are counted, not one: a guard covering a subset would
-- certify nothing about the rest. The exception names every failing table with
-- its count, so an operator can diagnose without re-querying.
--
-- 030's SET NOT NULL is a second, independent gate. It is not a substitute for
-- this one: discovering an incomplete backfill during 030 would mean two
-- migrations to unpick instead of one refused transaction, and the runner
-- rolls this whole body back on a raise.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    null_contacts   BIGINT;
    null_leads      BIGINT;
    null_activities BIGINT;
    null_tasks      BIGINT;
    null_total      BIGINT;
BEGIN
    SELECT count(*) INTO null_contacts   FROM contacts   WHERE agency_id IS NULL;
    SELECT count(*) INTO null_leads      FROM leads      WHERE agency_id IS NULL;
    SELECT count(*) INTO null_activities FROM activities WHERE agency_id IS NULL;
    SELECT count(*) INTO null_tasks      FROM tasks      WHERE agency_id IS NULL;

    null_total := null_contacts + null_leads + null_activities + null_tasks;

    IF null_total <> 0 THEN
        RAISE EXCEPTION
            'P26-1 backfill incomplete: % row(s) still carry a NULL agency_id '
            '(contacts %, leads %, activities %, tasks %). The transaction is '
            'rolled back; no partial backfill is committed.',
            null_total, null_contacts, null_leads, null_activities, null_tasks;
    END IF;
END
$do$;
