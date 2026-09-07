-- P26-2B, part 1 of the STIMA root ownership slice: the additive half.
--
-- Adds one nullable column to one table, with its foreign key and its index.
-- Nothing else. This migration is deliberately applicable to a running system
-- without changing any behaviour: no writer sets the column yet, nothing reads
-- it, and no constraint requires it.
--
-- Ownership matrix (P26-2A, approved):
--
--   stime              ROOT-OWNED     -> physical agency_id      (this file)
--   lead_stime         CHILD-DERIVED  -> derives from its lead
--   stime_dettagliate  CHILD-DERIVED  -> derives from its stima
--
-- Only the root gets a column. The two children are named here so the omission
-- reads as a decision rather than an oversight; neither is referenced below.
--
-- Staging, from P26-0 section 6 rule 9:
--
--   031 = nullable structure      (this file)
--   ---  = controlled backfill    (a later block)
--   ---  = constraints and NOT NULL
--
-- Fusing them would remove the gate between them. A later SET NOT NULL is what
-- *proves* the backfill reached every row; a DEFAULT here would make every row
-- look correct and destroy that proof. So: no DEFAULT, no backfill, no
-- NOT NULL, and agency_id stays nullable when this migration completes.
--
-- The Default Agency is not named anywhere in this file. It is resolved from
-- slug 'stima360' at backfill time, never hardcoded to a numeric id.
--
-- Type: BIGINT, because agencies.id is BIGSERIAL. stime.id is INTEGER, but
-- that is irrelevant here - this column points at agencies, not at stime.
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
ALTER TABLE stime
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
       AND table_name   = 'stime'
       AND column_name  = 'agency_id';

    IF v_type IS NULL THEN
        RAISE EXCEPTION
            'P26-2B 031: stime.agency_id is missing after ADD COLUMN';
    END IF;

    IF v_type <> 'bigint' THEN
        RAISE EXCEPTION
            'P26-2B 031: stime.agency_id must be bigint, found %', v_type;
    END IF;

    IF v_null <> 'YES' THEN
        RAISE EXCEPTION
            'P26-2B 031: stime.agency_id must stay nullable; NOT NULL belongs to a later migration';
    END IF;

    IF v_default IS NOT NULL THEN
        RAISE EXCEPTION
            'P26-2B 031: stime.agency_id must have no default, found %', v_default;
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
-- ON DELETE RESTRICT matches what 028 chose for the four CORE tables. An
-- agency holding estimations must not be deletable by accident, and a SET NULL
-- would silently orphan a stima from its agency. 'r' is RESTRICT in
-- pg_constraint.confdeltype.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_con         pg_constraint%ROWTYPE;
    v_stime_att   smallint;
    v_agency_att  smallint;
BEGIN
    SELECT attnum INTO v_stime_att
      FROM pg_attribute
     WHERE attrelid = 'public.stime'::regclass AND attname = 'agency_id';

    SELECT attnum INTO v_agency_att
      FROM pg_attribute
     WHERE attrelid = 'public.agencies'::regclass AND attname = 'id';

    -- Scoped to stime by conrelid. Constraint names are unique per table, not
    -- per database, so a constraint of this name on some other table is
    -- entirely legitimate and must be ignored rather than inspected. With
    -- (conname, conrelid) the lookup matches at most one row, so there is no
    -- ambiguity to resolve with LIMIT or ORDER BY.
    SELECT * INTO v_con
      FROM pg_constraint
     WHERE conname  = 'stime_agency_id_fk'
       AND conrelid = 'public.stime'::regclass;

    IF NOT FOUND THEN
        ALTER TABLE stime
            ADD CONSTRAINT stime_agency_id_fk
            FOREIGN KEY (agency_id) REFERENCES agencies (id) ON DELETE RESTRICT;
    ELSE
        IF v_con.contype <> 'f'
           OR v_con.confrelid <> 'public.agencies'::regclass
           OR v_con.confdeltype <> 'r'
           OR v_con.conkey  <> ARRAY[v_stime_att]::smallint[]
           OR v_con.confkey <> ARRAY[v_agency_att]::smallint[]
        THEN
            RAISE EXCEPTION
                'P26-2B 031: a constraint named stime_agency_id_fk already exists but is not stime.agency_id -> agencies.id ON DELETE RESTRICT';
        END IF;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- 3. The index.
--
-- Plain single-column, matching the four 028 added. It is the index every
-- future agency-scoped read of stime will use, and creating it now keeps the
-- later enforcement migration free of index builds.
--
-- Not CONCURRENTLY: a concurrent build cannot run inside the runner's
-- transaction, and the table is small.
--
-- `CREATE INDEX IF NOT EXISTS` is deliberately not used: it is silent when a
-- relation of that name exists, even a table or an index on another column.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_oid       oid;
    v_stime_att smallint;
BEGIN
    SELECT attnum INTO v_stime_att
      FROM pg_attribute
     WHERE attrelid = 'public.stime'::regclass AND attname = 'agency_id';

    SELECT c.oid INTO v_oid
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE c.relname = 'idx_stime_agency_id' AND n.nspname = 'public';

    IF v_oid IS NULL THEN
        CREATE INDEX idx_stime_agency_id ON stime (agency_id);
    ELSE
        -- Accept the existing relation only if it is *equivalent* to
        -- CREATE INDEX idx_stime_agency_id ON stime (agency_id).
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
            AND i.indrelid    = 'public.stime'::regclass
            AND i.indnatts    = 1
            AND i.indnkeyatts = 1
            AND i.indkey[0]   = v_stime_att
            AND i.indisunique = false
            AND i.indisvalid  = true
            AND i.indisready  = true
            AND i.indislive   = true
            AND i.indpred   IS NULL
            AND i.indexprs  IS NULL;

        IF NOT FOUND THEN
            RAISE EXCEPTION
                'P26-2B 031: a relation named idx_stime_agency_id already exists but is not a plain, valid, non-unique index on stime(agency_id)';
        END IF;
    END IF;
END
$do$;
