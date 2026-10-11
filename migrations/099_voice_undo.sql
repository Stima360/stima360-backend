-- STIMA Voice Fase 4: il registro degli annullamenti (D-B). Solo una tabella
-- NUOVA: nessuna tabella esistente viene toccata, la 098 resta com'e'.
--
-- Perche' una tabella propria: nella 098 un comando o un passo concluso non
-- cambia piu' e gli eventi ammessi non comprendono l'annullamento. Un
-- annullamento NON riscrive cio' che e' concluso: si aggiunge qui, accanto.
--
-- Garanzie scritte nel database:
--   * append-only: nessuna riga si modifica o si cancella;
--   * una riga appartiene alla stessa agenzia e allo stesso attore del suo
--     comando, e riguarda un passo RIUSCITO;
--   * al massimo UN annullamento riuscito per passo (indice unico parziale):
--     due richieste concorrenti non annullano due volte;
--   * nessun trascritto nel dettaglio.

CREATE TABLE voice_step_undos (
    id             BIGSERIAL    PRIMARY KEY,
    agency_id      BIGINT       NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    command_id     BIGINT       NOT NULL REFERENCES voice_commands(id) ON DELETE RESTRICT,
    ordinal        SMALLINT     NOT NULL,
    actor_user_id  BIGINT       NOT NULL REFERENCES operator_users(id) ON DELETE RESTRICT,
    entity_type    VARCHAR(30)  NOT NULL,
    entity_id      BIGINT       NOT NULL,
    outcome        VARCHAR(20)  NOT NULL,
    code           VARCHAR(60),
    detail         JSONB        NOT NULL DEFAULT '{}'::jsonb,
    db_user        TEXT         NOT NULL DEFAULT CURRENT_USER,
    occurred_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT voice_undos_step_fk FOREIGN KEY (command_id, ordinal)
        REFERENCES voice_command_steps (command_id, ordinal) ON DELETE RESTRICT,
    CONSTRAINT voice_undos_entity_chk CHECK (entity_type IN (
        'contact', 'property', 'building', 'appointment', 'activity', 'task', 'seller_lead')),
    CONSTRAINT voice_undos_outcome_chk CHECK (outcome IN ('undone', 'blocked', 'expired', 'failed')),
    CONSTRAINT voice_undos_detail_chk CHECK (jsonb_typeof(detail) = 'object' AND NOT detail ? 'transcript')
);
CREATE UNIQUE INDEX uq_voice_step_undos_done ON voice_step_undos (command_id, ordinal) WHERE outcome = 'undone';
CREATE INDEX idx_voice_step_undos_command ON voice_step_undos (command_id, ordinal, id);

CREATE FUNCTION voice_step_undos_guard() RETURNS trigger AS $fn$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'VOICE: voice_step_undos is append-only (% refused, id=%)', TG_OP, OLD.id;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM voice_commands
                    WHERE id = NEW.command_id AND agency_id = NEW.agency_id AND actor_user_id = NEW.actor_user_id) THEN
        RAISE EXCEPTION 'VOICE: undo agency or actor differs from its command (command=%)', NEW.command_id;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM voice_command_steps
                    WHERE command_id = NEW.command_id AND ordinal = NEW.ordinal AND state = 'succeeded') THEN
        RAISE EXCEPTION 'VOICE: only a succeeded step can be undone (command=%, step=%)', NEW.command_id, NEW.ordinal;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

CREATE TRIGGER trg_voice_step_undos_guard BEFORE INSERT OR UPDATE OR DELETE ON voice_step_undos
    FOR EACH ROW EXECUTE FUNCTION voice_step_undos_guard();
