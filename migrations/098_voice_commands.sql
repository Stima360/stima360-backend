-- STIMA Voice Fase 3: il registro dei comandi vocali. Solo tabelle NUOVE:
-- nessuna tabella esistente viene toccata. Nessuna attivazione: senza una
-- riga in voice_agency_settings STIMA Voice e' spento per l'agenzia.
--
-- Garanzie scritte qui, nel database:
--   * un comando per (agenzia, attore, client_command_id): un invio ripetuto
--     ritrova lo stesso comando, mai quello di un altro agente;
--   * agenzia, attore, chiave, impronta della richiesta e piano sono
--     immutabili; le chiavi congelate dei passi non si riscrivono;
--   * gli stati seguono una macchina a stati; quelli conclusi non si riaprono;
--   * lo storico (voice_command_events) e' append-only;
--   * un passo appartiene alla stessa agenzia del suo comando;
--   * il trascritto ha una scadenza e, cancellato, non torna.

CREATE TABLE voice_agency_settings (
    agency_id                  BIGINT       PRIMARY KEY REFERENCES agencies(id) ON DELETE RESTRICT,
    enabled                    BOOLEAN      NOT NULL DEFAULT FALSE,
    mode                       VARCHAR(10)  NOT NULL DEFAULT 'review',
    auto_intents               TEXT[]       NOT NULL DEFAULT ARRAY['add_note', 'add_task']::TEXT[],
    hidden_duplicate_check     BOOLEAN      NOT NULL DEFAULT TRUE,
    max_commands_per_day       INTEGER      NOT NULL DEFAULT 50,
    max_audio_seconds_month    INTEGER      NOT NULL DEFAULT 3600,
    transcript_retention_days  INTEGER      NOT NULL DEFAULT 30,
    updated_at                 TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_by_user_id         BIGINT       REFERENCES operator_users(id) ON DELETE RESTRICT,
    CONSTRAINT voice_settings_mode_chk CHECK (mode IN ('review', 'assisted', 'auto')),
    CONSTRAINT voice_settings_intents_chk CHECK (auto_intents <@ ARRAY[
        'add_note', 'add_task', 'create_contact', 'create_unit', 'create_building',
        'link_owner', 'activate_seller', 'create_appointment']::TEXT[]),
    CONSTRAINT voice_settings_limits_chk CHECK (max_commands_per_day BETWEEN 1 AND 10000
        AND max_audio_seconds_month BETWEEN 1 AND 10000000),
    CONSTRAINT voice_settings_retention_chk CHECK (transcript_retention_days BETWEEN 1 AND 365)
);

CREATE TABLE voice_commands (
    id                     BIGSERIAL    PRIMARY KEY,
    agency_id              BIGINT       NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    actor_user_id          BIGINT       NOT NULL REFERENCES operator_users(id) ON DELETE RESTRICT,
    client_command_id      UUID         NOT NULL,
    request_sha256         CHAR(64)     NOT NULL,
    input_kind             VARCHAR(10)  NOT NULL,
    recorded_at            TIMESTAMPTZ  NOT NULL,
    status                 VARCHAR(20)  NOT NULL DEFAULT 'received',
    mode                   VARCHAR(10),
    transcript             TEXT,
    transcript_expires_at  TIMESTAMPTZ,
    transcript_purged_at   TIMESTAMPTZ,
    plan                   JSONB,
    plan_sha256            CHAR(64),
    frozen                 JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at             TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    completed_at           TIMESTAMPTZ,
    CONSTRAINT voice_commands_client_uq UNIQUE (agency_id, actor_user_id, client_command_id),
    CONSTRAINT voice_commands_request_sha_chk CHECK (request_sha256 ~ '^[a-f0-9]{64}$'),
    CONSTRAINT voice_commands_input_chk CHECK (input_kind IN ('audio', 'text')),
    CONSTRAINT voice_commands_status_chk CHECK (status IN (
        'received', 'planned', 'awaiting_answers', 'executing', 'completed', 'partial', 'failed', 'cancelled')),
    CONSTRAINT voice_commands_mode_chk CHECK (mode IS NULL OR mode IN ('review', 'assisted', 'auto')),
    CONSTRAINT voice_commands_plan_chk CHECK ((plan IS NULL) = (plan_sha256 IS NULL)
        AND (plan_sha256 IS NULL OR plan_sha256 ~ '^[a-f0-9]{64}$')
        AND (plan IS NULL OR jsonb_typeof(plan) = 'object')),
    CONSTRAINT voice_commands_frozen_chk CHECK (jsonb_typeof(frozen) = 'object'),
    CONSTRAINT voice_commands_transcript_chk CHECK (
        (transcript IS NULL OR transcript_expires_at IS NOT NULL)
        AND (transcript_purged_at IS NULL OR transcript IS NULL)),
    CONSTRAINT voice_commands_completed_chk CHECK (
        (status IN ('completed', 'failed', 'cancelled')) = (completed_at IS NOT NULL))
);
CREATE INDEX idx_voice_commands_actor ON voice_commands (agency_id, actor_user_id, created_at DESC);
CREATE INDEX idx_voice_commands_transcript_expiry ON voice_commands (transcript_expires_at)
    WHERE transcript IS NOT NULL;

CREATE TABLE voice_command_steps (
    command_id         BIGINT       NOT NULL REFERENCES voice_commands(id) ON DELETE RESTRICT,
    ordinal            SMALLINT     NOT NULL,
    agency_id          BIGINT       NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    intent             VARCHAR(30)  NOT NULL,
    decision           VARCHAR(10)  NOT NULL,
    reasons            TEXT[]       NOT NULL DEFAULT ARRAY[]::TEXT[],
    step_class         VARCHAR(15)  NOT NULL,
    client_request_id  UUID,
    state              VARCHAR(15)  NOT NULL DEFAULT 'pending',
    result             JSONB        NOT NULL DEFAULT '{}'::jsonb,
    error_type         VARCHAR(60),
    attempts           INTEGER      NOT NULL DEFAULT 0,
    started_at         TIMESTAMPTZ,
    finished_at        TIMESTAMPTZ,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (command_id, ordinal),
    CONSTRAINT voice_steps_ordinal_chk CHECK (ordinal BETWEEN 1 AND 12),
    CONSTRAINT voice_steps_intent_chk CHECK (intent IN (
        'add_note', 'add_task', 'create_contact', 'create_unit', 'create_building',
        'link_owner', 'activate_seller', 'create_appointment', 'unsupported')),
    CONSTRAINT voice_steps_decision_chk CHECK (decision IN ('auto', 'ask', 'blocked')),
    CONSTRAINT voice_steps_class_chk CHECK (step_class IN ('replayable', 'at_most_once')),
    CONSTRAINT voice_steps_key_chk CHECK ((step_class = 'replayable') = (client_request_id IS NOT NULL)),
    CONSTRAINT voice_steps_state_chk CHECK (state IN (
        'pending', 'running', 'succeeded', 'failed', 'indeterminate', 'skipped')),
    CONSTRAINT voice_steps_result_chk CHECK (jsonb_typeof(result) = 'object'),
    CONSTRAINT voice_steps_attempts_chk CHECK (attempts >= 0)
);

CREATE TABLE voice_command_events (
    id             BIGSERIAL    PRIMARY KEY,
    agency_id      BIGINT       NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    command_id     BIGINT       NOT NULL REFERENCES voice_commands(id) ON DELETE RESTRICT,
    ordinal        SMALLINT,
    event_type     VARCHAR(30)  NOT NULL,
    actor_user_id  BIGINT       REFERENCES operator_users(id) ON DELETE RESTRICT,
    db_user        TEXT         NOT NULL DEFAULT CURRENT_USER,
    occurred_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    detail         JSONB        NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT voice_events_type_chk CHECK (event_type IN (
        'received', 'replayed', 'planned', 'status_changed',
        'step_running', 'step_succeeded', 'step_failed', 'step_indeterminate', 'step_skipped',
        'step_recovered', 'transcript_purged')),
    CONSTRAINT voice_events_detail_chk CHECK (jsonb_typeof(detail) = 'object'
        AND NOT detail ? 'transcript')
);
CREATE INDEX idx_voice_command_events_command ON voice_command_events (command_id, id);

-- ---------------------------------------------------------------------------
-- Guardie
-- ---------------------------------------------------------------------------

CREATE FUNCTION voice_commands_guard() RETURNS trigger AS $fn$
DECLARE
    chiave TEXT;
    ammessi TEXT[];
BEGIN
    IF NEW.agency_id IS DISTINCT FROM OLD.agency_id OR NEW.actor_user_id IS DISTINCT FROM OLD.actor_user_id
       OR NEW.client_command_id IS DISTINCT FROM OLD.client_command_id
       OR NEW.request_sha256 IS DISTINCT FROM OLD.request_sha256
       OR NEW.input_kind IS DISTINCT FROM OLD.input_kind OR NEW.recorded_at IS DISTINCT FROM OLD.recorded_at
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'VOICE: identity of a voice command is immutable (id=%)', OLD.id;
    END IF;
    IF OLD.plan_sha256 IS NOT NULL AND (NEW.plan_sha256 IS DISTINCT FROM OLD.plan_sha256
                                        OR NEW.plan IS DISTINCT FROM OLD.plan OR NEW.mode IS DISTINCT FROM OLD.mode) THEN
        RAISE EXCEPTION 'VOICE: the plan of a voice command is immutable (id=%)', OLD.id;
    END IF;
    FOR chiave IN SELECT jsonb_object_keys(OLD.frozen) LOOP
        IF NOT NEW.frozen ? chiave OR NEW.frozen -> chiave IS DISTINCT FROM OLD.frozen -> chiave THEN
            RAISE EXCEPTION 'VOICE: frozen value % cannot change (id=%)', chiave, OLD.id;
        END IF;
    END LOOP;
    IF OLD.transcript_purged_at IS NOT NULL AND (NEW.transcript IS NOT NULL
                                                 OR NEW.transcript_purged_at IS DISTINCT FROM OLD.transcript_purged_at) THEN
        RAISE EXCEPTION 'VOICE: a purged transcript cannot come back (id=%)', OLD.id;
    END IF;
    IF OLD.transcript IS NOT NULL AND NEW.transcript IS NOT NULL AND NEW.transcript IS DISTINCT FROM OLD.transcript THEN
        RAISE EXCEPTION 'VOICE: a transcript cannot be rewritten (id=%)', OLD.id;
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        ammessi := CASE OLD.status
            WHEN 'received' THEN ARRAY['planned', 'awaiting_answers', 'failed', 'cancelled']
            WHEN 'planned' THEN ARRAY['awaiting_answers', 'executing', 'cancelled']
            WHEN 'awaiting_answers' THEN ARRAY['planned', 'executing', 'cancelled']
            WHEN 'executing' THEN ARRAY['completed', 'partial', 'failed']
            WHEN 'partial' THEN ARRAY['executing', 'cancelled']
            ELSE ARRAY[]::TEXT[]
        END;
        IF NOT NEW.status = ANY (ammessi) THEN
            RAISE EXCEPTION 'VOICE: status % -> % is not allowed (id=%)', OLD.status, NEW.status, OLD.id;
        END IF;
    ELSIF OLD.status IN ('completed', 'failed', 'cancelled')
          AND (NEW.frozen IS DISTINCT FROM OLD.frozen OR NEW.completed_at IS DISTINCT FROM OLD.completed_at) THEN
        RAISE EXCEPTION 'VOICE: a concluded voice command cannot change (id=%)', OLD.id;
    END IF;
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

CREATE TRIGGER trg_voice_commands_guard BEFORE UPDATE ON voice_commands
    FOR EACH ROW EXECUTE FUNCTION voice_commands_guard();

CREATE FUNCTION voice_command_steps_guard() RETURNS trigger AS $fn$
DECLARE
    agenzia BIGINT;
    ammessi TEXT[];
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT agency_id INTO agenzia FROM voice_commands WHERE id = NEW.command_id;
        IF agenzia IS DISTINCT FROM NEW.agency_id THEN
            RAISE EXCEPTION 'VOICE: step agency differs from its command (command=%)', NEW.command_id;
        END IF;
        IF NEW.state <> 'pending' THEN
            RAISE EXCEPTION 'VOICE: a step is born pending (command=%)', NEW.command_id;
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.command_id IS DISTINCT FROM OLD.command_id OR NEW.ordinal IS DISTINCT FROM OLD.ordinal
       OR NEW.agency_id IS DISTINCT FROM OLD.agency_id OR NEW.intent IS DISTINCT FROM OLD.intent
       OR NEW.step_class IS DISTINCT FROM OLD.step_class
       OR NEW.client_request_id IS DISTINCT FROM OLD.client_request_id THEN
        RAISE EXCEPTION 'VOICE: identity of a step is immutable (command=%, step=%)', OLD.command_id, OLD.ordinal;
    END IF;
    IF NEW.state IS DISTINCT FROM OLD.state THEN
        ammessi := CASE OLD.state
            WHEN 'pending' THEN ARRAY['running', 'skipped']
            -- running -> pending: ripresa dopo un crash, SOLO per i passi ripetibili.
            WHEN 'running' THEN CASE WHEN OLD.step_class = 'replayable'
                                     THEN ARRAY['succeeded', 'failed', 'indeterminate', 'pending']
                                     ELSE ARRAY['succeeded', 'failed', 'indeterminate'] END
            WHEN 'failed' THEN ARRAY['pending', 'skipped']
            -- indeterminate: solo una riconciliazione esplicita lo chiude.
            WHEN 'indeterminate' THEN ARRAY['succeeded', 'skipped']
            ELSE ARRAY[]::TEXT[]
        END;
        IF NOT NEW.state = ANY (ammessi) THEN
            RAISE EXCEPTION 'VOICE: step state % -> % is not allowed (command=%, step=%)',
                OLD.state, NEW.state, OLD.command_id, OLD.ordinal;
        END IF;
    ELSIF OLD.state IN ('succeeded', 'skipped') AND NEW.result IS DISTINCT FROM OLD.result THEN
        RAISE EXCEPTION 'VOICE: a concluded step cannot change (command=%, step=%)', OLD.command_id, OLD.ordinal;
    END IF;
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

CREATE TRIGGER trg_voice_command_steps_guard BEFORE INSERT OR UPDATE ON voice_command_steps
    FOR EACH ROW EXECUTE FUNCTION voice_command_steps_guard();

CREATE FUNCTION voice_rows_no_delete() RETURNS trigger AS $fn$
BEGIN
    RAISE EXCEPTION 'VOICE: % rows are never deleted (%)', TG_TABLE_NAME, TG_OP;
END;
$fn$ LANGUAGE plpgsql;

CREATE TRIGGER trg_voice_commands_no_delete BEFORE DELETE ON voice_commands
    FOR EACH ROW EXECUTE FUNCTION voice_rows_no_delete();
CREATE TRIGGER trg_voice_command_steps_no_delete BEFORE DELETE ON voice_command_steps
    FOR EACH ROW EXECUTE FUNCTION voice_rows_no_delete();

CREATE FUNCTION voice_command_events_append_only() RETURNS trigger AS $fn$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NOT EXISTS (SELECT 1 FROM voice_commands WHERE id = NEW.command_id AND agency_id = NEW.agency_id) THEN
            RAISE EXCEPTION 'VOICE: event agency differs from its command (command=%)', NEW.command_id;
        END IF;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'VOICE: voice_command_events is append-only (% refused, id=%)', TG_OP, OLD.id;
END;
$fn$ LANGUAGE plpgsql;

CREATE TRIGGER trg_voice_command_events_append_only BEFORE INSERT OR UPDATE OR DELETE ON voice_command_events
    FOR EACH ROW EXECUTE FUNCTION voice_command_events_append_only();
