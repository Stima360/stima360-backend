-- 075 down - rimuove SOLO le colonne/indici/vincoli aggiunti dalla 075.
-- Nessun altro effetto sulla 074: le tre tabelle e le guardie outbound
-- restano intatte.

DROP INDEX IF EXISTS idx_appointment_calendar_sync_inbound_claimed;
DROP INDEX IF EXISTS idx_appointment_calendar_sync_inbound_due;

ALTER TABLE appointment_calendar_sync
    DROP CONSTRAINT IF EXISTS appointment_calendar_sync_inbound_error_code_chk,
    DROP CONSTRAINT IF EXISTS appointment_calendar_sync_inbound_claim_pair_chk,
    DROP CONSTRAINT IF EXISTS appointment_calendar_sync_inbound_attempts_chk;

ALTER TABLE appointment_calendar_sync
    DROP COLUMN IF EXISTS inbound_last_error_code,
    DROP COLUMN IF EXISTS inbound_claimed_at,
    DROP COLUMN IF EXISTS inbound_claim_token,
    DROP COLUMN IF EXISTS inbound_attempt_count,
    DROP COLUMN IF EXISTS inbound_checked_at,
    DROP COLUMN IF EXISTS inbound_next_check_at;
