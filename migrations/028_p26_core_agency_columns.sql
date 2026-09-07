-- P26-1 CORE agency scoping, part 1 of 3: nullable structure.
--
-- Additive, idempotent, and deliberately inert. This migration adds ten
-- nullable columns and four plain indexes. It writes no row, constrains
-- nothing, defaults nothing and resolves no agency.
--
-- Staging, from P26-0 section 6 rule 9:
--
--   028  nullable structure          <- this file
--   029  controlled backfill
--   030  NOT NULL, composite FKs, agency-aware indexes, integrity trigger
--
-- The three steps stay separate because the gate between them is the point.
-- 029 can be run and its row counts verified against the pre-migration totals
-- before 030 is allowed to run, and 030's SET NOT NULL is what *proves* the
-- backfill reached every row. A DEFAULT here would make every row look correct
-- and destroy that proof, which is why none of these columns has one.
--
-- Transaction ownership: this file opens no transaction and commits nothing.
-- scripts/p26_migrate.py executes this body, writes the schema_migrations row
-- through register(), and commits both together. See
-- docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md. The paired _down.sql keeps its
-- own BEGIN/COMMIT because it is executed manually.
--
-- Column plan:
--
--   contacts    agency_id, assigned_agent_id, created_by_user_id
--   leads       agency_id, assigned_agent_id, created_by_user_id
--   activities  agency_id, created_by_user_id
--   tasks       agency_id, created_by_user_id
--
-- activities and tasks get no assigned_agent_id: they are visible through
-- their agency rather than per record, and they already carry a free-text
-- assigned_to written by flow/, followup/ and buy/. Narrowing an agent's view
-- of them is a later phase, not this one.
--
-- BIGINT throughout, because agencies.id and operator_users.id are BIGSERIAL.
--
-- Delete actions:
--   agency_id           ON DELETE RESTRICT   an agency holding CORE records
--                                            must not vanish by accident
--   assigned_agent_id   ON DELETE SET NULL   removing a person must never
--   created_by_user_id  ON DELETE SET NULL   cascade-delete customer records

ALTER TABLE contacts
    ADD COLUMN IF NOT EXISTS agency_id          BIGINT REFERENCES agencies(id)       ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS assigned_agent_id  BIGINT REFERENCES operator_users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS created_by_user_id BIGINT REFERENCES operator_users(id) ON DELETE SET NULL;

ALTER TABLE leads
    ADD COLUMN IF NOT EXISTS agency_id          BIGINT REFERENCES agencies(id)       ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS assigned_agent_id  BIGINT REFERENCES operator_users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS created_by_user_id BIGINT REFERENCES operator_users(id) ON DELETE SET NULL;

ALTER TABLE activities
    ADD COLUMN IF NOT EXISTS agency_id          BIGINT REFERENCES agencies(id)       ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS created_by_user_id BIGINT REFERENCES operator_users(id) ON DELETE SET NULL;

ALTER TABLE tasks
    ADD COLUMN IF NOT EXISTS agency_id          BIGINT REFERENCES agencies(id)       ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS created_by_user_id BIGINT REFERENCES operator_users(id) ON DELETE SET NULL;

-- Plain single-column indexes only. They serve the 029 backfill's
-- WHERE agency_id IS NULL and 030's SET NOT NULL scan.
--
-- The agency-aware composite indexes that mirror the real ORDER BY clauses in
-- core/repository.py belong to 030, once the column is known to be populated
-- and NOT NULL; building them here would index a column that is entirely NULL.
--
-- These are ordinary, transactional index builds. A concurrent build cannot
-- run inside a transaction and the runner owns this one, so if PROD volumes
-- ever require that form it becomes a dedicated migration marked
-- '-- NON-TRANSACTIONAL' under P26-0 rule 10.

CREATE INDEX IF NOT EXISTS idx_contacts_agency_id   ON contacts   (agency_id);
CREATE INDEX IF NOT EXISTS idx_leads_agency_id      ON leads      (agency_id);
CREATE INDEX IF NOT EXISTS idx_activities_agency_id ON activities (agency_id);
CREATE INDEX IF NOT EXISTS idx_tasks_agency_id      ON tasks      (agency_id);
