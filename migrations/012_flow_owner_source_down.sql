-- FLOW - P8.3A rollback
-- Rimuove esclusivamente l'estensione della allowlist source_module di flow_events introdotta da 012.
-- Ripristina la constraint storica precedente (definita in 008_flow_01.sql), senza 'owner'.
-- La migration 012 originale non conteneva una guardia environment-specific: questo down non ne introduce una.
-- Il rollback è bloccato se esistono righe con source_module = 'owner' (nessuna cancellazione o coercizione automatica dei dati).

BEGIN;

DO $$
DECLARE
    owner_rows_count BIGINT;
    current_constraint_def TEXT;
BEGIN
    IF to_regclass('public.flow_events') IS NULL THEN
        RAISE EXCEPTION 'Rollback FLOW P8.3A bloccato: tabella public.flow_events assente';
    END IF;

    SELECT pg_get_constraintdef(c.oid) INTO current_constraint_def
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE n.nspname = 'public'
      AND t.relname = 'flow_events'
      AND c.conname = 'flow_events_source_module_check';

    IF current_constraint_def IS NULL THEN
        RAISE EXCEPTION 'Rollback FLOW P8.3A bloccato: constraint flow_events_source_module_check assente su public.flow_events';
    END IF;

    IF current_constraint_def NOT LIKE '%owner%' THEN
        RAISE EXCEPTION 'Rollback FLOW P8.3A bloccato: la constraint flow_events_source_module_check non include ''owner'' (stato inatteso: rollback già eseguito o schema non conforme). Definizione corrente: %', current_constraint_def;
    END IF;

    SELECT COUNT(*) INTO owner_rows_count
    FROM public.flow_events
    WHERE source_module = 'owner';

    IF owner_rows_count <> 0 THEN
        RAISE EXCEPTION 'Rollback FLOW P8.3A bloccato: esistono % righe con source_module = ''owner''. Nessuna DELETE/UPDATE automatica: richiesto intervento umano autorizzato prima del rollback.', owner_rows_count;
    END IF;
END
$$;

ALTER TABLE flow_events
DROP CONSTRAINT flow_events_source_module_check;

ALTER TABLE flow_events
ADD CONSTRAINT flow_events_source_module_check
CHECK (source_module IN ('core','property','buy','match','flow'));

COMMIT;
