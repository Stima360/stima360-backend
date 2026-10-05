-- Down for 084. Toglie `appointments.cancelled_kind` (vincolo, indice,
-- colonna) e riporta `acquisitions_lost_reason_chk` ai valori di 081.
--
-- RIFIUTA SE ESISTE ANCHE UNA SOLA RIGA QUALIFICATA: un annullamento con
-- `cancelled_kind` perderebbe la qualifica (un "creato per errore" tornerebbe
-- un annullamento normale, visibile e contato), e un'acquisizione
-- `created_by_mistake` violerebbe il CHECK ripristinato. Stessa regola delle
-- down di 072, 076-083.

BEGIN;

DO $$
DECLARE
    v_kind INTEGER := 0;
    v_acq  INTEGER := 0;
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'appointments'
                  AND column_name = 'cancelled_kind') THEN
        EXECUTE 'SELECT count(*) FROM appointments WHERE cancelled_kind IS NOT NULL' INTO v_kind;
    END IF;
    SELECT count(*) INTO v_acq FROM acquisitions WHERE lost_reason = 'created_by_mistake';
    IF v_kind > 0 OR v_acq > 0 THEN
        RAISE EXCEPTION
            'DELETE-ARCH 084 down: % appointment(s) carry cancelled_kind and % acquisition(s) are created_by_mistake. Nothing has been changed.',
            v_kind, v_acq;
    END IF;
END
$$;

ALTER TABLE acquisitions DROP CONSTRAINT IF EXISTS acquisitions_lost_reason_chk;
ALTER TABLE acquisitions ADD CONSTRAINT acquisitions_lost_reason_chk CHECK (
    lost_reason IS NULL OR lost_reason IN (
        'other_agency', 'commission', 'price_disagreement', 'owner_no_longer_selling',
        'unreachable', 'property_or_documents_issue', 'other'));

DROP INDEX IF EXISTS idx_appointments_mistakes;
ALTER TABLE appointments DROP CONSTRAINT IF EXISTS appointments_cancelled_kind_chk;
ALTER TABLE appointments DROP COLUMN IF EXISTS cancelled_kind;

COMMIT;
