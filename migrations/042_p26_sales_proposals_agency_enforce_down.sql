-- Rollback of P26-5 SALES and PROPOSALS agency integrity.
--
-- Genuinely reversible. 042 wrote no data - it added two functions and two
-- triggers - so every effect can be lifted exactly and nothing is lost. That is
-- why this file reverses rather than refusing, unlike the rollbacks of 029, 032
-- and 038, which undid backfills they could no longer identify.
--
-- Order is the exact reverse of the up migration:
--
--   1. the two triggers   (they depend on their functions)
--   2. the two functions
--
-- Deliberately NOT touched:
--
--   * every proposal, sale and seller row. 042 changed no data.
--   * the shape of property_proposals, property_sales and
--     property_sale_sellers: 042 added no column, so there is none to drop.
--   * buy_requests.agency_id and properties.agency_id, which belong to the BUY
--     and PROPERTY slices and are the roots this chain derives from.
--   * the MATCH triggers from 040/041, which guard the link above this one.
--
-- No CASCADE anywhere. If something has come to depend on these objects, that
-- must fail loudly rather than be swept away silently.
--
-- This file brackets its own transaction because it is executed manually rather
-- than by scripts/p26_migrate.py, which has no down command.

BEGIN;

-- 1. The triggers, before the functions they call.
DROP TRIGGER IF EXISTS trg_property_sales_chain_integrity ON property_sales;
DROP TRIGGER IF EXISTS trg_property_sale_sellers_agency_integrity ON property_sale_sellers;

-- 2. The functions, once nothing calls them.
DROP FUNCTION IF EXISTS property_sale_chain_integrity();
DROP FUNCTION IF EXISTS property_sale_seller_agency_integrity();

COMMIT;
