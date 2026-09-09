-- P26-2D BUY root ownership, part 2 of 3: deterministic historical backfill.
-- Data-only. Provenance: buy_requests.contact_id -> contacts.agency_id.
-- A linked lead, when present, must agree. No fallback, no hardcoded agency.

DO $do$
DECLARE v_before BIGINT;
BEGIN
    SELECT count(*) INTO v_before FROM buy_requests;
    PERFORM set_config('p26.buy_requests_before', v_before::text, true);
END
$do$;

DO $do$
DECLARE v_bad BIGINT;
BEGIN
    SELECT count(*) INTO v_bad
      FROM buy_requests b
      LEFT JOIN contacts c ON c.id=b.contact_id
     WHERE c.id IS NULL OR c.agency_id IS NULL;
    IF v_bad <> 0 THEN
        RAISE EXCEPTION
            'P26-2D 038 refused: % BUY row(s) have missing/unowned contact provenance',
            v_bad;
    END IF;
END
$do$;

DO $do$
DECLARE v_bad BIGINT;
BEGIN
    SELECT count(*) INTO v_bad
      FROM buy_requests b
      LEFT JOIN leads l ON l.id=b.lead_id
     WHERE b.lead_id IS NOT NULL
       AND (l.id IS NULL OR l.agency_id IS NULL);
    IF v_bad <> 0 THEN
        RAISE EXCEPTION
            'P26-2D 038 refused: % BUY row(s) reference a missing/unowned lead',
            v_bad;
    END IF;
END
$do$;

DO $do$
DECLARE v_bad BIGINT;
BEGIN
    SELECT count(*) INTO v_bad
      FROM buy_requests b
      JOIN contacts c ON c.id=b.contact_id
      JOIN leads l ON l.id=b.lead_id
     WHERE b.lead_id IS NOT NULL
       AND c.agency_id <> l.agency_id;
    IF v_bad <> 0 THEN
        RAISE EXCEPTION
            'P26-2D 038 refused: % BUY row(s) have contact/lead agency conflict',
            v_bad;
    END IF;
END
$do$;

DO $do$
DECLARE v_bad BIGINT;
BEGIN
    SELECT count(*) INTO v_bad
      FROM buy_requests b
      JOIN contacts c ON c.id=b.contact_id
     WHERE b.agency_id IS NOT NULL
       AND b.agency_id <> c.agency_id;
    IF v_bad <> 0 THEN
        RAISE EXCEPTION
            'P26-2D 038 refused: % prefilled BUY row(s) contradict contact provenance',
            v_bad;
    END IF;
END
$do$;

UPDATE buy_requests b
   SET agency_id=c.agency_id
  FROM contacts c
 WHERE c.id=b.contact_id
   AND b.agency_id IS NULL;

DO $do$
DECLARE
    v_before BIGINT;
    v_after BIGINT;
    v_null BIGINT;
    v_contact_mismatch BIGINT;
    v_lead_mismatch BIGINT;
BEGIN
    v_before := current_setting('p26.buy_requests_before')::bigint;
    SELECT count(*) INTO v_after FROM buy_requests;
    IF v_before <> v_after THEN
        RAISE EXCEPTION
            'P26-2D 038 post-check: buy_requests count changed from % to %',
            v_before, v_after;
    END IF;

    SELECT count(*) INTO v_null
      FROM buy_requests WHERE agency_id IS NULL;
    IF v_null <> 0 THEN
        RAISE EXCEPTION
            'P26-2D 038 post-check: % BUY row(s) still have NULL agency_id',
            v_null;
    END IF;

    SELECT count(*) INTO v_contact_mismatch
      FROM buy_requests b
      JOIN contacts c ON c.id=b.contact_id
     WHERE b.agency_id <> c.agency_id;
    IF v_contact_mismatch <> 0 THEN
        RAISE EXCEPTION
            'P26-2D 038 post-check: % BUY/contact agency mismatch(es)',
            v_contact_mismatch;
    END IF;

    SELECT count(*) INTO v_lead_mismatch
      FROM buy_requests b
      JOIN leads l ON l.id=b.lead_id
     WHERE b.lead_id IS NOT NULL
       AND b.agency_id <> l.agency_id;
    IF v_lead_mismatch <> 0 THEN
        RAISE EXCEPTION
            'P26-2D 038 post-check: % BUY/lead agency mismatch(es)',
            v_lead_mismatch;
    END IF;
END
$do$;
