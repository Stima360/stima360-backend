-- BLOCCO-5 - firma elettronica: le parti INDIPENDENTI dal fornitore, pronte
-- per un fornitore reale (nessun fornitore e' scelto ne' integrato).
--
-- Additiva. Nessuna riga cambia significato: le richieste esistenti sono
-- tutte dell'adattatore `simulated` e prendono l'ambiente 'simulation'.
--
--   signature_requests.provider_environment   'simulation' | 'sandbox' |
--       'production'. Una firma VALIDA esiste solo in 'production': una firma
--       ottenuta nell'ambiente di prova di un fornitore resta di prova.
--       `simulated` <-> 'simulation' (vincolo). Immutabile.
--   signature_requests.signature_level        'ses' | 'aes' | 'qes', come
--       DICHIARATO dal fornitore per una richiesta firmata (mai dedotto).
--   signature_requests.signed_at              l'istante di firma dichiarato
--       dal fornitore.
--   signature_requests.audit_trail(+_sha256)  il rapporto di completamento /
--       audit trail del fornitore, con la sua impronta, accanto al documento
--       firmato.
--   provider_reference                        unico per fornitore: un evento
--       del fornitore indica UNA richiesta. Si assegna una volta (da NULL).
--   signature_provider_events                 registro append-only degli eventi
--       ricevuti dal fornitore (webhook): unico per (fornitore, id evento),
--       cosi' un evento ripetuto non si applica due volte; impronta del corpo
--       grezzo, esito dell'elaborazione. Il corpo non si conserva.
--
-- La transazione e' del runner.

ALTER TABLE signature_requests
    ADD COLUMN provider_environment  VARCHAR(12) NOT NULL DEFAULT 'simulation',
    ADD COLUMN signature_level       VARCHAR(3),
    ADD COLUMN signed_at             TIMESTAMPTZ,
    ADD COLUMN audit_trail           BYTEA,
    ADD COLUMN audit_trail_sha256    CHAR(64),
    ADD CONSTRAINT signature_requests_environment_chk
        CHECK (provider_environment IN ('simulation', 'sandbox', 'production')),
    ADD CONSTRAINT signature_requests_simulation_env_chk
        CHECK ((provider = 'simulated') = (provider_environment = 'simulation')),
    ADD CONSTRAINT signature_requests_level_chk
        CHECK (signature_level IS NULL OR (signature_level IN ('ses', 'aes', 'qes') AND status = 'signed')),
    ADD CONSTRAINT signature_requests_signed_evidence_chk
        CHECK (status <> 'signed' OR (received_document IS NOT NULL AND signed_at IS NOT NULL
                                      AND signature_level IS NOT NULL)),
    ADD CONSTRAINT signature_requests_audit_chk
        CHECK ((audit_trail IS NULL) = (audit_trail_sha256 IS NULL)
               AND (audit_trail_sha256 IS NULL OR audit_trail_sha256 ~ '^[0-9a-f]{64}$'));

CREATE UNIQUE INDEX uq_signature_requests_provider_reference
    ON signature_requests (provider, provider_reference) WHERE provider_reference IS NOT NULL;

CREATE OR REPLACE FUNCTION signature_requests_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
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
           OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
           OR NEW.provider_environment IS DISTINCT FROM OLD.provider_environment
           OR (OLD.provider_reference IS NOT NULL AND NEW.provider_reference IS DISTINCT FROM OLD.provider_reference)
           THEN
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

CREATE TABLE signature_provider_events (
    id                  BIGSERIAL    PRIMARY KEY,
    provider            VARCHAR(30)  NOT NULL,
    provider_event_id   VARCHAR(200) NOT NULL,
    event_type          VARCHAR(80)  NOT NULL,
    provider_reference  VARCHAR(120),
    request_id          BIGINT       REFERENCES signature_requests(id),
    agency_id           BIGINT       REFERENCES agencies(id),
    body_sha256         CHAR(64)     NOT NULL,
    outcome             VARCHAR(20)  NOT NULL,
    detail              VARCHAR(200),
    received_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT signature_provider_events_unq UNIQUE (provider, provider_event_id),
    CONSTRAINT signature_provider_events_outcome_chk
        CHECK (outcome IN ('applied', 'no_change', 'unknown_request', 'status_mismatch', 'failed')),
    CONSTRAINT signature_provider_events_request_chk CHECK ((request_id IS NULL) = (agency_id IS NULL)),
    CONSTRAINT signature_provider_events_sha_chk CHECK (body_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT signature_provider_events_not_simulated_chk CHECK (provider <> 'simulated')
);

CREATE FUNCTION signature_provider_events_append_only() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'signature provider events are append-only';
END;
$$;

CREATE TRIGGER trg_signature_provider_events_append_only
    BEFORE UPDATE OR DELETE ON signature_provider_events
    FOR EACH ROW EXECUTE FUNCTION signature_provider_events_append_only();
