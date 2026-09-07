-- P26-2C, part 1 of the PROPERTY root ownership slice: the additive half.
--
-- Adds one nullable column to one table, with its foreign key and its index.
-- Nothing else. This migration is deliberately applicable to a running system
-- without changing any behaviour: no writer sets the column yet, nothing reads
-- it, and no constraint requires it.
--
-- Ownership matrix (P26-2C, approved):
--
--   properties                   ROOT-OWNED     -> physical agency_id  (this file)
--   property_contacts            CHILD-DERIVED  -> derives from its property
--   property_leads               CHILD-DERIVED
--   property_documents           CHILD-DERIVED
--   property_photos              CHILD-DERIVED
--   property_visits              CHILD-DERIVED
--   property_price_history       CHILD-DERIVED
--   property_status_history      CHILD-DERIVED
--   property_watches             CHILD-DERIVED
--   property_watch_observations  CHILD-DERIVED
--
-- Only the root gets a column. The nine children are named here so the
-- omission reads as a decision rather than an oversight; none is referenced
-- below. An agency column on a child would be a second copy of the same fact -
-- one more thing to keep consistent, and one more place for the two to
-- disagree.
--
-- Staging, from P26-0 section 6 rule 9:
--
--   034 = nullable structure      (this file)
--   ---  = controlled backfill    (a later block)
--   ---  = constraints and NOT NULL
--
-- Fusing them would remove the gate between them. A later SET NOT NULL is what
-- *proves* the backfill reached every row; a DEFAULT here would make every row
-- look correct and destroy that proof. So: no DEFAULT, no backfill, no
-- NOT NULL, and agency_id stays nullable when this migration completes.
--
-- The backfill is a real decision, not a formality, which is a further reason
-- it is not in this file. Of 22 properties, 13 resolve to exactly one agency
-- through their own children and 9 have no provenance at all. Those 9 all
-- predate 027 and the second agency was created after it, so they are legacy
-- rather than ambiguous - but choosing what to do with them is a data question
-- for the next block.
--
-- No agency is named anywhere in this file, by slug or by id. This migration
-- resolves no owner at all.
--
-- Type: BIGINT, because agencies.id is BIGSERIAL. properties.id is BIGSERIAL
-- too, but that is beside the point - this column points at agencies.
--
-- IDEMPOTENCE AND FAIL-LOUD
--
-- Each of the three objects is handled in the same three cases:
--
--   A. already present and structurally correct -> no-op
--   B. absent                                   -> create it
--   C. present but incompatible                 -> RAISE, abort the migration
--
-- Case C is the one that needs the care. `ADD COLUMN IF NOT EXISTS` is silent
-- when a column of that name exists whatever its type, and a `conname` lookup
-- proves only that *some* constraint carries that name - it could belong to
-- another table entirely. So each object is verified against the catalogue
-- after the additive step, and a mismatch aborts rather than being absorbed.
--
-- There is no `EXCEPTION WHEN` handler anywhere in this file: a failure other
-- than "already present and correct" must surface, not be swallowed.
--
-- Transaction ownership: from 027 the runner owns the UP transaction, so this
-- file carries no BEGIN/COMMIT and writes no schema_migrations row -
-- register() owns the ledger. The paired _down.sql is executed by hand and
-- therefore brackets itself. See docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.

-- ---------------------------------------------------------------------------
-- 1. The column. Nullable, no default.
-- ---------------------------------------------------------------------------
ALTER TABLE properties
    ADD COLUMN IF NOT EXISTS agency_id BIGINT;

-- 1b. Verify the column that is actually there. ADD COLUMN IF NOT EXISTS would
-- have accepted a pre-existing INTEGER, NOT NULL or DEFAULT-carrying column of
-- the same name without a word.
DO $do$
DECLARE
    v_type    text;
    v_null    text;
    v_default text;
BEGIN
    SELECT data_type, is_nullable, column_default
      INTO v_type, v_null, v_default
      FROM information_schema.columns
     WHERE table_schema = 'public'
       AND table_name   = 'properties'
       AND column_name  = 'agency_id';

    IF v_type IS NULL THEN
        RAISE EXCEPTION
            'P26-2C 034: properties.agency_id is missing after ADD COLUMN';
    END IF;

    IF v_type <> 'bigint' THEN
        RAISE EXCEPTION
            'P26-2C 034: properties.agency_id must be bigint, found %', v_type;
    END IF;

    IF v_null <> 'YES' THEN
        RAISE EXCEPTION
            'P26-2C 034: properties.agency_id must stay nullable; enforcement belongs to a later migration';
    END IF;

    IF v_default IS NOT NULL THEN
        RAISE EXCEPTION
            'P26-2C 034: properties.agency_id must carry no column default, found %', v_default;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- 2. The foreign key.
--
-- Named explicitly rather than left to PostgreSQL's inline REFERENCES form, so
-- the down file has a deterministic name to drop and a post-migration check
-- has something to look for.
--
-- ON DELETE RESTRICT matches what 028 chose for the four CORE tables and 031
-- for stime. An agency holding real estate must not be deletable by accident,
-- and a SET NULL would silently orphan a property from its agency. 'r' is
-- RESTRICT in pg_constraint.confdeltype.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_con           pg_constraint%ROWTYPE;
    v_property_att  smallint;
    v_agency_att    smallint;
BEGIN
    SELECT attnum INTO v_property_att
      FROM pg_attribute
     WHERE attrelid = 'public.properties'::regclass AND attname = 'agency_id';

    SELECT attnum INTO v_agency_att
      FROM pg_attribute
     WHERE attrelid = 'public.agencies'::regclass AND attname = 'id';

    -- Scoped to properties by conrelid. Constraint names are unique per table,
    -- not per database, so a constraint of this name on some other table is
    -- entirely legitimate and must be ignored rather than inspected. With
    -- (conname, conrelid) the lookup matches at most one row, so there is no
    -- ambiguity to resolve with a row limit or an ordering.
    SELECT * INTO v_con
      FROM pg_constraint
     WHERE conname  = 'properties_agency_id_fk'
       AND conrelid = 'public.properties'::regclass;

    IF NOT FOUND THEN
        ALTER TABLE properties
            ADD CONSTRAINT properties_agency_id_fk
            FOREIGN KEY (agency_id) REFERENCES agencies (id) ON DELETE RESTRICT;
    ELSE
        IF v_con.contype <> 'f'
           OR v_con.confrelid <> 'public.agencies'::regclass
           OR v_con.confdeltype <> 'r'
           OR v_con.conkey  <> ARRAY[v_property_att]::smallint[]
           OR v_con.confkey <> ARRAY[v_agency_att]::smallint[]
        THEN
            RAISE EXCEPTION
                'P26-2C 034: a constraint named properties_agency_id_fk already exists but is not properties.agency_id -> agencies.id with a restricting delete rule';
        END IF;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- 3. The index.
--
-- Plain single-column, matching the four 028 added and the one 031 added. It is
-- the index every future agency-scoped read of properties will use, and
-- creating it now keeps the later enforcement migration free of index builds.
--
-- Not CONCURRENTLY: a concurrent build cannot run inside the runner's
-- transaction, and the table is small.
--
-- `CREATE INDEX IF NOT EXISTS` is deliberately not used: it is silent when a
-- relation of that name exists, even a table or an index on another column.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_oid          oid;
    v_property_att smallint;
BEGIN
    SELECT attnum INTO v_property_att
      FROM pg_attribute
     WHERE attrelid = 'public.properties'::regclass AND attname = 'agency_id';

    SELECT c.oid INTO v_oid
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE c.relname = 'idx_properties_agency_id' AND n.nspname = 'public';

    IF v_oid IS NULL THEN
        CREATE INDEX idx_properties_agency_id ON properties (agency_id);
    ELSE
        -- Accept the existing relation only if it is *equivalent* to
        -- CREATE INDEX idx_properties_agency_id ON properties (agency_id).
        --
        -- indrelid/indkey alone would still admit a UNIQUE index, a partial
        -- one, an expression one, a covering one, or one left invalid by an
        -- interrupted concurrent build. A later migration relying on this
        -- index would then be relying on something else entirely.
        --
        -- (This text sits inside a dollar-quoted block, which is a string
        -- literal to the migration runner's validator - so it is scanned as
        -- SQL, not skipped as a comment. Keywords are avoided here on purpose.)
        PERFORM 1
           FROM pg_index i
          WHERE i.indexrelid  = v_oid
            AND i.indrelid    = 'public.properties'::regclass
            AND i.indnatts    = 1
            AND i.indnkeyatts = 1
            AND i.indkey[0]   = v_property_att
            AND i.indisunique = false
            AND i.indisvalid  = true
            AND i.indisready  = true
            AND i.indislive   = true
            AND i.indpred   IS NULL
            AND i.indexprs  IS NULL;

        IF NOT FOUND THEN
            RAISE EXCEPTION
                'P26-2C 034: a relation named idx_properties_agency_id already exists but is not a plain, valid, non-unique index on properties(agency_id)';
        END IF;
    END IF;
END
$do$;
