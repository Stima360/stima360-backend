-- Down for 104. Toglie le proposte di modifica territoriali e il loro registro.
--
-- Si FERMA, senza modifiche, se esiste anche una sola proposta: toglierle
-- cancellerebbe chi ha proposto, chi ha deciso e che cosa e' stato applicato.
-- Nessun'altra tabella viene toccata (le modifiche gia' applicate agli immobili
-- restano: sono dati dell'immobile).

BEGIN;

DO $do$
BEGIN
    IF to_regclass('public.property_territorial_change_requests') IS NOT NULL
       AND EXISTS (SELECT 1 FROM property_territorial_change_requests) THEN
        RAISE EXCEPTION '104 down: esistono proposte di modifica territoriali; nessuna modifica eseguita';
    END IF;
END
$do$;

DROP TABLE IF EXISTS property_territorial_change_events;
DROP FUNCTION IF EXISTS property_territorial_change_events_append_only();
DROP TABLE IF EXISTS property_territorial_change_requests;
DROP FUNCTION IF EXISTS property_territorial_change_requests_guard();

COMMIT;
