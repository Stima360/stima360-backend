-- P26-5 SALES and PROPOSALS agency integrity: enforcement only.
--
-- PROPOSAL and SALE are CHILD-DERIVED. No table below gains an agency_id
-- column: the chain already records the tenant twice, at its roots.
--
--     BUY[A] + PROPERTY[A] -> MATCH -> PROPOSAL -> SALE
--
-- TWO DIFFERENT INVARIANTS
--
-- Conflating them is the easiest way to get this slice wrong:
--
--   CROSS-AGENCY            a sale citing another tenant's property
--   SAME-AGENCY WRONG PAIR  a sale citing this tenant's *other* property -
--                           one belonging to a different match
--
-- The second is not a tenancy breach and no agency comparison would catch it,
-- but it corrupts the chain just as badly: completing that sale would mark the
-- wrong property sold and satisfy the wrong buy request. Both are enforced.
--
-- WHICH TABLES GET A TRIGGER
--
--   property_sales         property + buy request + proposal  -> guarded
--   property_sale_sellers  sale + contact                     -> guarded
--   property_proposals     match_id only                      -> NOT guarded
--
-- property_proposals is excluded for a structural reason, not a preference: it
-- has exactly one foreign key, to `matches`, which migrations 040 and 041
-- already guard. A row that names one other row cannot straddle two agencies,
-- so a trigger here could never fire while costing a write on every insert.
--
-- TWO FUNCTIONS, NOT ONE
--
-- 040 installed a single function on four tables and wrote
--
--     IF TG_TABLE_NAME = 'matches' AND NEW.latest_run_id IS NOT NULL THEN
--
-- which fails on every table lacking that column, because PL/pgSQL resolves a
-- record field when it prepares the expression, not only when the preceding
-- conjunct is true. 041 fixed it by nesting. Here the two tables share no
-- columns at all, so the safest form is two functions: there is no condition
-- that could reach for a field the record does not have, by construction
-- rather than by care.
--
-- Transaction ownership: the runner owns the UP transaction, so this file
-- carries no BEGIN/COMMIT. Reversible - it writes no data.

-- ---------------------------------------------------------------------------
-- 1. Prechecks. The migration refuses on inconsistent history and repairs
--    nothing: a sale whose chain has diverged cannot be corrected
--    automatically, because there is no way to know which reference was the
--    mistake.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_bad BIGINT;
BEGIN
    -- A. A proposal whose match no longer joins one agency. Existence is a
    --    foreign key; agreement is not.
    SELECT count(*) INTO v_bad
      FROM property_proposals pp
      JOIN matches m ON m.id = pp.match_id
      JOIN buy_requests b ON b.id = m.buy_request_id
      JOIN properties  p ON p.id = m.property_id
     WHERE b.agency_id <> p.agency_id;
    IF v_bad <> 0 THEN
        RAISE EXCEPTION
            'P26-5 042 refused: % proposal(s) hang off a match whose buy request and property are in different agencies', v_bad;
    END IF;

    -- B. The chain: a sale must name the pair of its proposal's match.
    SELECT count(*) INTO v_bad
      FROM property_sales ps
      JOIN property_proposals pp ON pp.id = ps.proposal_id
      JOIN matches m ON m.id = pp.match_id
     WHERE ps.property_id <> m.property_id
        OR ps.buy_request_id <> m.buy_request_id;
    IF v_bad <> 0 THEN
        RAISE EXCEPTION
            'P26-5 042 refused: % sale(s) name a property or buy request that is not the pair of their proposal''s match', v_bad;
    END IF;

    -- C. A sale's own two roots must sit in one agency.
    SELECT count(*) INTO v_bad
      FROM property_sales ps
      JOIN buy_requests b ON b.id = ps.buy_request_id
      JOIN properties  p ON p.id = ps.property_id
     WHERE b.agency_id <> p.agency_id;
    IF v_bad <> 0 THEN
        RAISE EXCEPTION
            'P26-5 042 refused: % sale(s) join a buy request and a property in different agencies', v_bad;
    END IF;

    -- D. The agency the proposal derives must be the agency the sale derives.
    SELECT count(*) INTO v_bad
      FROM property_sales ps
      JOIN property_proposals pp ON pp.id = ps.proposal_id
      JOIN matches m ON m.id = pp.match_id
      JOIN buy_requests mb ON mb.id = m.buy_request_id
      JOIN buy_requests sb ON sb.id = ps.buy_request_id
     WHERE mb.agency_id <> sb.agency_id;
    IF v_bad <> 0 THEN
        RAISE EXCEPTION
            'P26-5 042 refused: % sale(s) derive a different agency from their proposal', v_bad;
    END IF;

    -- E. A seller snapshot must name a contact of the sale's own agency.
    SELECT count(*) INTO v_bad
      FROM property_sale_sellers pss
      JOIN property_sales ps ON ps.id = pss.sale_id
      JOIN buy_requests b ON b.id = ps.buy_request_id
      JOIN contacts c ON c.id = pss.contact_id
     WHERE c.agency_id <> b.agency_id;
    IF v_bad <> 0 THEN
        RAISE EXCEPTION
            'P26-5 042 refused: % seller snapshot(s) name a contact from another agency', v_bad;
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- 2. property_sales integrity.
--
-- Three references that can disagree, so all three are resolved and compared:
-- the pair must be the proposal's match pair, and the pair must sit in one
-- agency. The first catches same-agency wrong pair, the second catches
-- cross-agency; neither subsumes the other.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION property_sale_chain_integrity() RETURNS trigger AS $fn$
DECLARE
    a_buy      BIGINT;
    a_property BIGINT;
    m_buy      BIGINT;
    m_property BIGINT;
BEGIN
    SELECT agency_id INTO a_buy FROM buy_requests WHERE id = NEW.buy_request_id;
    IF a_buy IS NULL THEN
        RAISE EXCEPTION
            'P26-5 sale integrity: buy request % does not exist or carries no agency',
            NEW.buy_request_id;
    END IF;

    SELECT agency_id INTO a_property FROM properties WHERE id = NEW.property_id;
    IF a_property IS NULL THEN
        RAISE EXCEPTION
            'P26-5 sale integrity: property % does not exist or carries no agency',
            NEW.property_id;
    END IF;

    IF a_buy <> a_property THEN
        RAISE EXCEPTION
            'P26-5 sale integrity: buy request % is in agency %, property % is in agency %',
            NEW.buy_request_id, a_buy, NEW.property_id, a_property;
    END IF;

    SELECT m.buy_request_id, m.property_id INTO m_buy, m_property
      FROM property_proposals pp
      JOIN matches m ON m.id = pp.match_id
     WHERE pp.id = NEW.proposal_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION
            'P26-5 sale integrity: proposal % does not exist or has no match',
            NEW.proposal_id;
    END IF;

    -- Same-agency wrong pair. Both rows may legitimately belong to this
    -- tenant and the sale would still be describing a chain that does not
    -- exist.
    IF m_buy <> NEW.buy_request_id OR m_property <> NEW.property_id THEN
        RAISE EXCEPTION
            'P26-5 sale integrity: sale names buy request % and property %, but proposal % belongs to the match of buy request % and property %',
            NEW.buy_request_id, NEW.property_id, NEW.proposal_id, m_buy, m_property;
    END IF;

    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- 3. property_sale_sellers integrity.
--
-- A separate function, sharing no columns with the one above. The seller
-- snapshot is historical by design - it records who the owners were at the
-- moment of sale, and is deliberately NOT re-checked against property_contacts,
-- which may change afterwards. What must hold is narrower and permanent: the
-- contact belongs to the same agency as the sale.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION property_sale_seller_agency_integrity() RETURNS trigger AS $fn$
DECLARE
    a_sale    BIGINT;
    a_contact BIGINT;
BEGIN
    SELECT b.agency_id INTO a_sale
      FROM property_sales ps
      JOIN buy_requests b ON b.id = ps.buy_request_id
     WHERE ps.id = NEW.sale_id;
    IF NOT FOUND OR a_sale IS NULL THEN
        RAISE EXCEPTION
            'P26-5 seller integrity: sale % does not exist or derives no agency',
            NEW.sale_id;
    END IF;

    SELECT agency_id INTO a_contact FROM contacts WHERE id = NEW.contact_id;
    IF a_contact IS NULL THEN
        RAISE EXCEPTION
            'P26-5 seller integrity: contact % does not exist or carries no agency',
            NEW.contact_id;
    END IF;

    IF a_contact <> a_sale THEN
        RAISE EXCEPTION
            'P26-5 seller integrity: contact % is in agency %, sale % is in agency %',
            NEW.contact_id, a_contact, NEW.sale_id, a_sale;
    END IF;

    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- 4. Triggers.
--
-- BEFORE INSERT OR UPDATE OF the reference columns. The UPDATE branch is not
-- optional: creating a legitimate sale and then repointing it at another
-- agency's property is the same breach, one statement later.
--
-- PostgreSQL has no CREATE TRIGGER IF NOT EXISTS, so the catalogue is consulted
-- directly - the pattern 026 established. tgisinternal is excluded so a
-- constraint-backed internal trigger cannot produce a false positive.
--
-- property_proposals is deliberately absent.
-- ---------------------------------------------------------------------------
DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE t.tgname = 'trg_property_sales_chain_integrity'
          AND c.relname = 'property_sales' AND n.nspname = 'public'
          AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_property_sales_chain_integrity
            BEFORE INSERT OR UPDATE OF property_id, buy_request_id, proposal_id
            ON property_sales
            FOR EACH ROW EXECUTE FUNCTION property_sale_chain_integrity();
    END IF;
END
$do$;

DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE t.tgname = 'trg_property_sale_sellers_agency_integrity'
          AND c.relname = 'property_sale_sellers' AND n.nspname = 'public'
          AND NOT t.tgisinternal
    ) THEN
        CREATE TRIGGER trg_property_sale_sellers_agency_integrity
            BEFORE INSERT OR UPDATE OF sale_id, contact_id
            ON property_sale_sellers
            FOR EACH ROW EXECUTE FUNCTION property_sale_seller_agency_integrity();
    END IF;
END
$do$;
