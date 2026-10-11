-- STIMA Voice Fase 4 down: rifiuta se il registro degli annullamenti contiene
-- righe. Lo storico degli annullamenti non si cancella per un rollback.
LOCK TABLE voice_step_undos IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM voice_step_undos) THEN
        RAISE EXCEPTION 'Cannot rollback a populated voice undo ledger';
    END IF;
END $$;
DROP TABLE voice_step_undos;
DROP FUNCTION voice_step_undos_guard();
