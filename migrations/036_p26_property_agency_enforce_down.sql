-- Rollback of P26-2C PROPERTY root ownership, part 3 of 3.
--
-- Genuinely reversible. This migration wrote no data: it only constrained
-- and installed DDL objects. Every effect can be lifted exactly, and no
-- agency value is lost in the process.
--
-- Order is the exact reverse of the up migration:
--
--   1. the three child triggers          (they depend on the function)
--   2. the function
--   3. DROP NOT NULL on properties.agency_id
--
-- Deliberately NOT touched:
--
--   * properties.agency_id itself, and every value in it - those belong
--     to 034 and 035. Clearing or zeroing the column would discard the
--     backfill and the ownership of every property written since.
--   * The index idx_properties_agency_id from 034.
--   * The foreign key properties_agency_id_fk from 034.
--   * Every business row: this file changes no data.
--
-- No CASCADE anywhere. If something has come to depend on these objects,
-- that must fail loudly rather than be swept away silently.
--
-- This file brackets its own transaction because it is executed manually
-- rather than by scripts/p26_migrate.py, which has no down command.

BEGIN;

-- 1. Triggers on the three child tables (must precede function drop).
DROP TRIGGER IF EXISTS trg_property_contacts_agency_integrity ON property_contacts;
DROP TRIGGER IF EXISTS trg_property_leads_agency_integrity    ON property_leads;
DROP TRIGGER IF EXISTS trg_property_visits_agency_integrity   ON property_visits;

-- 2. The function, once nothing calls it.
DROP FUNCTION IF EXISTS property_agency_integrity();

-- 3. Nullability. Every value written by 035 and by the runtime writer
--    stays exactly where it is; only the constraint is lifted.
ALTER TABLE properties
    ALTER COLUMN agency_id DROP NOT NULL;

COMMIT;
