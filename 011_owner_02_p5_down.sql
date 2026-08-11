-- OWNER 0.2 - P5 rollback
-- Esclusivamente ambiente TEST. Distruttivo: rimuove solo le strutture P5.
-- PREPARATO, NON ESEGUIRE senza approvazione separata.

BEGIN;

DO $$
DECLARE
    notifications_count BIGINT;
    preferences_count BIGINT;
BEGIN
    IF current_database() <> 'stima360_db_test' THEN
        RAISE EXCEPTION 'OWNER 0.2 P5 rollback bloccato: database corrente %, atteso stima360_db_test', current_database();
    END IF;

    IF current_schema() <> 'public' THEN
        RAISE EXCEPTION 'OWNER 0.2 P5 rollback bloccato: schema corrente %, atteso public', current_schema();
    END IF;

    IF to_regclass('public.owner_notifications') IS NULL
       OR to_regclass('public.owner_notification_preferences') IS NULL THEN
        RAISE EXCEPTION 'OWNER 0.2 P5 rollback bloccato: strutture P5 assenti o parziali';
    END IF;

    SELECT COUNT(*) INTO notifications_count FROM owner_notifications;
    SELECT COUNT(*) INTO preferences_count FROM owner_notification_preferences;

    IF notifications_count <> 0 OR preferences_count <> 0 THEN
        RAISE EXCEPTION
            'OWNER 0.2 P5 rollback bloccato: dati presenti (notifications=%, preferences=%). Eseguire cleanup autorizzato prima del rollback.',
            notifications_count, preferences_count;
    END IF;
END
$$;

DROP TABLE owner_notification_preferences;
DROP TABLE owner_notifications;

COMMIT;
