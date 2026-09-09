-- P26-6A SELLER ENGINE, part 1 of 3: the additive half.
--
-- Adds one nullable column to each of two tables, with its foreign key and its
-- index. Nothing else. Applicable to a running system without changing any
-- behaviour: no writer sets the column yet, nothing reads it, no constraint
-- requires it.
--
-- WHY THESE TWO TABLES GET A PHYSICAL COLUMN
--
-- Every earlier P26 slice refused one. PROPERTY, BUY, MATCH, PROPOSAL and SALE
-- are all CHILD-DERIVED: their tenancy is recoverable from a parent, so a
-- second copy would only be one more thing to keep consistent.
--
-- The seller engine is different, and the difference is in the schema rather
-- than in taste. On `seller_timeline_events` all four references -
-- contact_id, lead_id, stima_id, property_id - are NULLABLE and
-- ON DELETE SET NULL. On `followup_actions` the same is true of contact_id,
-- lead_id, stima_id and task_id. Deleting a contact does not delete the log
-- row; it blanks the reference. So a historical row can outlive every parent it
-- was derived from, and a derived-only design would eventually leave a row with
-- no tenant at all - unscopeable, and unassignable by any later migration.
--
-- These are logs. A log that forgets whose it is cannot be scoped afterwards.
--
-- `seller_intent` is not touched: it computes on demand and stores nothing, so
-- its tenancy is whatever the caller's lead is.
--
-- Staging, from P26-0 section 6 rule 9:
--
--   043 = nullable structure      (this file)
--   044 = controlled backfill
--   045 = constraints and NOT NULL
--
-- A DEFAULT here would make every row look correct and destroy the proof 045's
-- SET NOT NULL exists to provide. So: no DEFAULT, no backfill, no NOT NULL.
--
-- No agency is named anywhere in this file, by slug or by id.
--
-- Type: BIGINT, because agencies.id is BIGSERIAL.
--
-- Transaction ownership: the runner owns the UP transaction, so this file
-- carries no BEGIN/COMMIT. See docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.

-- ---------------------------------------------------------------------------
-- 1. seller_timeline_events.agency_id
-- ---------------------------------------------------------------------------
ALTER TABLE seller_timeline_events
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
       AND table_name   = 'seller_timeline_events'
       AND column_name  = 'agency_id';

    IF v_type IS NULL THEN
        RAISE EXCEPTION
            'P26-6A 043: seller_timeline_events.agency_id is missing after ADD COLUMN';
    END IF;
    IF v_type <> 'bigint' THEN
        RAISE EXCEPTION
            'P26-6A 043: seller_timeline_events.agency_id must be bigint, found %', v_type;
    END IF;
    IF v_null <> 'YES' THEN
        RAISE EXCEPTION
            'P26-6A 043: seller_timeline_events.agency_id must stay nullable; enforcement belongs to 045';
    END IF;
    IF v_default IS NOT NULL THEN
        RAISE EXCEPTION
            'P26-6A 043: seller_timeline_events.agency_id must carry no column default, found %', v_default;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- 2. followup_actions.agency_id
-- ---------------------------------------------------------------------------
ALTER TABLE followup_actions
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
       AND table_name   = 'followup_actions'
       AND column_name  = 'agency_id';

    IF v_type IS NULL THEN
        RAISE EXCEPTION
            'P26-6A 043: followup_actions.agency_id is missing after ADD COLUMN';
    END IF;
    IF v_type <> 'bigint' THEN
        RAISE EXCEPTION
            'P26-6A 043: followup_actions.agency_id must be bigint, found %', v_type;
    END IF;
    IF v_null <> 'YES' THEN
        RAISE EXCEPTION
            'P26-6A 043: followup_actions.agency_id must stay nullable; enforcement belongs to 045';
    END IF;
    IF v_default IS NOT NULL THEN
        RAISE EXCEPTION
            'P26-6A 043: followup_actions.agency_id must carry no column default, found %', v_default;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- 3. Foreign keys.
--
-- Named explicitly so the down file has a deterministic name to drop, and
-- scoped by conrelid: constraint names are unique per table, not per database,
-- so a lookup by conname alone could inspect something belonging elsewhere.
--
-- ON DELETE RESTRICT, matching every other agency FK in P26. An agency holding
-- history must not be deletable by accident, and a SET NULL would silently
-- re-create exactly the ownerless rows this column exists to prevent.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_con        pg_constraint%ROWTYPE;
    v_own_att    smallint;
    v_agency_att smallint;
BEGIN
    SELECT attnum INTO v_own_att
      FROM pg_attribute
     WHERE attrelid = 'public.seller_timeline_events'::regclass AND attname = 'agency_id';
    SELECT attnum INTO v_agency_att
      FROM pg_attribute
     WHERE attrelid = 'public.agencies'::regclass AND attname = 'id';

    SELECT * INTO v_con
      FROM pg_constraint
     WHERE conname  = 'seller_timeline_events_agency_id_fk'
       AND conrelid = 'public.seller_timeline_events'::regclass;

    IF NOT FOUND THEN
        ALTER TABLE seller_timeline_events
            ADD CONSTRAINT seller_timeline_events_agency_id_fk
            FOREIGN KEY (agency_id) REFERENCES agencies (id) ON DELETE RESTRICT;
    ELSE
        IF v_con.contype <> 'f'
           OR v_con.confrelid <> 'public.agencies'::regclass
           OR v_con.confdeltype <> 'r'
           OR v_con.conkey  <> ARRAY[v_own_att]::smallint[]
           OR v_con.confkey <> ARRAY[v_agency_att]::smallint[]
        THEN
            RAISE EXCEPTION
                'P26-6A 043: a constraint named seller_timeline_events_agency_id_fk already exists but is not the expected reference to agencies';
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
     WHERE attrelid = 'public.followup_actions'::regclass AND attname = 'agency_id';
    SELECT attnum INTO v_agency_att
      FROM pg_attribute
     WHERE attrelid = 'public.agencies'::regclass AND attname = 'id';

    SELECT * INTO v_con
      FROM pg_constraint
     WHERE conname  = 'followup_actions_agency_id_fk'
       AND conrelid = 'public.followup_actions'::regclass;

    IF NOT FOUND THEN
        ALTER TABLE followup_actions
            ADD CONSTRAINT followup_actions_agency_id_fk
            FOREIGN KEY (agency_id) REFERENCES agencies (id) ON DELETE RESTRICT;
    ELSE
        IF v_con.contype <> 'f'
           OR v_con.confrelid <> 'public.agencies'::regclass
           OR v_con.confdeltype <> 'r'
           OR v_con.conkey  <> ARRAY[v_own_att]::smallint[]
           OR v_con.confkey <> ARRAY[v_agency_att]::smallint[]
        THEN
            RAISE EXCEPTION
                'P26-6A 043: a constraint named followup_actions_agency_id_fk already exists but is not the expected reference to agencies';
        END IF;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- 4. Indexes.
--
-- Composite rather than single-column: every scoped read of these tables filters
-- on the agency and then orders or filters by the log's own key - the timeline
-- by occurred_at, the actions by their idempotency key. A plain agency_id index
-- would be used and then discarded.
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
     WHERE c.relname = 'idx_seller_timeline_events_agency_id' AND n.nspname = 'public';
    IF v_oid IS NULL THEN
        CREATE INDEX idx_seller_timeline_events_agency_id
            ON seller_timeline_events (agency_id, occurred_at DESC, id DESC);
    END IF;
END
$do$;

DO $do$
DECLARE
    v_oid oid;
BEGIN
    SELECT c.oid INTO v_oid
      FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE c.relname = 'idx_followup_actions_agency_id' AND n.nspname = 'public';
    IF v_oid IS NULL THEN
        CREATE INDEX idx_followup_actions_agency_id
            ON followup_actions (agency_id, idempotency_key);
    END IF;
END
$do$;
