-- BLOCCO-4D - motore delle risposte automatiche WhatsApp, SOLO SIMULAZIONE.
--
-- Tre tabelle nuove, additive, nessun backfill. Nessuna tabella esistente
-- viene modificata. Il motore registra la decisione che prenderebbe: NON
-- accoda messaggi da spedire e non chiama Meta (il modo 'simulate' e' l'unico
-- ammesso dal vincolo: un modo d'invio reale richiedera' una migrazione).
--
--   whatsapp_auto_reply_settings   una riga per agenzia: modo ('off' di
--       partenza, 'simulate') e da quando e' attivo. Solo i messaggi ricevuti
--       DOPO l'attivazione vengono elaborati.
--   whatsapp_auto_reply_pauses     pausa manuale di una conversazione
--       (agenzia + telefono), con autore; al piu' una pausa aperta per
--       conversazione. La ripresa chiude la riga, non la cancella.
--   whatsapp_auto_reply_decisions  registro append-only delle decisioni:
--       messaggio d'origine, decisione, testo proposto, motivo, versione delle
--       regole, dati usati. UNA decisione per (messaggio, versione delle
--       regole): un reinvio del webhook o due giri concorrenti non la
--       duplicano. Il messaggio deve essere della stessa agenzia (trigger).
--
-- La transazione e' del runner.

CREATE TABLE whatsapp_auto_reply_settings (
    agency_id            BIGINT       PRIMARY KEY REFERENCES agencies(id),
    mode                 VARCHAR(10)  NOT NULL DEFAULT 'off',
    enabled_at           TIMESTAMPTZ,
    updated_by_user_id   BIGINT       REFERENCES operator_users(id),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT whatsapp_auto_reply_settings_mode_chk CHECK (mode IN ('off', 'simulate')),
    CONSTRAINT whatsapp_auto_reply_settings_enabled_chk CHECK ((mode = 'off') = (enabled_at IS NULL))
);

CREATE TABLE whatsapp_auto_reply_pauses (
    id                   BIGSERIAL    PRIMARY KEY,
    agency_id            BIGINT       NOT NULL REFERENCES agencies(id),
    sender_phone         TEXT         NOT NULL,
    reason               VARCHAR(30)  NOT NULL DEFAULT 'operator',
    paused_by_user_id    BIGINT       NOT NULL REFERENCES operator_users(id),
    paused_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    resumed_by_user_id   BIGINT       REFERENCES operator_users(id),
    resumed_at           TIMESTAMPTZ,
    CONSTRAINT whatsapp_auto_reply_pauses_sender_chk CHECK (sender_phone ~ '^[0-9]{6,15}$'),
    CONSTRAINT whatsapp_auto_reply_pauses_reason_chk CHECK (reason IN ('operator')),
    CONSTRAINT whatsapp_auto_reply_pauses_resume_chk CHECK ((resumed_at IS NULL) = (resumed_by_user_id IS NULL)),
    CONSTRAINT whatsapp_auto_reply_pauses_order_chk CHECK (resumed_at IS NULL OR resumed_at >= paused_at)
);

CREATE UNIQUE INDEX uq_whatsapp_auto_reply_pauses_open
    ON whatsapp_auto_reply_pauses (agency_id, sender_phone) WHERE resumed_at IS NULL;

CREATE TABLE whatsapp_auto_reply_decisions (
    id                   BIGSERIAL    PRIMARY KEY,
    agency_id            BIGINT       NOT NULL REFERENCES agencies(id),
    inbound_message_id   BIGINT       NOT NULL REFERENCES whatsapp_inbound_messages(id),
    rules_version        VARCHAR(20)  NOT NULL,
    mode                 VARCHAR(10)  NOT NULL,
    decision             VARCHAR(20)  NOT NULL,
    reason_code          VARCHAR(40),
    proposed_text        TEXT,
    data_used            JSONB        NOT NULL DEFAULT '{}'::jsonb,
    handoff              JSONB,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT whatsapp_auto_reply_decisions_unq UNIQUE (inbound_message_id, rules_version),
    CONSTRAINT whatsapp_auto_reply_decisions_mode_chk CHECK (mode = 'simulate'),
    CONSTRAINT whatsapp_auto_reply_decisions_decision_chk
        CHECK (decision IN ('answer', 'ask_reference', 'handoff', 'none', 'skipped')),
    CONSTRAINT whatsapp_auto_reply_decisions_text_chk
        CHECK (decision IN ('answer', 'ask_reference', 'handoff') OR proposed_text IS NULL),
    CONSTRAINT whatsapp_auto_reply_decisions_reason_chk CHECK (decision = 'answer' OR reason_code IS NOT NULL),
    CONSTRAINT whatsapp_auto_reply_decisions_version_chk CHECK (BTRIM(rules_version) <> ''),
    CONSTRAINT whatsapp_auto_reply_decisions_data_chk CHECK (jsonb_typeof(data_used) = 'object')
);

CREATE INDEX idx_whatsapp_auto_reply_decisions_agency
    ON whatsapp_auto_reply_decisions (agency_id, created_at DESC);

CREATE FUNCTION whatsapp_auto_reply_decisions_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_agency BIGINT;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'whatsapp auto-reply decisions are append-only';
    END IF;
    SELECT agency_id INTO v_agency FROM whatsapp_inbound_messages WHERE id = NEW.inbound_message_id;
    IF v_agency IS DISTINCT FROM NEW.agency_id THEN
        RAISE EXCEPTION 'whatsapp auto-reply decision: message % belongs to another agency', NEW.inbound_message_id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_whatsapp_auto_reply_decisions_guard
    BEFORE INSERT OR UPDATE OR DELETE ON whatsapp_auto_reply_decisions
    FOR EACH ROW EXECUTE FUNCTION whatsapp_auto_reply_decisions_guard();
