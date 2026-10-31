-- BLOCCO-6A - foglio visita precompilato, generato da una visita in agenda.
--
-- Una tabella nuova, additiva, immutabile. Nessuna tabella esistente cambia.
--
--   visit_sheets  un PDF per versione: visita (`appointments`, tipo
--       `buyer_visit`), immobile, versione del modello, fotografia dei dati
--       usati, PDF, SHA-256, autore e data. Lo stato della visita AL MOMENTO
--       della generazione e' esplicito:
--           booked     visita prenotata, esito non ancora registrato: il
--                      foglio NON attesta che la visita sia avvenuta;
--           completed  l'operatore ha registrato l'esito in agenda.
--       Un foglio non e' mai «firmato»: la sottoscrizione e' un fatto
--       separato (BLOCCO-6B), registrato altrove.
--
-- La transazione e' del runner.

CREATE TABLE visit_sheets (
    id                   BIGSERIAL    PRIMARY KEY,
    agency_id            BIGINT       NOT NULL REFERENCES agencies(id),
    appointment_id       BIGINT       NOT NULL REFERENCES appointments(id),
    property_id          BIGINT       NOT NULL REFERENCES properties(id),
    sheet_version        INTEGER      NOT NULL,
    template_version     VARCHAR(40)  NOT NULL,
    visit_state          VARCHAR(10)  NOT NULL,
    data_snapshot        JSONB        NOT NULL,
    pdf_bytes            BYTEA        NOT NULL,
    sha256               CHAR(64)     NOT NULL,
    generated_by_user_id BIGINT       NOT NULL REFERENCES operator_users(id),
    generated_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT visit_sheets_version_unq UNIQUE (appointment_id, sheet_version),
    CONSTRAINT visit_sheets_version_chk CHECK (sheet_version >= 1),
    CONSTRAINT visit_sheets_template_chk CHECK (BTRIM(template_version) <> ''),
    CONSTRAINT visit_sheets_state_chk CHECK (visit_state IN ('booked', 'completed')),
    CONSTRAINT visit_sheets_snapshot_chk CHECK (jsonb_typeof(data_snapshot) = 'object'),
    CONSTRAINT visit_sheets_pdf_chk CHECK (octet_length(pdf_bytes) > 0),
    CONSTRAINT visit_sheets_sha_chk CHECK (sha256 ~ '^[0-9a-f]{64}$')
);

CREATE INDEX idx_visit_sheets_agency_property ON visit_sheets (agency_id, property_id, generated_at DESC);

CREATE FUNCTION visit_sheets_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_row RECORD;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'visit sheet % is immutable', OLD.id;
    END IF;
    SELECT agency_id, property_id, appointment_type, status INTO v_row FROM appointments WHERE id = NEW.appointment_id;
    IF v_row.agency_id IS DISTINCT FROM NEW.agency_id OR v_row.property_id IS DISTINCT FROM NEW.property_id
       OR v_row.appointment_type IS DISTINCT FROM 'buyer_visit' THEN
        RAISE EXCEPTION 'visit sheet: appointment % is not a visit of property % in agency %',
            NEW.appointment_id, NEW.property_id, NEW.agency_id;
    END IF;
    IF NEW.visit_state = 'completed' AND v_row.status IS DISTINCT FROM 'completed' THEN
        RAISE EXCEPTION 'visit sheet: the visit % has no recorded outcome', NEW.appointment_id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_visit_sheets_guard
    BEFORE INSERT OR UPDATE OR DELETE ON visit_sheets
    FOR EACH ROW EXECUTE FUNCTION visit_sheets_guard();
