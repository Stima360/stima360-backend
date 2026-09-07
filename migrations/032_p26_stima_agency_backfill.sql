-- P26-2B STIMA root ownership, part 2 of 3: the historical backfill.
--
-- Assigns an agency to the estimations that predate the tenant-aware writer.
-- Data only: no structure, no constraint, no index, no trigger.
--
--   031  nullable structure         (applied)
--   032  controlled backfill        <- this file
--   ---  NOT NULL / integrity       (a later block)
--
-- WHERE THE AGENCY COMES FROM
--
-- Provenance, and only provenance:
--
--     stime -> lead_stime -> leads.agency_id
--
-- Every historical stima reached CORE through the public bridge, which created
-- or linked a lead for it. That lead carries an agency. So the estimation's
-- owner is not a guess - it is recorded, one join away.
--
-- There is deliberately NO Default Agency fallback here, and that is the single
-- most important difference between this file and 029.
--
-- 029 could legitimately name the Default Agency: it was assigning the entire
-- legacy estate, at a moment when exactly one agency existed, to that one
-- agency. Every row got the same answer and the answer was known to be right.
--
-- 032 is a different act. It assigns *specific* estimations to *specific*
-- agencies, in a system that now has more than one. A fallback would mean:
-- "provenance did not answer, so put it in the Default Agency." That produces
-- a row indistinguishable from a correct one, in the wrong tenant, with a
-- foreign key that resolves and a constraint that passes. It would not fail -
-- it would silently be a cross-tenant leak. A refused migration is recoverable;
-- a plausible wrong owner is not.
--
-- For the same reason there is no MIN, MAX, LIMIT 1, ORDER BY or COALESCE
-- anywhere below. Each of those turns "this data is ambiguous" into "here is an
-- answer", which is precisely the conversion this migration must refuse to
-- make. Ambiguity is a hard failure, not an input to a heuristic.
--
-- WHAT MUST HOLD FOR EVERY ROW BEING BACKFILLED
--
--   1. at least one lead is reachable through lead_stime      (else: refuse)
--   2. no reachable lead has a NULL agency of its own          (else: refuse)
--   3. the reachable leads agree on exactly one agency         (else: refuse)
--
-- Note that lead_stime's unique key is (lead_id, stima_id), so the schema
-- permits several leads per stima. One-lead-per-stima has only been enforced in
-- application code since P26-1, which means historical rows genuinely can carry
-- more than one link. Guard 3 is therefore a live possibility, not a formality.
--
-- ROWS THAT ALREADY HAVE AN AGENCY ARE NEVER OVERWRITTEN
--
-- The UPDATE carries `agency_id IS NULL`, so a row the runtime writer already
-- stamped is left exactly as it is. That also makes the file re-runnable: if
-- the runner dies between this body and register(), the next apply re-executes
-- it as a zero-row no-op rather than reassigning anything.
--
-- Guard 4 still *inspects* those rows: an already-owned stima whose linked lead
-- disagrees with it is a pre-existing integrity fault, and this migration
-- refuses to build a NOT NULL constraint on top of it rather than stepping past
-- it quietly.
--
-- Guard 5 does the same for activities and tasks, which carry their own
-- agency_id and reference a stima directly. Both are read only - 032 writes
-- exactly one column of exactly one table.
--
-- Transaction ownership: this file opens no transaction and commits nothing.
-- scripts/p26_migrate.py executes this body, writes the schema_migrations row
-- through register(), and commits both together, rolling back on any error. So
-- a failed guard below leaves the database exactly as it was.
-- See docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.
--
-- Irreversible, like 029, and for the same reason: afterwards a backfilled
-- historical row is indistinguishable from one the runtime writer stamped.
-- See 032_p26_stima_agency_backfill_down.sql.

-- ---------------------------------------------------------------------------
-- Guard 1 - zero provenance.
--
-- A stima with no agency and no reachable lead cannot have an owner derived at
-- all. There is nothing to fall back to, so the migration refuses.
--
-- The NOT EXISTS goes through the join rather than testing lead_stime alone: a
-- link whose lead has vanished proves nothing about ownership.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    orphan_count BIGINT;
    sample_ids   TEXT;
BEGIN
    SELECT count(*) INTO orphan_count
      FROM stime s
     WHERE s.agency_id IS NULL
       AND NOT EXISTS (
           SELECT 1
             FROM lead_stime ls
             JOIN leads l ON l.id = ls.lead_id
            WHERE ls.stima_id = s.id
       );

    IF orphan_count <> 0 THEN
        SELECT string_agg(t.id::text, ', ') INTO sample_ids
          FROM (
                SELECT s.id
                  FROM stime s
                 WHERE s.agency_id IS NULL
                   AND NOT EXISTS (
                       SELECT 1
                         FROM lead_stime ls
                         JOIN leads l ON l.id = ls.lead_id
                        WHERE ls.stima_id = s.id
                   )
               ) AS t;

        RAISE EXCEPTION
            'P26-2B 032 refused: % estimation(s) have no agency and no lead to '
            'derive one from (stime.id: %). This migration assigns owners from '
            'recorded provenance only and has no fallback, because an invented '
            'owner is a cross-tenant leak that looks like correct data. '
            'Resolve the provenance of these rows, then re-apply. '
            'No row was modified.',
            orphan_count, sample_ids;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- Guard 2 - a linked lead with no agency of its own.
--
-- Checked separately from Guard 3 because count(DISTINCT ...) skips NULLs: a
-- stima linked to one lead in agency A and one lead with a NULL agency would
-- report exactly one distinct agency and slip through. The NULL is the signal
-- that 029 did not reach that lead, and deriving from a half-migrated CORE row
-- is not derivation.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    unowned_leads BIGINT;
BEGIN
    SELECT count(*) INTO unowned_leads
      FROM stime s
      JOIN lead_stime ls ON ls.stima_id = s.id
      JOIN leads l ON l.id = ls.lead_id
     WHERE s.agency_id IS NULL
       AND l.agency_id IS NULL;

    IF unowned_leads <> 0 THEN
        RAISE EXCEPTION
            'P26-2B 032 refused: % lead(s) linked to an unowned estimation '
            'carry no agency themselves, so they cannot confer one. Apply the '
            'CORE backfill (029) first. No row was modified.',
            unowned_leads;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- Guard 3 - more than one candidate agency.
--
-- The estimation is linked to leads in two different agencies. There is no
-- correct automatic answer: choosing either one silently places the row, and
-- everything derived from it, in a tenant that may not own it.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    ambiguous_count BIGINT;
    sample_ids      TEXT;
BEGIN
    SELECT count(*) INTO ambiguous_count
      FROM (
            SELECT s.id
              FROM stime s
              JOIN lead_stime ls ON ls.stima_id = s.id
              JOIN leads l ON l.id = ls.lead_id
             WHERE s.agency_id IS NULL
             GROUP BY s.id
            HAVING count(DISTINCT l.agency_id) > 1
           ) AS ambiguous;

    IF ambiguous_count <> 0 THEN
        SELECT string_agg(t.id::text, ', ') INTO sample_ids
          FROM (
                SELECT s.id
                  FROM stime s
                  JOIN lead_stime ls ON ls.stima_id = s.id
                  JOIN leads l ON l.id = ls.lead_id
                 WHERE s.agency_id IS NULL
                 GROUP BY s.id
                HAVING count(DISTINCT l.agency_id) > 1
               ) AS t;

        RAISE EXCEPTION
            'P26-2B 032 refused: % estimation(s) resolve to more than one '
            'agency through their linked leads (stime.id: %). Ambiguity is a '
            'refusal, not an input to a heuristic. Reconcile the links by hand, '
            'then re-apply. No row was modified.',
            ambiguous_count, sample_ids;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- Guard 4 - an already-owned estimation that disagrees with its lead.
--
-- These rows are not written by this migration. They are inspected because the
-- next block puts NOT NULL and integrity constraints on this column, and doing
-- that over a known contradiction would bake it in.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    conflicting BIGINT;
    sample_ids  TEXT;
BEGIN
    SELECT count(*) INTO conflicting
      FROM stime s
      JOIN lead_stime ls ON ls.stima_id = s.id
      JOIN leads l ON l.id = ls.lead_id
     WHERE s.agency_id IS NOT NULL
       AND l.agency_id IS NOT NULL
       AND s.agency_id <> l.agency_id;

    IF conflicting <> 0 THEN
        SELECT string_agg(DISTINCT s.id::text, ', ') INTO sample_ids
          FROM stime s
          JOIN lead_stime ls ON ls.stima_id = s.id
          JOIN leads l ON l.id = ls.lead_id
         WHERE s.agency_id IS NOT NULL
           AND l.agency_id IS NOT NULL
           AND s.agency_id <> l.agency_id;

        RAISE EXCEPTION
            'P26-2B 032 refused: % link(s) place an already-owned estimation '
            'and its lead in different agencies (stime.id: %). This migration '
            'never overwrites an existing owner, and it will not add '
            'constraints over a contradiction it can see. Reconcile these rows, '
            'then re-apply. No row was modified.',
            conflicting, sample_ids;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- Guard 5 - dependent CORE rows that disagree with the agency about to be
-- written.
--
-- activities and tasks carry their own agency_id (028/029) and reference a
-- stima directly. If one of them already sits in a different agency from the
-- one provenance derives, then either the dependent row or the derivation is
-- wrong, and this migration is not the place to decide which.
--
-- Both tables are read here and nowhere else. 032 writes one column of one
-- table.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    conflicting_activities BIGINT;
    conflicting_tasks      BIGINT;
BEGIN
    SELECT count(*) INTO conflicting_activities
      FROM activities a
      JOIN stime s ON s.id = a.stima_id
      JOIN lead_stime ls ON ls.stima_id = s.id
      JOIN leads l ON l.id = ls.lead_id
     WHERE s.agency_id IS NULL
       AND a.agency_id IS NOT NULL
       AND l.agency_id IS NOT NULL
       AND a.agency_id <> l.agency_id;

    SELECT count(*) INTO conflicting_tasks
      FROM tasks t
      JOIN stime s ON s.id = t.stima_id
      JOIN lead_stime ls ON ls.stima_id = s.id
      JOIN leads l ON l.id = ls.lead_id
     WHERE s.agency_id IS NULL
       AND t.agency_id IS NOT NULL
       AND l.agency_id IS NOT NULL
       AND t.agency_id <> l.agency_id;

    IF conflicting_activities <> 0 OR conflicting_tasks <> 0 THEN
        RAISE EXCEPTION
            'P26-2B 032 refused: % activity row(s) and % task row(s) reference '
            'an unowned estimation but sit in a different agency from the one '
            'its provenance derives. Backfilling would split one estimation '
            'across tenants. Reconcile these rows, then re-apply. '
            'No row was modified.',
            conflicting_activities, conflicting_tasks;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- The backfill.
--
-- Guards 1-3 have established that every unowned stima resolves to exactly one
-- agency, so the DISTINCT projection below yields exactly one row for each of
-- them. No aggregate touches the value itself: the agency written is the agency
-- recorded on the lead, carried through the join unchanged.
--
-- `agency_id IS NULL` keeps an already-owned row untouched and makes a repeat
-- execution a zero-row no-op.
-- ---------------------------------------------------------------------------
UPDATE stime s
   SET agency_id = derived.agency_id
  FROM (
        SELECT DISTINCT ls.stima_id AS stima_id,
                        l.agency_id AS agency_id
          FROM lead_stime ls
          JOIN leads l ON l.id = ls.lead_id
         WHERE l.agency_id IS NOT NULL
       ) AS derived
 WHERE s.id = derived.stima_id
   AND s.agency_id IS NULL;

-- ---------------------------------------------------------------------------
-- Guard 6 - 032 certifies its own post-condition.
--
-- The later NOT NULL migration is a second, independent gate, not a substitute
-- for this one: discovering an incomplete backfill there would mean two
-- migrations to unpick instead of one refused transaction.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    still_unowned BIGINT;
BEGIN
    SELECT count(*) INTO still_unowned
      FROM stime
     WHERE agency_id IS NULL;

    IF still_unowned <> 0 THEN
        RAISE EXCEPTION
            'P26-2B 032 incomplete: % estimation(s) still carry no agency '
            'after the backfill. The transaction is rolled back; no partial '
            'backfill is committed.',
            still_unowned;
    END IF;
END
$do$;
