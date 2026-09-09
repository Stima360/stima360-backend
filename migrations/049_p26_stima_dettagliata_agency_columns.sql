-- P26-6C STIME DETTAGLIATE, part 1 of 3: the additive half.
--
-- One nullable column, its foreign key, its index. Nothing else. Applicable to
-- a running system without changing any behaviour: no writer sets it yet,
-- nothing reads it, no constraint requires it.
--
-- WHY THIS TABLE EARNS A PHYSICAL COLUMN
--
-- P26-6A fixed the test and 046 restated it: a table earns a physical
-- `agency_id` only when the schema lets a row exist without the parent it
-- would otherwise derive from. `stime_dettagliate` does, twice over.
--
--   stima_id INTEGER REFERENCES stime(id)
--
-- Nullable, with no NOT NULL and no ON DELETE clause. Two consequences:
--
--   1. A row can be born with no parent. `/api/salva_stima_dettagliata` writes
--      `to_int_safe(data.get("stima_id"))` from the request body, so a payload
--      that omits the field - or sends something unparseable - produces a
--      detail row with a NULL parent. It is not a hypothetical shape; it is
--      the shape the public funnel can already write today.
--
--   2. `/api/admin/stime/delete` deletes the children first and the parent
--      second, in that order and in one transaction. The ordering is what
--      makes the delete legal at all under the implicit NO ACTION, and it is
--      also why a child never has to survive its parent - but nothing in the
--      schema requires callers to keep that order.
--
-- Case 1 alone settles it. A row with no parent has nothing to derive from,
-- and a scope that resolved it through `JOIN stime` would not merely mis-file
-- such a row: it would make it permanently invisible to every agency, which is
-- data loss wearing the costume of isolation.
--
-- WHY NOT DERIVE ANYWAY AND ACCEPT THE ORPHANS
--
-- Because the admin delete route addresses these rows by their own id. A
-- derived scope has to reach the parent to decide, and for an orphan there is
-- no decision to reach - so the row would be undeletable as well as unreadable.
--
-- Staging, from P26-0 section 6 rule 9:
--
--   049 = nullable structure      (this file)
--   050 = controlled backfill
--   051 = constraints and NOT NULL
--
-- No DEFAULT: it would make every row look correct and destroy the proof
-- 051's SET NOT NULL exists to provide. No agency is named here, by slug or
-- by id.
--
-- Transaction ownership: the runner owns the UP transaction, so this file
-- carries no BEGIN/COMMIT.

-- ---------------------------------------------------------------------------
-- 1. The column.
-- ---------------------------------------------------------------------------
ALTER TABLE stime_dettagliate
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
       AND table_name   = 'stime_dettagliate'
       AND column_name  = 'agency_id';

    IF v_type IS NULL THEN
        RAISE EXCEPTION 'P26-6C 049: stime_dettagliate.agency_id is missing after ADD COLUMN';
    END IF;
    IF v_type <> 'bigint' THEN
        RAISE EXCEPTION 'P26-6C 049: stime_dettagliate.agency_id must be bigint, found %', v_type;
    END IF;
    IF v_null <> 'YES' THEN
        RAISE EXCEPTION 'P26-6C 049: stime_dettagliate.agency_id must stay nullable; enforcement belongs to 051';
    END IF;
    IF v_default IS NOT NULL THEN
        RAISE EXCEPTION 'P26-6C 049: stime_dettagliate.agency_id must carry no column default, found %', v_default;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- 2. Foreign key.
--
-- Named explicitly so the down file has a deterministic name to drop, and the
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
     WHERE attrelid = 'public.stime_dettagliate'::regclass AND attname = 'agency_id';
    SELECT attnum INTO v_agency_att
      FROM pg_attribute
     WHERE attrelid = 'public.agencies'::regclass AND attname = 'id';

    SELECT * INTO v_con
      FROM pg_constraint
     WHERE conname  = 'stime_dettagliate_agency_id_fk'
       AND conrelid = 'public.stime_dettagliate'::regclass;

    IF NOT FOUND THEN
        ALTER TABLE stime_dettagliate
            ADD CONSTRAINT stime_dettagliate_agency_id_fk
            FOREIGN KEY (agency_id) REFERENCES agencies (id) ON DELETE RESTRICT;
    ELSE
        IF v_con.contype <> 'f'
           OR v_con.confrelid <> 'public.agencies'::regclass
           OR v_con.confdeltype <> 'r'
           OR v_con.conkey  <> ARRAY[v_own_att]::smallint[]
           OR v_con.confkey <> ARRAY[v_agency_att]::smallint[]
        THEN
            RAISE EXCEPTION
                'P26-6C 049: a constraint named stime_dettagliate_agency_id_fk already exists but is not the expected reference to agencies';
        END IF;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- 3. Index.
--
-- Composite, leading with the agency: the admin list filters on the tenant
-- first and then orders by `data`. A plain agency_id index would be used and
-- then discarded for the sort.
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
     WHERE c.relname = 'idx_stime_dettagliate_agency_id' AND n.nspname = 'public';
    IF v_oid IS NULL THEN
        CREATE INDEX idx_stime_dettagliate_agency_id
            ON stime_dettagliate (agency_id, data DESC);
    END IF;
END
$do$;
