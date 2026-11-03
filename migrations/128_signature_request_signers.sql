-- DOCUMENSO - i firmatari di una richiesta di firma (cliente E agente), uno per riga.
--
-- Una tabella nuova, additiva. Nessuna tabella esistente cambia: `signature_requests` continua a descrivere la
-- richiesta e il suo esito (stato, documento firmato, audit trail, livello); qui sta CHI deve firmare, con il suo
-- identificativo presso il fornitore, il suo stato personale e il suo collegamento di firma.
--
--   role             'visitor' (il cliente) | 'agent' (l'agente assegnato alla visita). Uno per ruolo e per richiesta.
--   email            l'indirizzo con cui il fornitore identifica il firmatario. Con la distribuzione del fornitore
--                    spenta (sempre, oggi) il fornitore NON gli scrive.
--   signing_url      il collegamento di firma PERSONALE: e' un segreto (chi lo apre firma al posto di quel firmatario).
--                    Resta leggibile solo finche' la richiesta e' aperta: il servizio lo azzera alla chiusura.
--   status           pending -> signed | declined (finali). `signed_at` solo se firmato.
--
-- Nessuna firma e' dichiarata qui: l'esito della richiesta e il livello di firma restano su `signature_requests`.
-- La transazione e' del runner.

CREATE TABLE signature_request_signers (
    id                    BIGSERIAL    PRIMARY KEY,
    request_id            BIGINT       NOT NULL REFERENCES signature_requests(id),
    agency_id             BIGINT       NOT NULL REFERENCES agencies(id),
    role                  VARCHAR(10)  NOT NULL,
    signer_order          SMALLINT     NOT NULL DEFAULT 1,
    name                  VARCHAR(120) NOT NULL,
    email                 VARCHAR(320) NOT NULL,
    operator_user_id      BIGINT       REFERENCES operator_users(id),
    provider_recipient_id VARCHAR(60),
    signing_url           TEXT,
    status                VARCHAR(10)  NOT NULL DEFAULT 'pending',
    signed_at             TIMESTAMPTZ,
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT signature_request_signers_role_unq UNIQUE (request_id, role),
    CONSTRAINT signature_request_signers_role_chk CHECK (role IN ('visitor', 'agent')),
    CONSTRAINT signature_request_signers_status_chk CHECK (status IN ('pending', 'signed', 'declined')),
    CONSTRAINT signature_request_signers_name_chk CHECK (BTRIM(name) <> ''),
    CONSTRAINT signature_request_signers_email_chk CHECK (email ~ '^[^@[:space:]]+@[^@[:space:]]+$'),
    CONSTRAINT signature_request_signers_signed_chk CHECK ((status = 'signed') = (signed_at IS NOT NULL)),
    CONSTRAINT signature_request_signers_agent_chk CHECK (role = 'agent' OR operator_user_id IS NULL),
    CONSTRAINT signature_request_signers_order_chk CHECK (signer_order BETWEEN 1 AND 9)
);

CREATE INDEX idx_signature_request_signers_request ON signature_request_signers (request_id);

CREATE FUNCTION signature_request_signers_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_agency BIGINT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'signature request signer % cannot be deleted', OLD.id;
    END IF;
    SELECT agency_id INTO v_agency FROM signature_requests WHERE id = NEW.request_id;
    IF v_agency IS DISTINCT FROM NEW.agency_id THEN
        RAISE EXCEPTION 'signature request signer: request % belongs to another agency', NEW.request_id;
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF NEW.request_id IS DISTINCT FROM OLD.request_id OR NEW.agency_id IS DISTINCT FROM OLD.agency_id
           OR NEW.role IS DISTINCT FROM OLD.role OR NEW.name IS DISTINCT FROM OLD.name
           OR NEW.email IS DISTINCT FROM OLD.email OR NEW.operator_user_id IS DISTINCT FROM OLD.operator_user_id
           OR NEW.signer_order IS DISTINCT FROM OLD.signer_order OR NEW.created_at IS DISTINCT FROM OLD.created_at
           OR (OLD.provider_recipient_id IS NOT NULL AND NEW.provider_recipient_id IS DISTINCT FROM OLD.provider_recipient_id) THEN
            RAISE EXCEPTION 'signature request signer %: only status, signing link and recipient id may change', OLD.id;
        END IF;
        IF OLD.status <> 'pending' AND NEW.status IS DISTINCT FROM OLD.status THEN
            RAISE EXCEPTION 'signature request signer % is closed (%)', OLD.id, OLD.status;
        END IF;
        IF OLD.status <> 'pending' AND NEW.signed_at IS DISTINCT FROM OLD.signed_at THEN
            RAISE EXCEPTION 'signature request signer %: the signing time is final', OLD.id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_signature_request_signers_guard
    BEFORE INSERT OR UPDATE OR DELETE ON signature_request_signers
    FOR EACH ROW EXECUTE FUNCTION signature_request_signers_guard();
