-- P26-2C PROPERTY root ownership, part 2 of 3: controlled historical backfill.
--
-- Assigns an agency to properties that predate the tenant-aware writer.
-- Data only: no structure, no constraint, no index, no trigger, no function.
--
--   034  nullable structure         (applied)
--   035  controlled backfill        <- this file
--   ---  NOT NULL / integrity       (a later block)
--
-- properties.agency_id remains nullable after this migration completes.
--
-- WHERE THE AGENCY COMES FROM
--
-- Provenance:
--
--   1. property_contacts -> contacts.agency_id
--   2. property_leads    -> leads.agency_id
--
-- Every property with contacts or leads resolves candidate agencies from those
-- relationships. If exactly one distinct agency is reachable, that is the
-- property's owner.
--
-- If more than one distinct agency is reachable, the migration HARD FAILS:
-- ambiguity cannot be resolved by heuristics.
--
-- There is NO use of MIN, MAX, LIMIT 1, COALESCE, arbitrary ordering or
-- hardcoded IDs.
--
-- FALLBACK FOR HISTORICAL PROPERTIES WITHOUT PROVENANCE
--
-- For properties with zero contacts and zero leads, fallback to the Default
-- Agency (resolved by slug 'stima360' AND status 'active') is allowed ONLY if
-- no non-default agency was registered at or before the property's creation:
--
--     NOT EXISTS (
--         SELECT 1 FROM agencies a
--          WHERE a.slug <> 'stima360'
--            AND a.created_at <= properties.created_at
--     )
--
-- If a non-default agency already existed when the property was created, the
-- migration HARD FAILS because attribution to the Default Agency cannot be
-- made with certainty.
--
-- ROWS THAT ALREADY HAVE AN AGENCY ARE NEVER OVERWRITTEN
--
-- The UPDATE statements carry `agency_id IS NULL`, so any row already stamped
-- is untouched. If a prefilled row contradicts recorded provenance, the
-- migration HARD FAILS before writing.
--
-- Transaction ownership: this file opens no transaction and commits nothing.
-- scripts/p26_migrate.py executes this body, writes the schema_migrations row
-- through register(), and commits both together, rolling back on any error.
--
-- Irreversible: see 035_p26_property_agency_backfill_down.sql.

-- ---------------------------------------------------------------------------
-- Pre-guard 0 - Record initial properties row count.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_before BIGINT;
BEGIN
    SELECT count(*) INTO v_before FROM properties;
    PERFORM set_config('p26.properties_before', v_before::text, true);
END
$do$;

-- ---------------------------------------------------------------------------
-- Guard 1 - The Default Agency must resolve to exactly one active row.
--
-- Resolved by slug ('stima360') and status ('active'), never hardcoded.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_default_count INTEGER;
BEGIN
    SELECT count(*) INTO v_default_count
      FROM agencies
     WHERE slug = 'stima360' AND status = 'active';

    IF v_default_count <> 1 THEN
        RAISE EXCEPTION
            'P26-2C 035 refused: Default Agency (slug ''stima360'', status ''active'') '
            'resolved to % rows, expected exactly 1. No row was modified.',
            v_default_count;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- Guard 2 - A linked contact or lead with no agency of its own.
--
-- A contact or lead that carries agency_id IS NULL cannot confer an agency.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_unowned_links BIGINT;
BEGIN
    SELECT count(*) INTO v_unowned_links
      FROM (
          SELECT pc.property_id
            FROM property_contacts pc
            JOIN contacts c ON c.id = pc.contact_id
            JOIN properties p ON p.id = pc.property_id
           WHERE p.agency_id IS NULL AND c.agency_id IS NULL
          UNION ALL
          SELECT pl.property_id
            FROM property_leads pl
            JOIN leads l ON l.id = pl.lead_id
            JOIN properties p ON p.id = pl.property_id
           WHERE p.agency_id IS NULL AND l.agency_id IS NULL
      ) unowned;

    IF v_unowned_links <> 0 THEN
        RAISE EXCEPTION
            'P26-2C 035 refused: % linked contact/lead record(s) carry no agency_id. '
            'Apply CORE backfill (029) first. No row was modified.',
            v_unowned_links;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- Guard 3 - Ambiguous provenance (>1 distinct candidate agencies).
--
-- If a property resolves to more than one candidate agency from its contacts
-- and leads, the migration must hard-fail. Ambiguity is a refusal.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_ambiguous_count BIGINT;
    v_sample_ids      TEXT;
BEGIN
    SELECT count(*), string_agg(property_id::text, ', ')
      INTO v_ambiguous_count, v_sample_ids
      FROM (
          SELECT property_id
            FROM (
                SELECT pc.property_id, c.agency_id
                  FROM property_contacts pc
                  JOIN contacts c ON c.id = pc.contact_id
                 WHERE c.agency_id IS NOT NULL
                UNION
                SELECT pl.property_id, l.agency_id
                  FROM property_leads pl
                  JOIN leads l ON l.id = pl.lead_id
                 WHERE l.agency_id IS NOT NULL
            ) candidates
           GROUP BY property_id
          HAVING count(DISTINCT agency_id) > 1
      ) ambiguous;

    IF v_ambiguous_count <> 0 THEN
        RAISE EXCEPTION
            'P26-2C 035 refused: % properties resolve to more than one candidate agency '
            '(properties.id: %). Ambiguity is a refusal. No row was modified.',
            v_ambiguous_count, v_sample_ids;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- Guard 4 - Prefilled property contradicting recorded provenance.
--
-- If a property already has an agency_id, and its provenance derives an
-- agency that differs from it, this is a pre-existing integrity violation.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_conflicting_prefilled BIGINT;
    v_sample_ids            TEXT;
BEGIN
    SELECT count(*), string_agg(DISTINCT property_id::text, ', ')
      INTO v_conflicting_prefilled, v_sample_ids
      FROM (
          SELECT pc.property_id, c.agency_id
            FROM property_contacts pc
            JOIN contacts c ON c.id = pc.contact_id
           WHERE c.agency_id IS NOT NULL
          UNION
          SELECT pl.property_id, l.agency_id
            FROM property_leads pl
            JOIN leads l ON l.id = pl.lead_id
           WHERE l.agency_id IS NOT NULL
      ) candidates
      JOIN properties p ON p.id = candidates.property_id
     WHERE p.agency_id IS NOT NULL
       AND p.agency_id <> candidates.agency_id;

    IF v_conflicting_prefilled <> 0 THEN
        RAISE EXCEPTION
            'P26-2C 035 refused: % prefilled property(ies) contradict recorded provenance '
            '(properties.id: %). Reconcile these rows first. No row was modified.',
            v_conflicting_prefilled, v_sample_ids;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- Guard 5 - Fallback safety check for properties without provenance.
--
-- A property without contacts or leads may only fall back to the Default Agency
-- if NO non-default agency existed at or before properties.created_at.
-- If any non-default agency existed (agencies.created_at <= properties.created_at),
-- we cannot attribute the property to Default Agency with certainty: hard fail.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_default_agency_id     BIGINT;
    v_unresolvable_fallback BIGINT;
    v_sample_ids            TEXT;
BEGIN
    SELECT id INTO v_default_agency_id
      FROM agencies
     WHERE slug = 'stima360' AND status = 'active';

    SELECT count(*), string_agg(p.id::text, ', ')
      INTO v_unresolvable_fallback, v_sample_ids
      FROM properties p
     WHERE p.agency_id IS NULL
       AND NOT EXISTS (
           SELECT 1 FROM property_contacts pc
           JOIN contacts c ON c.id = pc.contact_id
           WHERE pc.property_id = p.id AND c.agency_id IS NOT NULL
       )
       AND NOT EXISTS (
           SELECT 1 FROM property_leads pl
           JOIN leads l ON l.id = pl.lead_id
           WHERE pl.property_id = p.id AND l.agency_id IS NOT NULL
       )
       AND EXISTS (
           SELECT 1 FROM agencies a
            WHERE a.id <> v_default_agency_id
              AND a.created_at <= p.created_at
       );

    IF v_unresolvable_fallback <> 0 THEN
        RAISE EXCEPTION
            'P26-2C 035 refused: % unowned property(ies) with no provenance were created after a '
            'non-default agency was registered (properties.id: %). Cannot safely fallback to Default Agency. '
            'No row was modified.',
            v_unresolvable_fallback, v_sample_ids;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- 1. Backfill properties with recorded provenance.
--
-- Resolves candidate agency from property_contacts and property_leads.
-- Guards 2-3 guaranteed exactly one candidate agency per property here.
-- WHERE p.agency_id IS NULL ensures existing agencies are never overwritten.
-- ---------------------------------------------------------------------------
UPDATE properties p
   SET agency_id = prov.agency_id
  FROM (
      SELECT property_id, agency_id
        FROM (
            SELECT pc.property_id, c.agency_id
              FROM property_contacts pc
              JOIN contacts c ON c.id = pc.contact_id
             WHERE c.agency_id IS NOT NULL
            UNION
            SELECT pl.property_id, l.agency_id
              FROM property_leads pl
              JOIN leads l ON l.id = pl.lead_id
             WHERE l.agency_id IS NOT NULL
        ) candidates
  ) prov
 WHERE p.id = prov.property_id
   AND p.agency_id IS NULL;

-- ---------------------------------------------------------------------------
-- 2. Backfill properties without provenance via Default Agency fallback.
--
-- Guard 5 verified that every such property was created before any non-default
-- agency was registered (agencies.created_at <= properties.created_at).
-- Resolves the Default Agency by slug 'stima360' and status 'active'.
-- WHERE p.agency_id IS NULL ensures only unassigned rows are touched.
-- ---------------------------------------------------------------------------
UPDATE properties p
   SET agency_id = (
       SELECT id
         FROM agencies
        WHERE slug = 'stima360' AND status = 'active'
   )
 WHERE p.agency_id IS NULL
   AND NOT EXISTS (
       SELECT 1 FROM property_contacts pc
       JOIN contacts c ON c.id = pc.contact_id
       WHERE pc.property_id = p.id AND c.agency_id IS NOT NULL
   )
   AND NOT EXISTS (
       SELECT 1 FROM property_leads pl
       JOIN leads l ON l.id = pl.lead_id
       WHERE pl.property_id = p.id AND l.agency_id IS NOT NULL
   )
   AND NOT EXISTS (
       SELECT 1 FROM agencies a
        WHERE a.slug <> 'stima360'
          AND a.created_at <= p.created_at
   );

-- ---------------------------------------------------------------------------
-- Post-checks: 035 certifies its own post-conditions.
--
-- 1. Number of properties rows is unchanged
-- 2. properties.agency_id IS NULL = 0 (every property has an agency)
-- 3. Provenance conflicts = 0 (no multi-agency ambiguity)
-- 4. No property has an agency different from its certain provenance
-- 5. No arbitrary tenant choice
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_before              BIGINT;
    v_after               BIGINT;
    v_null_agency_count   BIGINT;
    v_provenance_mismatch BIGINT;
    v_ambiguity_count     BIGINT;
BEGIN
    -- 1. Row count verification
    v_before := current_setting('p26.properties_before')::bigint;
    SELECT count(*) INTO v_after FROM properties;
    IF v_before <> v_after THEN
        RAISE EXCEPTION
            'P26-2C 035 post-check failed: properties row count changed from % to %. '
            'Transaction rolled back.',
            v_before, v_after;
    END IF;

    -- 2. properties.agency_id IS NULL = 0
    SELECT count(*) INTO v_null_agency_count
      FROM properties
     WHERE agency_id IS NULL;

    IF v_null_agency_count <> 0 THEN
        RAISE EXCEPTION
            'P26-2C 035 post-check failed: % property(ies) still have agency_id IS NULL. '
            'Transaction rolled back.',
            v_null_agency_count;
    END IF;

    -- 3 & 4. No property with agency different from its certain recorded provenance
    SELECT count(*) INTO v_provenance_mismatch
      FROM (
          SELECT pc.property_id, c.agency_id
            FROM property_contacts pc
            JOIN contacts c ON c.id = pc.contact_id
           WHERE c.agency_id IS NOT NULL
          UNION
          SELECT pl.property_id, l.agency_id
            FROM property_leads pl
            JOIN leads l ON l.id = pl.lead_id
           WHERE l.agency_id IS NOT NULL
      ) candidates
      JOIN properties p ON p.id = candidates.property_id
     WHERE p.agency_id <> candidates.agency_id;

    IF v_provenance_mismatch <> 0 THEN
        RAISE EXCEPTION
            'P26-2C 035 post-check failed: % property(ies) carry an agency different from '
            'recorded provenance. Transaction rolled back.',
            v_provenance_mismatch;
    END IF;

    -- 5. No arbitrary multi-agency ambiguity
    SELECT count(*) INTO v_ambiguity_count
      FROM (
          SELECT property_id
            FROM (
                SELECT pc.property_id, c.agency_id
                  FROM property_contacts pc
                  JOIN contacts c ON c.id = pc.contact_id
                 WHERE c.agency_id IS NOT NULL
                UNION
                SELECT pl.property_id, l.agency_id
                  FROM property_leads pl
                  JOIN leads l ON l.id = pl.lead_id
                 WHERE l.agency_id IS NOT NULL
            ) candidates
           GROUP BY property_id
          HAVING count(DISTINCT agency_id) > 1
      ) ambiguous;

    IF v_ambiguity_count <> 0 THEN
        RAISE EXCEPTION
            'P26-2C 035 post-check failed: % properties have ambiguous provenance. '
            'Transaction rolled back.',
            v_ambiguity_count;
    END IF;
END
$do$;
