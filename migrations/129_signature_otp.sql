-- FIRMA OTP STIMA360 - sottoscrizione elettronica SEMPLICE (FES) del foglio visita con codice OTP via email,
-- senza fornitori esterni. Additiva: due tabelle nuove e due motivi in piu' nel registro comunicazioni.
-- Le tabelle della 122/125/128 restano come sono (richieste, transizioni, firmatari); la 128 porta i firmatari
-- (cliente e agente) e si riusa senza modifiche: il collegamento di firma NON vi si scrive piu' in chiaro.
--
--   signature_challenges   le sfide di una sottoscrizione, una per riga:
--       access   il collegamento personale del firmatario: SOLO lo SHA-256 del token (alta entropia);
--       otp      il codice a 6 cifre: SOLO una derivazione lenta con sale e pepe del servizio
--                (pbkdf2_sha256, pepe fuori dal database), mai in chiaro, mai nei log.
--       Scadenza, consumo, invalidazione (con motivo), tentativi con massimo, messaggio email collegato,
--       impronta dell'IP e user-agent minimizzati di chi l'ha chiesta.
--   signature_evidence     il registro PROBATORIO, append-only e concatenato: ogni voce porta l'impronta della
--       precedente (`prev_hash`), la propria (`entry_hash` = SHA-256 di prev_hash + voce canonica) e un sigillo
--       HMAC calcolato con una chiave del servizio fuori dal database (`seal`). Una voce alterata, tolta o
--       inserita rompe la catena o il sigillo. Il rapporto delle prove ne riporta la testa.
--
-- Motivi nuovi nel registro comunicazioni: `signature_link` (l'email con il collegamento) e `signature_otp`
-- (l'email con il codice). Nessun invio parte da questa migrazione.
--
-- La transazione e' del runner.

ALTER TABLE communication_messages
    DROP CONSTRAINT IF EXISTS communication_messages_reason_code_chk;
ALTER TABLE communication_messages
    ADD CONSTRAINT communication_messages_reason_code_chk
    CHECK (reason_code IN ('stima_pdf','operator_manual','operator_reply','owner_login_link','appointment_reminder',
        'consent_confirmation', 'auto_reply', 'appointment_confirmation',
        'signature_link', 'signature_otp',
        'm1','m2','m3','m4','m5','m6','m7','m8','m9','m10',
        'm11','m12','m13','m14','m15','m16','m17','m18','m19','m20'));

CREATE TABLE signature_challenges (
    id                    BIGSERIAL    PRIMARY KEY,
    request_id            BIGINT       NOT NULL REFERENCES signature_requests(id),
    signer_id             BIGINT       NOT NULL REFERENCES signature_request_signers(id),
    agency_id             BIGINT       NOT NULL REFERENCES agencies(id),
    kind                  VARCHAR(10)  NOT NULL,
    secret_hash           TEXT         NOT NULL,
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    expires_at            TIMESTAMPTZ  NOT NULL,
    consumed_at           TIMESTAMPTZ,
    invalidated_at        TIMESTAMPTZ,
    invalidation_reason   VARCHAR(40),
    attempts              SMALLINT     NOT NULL DEFAULT 0,
    max_attempts          SMALLINT     NOT NULL DEFAULT 5,
    last_attempt_at       TIMESTAMPTZ,
    message_id            BIGINT       REFERENCES communication_messages(id),
    requester_ip_hash     CHAR(64),
    requester_user_agent  VARCHAR(200),
    CONSTRAINT signature_challenges_kind_chk CHECK (kind IN ('access', 'otp')),
    CONSTRAINT signature_challenges_hash_chk CHECK (BTRIM(secret_hash) <> ''),
    CONSTRAINT signature_challenges_expiry_chk CHECK (expires_at > created_at),
    CONSTRAINT signature_challenges_attempts_chk CHECK (attempts >= 0 AND attempts <= max_attempts AND max_attempts BETWEEN 1 AND 20),
    CONSTRAINT signature_challenges_reason_chk CHECK ((invalidated_at IS NULL) = (invalidation_reason IS NULL)),
    CONSTRAINT signature_challenges_ip_chk CHECK (requester_ip_hash IS NULL OR requester_ip_hash ~ '^[0-9a-f]{64}$')
);

CREATE INDEX idx_signature_challenges_signer ON signature_challenges (signer_id, kind, created_at DESC);
CREATE INDEX idx_signature_challenges_request ON signature_challenges (request_id);
CREATE UNIQUE INDEX uq_signature_challenges_access_hash ON signature_challenges (secret_hash) WHERE kind = 'access';

CREATE FUNCTION signature_challenges_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_agency BIGINT;
    v_request BIGINT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'signature challenge % cannot be deleted', OLD.id;
    END IF;
    SELECT agency_id INTO v_agency FROM signature_requests WHERE id = NEW.request_id;
    SELECT request_id INTO v_request FROM signature_request_signers WHERE id = NEW.signer_id;
    IF v_agency IS DISTINCT FROM NEW.agency_id OR v_request IS DISTINCT FROM NEW.request_id THEN
        RAISE EXCEPTION 'signature challenge: signer % does not belong to request % of agency %',
            NEW.signer_id, NEW.request_id, NEW.agency_id;
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF NEW.request_id IS DISTINCT FROM OLD.request_id OR NEW.signer_id IS DISTINCT FROM OLD.signer_id
           OR NEW.agency_id IS DISTINCT FROM OLD.agency_id OR NEW.kind IS DISTINCT FROM OLD.kind
           OR NEW.secret_hash IS DISTINCT FROM OLD.secret_hash OR NEW.created_at IS DISTINCT FROM OLD.created_at
           OR NEW.expires_at IS DISTINCT FROM OLD.expires_at OR NEW.max_attempts IS DISTINCT FROM OLD.max_attempts
           OR NEW.requester_ip_hash IS DISTINCT FROM OLD.requester_ip_hash
           OR NEW.requester_user_agent IS DISTINCT FROM OLD.requester_user_agent
           OR (OLD.message_id IS NOT NULL AND NEW.message_id IS DISTINCT FROM OLD.message_id) THEN
            RAISE EXCEPTION 'signature challenge %: only attempts, consumption, invalidation and message may change', OLD.id;
        END IF;
        IF (OLD.consumed_at IS NOT NULL OR OLD.invalidated_at IS NOT NULL)
           AND (NEW.consumed_at IS DISTINCT FROM OLD.consumed_at OR NEW.invalidated_at IS DISTINCT FROM OLD.invalidated_at
                OR NEW.invalidation_reason IS DISTINCT FROM OLD.invalidation_reason OR NEW.attempts IS DISTINCT FROM OLD.attempts) THEN
            RAISE EXCEPTION 'signature challenge % is closed', OLD.id;
        END IF;
        IF NEW.attempts < OLD.attempts THEN
            RAISE EXCEPTION 'signature challenge %: attempts never decrease', OLD.id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_signature_challenges_guard
    BEFORE INSERT OR UPDATE OR DELETE ON signature_challenges
    FOR EACH ROW EXECUTE FUNCTION signature_challenges_guard();

CREATE TABLE signature_evidence (
    id            BIGSERIAL    PRIMARY KEY,
    request_id    BIGINT       NOT NULL REFERENCES signature_requests(id),
    agency_id     BIGINT       NOT NULL REFERENCES agencies(id),
    seq           INTEGER      NOT NULL,
    event_type    VARCHAR(40)  NOT NULL,
    occurred_at   TIMESTAMPTZ  NOT NULL,
    actor_kind    VARCHAR(10)  NOT NULL,
    actor_ref     VARCHAR(120),
    signer_id     BIGINT       REFERENCES signature_request_signers(id),
    details       JSONB        NOT NULL DEFAULT '{}'::jsonb,
    prev_hash     CHAR(64),
    entry_hash    CHAR(64)     NOT NULL,
    seal          CHAR(64)     NOT NULL,
    CONSTRAINT signature_evidence_seq_unq UNIQUE (request_id, seq),
    CONSTRAINT signature_evidence_seq_chk CHECK (seq >= 1),
    CONSTRAINT signature_evidence_actor_chk CHECK (actor_kind IN ('operator', 'signer', 'system')),
    CONSTRAINT signature_evidence_type_chk CHECK (BTRIM(event_type) <> ''),
    CONSTRAINT signature_evidence_details_chk CHECK (jsonb_typeof(details) = 'object'),
    CONSTRAINT signature_evidence_hash_chk CHECK (entry_hash ~ '^[0-9a-f]{64}$' AND seal ~ '^[0-9a-f]{64}$'
                                                  AND (prev_hash IS NULL OR prev_hash ~ '^[0-9a-f]{64}$')),
    CONSTRAINT signature_evidence_first_chk CHECK ((seq = 1) = (prev_hash IS NULL))
);

CREATE FUNCTION signature_evidence_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_agency BIGINT;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'signature evidence is append-only (% refused, id=%)', TG_OP, OLD.id;
    END IF;
    SELECT agency_id INTO v_agency FROM signature_requests WHERE id = NEW.request_id;
    IF v_agency IS DISTINCT FROM NEW.agency_id THEN
        RAISE EXCEPTION 'signature evidence: request % belongs to another agency', NEW.request_id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_signature_evidence_guard
    BEFORE INSERT OR UPDATE OR DELETE ON signature_evidence
    FOR EACH ROW EXECUTE FUNCTION signature_evidence_guard();
