-- Down for 069. Toglie lo stream di notifiche PRE-INCARICO e nient'altro.
--
-- RIFIUTA SE LA TABELLA CONTIENE ANCHE UNA SOLA RIGA.
--
-- Lo stesso principio della 067 e della 068: un rollback di schema non
-- cancella dati reali. Qui i dati sono le notifiche gia' mostrate (o ancora
-- da leggere) ai proprietari, con il loro `read_at` e la loro `evidence` -
-- cioe' il riferimento da cui il rilevatore riparte. Perderli vorrebbe dire
-- rinotificare fatti gia' notificati alla prima esecuzione successiva.
--
-- La via d'uscita non e' automatica ed e' giusto che non lo sia: esportare
-- `owner_home_notifications`, e solo allora svuotarla, e' una decisione sui
-- dati, non un effetto collaterale di un rollback di schema.
--
-- Nessun CASCADE sul DROP: gli indici e il trigger dipendono dalla tabella e
-- cadono con lei; nient'altro nel database la referenzia. `stime`,
-- `owner_accounts`, `owner_stima_access`, `owner_notifications`,
-- `owner_notification_preferences`, `property_watches` e
-- `property_watch_observations` non vengono toccati, ne' qui ne' nella UP.

BEGIN;

DO $$
DECLARE
    v_righe INTEGER;
BEGIN
    IF to_regclass('public.owner_home_notifications') IS NULL THEN
        RETURN;
    END IF;

    SELECT count(*) INTO v_righe FROM owner_home_notifications;
    IF v_righe > 0 THEN
        RAISE EXCEPTION
            'LMC-12 069 down: % owner home notification(s) would be destroyed, and no other table holds them. Nothing has been changed. Export owner_home_notifications and empty it deliberately first.',
            v_righe;
    END IF;
END
$$;

DROP TRIGGER IF EXISTS trg_owner_home_notifications_agency_integrity ON owner_home_notifications;
DROP FUNCTION IF EXISTS owner_home_notifications_agency_integrity();
DROP TABLE IF EXISTS owner_home_notifications;

DELETE FROM schema_migrations WHERE version = '069_lmc12_owner_home_notifications';

COMMIT;
