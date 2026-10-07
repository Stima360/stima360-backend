-- F07: private reports for NEW submissions. No historical import/backfill.
-- The migration runner owns the transaction. No public filesystem/object URL.
CREATE TABLE stima_pdf_artifacts (
    stima_id INTEGER PRIMARY KEY REFERENCES stime(id) ON DELETE CASCADE,
    agency_id BIGINT NOT NULL REFERENCES agencies(id),
    render_payload JSONB NOT NULL CHECK (jsonb_typeof(render_payload) = 'object'),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','ready','failed')),
    pdf_bytes BYTEA,
    sha256 TEXT,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error VARCHAR(120),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK ((status = 'ready' AND pdf_bytes IS NOT NULL AND octet_length(pdf_bytes) > 0
            AND sha256 IS NOT NULL AND sha256 ~ '^[a-f0-9]{64}$')
           OR (status <> 'ready' AND pdf_bytes IS NULL AND sha256 IS NULL))
);

CREATE FUNCTION enforce_stima_pdf_agency() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    PERFORM 1 FROM stime WHERE id = NEW.stima_id AND agency_id = NEW.agency_id FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'stima PDF agency mismatch';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER stima_pdf_agency_guard BEFORE INSERT OR UPDATE OF stima_id, agency_id
ON stima_pdf_artifacts FOR EACH ROW EXECUTE FUNCTION enforce_stima_pdf_agency();
