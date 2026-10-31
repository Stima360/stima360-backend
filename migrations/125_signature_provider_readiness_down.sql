-- Rientro della 125. RIFIUTA se esistono dati di un fornitore non simulato:
-- eventi ricevuti, richieste di un ambiente diverso da 'simulation', livelli,
-- audit trail. Conservarli o ripristinare da un backup verificato.
--
-- La transazione e' del runner.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM signature_provider_events)
       OR EXISTS (SELECT 1 FROM signature_requests
                   WHERE provider_environment <> 'simulation' OR signature_level IS NOT NULL
                      OR signed_at IS NOT NULL OR audit_trail IS NOT NULL) THEN
        RAISE EXCEPTION '125 down refused: real provider data exists';
    END IF;
END;
$$;

DROP TRIGGER trg_signature_provider_events_append_only ON signature_provider_events;
DROP FUNCTION signature_provider_events_append_only();
DROP TABLE signature_provider_events;

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

DROP INDEX uq_signature_requests_provider_reference;
ALTER TABLE signature_requests
    DROP CONSTRAINT signature_requests_audit_chk,
    DROP CONSTRAINT signature_requests_signed_evidence_chk,
    DROP CONSTRAINT signature_requests_level_chk,
    DROP CONSTRAINT signature_requests_simulation_env_chk,
    DROP CONSTRAINT signature_requests_environment_chk,
    DROP COLUMN audit_trail_sha256,
    DROP COLUMN audit_trail,
    DROP COLUMN signed_at,
    DROP COLUMN signature_level,
    DROP COLUMN provider_environment;
