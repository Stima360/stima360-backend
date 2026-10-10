-- STIMA Voice Fase 3 down: rifiuta se il registro contiene comandi o
-- impostazioni. Lo storico dei comandi vocali non si cancella per un rollback.
LOCK TABLE voice_commands, voice_command_steps, voice_command_events, voice_agency_settings
    IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM voice_commands) OR EXISTS (SELECT 1 FROM voice_agency_settings) THEN
        RAISE EXCEPTION 'Cannot rollback a populated voice command ledger';
    END IF;
END $$;
DROP TABLE voice_command_events;
DROP TABLE voice_command_steps;
DROP TABLE voice_commands;
DROP TABLE voice_agency_settings;
DROP FUNCTION voice_command_events_append_only();
DROP FUNCTION voice_rows_no_delete();
DROP FUNCTION voice_command_steps_guard();
DROP FUNCTION voice_commands_guard();
