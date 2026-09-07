-- Rollback of P26-1 CORE agency scoping, part 3 of 3.
--
-- Fully reversible. It removes exactly what 030 added and returns the schema
-- to its post-029 state: the agency_id columns stay, still populated, merely
-- no longer enforced.
--
-- Order is the exact reverse of the up migration:
--
--   1. the two triggers        (they depend on the function)
--   2. the function
--   3. the three composite FKs (they depend on the unique key)
--   4. the one unique key
--   5. the nine agency-aware indexes
--   6. DROP NOT NULL on the four agency_id columns
--
-- Deliberately NOT touched:
--
--   * the agency_id, assigned_agent_id and created_by_user_id columns - those
--     belong to 028, and dropping them here would discard the 029 backfill
--   * the four plain indexes from 028
--   * agencies, operator_users, agency_memberships, operator_sessions
--   * every business row: this file changes no data
--
-- Only contacts_agency_scope_unq is dropped. leads_agency_scope_unq is not
-- dropped because it must never have existed: nothing references
-- leads(agency_id, id).
--
-- No CASCADE anywhere. If something has come to depend on these objects, that
-- must fail loudly rather than be swept away silently.
--
-- This file brackets its own transaction because it is executed manually
-- rather than by scripts/p26_migrate.py, which has no down command.

BEGIN;

-- 1. Triggers.
DROP TRIGGER IF EXISTS trg_activities_agency_integrity ON activities;
DROP TRIGGER IF EXISTS trg_tasks_agency_integrity ON tasks;

-- 2. The function, once nothing calls it.
DROP FUNCTION IF EXISTS core_agency_integrity();

-- 3. The composite foreign keys, before the key they reference.
ALTER TABLE leads    DROP CONSTRAINT IF EXISTS leads_contact_same_agency_fk;
ALTER TABLE contacts DROP CONSTRAINT IF EXISTS contacts_agent_same_agency_fk;
ALTER TABLE leads    DROP CONSTRAINT IF EXISTS leads_agent_same_agency_fk;

-- 4. The referencable unique key.
ALTER TABLE contacts DROP CONSTRAINT IF EXISTS contacts_agency_scope_unq;

-- 5. The nine agency-aware indexes. The four plain ones from 028 remain.
DROP INDEX IF EXISTS idx_contacts_agency_created;
DROP INDEX IF EXISTS idx_contacts_agency_agent;
DROP INDEX IF EXISTS idx_contacts_agency_email;
DROP INDEX IF EXISTS idx_contacts_agency_phone;
DROP INDEX IF EXISTS idx_leads_agency_created;
DROP INDEX IF EXISTS idx_leads_agency_agent;
DROP INDEX IF EXISTS idx_leads_agency_contact;
DROP INDEX IF EXISTS idx_activities_agency_occurred;
DROP INDEX IF EXISTS idx_tasks_agency_due;

-- 6. Nullability. The values written by 029 are left in place.
ALTER TABLE contacts   ALTER COLUMN agency_id DROP NOT NULL;
ALTER TABLE leads      ALTER COLUMN agency_id DROP NOT NULL;
ALTER TABLE activities ALTER COLUMN agency_id DROP NOT NULL;
ALTER TABLE tasks      ALTER COLUMN agency_id DROP NOT NULL;

COMMIT;
