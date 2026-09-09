-- P26-6C STIME DETTAGLIATE, part 2 of 3: the controlled backfill.
--
--   049  nullable structure       (applied)
--   050  controlled backfill      <- this file
--   051  NOT NULL and integrity
--
-- ONE PROVENANCE, AND ONLY ONE
--
--     stima_id -> stime.agency_id
--
-- `stime.agency_id` has been NOT NULL since 033, so every detail row that has
-- a parent has exactly one derivable agency. There is nothing to disambiguate
-- here and nothing to fall back to.
--
-- THE ORPHAN, AND WHY THIS FILE REFUSES IT
--
-- 049 exists precisely because a detail row can be born with `stima_id NULL` -
-- the public funnel writes `to_int_safe(data.get("stima_id"))` from the
-- request body. Such a row has no provenance at all.
--
-- That is not a defect in this migration; it is the reason the column is
-- physical. But it does mean this file cannot complete on its own if orphans
-- exist, and it must not pretend otherwise:
--
--   * assigning them the Default Agency is what P26-0 forbids, and would file
--     one tenant's estimation detail under another;
--   * skipping them leaves NULLs that 051's SET NOT NULL will reject anyway,
--     one migration later and with a far worse error message;
--   * inventing an agency from anything else in the row - the phone, the
--     email, the name - is a join on unvalidated user input.
--
-- So guard 1 refuses, names the count, and hands the decision to an operator.
-- The refusal is the useful outcome: an orphan detail row is a data-quality
-- problem that predates multi-agency, and it has to be resolved by someone who
-- can look at it, not by a rule written here in advance.
--
-- No Default Agency, no MIN, no MAX, no LIMIT 1, no COALESCE across
-- candidates.
--
-- Transaction ownership: the runner owns the UP transaction, so this file
-- carries no BEGIN/COMMIT. It is not idempotent by construction but it is by
-- effect: the UPDATE only touches rows whose agency_id IS NULL.

-- ---------------------------------------------------------------------------
-- Guard 1: every row must have provenance BEFORE anything is written.
--
-- Checked first, and against the whole table rather than the rows the UPDATE
-- would touch, so a second run cannot pass merely because the first one
-- already filled in everything it could.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_orphans bigint;
    v_sample  text;
BEGIN
    SELECT COUNT(*),
           COALESCE(string_agg(id::text, ', ' ORDER BY id), '')
      INTO v_orphans, v_sample
      FROM (
          SELECT id
            FROM stime_dettagliate
           WHERE stima_id IS NULL
           ORDER BY id
           FETCH FIRST 20 ROWS ONLY
      ) orphan;

    IF v_orphans > 0 THEN
        RAISE EXCEPTION
            'P26-6C 050: % stime_dettagliate row(s) have no stima_id and therefore no derivable agency (first ids: %). Resolve them - attach the parent estimation, or remove the row - and re-run. This migration will not guess an owner.',
            v_orphans, v_sample;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- Guard 2: every referenced stima must exist and carry an agency.
--
-- `stima_id` has no ON DELETE clause, so the reference cannot dangle - but the
-- constraint is only as good as the schema in front of us, and this costs one
-- scan to prove rather than assume.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_unresolvable bigint;
BEGIN
    SELECT COUNT(*)
      INTO v_unresolvable
      FROM stime_dettagliate d
      LEFT JOIN stime s ON s.id = d.stima_id
     WHERE d.stima_id IS NOT NULL
       AND (s.id IS NULL OR s.agency_id IS NULL);

    IF v_unresolvable > 0 THEN
        RAISE EXCEPTION
            'P26-6C 050: % stime_dettagliate row(s) reference a stima that is missing or carries no agency_id. 033 made stime.agency_id NOT NULL, so this indicates the ledger is not in the state 050 expects.',
            v_unresolvable;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- Guard 3: exactly one distinct agency per row.
--
-- `stima_id` is a single scalar reference, so a second distinct agency is not
-- reachable through it. The guard is written anyway, as a DISTINCT projection
-- rather than an assumption, because "cannot happen given the current schema"
-- is a claim about a schema that migrations exist to change.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_ambiguous bigint;
BEGIN
    SELECT COUNT(*)
      INTO v_ambiguous
      FROM (
          SELECT d.id
            FROM stime_dettagliate d
            JOIN stime s ON s.id = d.stima_id
           WHERE d.stima_id IS NOT NULL
           GROUP BY d.id
          HAVING COUNT(DISTINCT s.agency_id) <> 1
      ) ambiguous;

    IF v_ambiguous > 0 THEN
        RAISE EXCEPTION
            'P26-6C 050: % stime_dettagliate row(s) resolve to more than one distinct agency. Refusing rather than choosing.',
            v_ambiguous;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- The assignment.
-- ---------------------------------------------------------------------------
UPDATE stime_dettagliate d
   SET agency_id = s.agency_id
  FROM stime s
 WHERE s.id = d.stima_id
   AND d.agency_id IS NULL;

-- ---------------------------------------------------------------------------
-- Guard 4: nothing was left behind.
--
-- The proof that the UPDATE reached every row, taken after the write rather
-- than inferred from the guards before it.
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
            'P26-6C 050: % stime_dettagliate row(s) still have a NULL agency_id after the backfill',
            v_remaining;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- Guard 5: no row was filed against its parent's tenant incorrectly.
--
-- Re-derives the answer independently of the UPDATE that produced it and
-- compares. A backfill that verifies only "not null" proves it wrote
-- something, not that it wrote the right thing.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_mismatched bigint;
BEGIN
    SELECT COUNT(*) INTO v_mismatched
      FROM stime_dettagliate d
      JOIN stime s ON s.id = d.stima_id
     WHERE d.agency_id <> s.agency_id;

    IF v_mismatched > 0 THEN
        RAISE EXCEPTION
            'P26-6C 050: % stime_dettagliate row(s) disagree with their parent stima''s agency after the backfill',
            v_mismatched;
    END IF;
END
$do$;
