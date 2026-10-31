-- BLOCCO-6B - richieste di firma elettronica dei fogli visita (preparazione).
--
-- Due tabelle nuove, additive. Nessun fornitore reale e' integrato: l'unico
-- fornitore ammesso oggi e' l'adattatore locale `simulated`, riconoscibile in
-- ogni riga. Una richiesta simulata NON puo' mai essere «signed» (vincolo):
-- il suo esito positivo e' `simulated_completed`, che non e' una firma.
--
--   signature_requests        una richiesta per riga: foglio visita, fornitore,
--       firmatario (nome e recapito), scadenza, stato, riferimento del
--       fornitore, documento ricevuto (byte e SHA-256), chiave di idempotenza
--       per agenzia, autore. Stati:
--           sent                 consegnata al fornitore, in attesa
--           signed               firmata (SOLO fornitore reale: oggi nessuno)
--           simulated_completed  esito positivo SIMULATO: nessuna firma valida
--           declined             rifiutata dal firmatario
--           expired              scaduta senza esito
--           cancelled            annullata dall'agenzia
--   signature_request_events  registro append-only delle transizioni.
--
-- La transazione e' del runner.

CREATE TABLE signature_requests (
    id                     BIGSERIAL    PRIMARY KEY,
    agency_id              BIGINT       NOT NULL REFERENCES agencies(id),
    visit_sheet_id         BIGINT       NOT NULL REFERENCES visit_sheets(id),
    provider               VARCHAR(30)  NOT NULL,
    provider_reference     VARCHAR(120),
    signer_name            VARCHAR(120) NOT NULL,
    signer_contact         VARCHAR(200) NOT NULL,
    status                 VARCHAR(20)  NOT NULL,
    expires_at             TIMESTAMPTZ  NOT NULL,
    idempotency_key        VARCHAR(80)  NOT NULL,
    document_sha256        CHAR(64)     NOT NULL,
    received_document      BYTEA,
    received_document_sha256 CHAR(64),
    received_at            TIMESTAMPTZ,
    created_by_user_id     BIGINT       NOT NULL REFERENCES operator_users(id),
    created_at             TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    closed_at              TIMESTAMPTZ,
    CONSTRAINT signature_requests_idem_unq UNIQUE (agency_id, idempotency_key),
    CONSTRAINT signature_requests_status_chk
        CHECK (status IN ('sent', 'signed', 'simulated_completed', 'declined', 'expired', 'cancelled')),
    CONSTRAINT signature_requests_provider_chk CHECK (BTRIM(provider) <> ''),
    CONSTRAINT signature_requests_simulated_never_signed_chk CHECK (provider <> 'simulated' OR status <> 'signed'),
    CONSTRAINT signature_requests_simulated_outcome_chk
        CHECK (status <> 'simulated_completed' OR provider = 'simulated'),
    CONSTRAINT signature_requests_closed_chk CHECK ((status = 'sent') = (closed_at IS NULL)),
    CONSTRAINT signature_requests_document_chk
        CHECK ((received_document IS NULL) = (received_document_sha256 IS NULL)
               AND (received_document IS NULL) = (received_at IS NULL)),
    CONSTRAINT signature_requests_received_chk
        CHECK (received_document IS NULL OR status IN ('signed', 'simulated_completed')),
    CONSTRAINT signature_requests_sha_chk CHECK (document_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT signature_requests_idem_chk CHECK (BTRIM(idempotency_key) <> '')
);

CREATE INDEX idx_signature_requests_sheet ON signature_requests (visit_sheet_id, created_at DESC);

CREATE FUNCTION signature_requests_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_agency BIGINT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'signature request % cannot be deleted', OLD.id;
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF OLD.status <> 'sent' THEN
            RAISE EXCEPTION 'signature request % is closed (%)', OLD.id, OLD.status;
        END IF;
        IF NEW.agency_id IS DISTINCT FROM OLD.agency_id OR NEW.visit_sheet_id IS DISTINCT FROM OLD.visit_sheet_id
           OR NEW.provider IS DISTINCT FROM OLD.provider OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
           OR NEW.document_sha256 IS DISTINCT FROM OLD.document_sha256 OR NEW.created_at IS DISTINCT FROM OLD.created_at
           OR NEW.signer_name IS DISTINCT FROM OLD.signer_name OR NEW.signer_contact IS DISTINCT FROM OLD.signer_contact
           OR NEW.expires_at IS DISTINCT FROM OLD.expires_at THEN
            RAISE EXCEPTION 'signature request %: only status, outcome and document may change', OLD.id;
        END IF;
    END IF;
    SELECT agency_id INTO v_agency FROM visit_sheets WHERE id = NEW.visit_sheet_id;
    IF v_agency IS DISTINCT FROM NEW.agency_id THEN
        RAISE EXCEPTION 'signature request: visit sheet % belongs to another agency', NEW.visit_sheet_id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_signature_requests_guard
    BEFORE INSERT OR UPDATE OR DELETE ON signature_requests
    FOR EACH ROW EXECUTE FUNCTION signature_requests_guard();

CREATE TABLE signature_request_events (
    id                 BIGSERIAL    PRIMARY KEY,
    request_id         BIGINT       NOT NULL REFERENCES signature_requests(id),
    agency_id          BIGINT       NOT NULL REFERENCES agencies(id),
    from_status        VARCHAR(20),
    to_status          VARCHAR(20)  NOT NULL,
    actor              VARCHAR(40)  NOT NULL,
    details            JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT signature_request_events_details_chk CHECK (jsonb_typeof(details) = 'object')
);

CREATE INDEX idx_signature_request_events_request ON signature_request_events (request_id, id);

CREATE FUNCTION signature_request_events_append_only() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'signature request events are append-only';
END;
$$;

CREATE TRIGGER trg_signature_request_events_append_only
    BEFORE UPDATE OR DELETE ON signature_request_events
    FOR EACH ROW EXECUTE FUNCTION signature_request_events_append_only();
