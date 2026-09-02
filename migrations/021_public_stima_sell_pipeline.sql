BEGIN;

-- Public valuation requests are seller opportunities.
-- Backfill only legacy public_stima leads still classified as general.
-- Idempotent: once moved to sell, subsequent executions update zero rows.
UPDATE leads
SET
    pipeline = 'sell',
    updated_at = NOW()
WHERE source = 'public_stima'
  AND pipeline = 'general';

COMMIT;
