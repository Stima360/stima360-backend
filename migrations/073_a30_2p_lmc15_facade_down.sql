-- Down for 073. Toglie la fonte `lmc15_facade` e riporta i CHECK di
-- `appointments` ESATTAMENTE come li definisce la 072.
--
-- RIFIUTA SE ESISTE ANCHE UNA SOLA RIGA `source = 'lmc15_facade'`. Quelle
-- righe sono gli appuntamenti nati dalle rotte LMC-15 attraverso l'Agenda:
-- con i CHECK della 072 sarebbero irrappresentabili (fonte sconosciuta,
-- agente mancante), e cancellarle o riscriverle non e' una decisione che una
-- down puo' prendere. La via d'uscita e' migrarle deliberatamente, e solo
-- allora rieseguire questa down.
--
-- NON tocca `stima_inspections`, `stime`, `stime_dettagliate`,
-- `property_visits`, e nessuna riga di `appointments`.

BEGIN;

DO $$
DECLARE
    v_n INTEGER := 0;
BEGIN
    IF to_regclass('public.appointments') IS NOT NULL THEN
        SELECT count(*) INTO v_n FROM appointments WHERE source = 'lmc15_facade';
    END IF;
    IF v_n > 0 THEN
        RAISE EXCEPTION
            'A30-2P 073 down: % lmc15_facade appointment(s) would become unrepresentable under the 072 constraints. Nothing has been changed. Migrate them deliberately first.',
            v_n;
    END IF;
END
$$;

ALTER TABLE appointments DROP CONSTRAINT appointments_lmc15_facade_chk;

ALTER TABLE appointments DROP CONSTRAINT appointments_agent_required_chk;
ALTER TABLE appointments ADD CONSTRAINT appointments_agent_required_chk CHECK (
    assigned_user_id IS NOT NULL
    OR status IN ('requested', 'cancelled', 'rescheduled')
    OR (status IN ('completed', 'no_show')
        AND source IN ('legacy_stime_dettagliate', 'stima_inspections_backfill')));

ALTER TABLE appointments DROP CONSTRAINT appointments_source_chk;
ALTER TABLE appointments ADD CONSTRAINT appointments_source_chk CHECK (source IN (
    'crm_manual', 'legacy_stime_dettagliate', 'stima_inspections_backfill',
    'booking_link', 'system', 'a30_test'));

DELETE FROM schema_migrations WHERE version = '073_a30_2p_lmc15_facade';

COMMIT;
