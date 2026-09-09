-- P26-6B INTELLIGENCE, part 1 of 3: the additive half.
--
-- Adds one nullable column to each of two tables, with its foreign key and its
-- index. Nothing else. Applicable to a running system without changing any
-- behaviour: no writer sets the column yet, nothing reads it, no constraint
-- requires it.
--
-- WHY EXACTLY TWO TABLES
--
-- P26-6A established the test: a table earns a physical `agency_id` only when
-- the schema lets a row outlive the parent it would otherwise derive from.
-- Across P20-P24 exactly two do.
--
--   property_watches   stima_id is NULLABLE and ON DELETE SET NULL (022), so a
--                      watch survives the deletion of the estimation it was
--                      opened for and keeps running with no provenance left.
--
--   next_best_actions  contact_id, lead_id and stima_id are all nullable and
--                      ON DELETE SET NULL (024), and the subject is polymorphic
--                      - subject_type/subject_id are plain columns with no
--                      foreign key at all. A materialised read model whose every
--                      reference can be blanked cannot be scoped from what
--                      remains.
--
-- Everything else in this slice derives, because each has a mandatory parent:
--
--   property_watch_observations    watch_id NOT NULL
--   invisible_sale_opportunities   watch_id NOT NULL
--   invisible_sale_candidates      opportunity_id + buy_request_id, both NOT NULL
--   invisible_sale_events          opportunity_id NOT NULL
--   seller_revival_suppressions    contact_id NOT NULL, ON DELETE CASCADE
--
-- A physical column on any of those would be a second copy of a fact already
-- recorded once, and the failure mode of two copies is that they disagree.
--
-- A NOTE ON MIGRATION 034
--
-- 034 carries a comment classifying property_watches as CHILD-DERIVED. That was
-- wrong - it did not account for ON DELETE SET NULL on stima_id - but 034 is
-- applied and its checksum is immutable. The correction belongs here, in the
-- migration that acts on it, not in a rewrite of history.
--
-- Staging, from P26-0 section 6 rule 9:
--
--   046 = nullable structure      (this file)
--   047 = controlled backfill
--   048 = constraints and NOT NULL
--
-- No DEFAULT: it would make every row look correct and destroy the proof 048's
-- SET NOT NULL exists to provide. No agency is named here, by slug or by id.
--
-- Transaction ownership: the runner owns the UP transaction, so this file
-- carries no BEGIN/COMMIT.

-- ---------------------------------------------------------------------------
-- 1. property_watches.agency_id
-- ---------------------------------------------------------------------------
ALTER TABLE property_watches
    ADD COLUMN IF NOT EXISTS agency_id BIGINT;

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
       AND table_name   = 'property_watches'
       AND column_name  = 'agency_id';

    IF v_type IS NULL THEN
        RAISE EXCEPTION 'P26-6B 046: property_watches.agency_id is missing after ADD COLUMN';
    END IF;
    IF v_type <> 'bigint' THEN
        RAISE EXCEPTION 'P26-6B 046: property_watches.agency_id must be bigint, found %', v_type;
    END IF;
    IF v_null <> 'YES' THEN
        RAISE EXCEPTION 'P26-6B 046: property_watches.agency_id must stay nullable; enforcement belongs to 048';
    END IF;
    IF v_default IS NOT NULL THEN
        RAISE EXCEPTION 'P26-6B 046: property_watches.agency_id must carry no column default, found %', v_default;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- 2. next_best_actions.agency_id
-- ---------------------------------------------------------------------------
ALTER TABLE next_best_actions
    ADD COLUMN IF NOT EXISTS agency_id BIGINT;

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
       AND table_name   = 'next_best_actions'
       AND column_name  = 'agency_id';

    IF v_type IS NULL THEN
        RAISE EXCEPTION 'P26-6B 046: next_best_actions.agency_id is missing after ADD COLUMN';
    END IF;
    IF v_type <> 'bigint' THEN
        RAISE EXCEPTION 'P26-6B 046: next_best_actions.agency_id must be bigint, found %', v_type;
    END IF;
    IF v_null <> 'YES' THEN
        RAISE EXCEPTION 'P26-6B 046: next_best_actions.agency_id must stay nullable; enforcement belongs to 048';
    END IF;
    IF v_default IS NOT NULL THEN
        RAISE EXCEPTION 'P26-6B 046: next_best_actions.agency_id must carry no column default, found %', v_default;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- 3. Foreign keys.
--
-- Named explicitly so the down file has a deterministic name to drop, and each
-- lookup is scoped by conrelid: constraint names are unique per table, not per
-- database, so a lookup by conname alone could inspect something belonging
-- elsewhere.
--
-- ON DELETE RESTRICT, matching every other agency FK in P26.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_con        pg_constraint%ROWTYPE;
    v_own_att    smallint;
    v_agency_att smallint;
BEGIN
    SELECT attnum INTO v_own_att
      FROM pg_attribute
     WHERE attrelid = 'public.property_watches'::regclass AND attname = 'agency_id';
    SELECT attnum INTO v_agency_att
      FROM pg_attribute
     WHERE attrelid = 'public.agencies'::regclass AND attname = 'id';

    SELECT * INTO v_con
      FROM pg_constraint
     WHERE conname  = 'property_watches_agency_id_fk'
       AND conrelid = 'public.property_watches'::regclass;

    IF NOT FOUND THEN
        ALTER TABLE property_watches
            ADD CONSTRAINT property_watches_agency_id_fk
            FOREIGN KEY (agency_id) REFERENCES agencies (id) ON DELETE RESTRICT;
    ELSE
        IF v_con.contype <> 'f'
           OR v_con.confrelid <> 'public.agencies'::regclass
           OR v_con.confdeltype <> 'r'
           OR v_con.conkey  <> ARRAY[v_own_att]::smallint[]
           OR v_con.confkey <> ARRAY[v_agency_att]::smallint[]
        THEN
            RAISE EXCEPTION
                'P26-6B 046: a constraint named property_watches_agency_id_fk already exists but is not the expected reference to agencies';
        END IF;
    END IF;
END
$do$;

DO $do$
DECLARE
    v_con        pg_constraint%ROWTYPE;
    v_own_att    smallint;
    v_agency_att smallint;
BEGIN
    SELECT attnum INTO v_own_att
      FROM pg_attribute
     WHERE attrelid = 'public.next_best_actions'::regclass AND attname = 'agency_id';
    SELECT attnum INTO v_agency_att
      FROM pg_attribute
     WHERE attrelid = 'public.agencies'::regclass AND attname = 'id';

    SELECT * INTO v_con
      FROM pg_constraint
     WHERE conname  = 'next_best_actions_agency_id_fk'
       AND conrelid = 'public.next_best_actions'::regclass;

    IF NOT FOUND THEN
        ALTER TABLE next_best_actions
            ADD CONSTRAINT next_best_actions_agency_id_fk
            FOREIGN KEY (agency_id) REFERENCES agencies (id) ON DELETE RESTRICT;
    ELSE
        IF v_con.contype <> 'f'
           OR v_con.confrelid <> 'public.agencies'::regclass
           OR v_con.confdeltype <> 'r'
           OR v_con.conkey  <> ARRAY[v_own_att]::smallint[]
           OR v_con.confkey <> ARRAY[v_agency_att]::smallint[]
        THEN
            RAISE EXCEPTION
                'P26-6B 046: a constraint named next_best_actions_agency_id_fk already exists but is not the expected reference to agencies';
        END IF;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- 4. Indexes.
--
-- Composite, leading with the agency, because every scoped read filters on the
-- tenant first and then orders by the table's own key: watches are swept by
-- status for the active-batch cycles, and the NBA list is ordered by priority.
-- A plain agency_id index would be used and then discarded.
--
-- Not CONCURRENTLY: a concurrent build cannot run inside the runner's
-- transaction.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_oid oid;
BEGIN
    SELECT c.oid INTO v_oid
      FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE c.relname = 'idx_property_watches_agency_id' AND n.nspname = 'public';
    IF v_oid IS NULL THEN
        CREATE INDEX idx_property_watches_agency_id
            ON property_watches (agency_id, status);
    END IF;
END
$do$;

DO $do$
DECLARE
    v_oid oid;
BEGIN
    SELECT c.oid INTO v_oid
      FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE c.relname = 'idx_next_best_actions_agency_id' AND n.nspname = 'public';
    IF v_oid IS NULL THEN
        CREATE INDEX idx_next_best_actions_agency_id
            ON next_best_actions (agency_id, priority, generated_at DESC);
    END IF;
END
$do$;
