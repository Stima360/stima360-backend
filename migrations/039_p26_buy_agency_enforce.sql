-- P26-2D BUY root ownership, part 3 of 3: NOT NULL + DB integrity.
-- Runner-owned transaction: no BEGIN/COMMIT here.

DO $do$
DECLARE v_bad BIGINT;
BEGIN
    SELECT count(*) INTO v_bad FROM buy_requests WHERE agency_id IS NULL;
    IF v_bad <> 0 THEN
        RAISE EXCEPTION 'P26-2D 039 refused: % BUY row(s) have NULL agency_id', v_bad;
    END IF;
END
$do$;

DO $do$
DECLARE v_bad BIGINT;
BEGIN
    SELECT count(*) INTO v_bad
      FROM buy_requests b JOIN contacts c ON c.id=b.contact_id
     WHERE b.agency_id <> c.agency_id;
    IF v_bad <> 0 THEN
        RAISE EXCEPTION 'P26-2D 039 refused: % BUY/contact mismatch(es)', v_bad;
    END IF;

    SELECT count(*) INTO v_bad
      FROM buy_requests b JOIN leads l ON l.id=b.lead_id
     WHERE b.lead_id IS NOT NULL AND b.agency_id <> l.agency_id;
    IF v_bad <> 0 THEN
        RAISE EXCEPTION 'P26-2D 039 refused: % BUY/lead mismatch(es)', v_bad;
    END IF;
END
$do$;

DO $do$
DECLARE v_bad BIGINT;
BEGIN
    SELECT count(*) INTO v_bad
      FROM buy_request_interactions i
      JOIN buy_requests b ON b.id=i.buy_request_id
      JOIN properties p ON p.id=i.property_id
     WHERE i.property_id IS NOT NULL AND b.agency_id <> p.agency_id;
    IF v_bad <> 0 THEN
        RAISE EXCEPTION 'P26-2D 039 refused: % interaction/property mismatch(es)', v_bad;
    END IF;

    SELECT count(*) INTO v_bad
      FROM buy_request_interactions i
      JOIN buy_requests b ON b.id=i.buy_request_id
      JOIN property_visits v ON v.id=i.property_visit_id
      JOIN properties p ON p.id=v.property_id
     WHERE i.property_visit_id IS NOT NULL
       AND (b.agency_id <> p.agency_id
            OR (i.property_id IS NOT NULL AND i.property_id <> v.property_id));
    IF v_bad <> 0 THEN
        RAISE EXCEPTION 'P26-2D 039 refused: % interaction/visit mismatch(es)', v_bad;
    END IF;

    SELECT count(*) INTO v_bad
      FROM buy_request_interactions i
      JOIN buy_requests b ON b.id=i.buy_request_id
      JOIN matches m ON m.id=i.match_id
      JOIN properties p ON p.id=m.property_id
     WHERE i.match_id IS NOT NULL
       AND (m.buy_request_id <> i.buy_request_id
            OR b.agency_id <> p.agency_id
            OR (i.property_id IS NOT NULL AND i.property_id <> m.property_id));
    IF v_bad <> 0 THEN
        RAISE EXCEPTION 'P26-2D 039 refused: % interaction/match mismatch(es)', v_bad;
    END IF;
END
$do$;

DO $do$
DECLARE v_bad BIGINT;
BEGIN
    SELECT count(*) INTO v_bad
      FROM buy_request_task_links l
      JOIN buy_requests b ON b.id=l.buy_request_id
      JOIN tasks t ON t.id=l.task_id
     WHERE b.agency_id <> t.agency_id;
    IF v_bad <> 0 THEN
        RAISE EXCEPTION 'P26-2D 039 refused: % task-link agency mismatch(es)', v_bad;
    END IF;

    SELECT count(*) INTO v_bad
      FROM buy_request_history h
      JOIN buy_requests b ON b.id=h.buy_request_id
      JOIN properties p ON p.id=h.property_id
     WHERE h.property_id IS NOT NULL AND b.agency_id <> p.agency_id;
    IF v_bad <> 0 THEN
        RAISE EXCEPTION 'P26-2D 039 refused: % history/property mismatch(es)', v_bad;
    END IF;

    SELECT count(*) INTO v_bad
      FROM buy_request_history h
      JOIN buy_requests b ON b.id=h.buy_request_id
      JOIN tasks t ON t.id=h.task_id
     WHERE h.task_id IS NOT NULL AND b.agency_id <> t.agency_id;
    IF v_bad <> 0 THEN
        RAISE EXCEPTION 'P26-2D 039 refused: % history/task mismatch(es)', v_bad;
    END IF;

    SELECT count(*) INTO v_bad
      FROM buy_request_history h
      JOIN buy_requests b ON b.id=h.buy_request_id
      JOIN matches m ON m.id=h.match_id
      JOIN properties p ON p.id=m.property_id
     WHERE h.match_id IS NOT NULL
       AND (m.buy_request_id <> h.buy_request_id
            OR b.agency_id <> p.agency_id
            OR (h.property_id IS NOT NULL AND h.property_id <> m.property_id));
    IF v_bad <> 0 THEN
        RAISE EXCEPTION 'P26-2D 039 refused: % history/match mismatch(es)', v_bad;
    END IF;
END
$do$;

ALTER TABLE buy_requests ALTER COLUMN agency_id SET NOT NULL;

CREATE OR REPLACE FUNCTION buy_agency_integrity() RETURNS trigger AS $fn$
DECLARE
    a_buy BIGINT;
    a_ref BIGINT;
    ref_property BIGINT;
    ref_buy BIGINT;
BEGIN
    IF TG_TABLE_NAME='buy_requests' THEN
        SELECT agency_id INTO a_ref FROM contacts WHERE id=NEW.contact_id;
        IF a_ref IS NULL OR a_ref <> NEW.agency_id THEN
            RAISE EXCEPTION 'P26-2D agency integrity: BUY/contact agency mismatch';
        END IF;
        IF NEW.lead_id IS NOT NULL THEN
            SELECT agency_id INTO a_ref FROM leads WHERE id=NEW.lead_id;
            IF a_ref IS NULL OR a_ref <> NEW.agency_id THEN
                RAISE EXCEPTION 'P26-2D agency integrity: BUY/lead agency mismatch';
            END IF;
        END IF;
        RETURN NEW;
    END IF;

    SELECT agency_id INTO a_buy FROM buy_requests WHERE id=NEW.buy_request_id;
    IF a_buy IS NULL THEN
        RAISE EXCEPTION 'P26-2D agency integrity: parent BUY missing';
    END IF;

    IF TG_TABLE_NAME='buy_request_interactions' THEN
        IF NEW.property_id IS NOT NULL THEN
            SELECT agency_id INTO a_ref FROM properties WHERE id=NEW.property_id;
            IF a_ref IS NULL OR a_ref <> a_buy THEN
                RAISE EXCEPTION 'P26-2D agency integrity: interaction property mismatch';
            END IF;
        END IF;
        IF NEW.match_id IS NOT NULL THEN
            SELECT m.buy_request_id,m.property_id,p.agency_id
              INTO ref_buy,ref_property,a_ref
              FROM matches m JOIN properties p ON p.id=m.property_id
             WHERE m.id=NEW.match_id;
            IF ref_buy IS NULL OR ref_buy <> NEW.buy_request_id OR a_ref <> a_buy THEN
                RAISE EXCEPTION 'P26-2D agency integrity: interaction match mismatch';
            END IF;
            IF NEW.property_id IS NOT NULL AND NEW.property_id <> ref_property THEN
                RAISE EXCEPTION 'P26-2D agency integrity: interaction property/match mismatch';
            END IF;
        END IF;
        IF NEW.property_visit_id IS NOT NULL THEN
            SELECT v.property_id,p.agency_id
              INTO ref_property,a_ref
              FROM property_visits v JOIN properties p ON p.id=v.property_id
             WHERE v.id=NEW.property_visit_id;
            IF ref_property IS NULL OR a_ref <> a_buy THEN
                RAISE EXCEPTION 'P26-2D agency integrity: interaction visit mismatch';
            END IF;
            IF NEW.property_id IS NOT NULL AND NEW.property_id <> ref_property THEN
                RAISE EXCEPTION 'P26-2D agency integrity: interaction visit/property mismatch';
            END IF;
        END IF;
        RETURN NEW;
    END IF;

    IF TG_TABLE_NAME='buy_request_task_links' THEN
        SELECT agency_id INTO a_ref FROM tasks WHERE id=NEW.task_id;
        IF a_ref IS NULL OR a_ref <> a_buy THEN
            RAISE EXCEPTION 'P26-2D agency integrity: task-link agency mismatch';
        END IF;
        RETURN NEW;
    END IF;

    IF TG_TABLE_NAME='buy_request_history' THEN
        IF NEW.property_id IS NOT NULL THEN
            SELECT agency_id INTO a_ref FROM properties WHERE id=NEW.property_id;
            IF a_ref IS NULL OR a_ref <> a_buy THEN
                RAISE EXCEPTION 'P26-2D agency integrity: history property mismatch';
            END IF;
        END IF;
        IF NEW.task_id IS NOT NULL THEN
            SELECT agency_id INTO a_ref FROM tasks WHERE id=NEW.task_id;
            IF a_ref IS NULL OR a_ref <> a_buy THEN
                RAISE EXCEPTION 'P26-2D agency integrity: history task mismatch';
            END IF;
        END IF;
        IF NEW.match_id IS NOT NULL THEN
            SELECT m.buy_request_id,m.property_id,p.agency_id
              INTO ref_buy,ref_property,a_ref
              FROM matches m JOIN properties p ON p.id=m.property_id
             WHERE m.id=NEW.match_id;
            IF ref_buy IS NULL OR ref_buy <> NEW.buy_request_id OR a_ref <> a_buy THEN
                RAISE EXCEPTION 'P26-2D agency integrity: history match mismatch';
            END IF;
            IF NEW.property_id IS NOT NULL AND NEW.property_id <> ref_property THEN
                RAISE EXCEPTION 'P26-2D agency integrity: history property/match mismatch';
            END IF;
        END IF;
        RETURN NEW;
    END IF;

    RAISE EXCEPTION 'P26-2D agency integrity invoked for unsupported table %', TG_TABLE_NAME;
END;
$fn$ LANGUAGE plpgsql;

DO $do$ BEGIN
IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_buy_requests_agency_integrity' AND NOT tgisinternal) THEN
    CREATE TRIGGER trg_buy_requests_agency_integrity
        BEFORE INSERT OR UPDATE ON buy_requests
        FOR EACH ROW EXECUTE FUNCTION buy_agency_integrity();
END IF;
END $do$;

DO $do$ BEGIN
IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_buy_interactions_agency_integrity' AND NOT tgisinternal) THEN
    CREATE TRIGGER trg_buy_interactions_agency_integrity
        BEFORE INSERT OR UPDATE ON buy_request_interactions
        FOR EACH ROW EXECUTE FUNCTION buy_agency_integrity();
END IF;
END $do$;

DO $do$ BEGIN
IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_buy_task_links_agency_integrity' AND NOT tgisinternal) THEN
    CREATE TRIGGER trg_buy_task_links_agency_integrity
        BEFORE INSERT OR UPDATE ON buy_request_task_links
        FOR EACH ROW EXECUTE FUNCTION buy_agency_integrity();
END IF;
END $do$;

DO $do$ BEGIN
IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='trg_buy_history_agency_integrity' AND NOT tgisinternal) THEN
    CREATE TRIGGER trg_buy_history_agency_integrity
        BEFORE INSERT OR UPDATE ON buy_request_history
        FOR EACH ROW EXECUTE FUNCTION buy_agency_integrity();
END IF;
END $do$;
