BEGIN;
DROP INDEX IF EXISTS idx_buy_requests_agency_id;
ALTER TABLE buy_requests DROP CONSTRAINT IF EXISTS buy_requests_agency_id_fk;
ALTER TABLE buy_requests DROP COLUMN IF EXISTS agency_id;
COMMIT;
