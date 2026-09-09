-- P26-6C FLOW, part 1 of 3: the additive half.
--
-- Three nullable columns, their foreign keys, their indexes. Nothing else.
-- Applicable to a running system without changing any behaviour: no writer
-- sets them yet, nothing reads them, no constraint requires them.
--
-- WHY THESE THREE, AND WHY NOT THE OTHER TWO
--
-- P26-6A fixed the test and every slice since has applied it: a table earns a
-- physical `agency_id` only when the schema lets a row exist without the
-- parent it would otherwise derive from.
--
--   flow_events        entity_type/entity_id are plain columns with NO foreign
--                      key at all - the reference is polymorphic across six
--                      entity types in four modules. There is nothing to join
--                      to, so there is nothing to derive from.
--
--   flow_executions    event_id is nullable and ON DELETE SET NULL; rule_id is
--                      mandatory but points at flow_rules, which is a
--                      platform-global template and carries no tenant to lend;
--                      entity_type/entity_id are polymorphic, as above. Every
--                      one of its references is either blankable or tenant-free.
--
--   flow_suppressions  rule_id is mandatory and, again, tenant-free;
--                      entity_type/entity_id are polymorphic. Same shape.
--
-- The other two do NOT get a column:
--
--   flow_action_records  execution_id BIGINT NOT NULL REFERENCES
--                        flow_executions(id) ON DELETE CASCADE. Mandatory,
--                        non-nullable, cascading - it cannot outlive the
--                        execution it derives from, so a second copy of the
--                        tenant would only be a second thing to disagree.
--
--   flow_rules           the rule registry: one catalogue, the same template
--                        for every agency. Giving it an agency_id would fork
--                        the catalogue per tenant, which is a different
--                        product decision, not an isolation fix.
--
-- `flow_rule_configs` does not exist in this schema, so nothing is created for
-- per-agency rule configuration here. If it is introduced later it is
-- ROOT-OWNED and needs its own slice.
--
-- THE UNIQUE KEYS THIS DELIBERATELY DOES NOT CHANGE
--
-- `flow_events.deduplication_key` and `flow_suppressions(rule_id, entity_type,
-- entity_id)` are both GLOBAL unique keys, and they stay that way. Their
-- components already name a specific entity id, and entity ids come from
-- tenant-owned tables on a single sequence, so two agencies cannot collide on
-- either. Widening them with the agency would not add isolation - it would
-- weaken the dedup by letting the same occurrence through twice.
--
-- Staging, from P26-0 section 6 rule 9:
--
--   052 = nullable structure      (this file)
--   053 = controlled backfill
--   054 = constraints and NOT NULL
--
-- No DEFAULT: it would make every row look correct and destroy the proof
-- 054's SET NOT NULL exists to provide. No agency is named here, by slug or
-- by id.
--
-- Transaction ownership: the runner owns the UP transaction, so this file
-- carries no BEGIN/COMMIT.

-- ---------------------------------------------------------------------------
-- 1. The columns.
-- ---------------------------------------------------------------------------
ALTER TABLE flow_events       ADD COLUMN IF NOT EXISTS agency_id BIGINT;
ALTER TABLE flow_executions   ADD COLUMN IF NOT EXISTS agency_id BIGINT;
ALTER TABLE flow_suppressions ADD COLUMN IF NOT EXISTS agency_id BIGINT;

DO $do$
DECLARE
    v_table   text;
    v_type    text;
    v_null    text;
    v_default text;
BEGIN
    FOREACH v_table IN ARRAY ARRAY['flow_events','flow_executions','flow_suppressions']
    LOOP
        SELECT data_type, is_nullable, column_default
          INTO v_type, v_null, v_default
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name   = v_table
           AND column_name  = 'agency_id';

        IF v_type IS NULL THEN
            RAISE EXCEPTION 'P26-6C 052: %.agency_id is missing after ADD COLUMN', v_table;
        END IF;
        IF v_type <> 'bigint' THEN
            RAISE EXCEPTION 'P26-6C 052: %.agency_id must be bigint, found %', v_table, v_type;
        END IF;
        IF v_null <> 'YES' THEN
            RAISE EXCEPTION 'P26-6C 052: %.agency_id must stay nullable; enforcement belongs to 054', v_table;
        END IF;
        IF v_default IS NOT NULL THEN
            RAISE EXCEPTION 'P26-6C 052: %.agency_id must carry no column default, found %', v_table, v_default;
        END IF;
    END LOOP;
END
$do$;

-- ---------------------------------------------------------------------------
-- 2. The two tables that must NOT have gained one.
--
-- Asserted rather than left implicit: the ownership decision above is the
-- whole design, and a later hand adding a column to either of these would be
-- reintroducing the second copy this slice reasoned its way out of.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_table text;
    v_found text;
BEGIN
    FOREACH v_table IN ARRAY ARRAY['flow_action_records','flow_rules']
    LOOP
        SELECT column_name INTO v_found
          FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = v_table
           AND column_name = 'agency_id';
        IF v_found IS NOT NULL THEN
            RAISE EXCEPTION
                'P26-6C 052: % must not carry a physical agency_id - it is % ',
                v_table,
                CASE WHEN v_table = 'flow_rules'
                     THEN 'the platform-global rule registry'
                     ELSE 'derived from its mandatory execution parent' END;
        END IF;
    END LOOP;
END
$do$;

-- ---------------------------------------------------------------------------
-- 3. Foreign keys.
--
-- Each lookup is scoped by conrelid: constraint names are unique per table,
-- not per database, so a lookup by conname alone could inspect something
-- belonging elsewhere. ON DELETE RESTRICT, matching every other agency FK.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_table      text;
    v_name       text;
    v_con        pg_constraint%ROWTYPE;
    v_own_att    smallint;
    v_agency_att smallint;
BEGIN
    SELECT attnum INTO v_agency_att
      FROM pg_attribute
     WHERE attrelid = 'public.agencies'::regclass AND attname = 'id';

    FOREACH v_table IN ARRAY ARRAY['flow_events','flow_executions','flow_suppressions']
    LOOP
        v_name := v_table || '_agency_id_fk';

        SELECT attnum INTO v_own_att
          FROM pg_attribute
         WHERE attrelid = ('public.' || v_table)::regclass AND attname = 'agency_id';

        SELECT * INTO v_con
          FROM pg_constraint
         WHERE conname  = v_name
           AND conrelid = ('public.' || v_table)::regclass;

        IF NOT FOUND THEN
            EXECUTE format(
                'ALTER TABLE %I ADD CONSTRAINT %I FOREIGN KEY (agency_id) '
                'REFERENCES agencies (id) ON DELETE RESTRICT',
                v_table, v_name
            );
        ELSE
            IF v_con.contype <> 'f'
               OR v_con.confrelid <> 'public.agencies'::regclass
               OR v_con.confdeltype <> 'r'
               OR v_con.conkey  <> ARRAY[v_own_att]::smallint[]
               OR v_con.confkey <> ARRAY[v_agency_att]::smallint[]
            THEN
                RAISE EXCEPTION
                    'P26-6C 052: a constraint named % already exists but is not the expected reference to agencies', v_name;
            END IF;
        END IF;
    END LOOP;
END
$do$;

-- ---------------------------------------------------------------------------
-- 4. Indexes.
--
-- Composite, leading with the agency, because every scoped read filters on the
-- tenant first and then orders by the column the existing single-tenant index
-- already leads with. A plain agency_id index would be used and then discarded
-- for the sort.
--
-- Not CONCURRENTLY: a concurrent build cannot run inside the runner's
-- transaction.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_oid oid;
BEGIN
    SELECT c.oid INTO v_oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE c.relname = 'idx_flow_events_agency' AND n.nspname = 'public';
    IF v_oid IS NULL THEN
        CREATE INDEX idx_flow_events_agency
            ON flow_events (agency_id, status, received_at DESC);
    END IF;

    SELECT c.oid INTO v_oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE c.relname = 'idx_flow_executions_agency' AND n.nspname = 'public';
    IF v_oid IS NULL THEN
        CREATE INDEX idx_flow_executions_agency
            ON flow_executions (agency_id, status, created_at DESC);
    END IF;

    SELECT c.oid INTO v_oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE c.relname = 'idx_flow_suppressions_agency' AND n.nspname = 'public';
    IF v_oid IS NULL THEN
        CREATE INDEX idx_flow_suppressions_agency
            ON flow_suppressions (agency_id, rule_id, entity_type, entity_id);
    END IF;
END
$do$;
