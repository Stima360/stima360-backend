-- Down for 070. Toglie il ponte di acquisizione e i sopralluoghi.
--
-- RIFIUTA SE UNA DELLE DUE TABELLE CONTIENE ANCHE UNA SOLA RIGA.
--
-- Lo stesso principio di 068 e 069, e qui pesa di piu': queste righe sono
-- l'unico posto in cui il sistema sa che una casa PRE-incarico e' diventata un
-- incarico, e che un sopralluogo e' avvenuto. Nessun'altra tabella lo dice -
-- l'audit LMC-15A lo ha dimostrato - quindi perderle non e' reversibile in
-- nessun modo, e non si puo' ricostruirle senza inventare.
--
-- C'e' una seconda ragione. `schema_migrations.applied_at` di questa versione
-- e' il `measurement_started_at` di LMC-13: cancellare la migration azzera il
-- momento da cui le due metriche sono osservabili, e le coorti successive
-- tornerebbero `null`. E' il comportamento corretto, ma va deciso, non subito.
--
-- La via d'uscita non e' automatica ed e' giusto che non lo sia: esportare le
-- due tabelle, e solo allora svuotarle, e' una decisione sui dati.

BEGIN;

DO $$
DECLARE
    v_acq INTEGER := 0;
    v_ins INTEGER := 0;
BEGIN
    IF to_regclass('public.stima_acquisitions') IS NOT NULL THEN
        SELECT count(*) INTO v_acq FROM stima_acquisitions;
    END IF;
    IF to_regclass('public.stima_inspections') IS NOT NULL THEN
        SELECT count(*) INTO v_ins FROM stima_inspections;
    END IF;

    IF v_acq > 0 OR v_ins > 0 THEN
        RAISE EXCEPTION
            'LMC-15 070 down: % acquisition link(s) and % inspection(s) would be destroyed, and no other table holds them. Nothing has been changed. Export stima_acquisitions and stima_inspections and empty them deliberately first.',
            v_acq, v_ins;
    END IF;
END
$$;

DROP TRIGGER IF EXISTS trg_stima_acquisitions_guard ON stima_acquisitions;
DROP TRIGGER IF EXISTS trg_stima_inspections_guard ON stima_inspections;
DROP FUNCTION IF EXISTS stima_acquisitions_guard();
DROP FUNCTION IF EXISTS stima_inspections_guard();
DROP FUNCTION IF EXISTS lmc15_assert_operator_may_act(BIGINT, BIGINT, TEXT);
DROP TABLE IF EXISTS stima_acquisitions;
DROP TABLE IF EXISTS stima_inspections;

DELETE FROM schema_migrations WHERE version = '070_lmc15_acquisition_bridge';

COMMIT;
