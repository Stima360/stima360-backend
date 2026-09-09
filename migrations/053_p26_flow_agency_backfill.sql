-- P26-6C FLOW, part 2 of 3: the controlled backfill.
--
--   052  nullable structure       (applied)
--   053  controlled backfill      <- this file
--   054  NOT NULL and integrity
--
-- ONE RESOLUTION, SIX ENTITY TYPES
--
-- All three tables carry the same polymorphic reference - entity_type plus
-- entity_id, with no foreign key - so all three resolve the same way, and the
-- resolution is written once as a helper the three passes share. Each of the
-- six entity types FLOW's rules can name reaches a tenant by the route its own
-- module already certified:
--
--   lead            -> leads.agency_id                            (P26-1)
--   property        -> properties.agency_id                       (P26-2)
--   buy_request     -> buy_requests.agency_id                     (P26-3)
--   match           -> through its buy request                    (P26-4)
--                      Either side answers: 040/041 guarantee both roots of a
--                      match share one agency. The buy request is read because
--                      it is the side FLOW's own scan already joins.
--   property_visit  -> through its property; property_id is NOT NULL
--   owner_feedback  -> owner_account -> contact.agency_id
--                      owner_accounts.contact_id is NOT NULL UNIQUE with
--                      ON DELETE RESTRICT, so the chain cannot be broken. This
--                      is a read of OWNER's shape, not a migration of OWNER.
--
-- Any other entity_type resolves nothing and is refused rather than silently
-- assigned - which is how an unrecognised type surfaces instead of being
-- papered over.
--
-- THE ORDER MATTERS, AND IT IS THE PROVENANCE THE BRIEF ASKS FOR
--
-- Events are assigned first, from their entity. Executions are assigned second
-- and may use TWO sources - their own entity AND the event they came from -
-- because an execution's entity may since have been archived while the event
-- that produced it still carries the answer. Suppressions are assigned last,
-- from their entity alone; they have no event.
--
-- `retry_of_execution_id` is deliberately NOT a source. It points at another
-- row of the same table being written in the same statement, so using it would
-- make the result depend on evaluation order. It is checked afterwards, in
-- guard 5, where it can only agree or fail.
--
-- THE RULE
--
--   exactly one distinct agency across all available sources -> assign it
--   more than one (the sources disagree)                     -> REFUSE
--   none derivable                                           -> REFUSE
--
-- No Default Agency, no MIN, no MAX, no LIMIT 1, no COALESCE across
-- candidates, no hardcoded id. Each of those turns "undecidable" into "here is
-- an answer", which for a tenancy is the one conversion never to make: a wrong
-- owner does not fail, it produces a row that looks correct in the wrong
-- tenant.
--
-- Transaction ownership: the runner owns the UP transaction, so this file
-- carries no BEGIN/COMMIT. Idempotent by effect: each UPDATE touches only rows
-- whose agency_id IS NULL.

-- WHY EVERY CALL OF THE HELPER IS ALIASED
--
-- `p26_6c_flow_entity_agency` returns SETOF *bigint* - a base type, not a
-- composite - so the FROM item it produces exposes exactly one column, and
-- PostgreSQL names that column after the FUNCTION, not after whatever the
-- function body happened to select. The body's `agency_id` is invisible to the
-- caller.
--
-- So this, which is what the first version of this file wrote:
--
--     UPDATE flow_events e
--        SET agency_id = (SELECT agency_id FROM p26_6c_flow_entity_agency(...))
--
-- does NOT read the function's output. The unqualified `agency_id` finds no
-- match in the subquery's own range table, name resolution walks outward, and
-- it binds to `e.agency_id` - the very column being written. It is a legal
-- correlated outer reference, so it parses, plans and runs without a warning,
-- and it assigns the column to itself: NULL.
--
-- The trap is that 052 creates that outer column immediately before this file
-- runs. Without it these statements would have failed at parse time with
-- `column "agency_id" does not exist`; with it they silently become no-ops.
-- The staging that makes the migration safe is exactly what hid the defect.
--
-- What it cost, on TEST: PASS 1 wrote NULL into every flow_events row while
-- reporting success. PASS 2's ambiguity guard then saw one NULL candidate per
-- execution (its entity branch had the same bug, and its event branch was
-- filtered out by `e.agency_id IS NOT NULL`, which PASS 1 had just made false
-- everywhere). COUNT(DISTINCT) of a single NULL is 0, `0 <> 1` is true, and
-- all 3493 executions were reported as having "sources that disagree" - a
-- number that is simply the row count of the table, not a count of conflicts.
-- A read-only diagnostic run straight afterwards found 0 real disagreements.
--
-- Every call site below therefore names its output column explicitly:
--
--     ... FROM p26_6c_flow_entity_agency(...) AS resolved(agency_id)
--
-- and every reference to it is qualified. The sites that project no column at
-- all (`SELECT 1 FROM ...`, used by the three "unresolved" guards) need no
-- alias, because they never name the column and so cannot capture an outer
-- one. tests/test_p26_6c_flow_isolation.py enforces both halves of that rule.

-- ---------------------------------------------------------------------------
-- The shared resolution. Created for this migration and dropped at the end, so
-- it cannot be mistaken for runtime API.
--
-- Returns SETOF: zero rows when the entity does not resolve, which is what
-- lets the guards below distinguish "no source" from "a source that says
-- NULL".
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION p26_6c_flow_entity_agency(
    p_entity_type text, p_entity_id bigint
) RETURNS SETOF bigint
LANGUAGE sql
STABLE
AS $fn$
    SELECT agency_id FROM leads
     WHERE p_entity_type = 'lead' AND id = p_entity_id
    UNION ALL
    SELECT agency_id FROM properties
     WHERE p_entity_type = 'property' AND id = p_entity_id
    UNION ALL
    SELECT agency_id FROM buy_requests
     WHERE p_entity_type = 'buy_request' AND id = p_entity_id
    UNION ALL
    SELECT b.agency_id
      FROM matches m JOIN buy_requests b ON b.id = m.buy_request_id
     WHERE p_entity_type = 'match' AND m.id = p_entity_id
    UNION ALL
    SELECT p.agency_id
      FROM property_visits v JOIN properties p ON p.id = v.property_id
     WHERE p_entity_type = 'property_visit' AND v.id = p_entity_id
    UNION ALL
    SELECT c.agency_id
      FROM owner_feedback f
      JOIN owner_accounts oa ON oa.id = f.owner_account_id
      JOIN contacts c ON c.id = oa.contact_id
     WHERE p_entity_type = 'owner_feedback' AND f.id = p_entity_id
$fn$;

-- ===========================================================================
-- PASS 1 - flow_events
-- ===========================================================================

DO $do$
DECLARE
    v_unresolved bigint;
    v_sample     text;
BEGIN
    SELECT COUNT(*), COALESCE(string_agg(sample, ', '), '')
      INTO v_unresolved, v_sample
      FROM (
          SELECT e.entity_type || ':' || e.entity_id AS sample
            FROM flow_events e
           WHERE NOT EXISTS (
                 SELECT 1 FROM p26_6c_flow_entity_agency(e.entity_type, e.entity_id))
           ORDER BY 1
           FETCH FIRST 20 ROWS ONLY
      ) s;

    IF v_unresolved > 0 THEN
        RAISE EXCEPTION
            'P26-6C 053: % flow_events row(s) reference an entity with no derivable agency (first: %). Refusing rather than guessing an owner.',
            v_unresolved, v_sample;
    END IF;
END
$do$;

DO $do$
DECLARE
    v_ambiguous bigint;
BEGIN
    SELECT COUNT(*) INTO v_ambiguous
      FROM (
          SELECT e.id
            FROM flow_events e,
                 LATERAL p26_6c_flow_entity_agency(e.entity_type, e.entity_id) AS a(agency_id)
           GROUP BY e.id
          HAVING COUNT(DISTINCT a.agency_id) <> 1
      ) x;

    IF v_ambiguous > 0 THEN
        RAISE EXCEPTION
            'P26-6C 053: % flow_events row(s) resolve to more than one distinct agency. Refusing rather than choosing.',
            v_ambiguous;
    END IF;
END
$do$;

UPDATE flow_events e
   SET agency_id = (
       SELECT resolved.agency_id
         FROM p26_6c_flow_entity_agency(e.entity_type, e.entity_id)
              AS resolved(agency_id)
   )
 WHERE e.agency_id IS NULL;

-- ===========================================================================
-- PASS 2 - flow_executions
--
-- Two sources: the execution's own entity, and the event it came from (now
-- assigned by pass 1). They must agree.
-- ===========================================================================

DO $do$
DECLARE
    v_unresolved bigint;
    v_sample     text;
BEGIN
    SELECT COUNT(*), COALESCE(string_agg(sample, ', '), '')
      INTO v_unresolved, v_sample
      FROM (
          SELECT x.id || ' (' || x.entity_type || ':' || x.entity_id || ')' AS sample
            FROM flow_executions x
           WHERE NOT EXISTS (
                 SELECT 1 FROM p26_6c_flow_entity_agency(x.entity_type, x.entity_id))
             AND NOT EXISTS (
                 SELECT 1 FROM flow_events e
                  WHERE e.id = x.event_id AND e.agency_id IS NOT NULL)
           ORDER BY 1
           FETCH FIRST 20 ROWS ONLY
      ) s;

    IF v_unresolved > 0 THEN
        RAISE EXCEPTION
            'P26-6C 053: % flow_executions row(s) have neither a resolvable entity nor an assigned parent event (first: %). Refusing rather than guessing an owner.',
            v_unresolved, v_sample;
    END IF;
END
$do$;

DO $do$
DECLARE
    v_ambiguous bigint;
BEGIN
    SELECT COUNT(*) INTO v_ambiguous
      FROM (
          SELECT x.id
            FROM flow_executions x,
                 LATERAL (
                     SELECT resolved.agency_id
                       FROM p26_6c_flow_entity_agency(x.entity_type, x.entity_id)
                            AS resolved(agency_id)
                     UNION ALL
                     SELECT e.agency_id
                       FROM flow_events e
                      WHERE e.id = x.event_id AND e.agency_id IS NOT NULL
                 ) AS a(agency_id)
           GROUP BY x.id
          HAVING COUNT(DISTINCT a.agency_id) <> 1
      ) y;

    IF v_ambiguous > 0 THEN
        RAISE EXCEPTION
            'P26-6C 053: % flow_executions row(s) have sources that disagree about the agency (entity vs parent event). Refusing rather than choosing.',
            v_ambiguous;
    END IF;
END
$do$;

UPDATE flow_executions x
   SET agency_id = (
       SELECT DISTINCT candidates.agency_id
         FROM (
             SELECT resolved.agency_id
               FROM p26_6c_flow_entity_agency(x.entity_type, x.entity_id)
                    AS resolved(agency_id)
             UNION ALL
             SELECT e.agency_id
               FROM flow_events e
              WHERE e.id = x.event_id AND e.agency_id IS NOT NULL
         ) AS candidates(agency_id)
   )
 WHERE x.agency_id IS NULL;

-- ===========================================================================
-- PASS 3 - flow_suppressions
-- ===========================================================================

DO $do$
DECLARE
    v_unresolved bigint;
    v_sample     text;
BEGIN
    SELECT COUNT(*), COALESCE(string_agg(sample, ', '), '')
      INTO v_unresolved, v_sample
      FROM (
          SELECT s.entity_type || ':' || s.entity_id AS sample
            FROM flow_suppressions s
           WHERE NOT EXISTS (
                 SELECT 1 FROM p26_6c_flow_entity_agency(s.entity_type, s.entity_id))
           ORDER BY 1
           FETCH FIRST 20 ROWS ONLY
      ) t;

    IF v_unresolved > 0 THEN
        RAISE EXCEPTION
            'P26-6C 053: % flow_suppressions row(s) reference an entity with no derivable agency (first: %). Refusing rather than guessing an owner.',
            v_unresolved, v_sample;
    END IF;
END
$do$;

DO $do$
DECLARE
    v_ambiguous bigint;
BEGIN
    SELECT COUNT(*) INTO v_ambiguous
      FROM (
          SELECT s.id
            FROM flow_suppressions s,
                 LATERAL p26_6c_flow_entity_agency(s.entity_type, s.entity_id) AS a(agency_id)
           GROUP BY s.id
          HAVING COUNT(DISTINCT a.agency_id) <> 1
      ) x;

    IF v_ambiguous > 0 THEN
        RAISE EXCEPTION
            'P26-6C 053: % flow_suppressions row(s) resolve to more than one distinct agency. Refusing rather than choosing.',
            v_ambiguous;
    END IF;
END
$do$;

UPDATE flow_suppressions s
   SET agency_id = (
       SELECT resolved.agency_id
         FROM p26_6c_flow_entity_agency(s.entity_type, s.entity_id)
              AS resolved(agency_id)
   )
 WHERE s.agency_id IS NULL;

-- ===========================================================================
-- Guards, after the writes.
-- ===========================================================================

-- Guard 4: nothing left behind, and nothing filed against the wrong tenant.
--
-- The mismatch half re-derives the answer independently of the UPDATE that
-- produced it. A backfill that verifies only "not null" proves it wrote
-- something, not that it wrote the right thing. Executions are compared only
-- where their entity still resolves - the ones that came from the event alone
-- are covered by guard 5.
DO $do$
DECLARE
    v_table      text;
    v_remaining  bigint;
    v_mismatched bigint;
BEGIN
    FOREACH v_table IN ARRAY ARRAY['flow_events','flow_executions','flow_suppressions']
    LOOP
        EXECUTE format('SELECT COUNT(*) FROM %I WHERE agency_id IS NULL', v_table)
           INTO v_remaining;
        IF v_remaining > 0 THEN
            RAISE EXCEPTION
                'P26-6C 053: % row(s) in % still have a NULL agency_id after the backfill',
                v_remaining, v_table;
        END IF;

        EXECUTE format(
            'SELECT COUNT(*) FROM %I t, '
            '     LATERAL p26_6c_flow_entity_agency(t.entity_type, t.entity_id) AS a(agency_id) '
            ' WHERE t.agency_id <> a.agency_id', v_table
        ) INTO v_mismatched;
        IF v_mismatched > 0 THEN
            RAISE EXCEPTION
                'P26-6C 053: % row(s) in % disagree with their entity''s agency after the backfill',
                v_mismatched, v_table;
        END IF;
    END LOOP;
END
$do$;

-- Guard 5: the two optional links agree.
--
-- An execution and the event it came from describe one occurrence; an
-- execution and the execution it retries are the same work run twice. Neither
-- may cross agencies, and 054 will enforce both from here on.
DO $do$
DECLARE
    v_split_event bigint;
    v_split_retry bigint;
BEGIN
    SELECT COUNT(*) INTO v_split_event
      FROM flow_executions x
      JOIN flow_events e ON e.id = x.event_id
     WHERE x.agency_id <> e.agency_id;

    IF v_split_event > 0 THEN
        RAISE EXCEPTION
            'P26-6C 053: % execution(s) disagree with the agency of the event they came from',
            v_split_event;
    END IF;

    SELECT COUNT(*) INTO v_split_retry
      FROM flow_executions x
      JOIN flow_executions o ON o.id = x.retry_of_execution_id
     WHERE x.agency_id <> o.agency_id;

    IF v_split_retry > 0 THEN
        RAISE EXCEPTION
            'P26-6C 053: % execution(s) disagree with the agency of the execution they retry',
            v_split_retry;
    END IF;
END
$do$;

DROP FUNCTION p26_6c_flow_entity_agency(text, bigint);
