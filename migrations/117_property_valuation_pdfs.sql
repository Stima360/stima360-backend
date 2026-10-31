-- BLOCCO-2B - il PDF di una valutazione dell'agente APPROVATA.
--
-- Un artefatto per versione (chiave = la valutazione): generato una volta,
-- poi scaricato sempre con gli stessi byte. Accanto al PDF si conservano
-- l'impronta, chi l'ha generato e quando, la versione del modello e la
-- fotografia dell'intestazione agenzia usata (nomi, recapiti, impronta del
-- logo): un cambio successivo del profilo agenzia non tocca i documenti gia'
-- generati. Una nuova versione approvata ha un artefatto suo.
--
-- Perche' una tabella dedicata: i PDF privati delle stime pubbliche (093)
-- appartengono al percorso del sito; i documenti del portale proprietario
-- (R2) sono pubblicazioni verso il proprietario. Questi PDF interni stanno
-- come i loghi (115): BYTEA con impronta, nella stessa transazione dei dati.
--
-- Immutabile (trigger): nessuna UPDATE, nessuna DELETE.
-- Additiva: una tabella nuova, nessun backfill. La transazione e' del runner.

CREATE TABLE property_valuation_pdfs (
    valuation_id BIGINT PRIMARY KEY REFERENCES property_valuations(id),
    agency_id BIGINT NOT NULL REFERENCES agencies(id),
    property_id BIGINT NOT NULL REFERENCES properties(id),
    valuation_version INTEGER NOT NULL CHECK (valuation_version >= 1),
    template_version VARCHAR(40) NOT NULL CHECK (BTRIM(template_version) <> ''),
    header_snapshot JSONB NOT NULL CHECK (jsonb_typeof(header_snapshot) = 'object'),
    pdf_bytes BYTEA NOT NULL CHECK (octet_length(pdf_bytes) > 0),
    sha256 TEXT NOT NULL CHECK (sha256 ~ '^[a-f0-9]{64}$'),
    generated_by_user_id BIGINT NOT NULL REFERENCES operator_users(id),
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_property_valuation_pdfs_agency_property
    ON property_valuation_pdfs (agency_id, property_id);

CREATE FUNCTION property_valuation_pdfs_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'property valuation PDF % is immutable', OLD.valuation_id;
    END IF;
    -- La valutazione e' della stessa agenzia e dello stesso immobile, alla
    -- stessa versione, e approvata nel momento in cui il PDF viene salvato.
    PERFORM 1 FROM property_valuations
     WHERE id = NEW.valuation_id AND agency_id = NEW.agency_id AND property_id = NEW.property_id
       AND version = NEW.valuation_version AND status = 'approved';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'property valuation PDF requires the approved valuation of the same agency';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_property_valuation_pdfs_guard
    BEFORE INSERT OR UPDATE OR DELETE ON property_valuation_pdfs
    FOR EACH ROW EXECUTE FUNCTION property_valuation_pdfs_guard();
