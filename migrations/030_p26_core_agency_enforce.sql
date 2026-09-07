-- P26-1 CORE agency scoping, part 3 of 3: integrity enforcement.
--
-- Turns "a service must remember to check" into "the database refuses".
--
--   028  nullable structure
--   029  controlled backfill
--   030  NOT NULL, composite FKs, agency-aware indexes, integrity trigger
--
-- Transaction ownership: this file opens no transaction and commits nothing.
-- scripts/p26_migrate.py executes this body, writes the schema_migrations row
-- through register(), and commits both together, rolling back on any error.
-- So a SET NOT NULL that fails leaves nothing behind.
-- See docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.
--
-- Statement order is fixed and load-bearing:
--
--   1. SET NOT NULL     cheapest failure first - a partial backfill aborts
--                       before any DDL is attempted
--   2. constraints      validate existing rows
--   3. indexes
--   4. function
--   5. triggers         last, so nothing above can fire them
--
-- The triggers are created after the backfill has long since committed (029),
-- so they never fire during it.

-- ---------------------------------------------------------------------------
-- 1. NOT NULL.
--
-- This is the second, independent proof that 029 completed. 029 counted its
-- own remaining NULLs and refused; this refuses again, from the other side.
-- Only agency_id: assigned_agent_id and created_by_user_id stay nullable
-- because 029 deliberately wrote neither, and an unassigned record is a
-- legitimate state.
-- ---------------------------------------------------------------------------
ALTER TABLE contacts   ALTER COLUMN agency_id SET NOT NULL;
ALTER TABLE leads      ALTER COLUMN agency_id SET NOT NULL;
ALTER TABLE activities ALTER COLUMN agency_id SET NOT NULL;
ALTER TABLE tasks      ALTER COLUMN agency_id SET NOT NULL;

-- ---------------------------------------------------------------------------
-- 2. Structural cross-agency guarantees.
--
-- PostgreSQL has no ADD CONSTRAINT IF NOT EXISTS, so each named constraint is
-- guarded by its own pg_constraint lookup. No exception handler: a failure
-- other than "already present" must surface, not be swallowed.
--
-- Only contacts gets a (agency_id, id) referencable key. Nothing references
-- leads(agency_id, id), so leads_agency_scope_unq is deliberately not created:
-- it would be an index nothing reads.
--
-- The two assignment FKs target agency_memberships (agency_id,
-- operator_user_id), which 027 already provides as agency_memberships_unq. No
-- extra uniqueness is added there.
--
-- MATCH SIMPLE (the default) means a composite FK is not checked when any of
-- its columns is NULL. assigned_agent_id is nullable, so an unassigned record
-- is unaffected while an assigned one is verified against a real membership in
-- its own agency.
-- ---------------------------------------------------------------------------
DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'contacts_agency_scope_unq'
    ) THEN
        ALTER TABLE contacts
            ADD CONSTRAINT contacts_agency_scope_unq UNIQUE (agency_id, id);
    END IF;
END
$do$;

-- G-1: a lead can never point at a contact in another agency.
DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'leads_contact_same_agency_fk'
    ) THEN
        ALTER TABLE leads
            ADD CONSTRAINT leads_contact_same_agency_fk
            FOREIGN KEY (agency_id, contact_id) REFERENCES contacts (agency_id, id);
    END IF;
END
$do$;

-- G-2: a contact can only be assigned to an operator of its own agency.
DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'contacts_agent_same_agency_fk'
    ) THEN
        ALTER TABLE contacts
            ADD CONSTRAINT contacts_agent_same_agency_fk
            FOREIGN KEY (agency_id, assigned_agent_id)
            REFERENCES agency_memberships (agency_id, operator_user_id);
    END IF;
END
$do$;

-- G-3: the same, for leads.
DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'leads_agent_same_agency_fk'
    ) THEN
        ALTER TABLE leads
            ADD CONSTRAINT leads_agent_same_agency_fk
            FOREIGN KEY (agency_id, assigned_agent_id)
            REFERENCES agency_memberships (agency_id, operator_user_id);
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- 3. Agency-aware indexes.
--
-- Each mirrors an ORDER BY that already exists in core/repository.py, with
-- agency_id prefixed. The email and phone pair exist specifically to keep the
-- public-STIMA bridge's identity lookup on an index now that it is
-- agency-scoped.
--
-- The four plain indexes from 028 are NOT dropped: other modules still query
-- these tables without an agency predicate.
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_contacts_agency_created    ON contacts   (agency_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_contacts_agency_agent      ON contacts   (agency_id, assigned_agent_id);
CREATE INDEX IF NOT EXISTS idx_contacts_agency_email      ON contacts   (agency_id, email_normalized);
CREATE INDEX IF NOT EXISTS idx_contacts_agency_phone      ON contacts   (agency_id, phone_normalized);
CREATE INDEX IF NOT EXISTS idx_leads_agency_created       ON leads      (agency_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_leads_agency_agent         ON leads      (agency_id, assigned_agent_id);
CREATE INDEX IF NOT EXISTS idx_leads_agency_contact       ON leads      (agency_id, contact_id);
CREATE INDEX IF NOT EXISTS idx_activities_agency_occurred ON activities (agency_id, occurred_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_agency_due           ON tasks      (agency_id, due_at, id DESC);

-- ---------------------------------------------------------------------------
-- 4. core_agency_integrity().
--
-- activities and tasks are written by flow/, followup/, buy/, property/ and
-- core/ - modules P26-1 does not modify. Requiring each to supply agency_id
-- would be a cross-module change; this trigger derives it instead.
--
-- But it validates before it derives, and that distinction is the whole point.
-- A derive-only version would fill in a missing value and wave everything else
-- through: a row naming a lead in agency A and a contact in agency B, or one
-- carrying an explicit agency_id contradicting its own references, would be
-- accepted. Neither is caught by anything else - activities and tasks have no
-- composite-FK equivalent to the guarantees above, because their three
-- reference columns are independently nullable.
--
-- Decision order:
--
--   A  resolve every reference that is present
--   B  every resolved pair must agree            -> else RAISE
--   C  an explicit agency_id must equal them     -> else RAISE; value kept
--   D  otherwise derive from the resolved reference
--   E  bounded fallback: the P18 stima-only shape, and only that
--   F  any other unresolved shape                -> RAISE
--
-- There is deliberately no early return on an explicit agency_id: step C runs
-- after step B, never instead of it.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core_agency_integrity() RETURNS trigger AS $fn$
DECLARE
    a_lead    BIGINT;
    a_contact BIGINT;
    a_stima   BIGINT;
    resolved  BIGINT;
BEGIN
    -- A. Resolve every available reference.
    IF NEW.lead_id IS NOT NULL THEN
        SELECT agency_id INTO a_lead FROM leads WHERE id = NEW.lead_id;
    END IF;

    IF NEW.contact_id IS NOT NULL THEN
        SELECT agency_id INTO a_contact FROM contacts WHERE id = NEW.contact_id;
    END IF;

    IF NEW.stima_id IS NOT NULL THEN
        SELECT l.agency_id INTO a_stima
          FROM lead_stime ls
          JOIN leads l ON l.id = ls.lead_id
         WHERE ls.stima_id = NEW.stima_id
         ORDER BY ls.id
         LIMIT 1;
    END IF;

    -- B. Every resolved pair must agree.
    IF a_lead IS NOT NULL AND a_contact IS NOT NULL AND a_lead <> a_contact THEN
        RAISE EXCEPTION
            'P26-1 agency integrity on %: lead % is in agency %, contact % is in agency %',
            TG_TABLE_NAME, NEW.lead_id, a_lead, NEW.contact_id, a_contact;
    END IF;

    IF a_lead IS NOT NULL AND a_stima IS NOT NULL AND a_lead <> a_stima THEN
        RAISE EXCEPTION
            'P26-1 agency integrity on %: lead % is in agency %, stima % resolves to agency %',
            TG_TABLE_NAME, NEW.lead_id, a_lead, NEW.stima_id, a_stima;
    END IF;

    IF a_contact IS NOT NULL AND a_stima IS NOT NULL AND a_contact <> a_stima THEN
        RAISE EXCEPTION
            'P26-1 agency integrity on %: contact % is in agency %, stima % resolves to agency %',
            TG_TABLE_NAME, NEW.contact_id, a_contact, NEW.stima_id, a_stima;
    END IF;

    resolved := COALESCE(a_lead, a_contact, a_stima);

    -- C. An explicit agency_id is validated, never trusted - and never
    -- overwritten once it agrees.
    IF NEW.agency_id IS NOT NULL THEN
        IF resolved IS NOT NULL AND NEW.agency_id <> resolved THEN
            RAISE EXCEPTION
                'P26-1 agency integrity on %: explicit agency_id % contradicts agency % derived from the row references',
                TG_TABLE_NAME, NEW.agency_id, resolved;
        END IF;
        RETURN NEW;
    END IF;

    -- D. Derive.
    IF resolved IS NOT NULL THEN
        NEW.agency_id := resolved;
        RETURN NEW;
    END IF;

    -- E. Bounded fallback. Reachable by exactly one real flow: main.py calls
    -- followup_service.safe_run_followup(... stima_id=new_id, contact_id=None,
    -- lead_id=None ...) after the public bridge returned skipped, conflict or
    -- error, so the task carries a stima and nothing else, with no lead_stime
    -- row to resolve through. All three conditions are required: a generic
    -- activity or task must never be parked in the Default Agency.
    IF NEW.stima_id IS NOT NULL
       AND NEW.lead_id IS NULL
       AND NEW.contact_id IS NULL THEN
        SELECT id INTO NEW.agency_id
          FROM agencies
         WHERE slug = 'stima360' AND status = 'active';

        IF NEW.agency_id IS NULL THEN
            RAISE EXCEPTION
                'P26-1 agency integrity on %: the Default Agency is missing or inactive, so a stima-only row cannot be placed',
                TG_TABLE_NAME;
        END IF;

        RETURN NEW;
    END IF;

    -- F. Anything else is unresolvable and must not be guessed.
    RAISE EXCEPTION
        'P26-1 agency integrity on %: agency_id could not be resolved from contact_id=%, lead_id=%, stima_id=%',
        TG_TABLE_NAME, NEW.contact_id, NEW.lead_id, NEW.stima_id;
END;
$fn$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- 5. Triggers.
--
-- BEFORE INSERT OR UPDATE OF the four reference columns. The UPDATE branch
-- closes a hole the insert-only form leaves open: repointing an existing task
-- at another agency's lead. Naming the columns keeps the common updates
-- (status, completed_at, updated_at) free of trigger cost.
--
-- Only activities and tasks. contacts and leads are protected by the composite
-- foreign keys above instead.
--
-- PostgreSQL has no CREATE TRIGGER IF NOT EXISTS, so the catalogue is
-- consulted directly - the pattern 026 established. tgisinternal is excluded
-- so a constraint-backed internal trigger cannot produce a false positive.
-- ---------------------------------------------------------------------------
DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE t.tgname = 'trg_activities_agency_integrity'
          AND c.relname = 'activities'
          AND n.nspname = 'public'
          AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_activities_agency_integrity
            BEFORE INSERT OR UPDATE OF agency_id, contact_id, lead_id, stima_id
            ON activities
            FOR EACH ROW EXECUTE FUNCTION core_agency_integrity();
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
        WHERE t.tgname = 'trg_tasks_agency_integrity'
          AND c.relname = 'tasks'
          AND n.nspname = 'public'
          AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_tasks_agency_integrity
            BEFORE INSERT OR UPDATE OF agency_id, contact_id, lead_id, stima_id
            ON tasks
            FOR EACH ROW EXECUTE FUNCTION core_agency_integrity();
    END IF;
END
$do$;
