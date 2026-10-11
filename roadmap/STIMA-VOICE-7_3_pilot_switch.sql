-- STIMA Voice Fase 7 - passo 3/3: interruttore dell'agenzia pilota. SCRIVE (una riga).
-- Solo su `stima360_db_test`, solo con autorizzazione esplicita del passo.
-- Uso:
--   accensione: psql ... -v ON_ERROR_STOP=1 -v agency=<id> -v enabled=true  -v mode=auto -f questo_file
--   spegnimento: psql ... -v ON_ERROR_STOP=1 -v agency=<id> -v enabled=false -v mode=auto -f questo_file
-- `mode`: 'auto' (comportamento finale: esegue da solo cio' che e' sicuro, chiede su
-- ambiguita', doppioni e rischi), 'assisted' o 'review' (ogni passo chiede conferma).
-- Lo spegnimento vale dalla richiesta successiva, senza riavvio.
SELECT current_database() = 'stima360_db_test' AS database_giusto \gset
\if :database_giusto
BEGIN;
SELECT id, slug FROM agencies WHERE id = :agency;
INSERT INTO voice_agency_settings (agency_id, enabled, mode)
VALUES (:agency, :enabled, :'mode')
ON CONFLICT (agency_id) DO UPDATE SET enabled = EXCLUDED.enabled, mode = EXCLUDED.mode, updated_at = NOW();
SELECT agency_id, enabled, mode, auto_intents, max_commands_per_day, transcript_retention_days, updated_at
  FROM voice_agency_settings ORDER BY agency_id;
COMMIT;
\else
\echo 'BLOCCATO: questo non e'' stima360_db_test. Nessuna modifica.'
\endif
