-- DELETE-ARCH Fase 1A: "creato per errore" per appuntamenti e acquisizioni.
--
-- Additiva, nessun backfill. Due sole modifiche:
--
--   1. `appointments.cancelled_kind` (NULLABLE): la qualifica di un
--      annullamento - 'client' (annullato dal cliente), 'agency' (annullato
--      dall'agenzia), 'mistake' (creato per errore, mai esistito). Gli
--      annullamenti storici restano NULL. Lo stato resta `cancelled`: nessuno
--      stato nuovo, la macchina a stati dell'Agenda non cambia.
--      CHECK: valore nel catalogo, e presente SOLO su una riga `cancelled`.
--
--   2. `acquisitions_lost_reason_chk` accetta anche 'created_by_mistake'.
--      Stesso nome del vincolo, stessi valori di 081 piu' quello nuovo; la
--      macchina a stati delle acquisizioni (`lost` terminale, motivo e
--      istante obbligatori) non cambia.
--
-- Fuori da questa migration, di proposito (contratto REV 2, bozza M1):
--   * `activities.stima_id` / `tasks.stima_id` da CASCADE a SET NULL: non
--     serve agli appuntamenti ne' alle acquisizioni (Fase 1B o successiva);
--   * `record_lifecycle_events`, `deleted_at`, Cestino: M2.
--
-- Transaction ownership: the runner owns the UP transaction (version >= 027),
-- so this file carries no BEGIN/COMMIT. See
-- docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.

ALTER TABLE appointments ADD COLUMN IF NOT EXISTS cancelled_kind VARCHAR(10);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'appointments_cancelled_kind_chk'
                      AND conrelid = 'appointments'::regclass) THEN
        ALTER TABLE appointments ADD CONSTRAINT appointments_cancelled_kind_chk CHECK (
            cancelled_kind IS NULL
            OR (cancelled_kind IN ('client', 'agency', 'mistake') AND status = 'cancelled'));
    END IF;
END
$$;

-- Gli annullamenti "creati per errore" si leggono solo con il filtro
-- esplicito: indice parziale piccolo, nessun costo sulle righe normali.
CREATE INDEX IF NOT EXISTS idx_appointments_mistakes
    ON appointments (agency_id, start_at)
    WHERE cancelled_kind = 'mistake';

ALTER TABLE acquisitions DROP CONSTRAINT IF EXISTS acquisitions_lost_reason_chk;
ALTER TABLE acquisitions ADD CONSTRAINT acquisitions_lost_reason_chk CHECK (
    lost_reason IS NULL OR lost_reason IN (
        'other_agency', 'commission', 'price_disagreement', 'owner_no_longer_selling',
        'unreachable', 'property_or_documents_issue', 'other', 'created_by_mistake'));
