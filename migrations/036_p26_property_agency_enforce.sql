-- P26-2C PROPERTY root ownership, part 3 of 3: structural enforcement.
--
-- Closes the agency integrity of PROPERTY after:
--
--   034  nullable structure      (applied)
--   035  controlled backfill     (applied)
--   036  NOT NULL + triggers     <- this file
--
-- Three things happen, in this fixed order (each is only safe once the
-- prior holds):
--
--   1. Prechecks   -- hard fail if data is not clean (035 did not run, or
--                     a cross-agency relationship already exists)
--   2. SET NOT NULL on properties.agency_id
--   3. Function  property_agency_integrity()
--   4. Triggers  BEFORE INSERT OR UPDATE on the three child tables
--
-- WHAT THE TRIGGER DOES
--
-- Child tables (property_contacts, property_leads, property_visits) are
-- CHILD-DERIVED in the P26-2C ownership matrix: they carry no agency_id
-- column of their own. The trigger reads the parent property's agency_id and
-- compares it to the linked entity (contact, lead) from the same family.
-- Any mismatch is refused with RAISE EXCEPTION.
--
-- The function is generic: it branches on TG_TABLE_NAME so a single body
-- serves all three child tables. No agency_id is hardcoded.
--
-- REVERSIBILITY
--
-- This migration writes no data. Its down file therefore genuinely reverses
-- it: DROP the triggers, DROP the function, DROP NOT NULL.
--
-- Transaction ownership: this file opens no transaction and commits nothing.
-- scripts/p26_migrate.py executes this body, writes the schema_migrations row
-- through register(), and commits both together, rolling back on any error.
-- See docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.

-- ---------------------------------------------------------------------------
-- 1. Prechecks: data must be clean before any DDL is attempted.
--
-- All five checks are HARD FAIL (RAISE EXCEPTION) when their count > 0.
-- Any failure aborts the migration and leaves nothing behind.
-- ---------------------------------------------------------------------------

-- 1a. No NULL agency_id on properties (035 must have run).
DO $do$
DECLARE
    v_null_count BIGINT;
BEGIN
    SELECT count(*) INTO v_null_count
      FROM properties
     WHERE agency_id IS NULL;

    IF v_null_count > 0 THEN
        RAISE EXCEPTION
            'P26-2C 036 refused: % properties have agency_id IS NULL. '
            'Run migration 035 first. No DDL was applied.',
            v_null_count;
    END IF;
END
$do$;

-- 1b. No property_contacts row linking a contact from a different agency.
DO $do$
DECLARE
    v_mismatch BIGINT;
    v_sample   TEXT;
BEGIN
    SELECT count(*), string_agg(pc.id::text, ', ')
      INTO v_mismatch, v_sample
      FROM property_contacts pc
      JOIN properties  p ON p.id = pc.property_id
      JOIN contacts    c ON c.id = pc.contact_id
     WHERE c.agency_id IS NOT NULL
       AND p.agency_id <> c.agency_id;

    IF v_mismatch > 0 THEN
        RAISE EXCEPTION
            'P26-2C 036 refused: % property_contacts row(s) link a contact '
            'from a different agency (property_contacts.id: %). '
            'Reconcile these rows first. No DDL was applied.',
            v_mismatch, v_sample;
    END IF;
END
$do$;

-- 1c. No property_leads row linking a lead from a different agency.
DO $do$
DECLARE
    v_mismatch BIGINT;
    v_sample   TEXT;
BEGIN
    SELECT count(*), string_agg(pl.id::text, ', ')
      INTO v_mismatch, v_sample
      FROM property_leads pl
      JOIN properties p ON p.id = pl.property_id
      JOIN leads      l ON l.id = pl.lead_id
     WHERE l.agency_id IS NOT NULL
       AND p.agency_id <> l.agency_id;

    IF v_mismatch > 0 THEN
        RAISE EXCEPTION
            'P26-2C 036 refused: % property_leads row(s) link a lead '
            'from a different agency (property_leads.id: %). '
            'Reconcile these rows first. No DDL was applied.',
            v_mismatch, v_sample;
    END IF;
END
$do$;

-- 1d. No property_visits row whose contact is from a different agency.
DO $do$
DECLARE
    v_mismatch BIGINT;
    v_sample   TEXT;
BEGIN
    SELECT count(*), string_agg(pv.id::text, ', ')
      INTO v_mismatch, v_sample
      FROM property_visits pv
      JOIN properties p ON p.id = pv.property_id
      JOIN contacts   c ON c.id = pv.contact_id
     WHERE c.agency_id IS NOT NULL
       AND p.agency_id <> c.agency_id;

    IF v_mismatch > 0 THEN
        RAISE EXCEPTION
            'P26-2C 036 refused: % property_visits row(s) have a contact '
            'from a different agency (property_visits.id: %). '
            'Reconcile these rows first. No DDL was applied.',
            v_mismatch, v_sample;
    END IF;
END
$do$;

-- 1e. No property_visits row whose lead is from a different agency.
DO $do$
DECLARE
    v_mismatch BIGINT;
    v_sample   TEXT;
BEGIN
    SELECT count(*), string_agg(pv.id::text, ', ')
      INTO v_mismatch, v_sample
      FROM property_visits pv
      JOIN properties p ON p.id = pv.property_id
      JOIN leads      l ON l.id = pv.lead_id
     WHERE l.agency_id IS NOT NULL
       AND p.agency_id <> l.agency_id;

    IF v_mismatch > 0 THEN
        RAISE EXCEPTION
            'P26-2C 036 refused: % property_visits row(s) have a lead '
            'from a different agency (property_visits.id: %). '
            'Reconcile these rows first. No DDL was applied.',
            v_mismatch, v_sample;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- 2. SET NOT NULL on properties.agency_id.
--
-- This is the second, independent proof that 035 completed. 035 counted its
-- own remaining NULLs and refused; this refuses again, from the other side.
-- No DEFAULT is added: a default would let a future insert without an agency
-- look correct, which is precisely the proof this constraint exists to provide.
-- SET NOT NULL is a no-op when the constraint is already present.
-- ---------------------------------------------------------------------------
ALTER TABLE properties
    ALTER COLUMN agency_id SET NOT NULL;

-- ---------------------------------------------------------------------------
-- 3. property_agency_integrity().
--
-- Handles all three child tables via TG_TABLE_NAME. Reads the parent
-- property's agency_id and compares it to the agency of the linked entity
-- (contact for property_contacts, lead for property_leads, contact/lead for
-- property_visits).
--
-- Decision order:
--   A  resolve the parent property's agency_id (must be non-NULL after step 2)
--   B  resolve the linked entity's agency_id (when present)
--   C  if resolved pair disagrees: RAISE EXCEPTION
--   D  return NEW unchanged (no data modification, no agency_id on child)
--
-- No hardcoded agency_id value anywhere. No DEFAULT derivation: child tables
-- are CHILD-DERIVED and are never expected to write an agency_id themselves.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION property_agency_integrity() RETURNS trigger AS $fn$
DECLARE
    a_property BIGINT;
    a_entity   BIGINT;
BEGIN
    -- A. Parent property's agency (NOT NULL after 036 step 2).
    SELECT agency_id INTO a_property
      FROM properties
     WHERE id = NEW.property_id;

    IF a_property IS NULL THEN
        RAISE EXCEPTION
            'P26-2C agency integrity on %: property % does not exist or carries no agency',
            TG_TABLE_NAME, NEW.property_id;
    END IF;

    -- B. Resolve the linked entity depending on which child table fired.
    IF TG_TABLE_NAME = 'property_contacts' THEN
        SELECT agency_id INTO a_entity FROM contacts WHERE id = NEW.contact_id;
    ELSIF TG_TABLE_NAME = 'property_leads' THEN
        SELECT agency_id INTO a_entity FROM leads WHERE id = NEW.lead_id;
    ELSIF TG_TABLE_NAME = 'property_visits' THEN
        -- property_visits links both a contact and a lead; check both when present.
        DECLARE
            a_contact BIGINT;
            a_lead    BIGINT;
        BEGIN
            IF NEW.contact_id IS NOT NULL THEN
                SELECT agency_id INTO a_contact FROM contacts WHERE id = NEW.contact_id;
                IF a_contact IS NOT NULL AND a_contact <> a_property THEN
                    RAISE EXCEPTION
                        'P26-2C agency integrity on property_visits: contact % is in agency %, '
                        'property % is in agency %',
                        NEW.contact_id, a_contact, NEW.property_id, a_property;
                END IF;
            END IF;
            IF NEW.lead_id IS NOT NULL THEN
                SELECT agency_id INTO a_lead FROM leads WHERE id = NEW.lead_id;
                IF a_lead IS NOT NULL AND a_lead <> a_property THEN
                    RAISE EXCEPTION
                        'P26-2C agency integrity on property_visits: lead % is in agency %, '
                        'property % is in agency %',
                        NEW.lead_id, a_lead, NEW.property_id, a_property;
                END IF;
            END IF;
            RETURN NEW;
        END;
    END IF;

    -- C. Validate the single resolved entity agency for property_contacts / property_leads.
    IF a_entity IS NOT NULL AND a_entity <> a_property THEN
        RAISE EXCEPTION
            'P26-2C agency integrity on %: linked entity % is in agency %, '
            'property % is in agency %',
            TG_TABLE_NAME, COALESCE(NEW.contact_id, NEW.lead_id), a_entity,
            NEW.property_id, a_property;
    END IF;

    -- D. No data modification; pass the row through unchanged.
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- 4. Triggers on the three child tables.
--
-- BEFORE INSERT OR UPDATE. Only INSERT and UPDATE can introduce a cross-agency
-- link; DELETE cannot.
--
-- PostgreSQL has no CREATE TRIGGER IF NOT EXISTS, so the catalogue is
-- consulted directly - the pattern 026 established and 030/033 reused.
-- tgisinternal is excluded so a constraint-backed internal trigger cannot
-- produce a false positive.
-- ---------------------------------------------------------------------------
DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE t.tgname = 'trg_property_contacts_agency_integrity'
          AND c.relname = 'property_contacts'
          AND n.nspname = 'public'
          AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_property_contacts_agency_integrity
            BEFORE INSERT OR UPDATE
            ON property_contacts
            FOR EACH ROW EXECUTE FUNCTION property_agency_integrity();
    END IF;
END
$do$;

DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE t.tgname = 'trg_property_leads_agency_integrity'
          AND c.relname = 'property_leads'
          AND n.nspname = 'public'
          AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_property_leads_agency_integrity
            BEFORE INSERT OR UPDATE
            ON property_leads
            FOR EACH ROW EXECUTE FUNCTION property_agency_integrity();
    END IF;
END
$do$;

DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE t.tgname = 'trg_property_visits_agency_integrity'
          AND c.relname = 'property_visits'
          AND n.nspname = 'public'
          AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_property_visits_agency_integrity
            BEFORE INSERT OR UPDATE
            ON property_visits
            FOR EACH ROW EXECUTE FUNCTION property_agency_integrity();
    END IF;
END
$do$;
