-- Down for 068. Toglie gli override del proprietario e nient'altro.
--
-- RIFIUTA SE LA TABELLA CONTIENE ANCHE UNA SOLA RIGA.
--
-- Lo stesso principio della 067: un rollback di schema non cancella dati
-- reali. Qui i dati sono le correzioni che i proprietari hanno fatto alla
-- propria casa - un dato che nessun altro nel sistema possiede, perche'
-- `stime` conserva l'originale e non la correzione. Perderli non sarebbe
-- reversibile in nessun modo.
--
-- C'e' una seconda ragione, piu' sottile. Ogni `valuation_snapshot` calcolato
-- dopo un override porta un `input_digest` del profilo EFFETTIVO. Gli snapshot
-- sono immutabili e restano nello storico anche dopo questo rollback: se la
-- tabella sparisse, il database non saprebbe piu' ricostruire l'input di quei
-- punti, e il grafico del valore conterrebbe numeri che nessuno puo' piu'
-- spiegare. Con la tabella vuota il problema non si pone, perche' nessuno
-- snapshot ha mai visto un override.
--
-- La via d'uscita non e' automatica ed e' giusto che non lo sia: esportare
-- `owner_home_overrides`, e solo allora svuotarla, e' una decisione sui dati,
-- non un effetto collaterale di un rollback di schema.
--
-- Nessun CASCADE sul DROP: l'indice e il trigger dipendono dalla tabella e
-- cadono con lei; nient'altro nel database la referenzia. `stime`,
-- `owner_accounts`, `owner_stima_access`, `property_watches`,
-- `property_watch_observations` e `seller_timeline_events` non vengono
-- toccati, ne' qui ne' nella UP.

BEGIN;

DO $$
DECLARE
    v_righe INTEGER;
BEGIN
    IF to_regclass('public.owner_home_overrides') IS NULL THEN
        RETURN;
    END IF;

    SELECT count(*) INTO v_righe FROM owner_home_overrides;
    IF v_righe > 0 THEN
        RAISE EXCEPTION
            'LMC-10 068 down: % owner home override(s) would be destroyed, and no other table holds them. Nothing has been changed. Export owner_home_overrides and empty it deliberately first.',
            v_righe;
    END IF;
END
$$;

DROP TRIGGER IF EXISTS trg_owner_home_overrides_agency_integrity ON owner_home_overrides;
DROP FUNCTION IF EXISTS owner_home_overrides_agency_integrity();
DROP TABLE IF EXISTS owner_home_overrides;

DELETE FROM schema_migrations WHERE version = '068_lmc10_owner_home_overrides';

COMMIT;
