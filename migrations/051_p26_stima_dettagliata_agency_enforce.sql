-- P26-6C STIME DETTAGLIATE, part 3 of 3: constraints and integrity.
--
--   049  nullable structure       (applied)
--   050  controlled backfill      (applied)
--   051  NOT NULL and integrity   <- this file
--
-- SET NOT NULL is the proof 050 reached every row. It is the reason 049 was
-- forbidden a DEFAULT: with one, every row would look correct and this
-- statement would succeed without proving anything.
--
-- THE TRIGGER, AND WHAT IT IS ACTUALLY FOR
--
-- A detail row may have a parent estimation or none. When it has one, the two
-- must agree: a detail filed under agency A whose parent stima belongs to B is
-- a row that reads correctly from either side and is wrong from both. The
-- admin list would show it to A while the estimation it details belongs to B.
--
-- When it has no parent, there is nothing to check, and the trigger says so by
-- returning early rather than by refusing - an orphan is a legal row with an
-- explicit owner, which is exactly what 049 made possible.
--
-- One function for one table, not a shared multi-table function. Migration 040
-- shipped a bug this project pays attention to: PL/pgSQL resolves a record
-- field when it *prepares* the expression, not only when a preceding conjunct
-- is true, so `IF TG_TABLE_NAME = 'x' AND NEW.<field>` fails on every table
-- that lacks that field. A single-table function has no such conjunct to get
-- wrong.
--
-- Transaction ownership: the runner owns the UP transaction, so this file
-- carries no BEGIN/COMMIT.

-- ---------------------------------------------------------------------------
-- 1. NOT NULL.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_remaining bigint;
BEGIN
    SELECT COUNT(*) INTO v_remaining
      FROM stime_dettagliate
     WHERE agency_id IS NULL;

    IF v_remaining > 0 THEN
        RAISE EXCEPTION
            'P26-6C 051: % stime_dettagliate row(s) still have a NULL agency_id; 050 has not completed',
            v_remaining;
    END IF;
END
$do$;

ALTER TABLE stime_dettagliate
    ALTER COLUMN agency_id SET NOT NULL;

DO $do$
DECLARE
    v_null text;
BEGIN
    SELECT is_nullable INTO v_null
      FROM information_schema.columns
     WHERE table_schema = 'public'
       AND table_name   = 'stime_dettagliate'
       AND column_name  = 'agency_id';

    IF v_null <> 'NO' THEN
        RAISE EXCEPTION 'P26-6C 051: stime_dettagliate.agency_id is still nullable after SET NOT NULL';
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- 2. Parent agreement.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION stima_dettagliata_agency_integrity()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_parent_agency BIGINT;
BEGIN
    IF NEW.agency_id IS NULL THEN
        RAISE EXCEPTION
            'stime_dettagliate.agency_id cannot be NULL (id=%)', NEW.id;
    END IF;

    -- An orphan is legal and owns itself. Returning here is the whole reason
    -- this table has a physical column rather than a derived scope.
    IF NEW.stima_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT agency_id INTO v_parent_agency
      FROM stime
     WHERE id = NEW.stima_id;

    IF v_parent_agency IS NULL THEN
        RAISE EXCEPTION
            'stime_dettagliate.stima_id=% does not resolve to a stima with an agency',
            NEW.stima_id;
    END IF;

    IF v_parent_agency <> NEW.agency_id THEN
        RAISE EXCEPTION
            'stime_dettagliate.agency_id=% does not match its parent stima %''s agency %',
            NEW.agency_id, NEW.stima_id, v_parent_agency;
    END IF;

    RETURN NEW;
END
$fn$;

DROP TRIGGER IF EXISTS trg_stima_dettagliata_agency_integrity ON stime_dettagliate;
CREATE TRIGGER trg_stima_dettagliata_agency_integrity
    BEFORE INSERT OR UPDATE ON stime_dettagliate
    FOR EACH ROW EXECUTE FUNCTION stima_dettagliata_agency_integrity();

-- ---------------------------------------------------------------------------
-- 3. Proof the trigger is installed and armed for both events.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_tgtype smallint;
BEGIN
    SELECT tgtype INTO v_tgtype
      FROM pg_trigger
     WHERE tgname   = 'trg_stima_dettagliata_agency_integrity'
       AND tgrelid  = 'public.stime_dettagliate'::regclass
       AND NOT tgisinternal;

    IF v_tgtype IS NULL THEN
        RAISE EXCEPTION 'P26-6C 051: trg_stima_dettagliata_agency_integrity is not installed';
    END IF;
    -- pg_trigger.tgtype flags: ROW=1, BEFORE=2, INSERT=4, UPDATE=16
    IF (v_tgtype & 1) = 0 THEN
        RAISE EXCEPTION 'P26-6C 051: the integrity trigger must be FOR EACH ROW';
    END IF;
    IF (v_tgtype & 2) = 0 THEN
        RAISE EXCEPTION 'P26-6C 051: the integrity trigger must fire BEFORE, not AFTER';
    END IF;
    IF (v_tgtype & 4) = 0 OR (v_tgtype & 16) = 0 THEN
        RAISE EXCEPTION 'P26-6C 051: the integrity trigger must cover both INSERT and UPDATE';
    END IF;
END
$do$;
