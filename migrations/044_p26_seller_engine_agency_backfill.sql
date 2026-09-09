-- P26-6A SELLER ENGINE, part 2 of 3: the controlled backfill.
--
--   043  nullable structure       (applied)
--   044  controlled backfill      <- this file
--   045  NOT NULL and integrity
--
-- WHERE THE AGENCY COMES FROM
--
-- Provenance, and only provenance. Each log row is asked which agencies its own
-- references point at:
--
--   seller_timeline_events   contact_id -> contacts.agency_id
--                            lead_id    -> leads.agency_id
--                            stima_id   -> stime.agency_id
--                            property_id-> properties.agency_id
--
--   followup_actions         contact_id -> contacts.agency_id
--                            lead_id    -> leads.agency_id
--                            stima_id   -> stime.agency_id
--                            task_id    -> tasks.agency_id
--
-- and the row is assigned that agency only when the answer is unambiguous:
--
--   exactly one distinct agency  -> assign it
--   more than one                -> REFUSE
--   none derivable               -> REFUSE
--
-- There is no Default Agency fallback, no MIN, no MAX, no LIMIT 1, no COALESCE
-- across candidates. Each of those would convert "this row is undecidable" into
-- "here is an answer", which is exactly the conversion a tenancy backfill must
-- never make: a wrong owner does not fail, it produces a row that looks correct
-- in the wrong tenant and stays there.
--
-- WHY A ROW CAN BE UNDECIDABLE
--
-- Both tables use ON DELETE SET NULL on every reference. A row whose parents
-- have all been deleted has had every reference blanked and genuinely carries no
-- provenance left. That is not a defect in this migration - it is the reason the
-- physical column exists, and it is why the refusal is loud rather than quiet.
-- Such rows must be resolved by hand, once, before enforcement.
--
-- NO AUTOMATIC REPAIR
--
-- Nothing here corrects data. A row referencing two agencies is a real integrity
-- fault and there is no way to know which reference was the mistake.
--
-- Transaction ownership: the runner owns the UP transaction, so this file
-- carries no BEGIN/COMMIT, and a failed guard leaves the database exactly as it
-- was.
--
-- Irreversible: afterwards a backfilled row is indistinguishable from one the
-- runtime wrote. See 044_p26_seller_engine_agency_backfill_down.sql.

-- ---------------------------------------------------------------------------
-- Guard 1 - timeline rows with no derivable agency.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    orphan_count BIGINT;
    sample_ids   TEXT;
BEGIN
    SELECT count(*) INTO orphan_count
      FROM seller_timeline_events e
     WHERE e.agency_id IS NULL
       AND NOT EXISTS (
           SELECT 1 FROM contacts   c WHERE c.id = e.contact_id  AND c.agency_id IS NOT NULL
           UNION ALL
           SELECT 1 FROM leads      l WHERE l.id = e.lead_id     AND l.agency_id IS NOT NULL
           UNION ALL
           SELECT 1 FROM stime      s WHERE s.id = e.stima_id    AND s.agency_id IS NOT NULL
           UNION ALL
           SELECT 1 FROM properties p WHERE p.id = e.property_id AND p.agency_id IS NOT NULL
       );

    IF orphan_count <> 0 THEN
        SELECT string_agg(t.id::text, ', ') INTO sample_ids
          FROM (
                SELECT e.id
                  FROM seller_timeline_events e
                 WHERE e.agency_id IS NULL
                   AND NOT EXISTS (
                       SELECT 1 FROM contacts   c WHERE c.id = e.contact_id  AND c.agency_id IS NOT NULL
                       UNION ALL
                       SELECT 1 FROM leads      l WHERE l.id = e.lead_id     AND l.agency_id IS NOT NULL
                       UNION ALL
                       SELECT 1 FROM stime      s WHERE s.id = e.stima_id    AND s.agency_id IS NOT NULL
                       UNION ALL
                       SELECT 1 FROM properties p WHERE p.id = e.property_id AND p.agency_id IS NOT NULL
                   )
               ) AS t;

        RAISE EXCEPTION
            'P26-6A 044 refused: % timeline event(s) have no reference from which an agency can be derived (id: %). Every reference uses ON DELETE SET NULL, so these rows have outlived their parents and must be resolved by hand. No row was modified.',
            orphan_count, sample_ids;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- Guard 2 - timeline rows whose references disagree.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    ambiguous_count BIGINT;
    sample_ids      TEXT;
BEGIN
    SELECT count(*) INTO ambiguous_count
      FROM (
            SELECT e.id
              FROM seller_timeline_events e
              JOIN LATERAL (
                    SELECT c.agency_id FROM contacts   c WHERE c.id = e.contact_id
                    UNION
                    SELECT l.agency_id FROM leads      l WHERE l.id = e.lead_id
                    UNION
                    SELECT s.agency_id FROM stime      s WHERE s.id = e.stima_id
                    UNION
                    SELECT p.agency_id FROM properties p WHERE p.id = e.property_id
                   ) AS candidates(agency_id) ON TRUE
             WHERE e.agency_id IS NULL
               AND candidates.agency_id IS NOT NULL
             GROUP BY e.id
            HAVING count(DISTINCT candidates.agency_id) > 1
           ) AS ambiguous;

    IF ambiguous_count <> 0 THEN
        SELECT string_agg(t.id::text, ', ') INTO sample_ids
          FROM (
                SELECT e.id
                  FROM seller_timeline_events e
                  JOIN LATERAL (
                        SELECT c.agency_id FROM contacts   c WHERE c.id = e.contact_id
                        UNION
                        SELECT l.agency_id FROM leads      l WHERE l.id = e.lead_id
                        UNION
                        SELECT s.agency_id FROM stime      s WHERE s.id = e.stima_id
                        UNION
                        SELECT p.agency_id FROM properties p WHERE p.id = e.property_id
                       ) AS candidates(agency_id) ON TRUE
                 WHERE e.agency_id IS NULL
                   AND candidates.agency_id IS NOT NULL
                 GROUP BY e.id
                HAVING count(DISTINCT candidates.agency_id) > 1
               ) AS t;

        RAISE EXCEPTION
            'P26-6A 044 refused: % timeline event(s) reference more than one agency (id: %). Ambiguity is a refusal, not an input to a heuristic. Reconcile these rows by hand, then re-apply. No row was modified.',
            ambiguous_count, sample_ids;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- Guard 3 - follow-up actions with no derivable agency.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    orphan_count BIGINT;
    sample_ids   TEXT;
BEGIN
    SELECT count(*) INTO orphan_count
      FROM followup_actions a
     WHERE a.agency_id IS NULL
       AND NOT EXISTS (
           SELECT 1 FROM contacts c WHERE c.id = a.contact_id AND c.agency_id IS NOT NULL
           UNION ALL
           SELECT 1 FROM leads    l WHERE l.id = a.lead_id    AND l.agency_id IS NOT NULL
           UNION ALL
           SELECT 1 FROM stime    s WHERE s.id = a.stima_id   AND s.agency_id IS NOT NULL
           UNION ALL
           SELECT 1 FROM tasks    t WHERE t.id = a.task_id    AND t.agency_id IS NOT NULL
       );

    IF orphan_count <> 0 THEN
        SELECT string_agg(t.id::text, ', ') INTO sample_ids
          FROM (
                SELECT a.id
                  FROM followup_actions a
                 WHERE a.agency_id IS NULL
                   AND NOT EXISTS (
                       SELECT 1 FROM contacts c WHERE c.id = a.contact_id AND c.agency_id IS NOT NULL
                       UNION ALL
                       SELECT 1 FROM leads    l WHERE l.id = a.lead_id    AND l.agency_id IS NOT NULL
                       UNION ALL
                       SELECT 1 FROM stime    s WHERE s.id = a.stima_id   AND s.agency_id IS NOT NULL
                       UNION ALL
                       SELECT 1 FROM tasks    t WHERE t.id = a.task_id    AND t.agency_id IS NOT NULL
                   )
               ) AS t;

        RAISE EXCEPTION
            'P26-6A 044 refused: % follow-up action(s) have no reference from which an agency can be derived (id: %). No row was modified.',
            orphan_count, sample_ids;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- Guard 4 - follow-up actions whose references disagree.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    ambiguous_count BIGINT;
    sample_ids      TEXT;
BEGIN
    SELECT count(*) INTO ambiguous_count
      FROM (
            SELECT a.id
              FROM followup_actions a
              JOIN LATERAL (
                    SELECT c.agency_id FROM contacts c WHERE c.id = a.contact_id
                    UNION
                    SELECT l.agency_id FROM leads    l WHERE l.id = a.lead_id
                    UNION
                    SELECT s.agency_id FROM stime    s WHERE s.id = a.stima_id
                    UNION
                    SELECT t.agency_id FROM tasks    t WHERE t.id = a.task_id
                   ) AS candidates(agency_id) ON TRUE
             WHERE a.agency_id IS NULL
               AND candidates.agency_id IS NOT NULL
             GROUP BY a.id
            HAVING count(DISTINCT candidates.agency_id) > 1
           ) AS ambiguous;

    IF ambiguous_count <> 0 THEN
        SELECT string_agg(t.id::text, ', ') INTO sample_ids
          FROM (
                SELECT a.id
                  FROM followup_actions a
                  JOIN LATERAL (
                        SELECT c.agency_id FROM contacts c WHERE c.id = a.contact_id
                        UNION
                        SELECT l.agency_id FROM leads    l WHERE l.id = a.lead_id
                        UNION
                        SELECT s.agency_id FROM stime    s WHERE s.id = a.stima_id
                        UNION
                        SELECT t.agency_id FROM tasks    t WHERE t.id = a.task_id
                       ) AS candidates(agency_id) ON TRUE
                 WHERE a.agency_id IS NULL
                   AND candidates.agency_id IS NOT NULL
                 GROUP BY a.id
                HAVING count(DISTINCT candidates.agency_id) > 1
               ) AS t;

        RAISE EXCEPTION
            'P26-6A 044 refused: % follow-up action(s) reference more than one agency (id: %). No row was modified.',
            ambiguous_count, sample_ids;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- The backfill.
--
-- Guards 1-4 have established that every unowned row resolves to exactly one
-- agency, so the DISTINCT projection below yields exactly one row for each of
-- them. No aggregate touches the value: the agency written is the agency
-- recorded on the reference, carried through unchanged.
--
-- `agency_id IS NULL` keeps an already-owned row untouched and makes a repeat
-- execution a zero-row no-op - which matters if the runner dies between this
-- body and register().
-- ---------------------------------------------------------------------------
UPDATE seller_timeline_events e
   SET agency_id = derived.agency_id
  FROM (
        SELECT DISTINCT e2.id AS row_id, candidates.agency_id
          FROM seller_timeline_events e2
          JOIN LATERAL (
                SELECT c.agency_id FROM contacts   c WHERE c.id = e2.contact_id
                UNION
                SELECT l.agency_id FROM leads      l WHERE l.id = e2.lead_id
                UNION
                SELECT s.agency_id FROM stime      s WHERE s.id = e2.stima_id
                UNION
                SELECT p.agency_id FROM properties p WHERE p.id = e2.property_id
               ) AS candidates(agency_id) ON TRUE
         WHERE candidates.agency_id IS NOT NULL
       ) AS derived
 WHERE e.id = derived.row_id
   AND e.agency_id IS NULL;

UPDATE followup_actions a
   SET agency_id = derived.agency_id
  FROM (
        SELECT DISTINCT a2.id AS row_id, candidates.agency_id
          FROM followup_actions a2
          JOIN LATERAL (
                SELECT c.agency_id FROM contacts c WHERE c.id = a2.contact_id
                UNION
                SELECT l.agency_id FROM leads    l WHERE l.id = a2.lead_id
                UNION
                SELECT s.agency_id FROM stime    s WHERE s.id = a2.stima_id
                UNION
                SELECT t.agency_id FROM tasks    t WHERE t.id = a2.task_id
               ) AS candidates(agency_id) ON TRUE
         WHERE candidates.agency_id IS NOT NULL
       ) AS derived
 WHERE a.id = derived.row_id
   AND a.agency_id IS NULL;

-- ---------------------------------------------------------------------------
-- Guard 5 - 044 certifies its own post-condition.
--
-- 045's SET NOT NULL is a second, independent gate, not a substitute: finding an
-- incomplete backfill there would mean two migrations to unpick instead of one
-- refused transaction.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    unowned_events  BIGINT;
    unowned_actions BIGINT;
BEGIN
    SELECT count(*) INTO unowned_events  FROM seller_timeline_events WHERE agency_id IS NULL;
    SELECT count(*) INTO unowned_actions FROM followup_actions       WHERE agency_id IS NULL;

    IF unowned_events <> 0 OR unowned_actions <> 0 THEN
        RAISE EXCEPTION
            'P26-6A 044 incomplete: % timeline event(s) and % follow-up action(s) still carry no agency. The transaction is rolled back; no partial backfill is committed.',
            unowned_events, unowned_actions;
    END IF;
END
$do$;
