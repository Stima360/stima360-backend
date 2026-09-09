-- P26-2D BUY root ownership, part 1 of 3: nullable agency column.
-- Runner-owned transaction: no BEGIN/COMMIT here.

ALTER TABLE buy_requests
    ADD COLUMN IF NOT EXISTS agency_id BIGINT;

DO $do$
DECLARE
    v_type text;
    v_null text;
    v_default text;
BEGIN
    SELECT data_type, is_nullable, column_default
      INTO v_type, v_null, v_default
      FROM information_schema.columns
     WHERE table_schema='public'
       AND table_name='buy_requests'
       AND column_name='agency_id';

    IF v_type IS NULL THEN
        RAISE EXCEPTION 'P26-2D 037: buy_requests.agency_id missing after ADD COLUMN';
    END IF;
    IF v_type <> 'bigint' THEN
        RAISE EXCEPTION 'P26-2D 037: buy_requests.agency_id must be bigint, found %', v_type;
    END IF;
    IF v_null <> 'YES' THEN
        RAISE EXCEPTION 'P26-2D 037: buy_requests.agency_id must remain nullable';
    END IF;
    IF v_default IS NOT NULL THEN
        RAISE EXCEPTION 'P26-2D 037: buy_requests.agency_id must have no default';
    END IF;
END
$do$;

DO $do$
DECLARE
    v_con pg_constraint%ROWTYPE;
    v_buy_att smallint;
    v_agency_att smallint;
BEGIN
    SELECT attnum INTO v_buy_att
      FROM pg_attribute
     WHERE attrelid='public.buy_requests'::regclass AND attname='agency_id';
    SELECT attnum INTO v_agency_att
      FROM pg_attribute
     WHERE attrelid='public.agencies'::regclass AND attname='id';

    SELECT * INTO v_con
      FROM pg_constraint
     WHERE conname='buy_requests_agency_id_fk'
       AND conrelid='public.buy_requests'::regclass;

    IF NOT FOUND THEN
        ALTER TABLE buy_requests
            ADD CONSTRAINT buy_requests_agency_id_fk
            FOREIGN KEY (agency_id) REFERENCES agencies(id) ON DELETE RESTRICT;
    ELSE
        IF v_con.contype <> 'f'
           OR v_con.confrelid <> 'public.agencies'::regclass
           OR v_con.confdeltype <> 'r'
           OR v_con.conkey <> ARRAY[v_buy_att]::smallint[]
           OR v_con.confkey <> ARRAY[v_agency_att]::smallint[]
        THEN
            RAISE EXCEPTION
                'P26-2D 037: buy_requests_agency_id_fk exists with incompatible definition';
        END IF;
    END IF;
END
$do$;

DO $do$
DECLARE
    v_oid oid;
    v_buy_att smallint;
BEGIN
    SELECT attnum INTO v_buy_att
      FROM pg_attribute
     WHERE attrelid='public.buy_requests'::regclass AND attname='agency_id';

    SELECT c.oid INTO v_oid
      FROM pg_class c
      JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE c.relname='idx_buy_requests_agency_id' AND n.nspname='public';

    IF v_oid IS NULL THEN
        CREATE INDEX idx_buy_requests_agency_id ON buy_requests(agency_id);
    ELSE
        PERFORM 1
          FROM pg_index i
         WHERE i.indexrelid=v_oid
           AND i.indrelid='public.buy_requests'::regclass
           AND i.indnatts=1
           AND i.indnkeyatts=1
           AND i.indkey[0]=v_buy_att
           AND i.indisunique=false
           AND i.indisvalid=true
           AND i.indisready=true
           AND i.indislive=true
           AND i.indpred IS NULL
           AND i.indexprs IS NULL;
        IF NOT FOUND THEN
            RAISE EXCEPTION
                'P26-2D 037: idx_buy_requests_agency_id exists with incompatible definition';
        END IF;
    END IF;
END
$do$;
