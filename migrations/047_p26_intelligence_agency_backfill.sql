-- P26-6B INTELLIGENCE, part 2 of 3: the controlled backfill.
--
--   046  nullable structure       (applied)
--   047  controlled backfill      <- this file
--   048  NOT NULL and integrity
--
-- TWO TABLES, TWO DIFFERENT PROVENANCE SHAPES
--
-- property_watches has exactly one source: its stima.
--
--     stima_id -> stime.agency_id
--
-- There is nothing to disambiguate, and nothing to fall back to. A watch whose
-- stima_id has been blanked by ON DELETE SET NULL genuinely has no provenance
-- left, and this migration refuses rather than inventing one. That refusal is
-- the whole reason 046 gave the table a physical column: the alternative is a
-- row nobody can ever assign.
--
-- next_best_actions has several, and they can disagree. Candidates come from
-- the three direct references AND from the polymorphic subject:
--
--     contact_id -> contacts.agency_id
--     lead_id    -> leads.agency_id
--     stima_id   -> stime.agency_id
--     subject_type/subject_id, resolved per type:
--         'lead'        -> leads.agency_id
--         'buy_request' -> buy_requests.agency_id
--         'stima'       -> stime.agency_id
--         'match'       -> through the match's own pair, certified by P26-4:
--                          both roots of a match are already guaranteed to
--                          share one agency, so either side answers - this file
--                          reads the buy request's.
--
-- The subject types are the four next_best_action/signals.py emits. A row
-- carrying any other subject_type resolves no candidate from the subject and,
-- unless its direct references answer, is refused by guard 4 rather than
-- silently assigned - which is how an unrecognised type surfaces instead of
-- being papered over.
--
-- THE RULE, IN BOTH CASES
--
--   exactly one distinct agency -> assign it
--   more than one               -> REFUSE
--   none derivable              -> REFUSE
--
-- No Default Agency, no MIN, no MAX, no LIMIT 1, no COALESCE across candidates.
-- Each of those turns "undecidable" into "here is an answer", which for a
-- tenancy is the one conversion never to make: a wrong owner does not fail, it
-- produces a row that looks correct in the wrong tenant.
--
-- Nothing here repairs data. A row referencing two agencies is a real integrity
-- fault, and there is no way to know which reference was the mistake.
--
-- Transaction ownership: the runner owns the UP transaction, so a failed guard
-- leaves the database exactly as it was.
--
-- Irreversible: afterwards a backfilled row is indistinguishable from one the
-- runtime wrote. See 047_p26_intelligence_agency_backfill_down.sql.

-- ---------------------------------------------------------------------------
-- Guard 1 - watches with no derivable agency.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    orphan_count BIGINT;
    sample_ids   TEXT;
BEGIN
    SELECT count(*) INTO orphan_count
      FROM property_watches w
     WHERE w.agency_id IS NULL
       AND NOT EXISTS (
           SELECT 1 FROM stime s
            WHERE s.id = w.stima_id AND s.agency_id IS NOT NULL
       );

    IF orphan_count <> 0 THEN
        SELECT string_agg(t.id::text, ', ') INTO sample_ids
          FROM (
                SELECT w.id
                  FROM property_watches w
                 WHERE w.agency_id IS NULL
                   AND NOT EXISTS (
                       SELECT 1 FROM stime s
                        WHERE s.id = w.stima_id AND s.agency_id IS NOT NULL
                   )
               ) AS t;

        RAISE EXCEPTION
            'P26-6B 047 refused: % property watch(es) have no estimation from which an agency can be derived (id: %). stima_id is ON DELETE SET NULL, so these watches have outlived their estimation and must be resolved by hand. No row was modified.',
            orphan_count, sample_ids;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- Guard 2 - next best actions with no derivable agency.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    orphan_count BIGINT;
    sample_ids   TEXT;
BEGIN
    SELECT count(*) INTO orphan_count
      FROM next_best_actions n
     WHERE n.agency_id IS NULL
       AND NOT EXISTS (
           SELECT c.agency_id FROM contacts c WHERE c.id = n.contact_id AND c.agency_id IS NOT NULL
           UNION ALL
           SELECT l.agency_id FROM leads l WHERE l.id = n.lead_id AND l.agency_id IS NOT NULL
           UNION ALL
           SELECT s.agency_id FROM stime s WHERE s.id = n.stima_id AND s.agency_id IS NOT NULL
           UNION ALL
           SELECT sl.agency_id FROM leads sl
            WHERE n.subject_type = 'lead' AND sl.id = n.subject_id AND sl.agency_id IS NOT NULL
           UNION ALL
           SELECT sb.agency_id FROM buy_requests sb
            WHERE n.subject_type = 'buy_request' AND sb.id = n.subject_id AND sb.agency_id IS NOT NULL
           UNION ALL
           SELECT ss.agency_id FROM stime ss
            WHERE n.subject_type = 'stima' AND ss.id = n.subject_id AND ss.agency_id IS NOT NULL
           UNION ALL
           SELECT mb.agency_id FROM matches m
             JOIN buy_requests mb ON mb.id = m.buy_request_id
            WHERE n.subject_type = 'match' AND m.id = n.subject_id AND mb.agency_id IS NOT NULL
       );

    IF orphan_count <> 0 THEN
        SELECT string_agg(t.id::text, ', ') INTO sample_ids
          FROM (
                SELECT n.id
                  FROM next_best_actions n
                 WHERE n.agency_id IS NULL
                   AND NOT EXISTS (
                       SELECT c.agency_id FROM contacts c WHERE c.id = n.contact_id AND c.agency_id IS NOT NULL
                       UNION ALL
                       SELECT l.agency_id FROM leads l WHERE l.id = n.lead_id AND l.agency_id IS NOT NULL
                       UNION ALL
                       SELECT s.agency_id FROM stime s WHERE s.id = n.stima_id AND s.agency_id IS NOT NULL
                       UNION ALL
                       SELECT sl.agency_id FROM leads sl
                        WHERE n.subject_type = 'lead' AND sl.id = n.subject_id AND sl.agency_id IS NOT NULL
                       UNION ALL
                       SELECT sb.agency_id FROM buy_requests sb
                        WHERE n.subject_type = 'buy_request' AND sb.id = n.subject_id AND sb.agency_id IS NOT NULL
                       UNION ALL
                       SELECT ss.agency_id FROM stime ss
                        WHERE n.subject_type = 'stima' AND ss.id = n.subject_id AND ss.agency_id IS NOT NULL
                       UNION ALL
                       SELECT mb.agency_id FROM matches m
                         JOIN buy_requests mb ON mb.id = m.buy_request_id
                        WHERE n.subject_type = 'match' AND m.id = n.subject_id AND mb.agency_id IS NOT NULL
                   )
               ) AS t;

        RAISE EXCEPTION
            'P26-6B 047 refused: % next best action(s) have no reference and no resolvable subject from which an agency can be derived (id: %). An unrecognised subject_type surfaces here rather than being assigned. No row was modified.',
            orphan_count, sample_ids;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- Guard 3 - next best actions whose sources disagree.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    ambiguous_count BIGINT;
    sample_ids      TEXT;
BEGIN
    SELECT count(*) INTO ambiguous_count
      FROM (
            SELECT n.id
              FROM next_best_actions n
              JOIN LATERAL (
                    SELECT c.agency_id FROM contacts c WHERE c.id = n.contact_id
                    UNION
                    SELECT l.agency_id FROM leads l WHERE l.id = n.lead_id
                    UNION
                    SELECT s.agency_id FROM stime s WHERE s.id = n.stima_id
                    UNION
                    SELECT sl.agency_id FROM leads sl
                     WHERE n.subject_type = 'lead' AND sl.id = n.subject_id
                    UNION
                    SELECT sb.agency_id FROM buy_requests sb
                     WHERE n.subject_type = 'buy_request' AND sb.id = n.subject_id
                    UNION
                    SELECT ss.agency_id FROM stime ss
                     WHERE n.subject_type = 'stima' AND ss.id = n.subject_id
                    UNION
                    SELECT mb.agency_id FROM matches m
                      JOIN buy_requests mb ON mb.id = m.buy_request_id
                     WHERE n.subject_type = 'match' AND m.id = n.subject_id
                   ) AS candidates(agency_id) ON TRUE
             WHERE n.agency_id IS NULL
               AND candidates.agency_id IS NOT NULL
             GROUP BY n.id
            HAVING count(DISTINCT candidates.agency_id) > 1
           ) AS ambiguous;

    IF ambiguous_count <> 0 THEN
        SELECT string_agg(t.id::text, ', ') INTO sample_ids
          FROM (
                SELECT n.id
                  FROM next_best_actions n
                  JOIN LATERAL (
                        SELECT c.agency_id FROM contacts c WHERE c.id = n.contact_id
                        UNION
                        SELECT l.agency_id FROM leads l WHERE l.id = n.lead_id
                        UNION
                        SELECT s.agency_id FROM stime s WHERE s.id = n.stima_id
                        UNION
                        SELECT sl.agency_id FROM leads sl
                         WHERE n.subject_type = 'lead' AND sl.id = n.subject_id
                        UNION
                        SELECT sb.agency_id FROM buy_requests sb
                         WHERE n.subject_type = 'buy_request' AND sb.id = n.subject_id
                        UNION
                        SELECT ss.agency_id FROM stime ss
                         WHERE n.subject_type = 'stima' AND ss.id = n.subject_id
                        UNION
                        SELECT mb.agency_id FROM matches m
                          JOIN buy_requests mb ON mb.id = m.buy_request_id
                         WHERE n.subject_type = 'match' AND m.id = n.subject_id
                       ) AS candidates(agency_id) ON TRUE
                 WHERE n.agency_id IS NULL
                   AND candidates.agency_id IS NOT NULL
                 GROUP BY n.id
                HAVING count(DISTINCT candidates.agency_id) > 1
               ) AS t;

        RAISE EXCEPTION
            'P26-6B 047 refused: % next best action(s) resolve to more than one agency across their references and subject (id: %). Ambiguity is a refusal, not an input to a heuristic. No row was modified.',
            ambiguous_count, sample_ids;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- Guard 4 - a watch whose stima disagrees with an agency already recorded.
--
-- Rows that already carry an agency are never overwritten by the backfill
-- below, so a contradiction between the two would survive into 048's
-- constraints. It is caught here instead.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    conflicting BIGINT;
BEGIN
    SELECT count(*) INTO conflicting
      FROM property_watches w
      JOIN stime s ON s.id = w.stima_id
     WHERE w.agency_id IS NOT NULL
       AND s.agency_id IS NOT NULL
       AND w.agency_id <> s.agency_id;

    IF conflicting <> 0 THEN
        RAISE EXCEPTION
            'P26-6B 047 refused: % property watch(es) already carry an agency that contradicts their estimation. No row was modified.',
            conflicting;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- The backfill.
--
-- `agency_id IS NULL` keeps an already-owned row untouched and makes a repeat
-- execution a zero-row no-op - which matters if the runner dies between this
-- body and register().
-- ---------------------------------------------------------------------------
UPDATE property_watches w
   SET agency_id = s.agency_id
  FROM stime s
 WHERE s.id = w.stima_id
   AND s.agency_id IS NOT NULL
   AND w.agency_id IS NULL;

UPDATE next_best_actions n
   SET agency_id = derived.agency_id
  FROM (
        SELECT DISTINCT n2.id AS row_id, candidates.agency_id
          FROM next_best_actions n2
          JOIN LATERAL (
                SELECT c.agency_id FROM contacts c WHERE c.id = n2.contact_id
                UNION
                SELECT l.agency_id FROM leads l WHERE l.id = n2.lead_id
                UNION
                SELECT s.agency_id FROM stime s WHERE s.id = n2.stima_id
                UNION
                SELECT sl.agency_id FROM leads sl
                 WHERE n2.subject_type = 'lead' AND sl.id = n2.subject_id
                UNION
                SELECT sb.agency_id FROM buy_requests sb
                 WHERE n2.subject_type = 'buy_request' AND sb.id = n2.subject_id
                UNION
                SELECT ss.agency_id FROM stime ss
                 WHERE n2.subject_type = 'stima' AND ss.id = n2.subject_id
                UNION
                SELECT mb.agency_id FROM matches m
                  JOIN buy_requests mb ON mb.id = m.buy_request_id
                 WHERE n2.subject_type = 'match' AND m.id = n2.subject_id
               ) AS candidates(agency_id) ON TRUE
         WHERE candidates.agency_id IS NOT NULL
       ) AS derived
 WHERE n.id = derived.row_id
   AND n.agency_id IS NULL;

-- ---------------------------------------------------------------------------
-- Guard 5 - 047 certifies its own post-condition.
--
-- 048's SET NOT NULL is a second, independent gate, not a substitute: finding
-- an incomplete backfill there would mean two migrations to unpick instead of
-- one refused transaction.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    unowned_watches BIGINT;
    unowned_actions BIGINT;
BEGIN
    SELECT count(*) INTO unowned_watches FROM property_watches  WHERE agency_id IS NULL;
    SELECT count(*) INTO unowned_actions FROM next_best_actions WHERE agency_id IS NULL;

    IF unowned_watches <> 0 OR unowned_actions <> 0 THEN
        RAISE EXCEPTION
            'P26-6B 047 incomplete: % property watch(es) and % next best action(s) still carry no agency. The transaction is rolled back; no partial backfill is committed.',
            unowned_watches, unowned_actions;
    END IF;
END
$do$;
