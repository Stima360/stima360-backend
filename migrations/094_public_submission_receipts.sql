-- F04/F06: technical recovery ledger for NEW public requests only.
-- No historical import. The migration runner owns the transaction.
CREATE TABLE public_submission_receipts (
    request_id UUID PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('quick','detail')),
    proof_sha256 CHAR(64) NOT NULL CHECK (proof_sha256 ~ '^[a-f0-9]{64}$'),
    payload_sha256 CHAR(64) NOT NULL CHECK (payload_sha256 ~ '^[a-f0-9]{64}$'),
    request_payload JSONB NOT NULL CHECK (jsonb_typeof(request_payload) = 'object'),
    stima_id INTEGER REFERENCES stime(id) ON DELETE CASCADE,
    detail_id INTEGER UNIQUE REFERENCES stime_dettagliate(id) ON DELETE CASCADE,
    agency_id BIGINT REFERENCES agencies(id),
    checkpoints JSONB NOT NULL DEFAULT '{}'::jsonb,
    frozen JSONB NOT NULL DEFAULT '{}'::jsonb,
    response_payload JSONB,
    voided_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'received' CHECK (status IN ('received','partial','attention','completed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '7 days',
    CHECK ((stima_id IS NULL AND agency_id IS NULL AND detail_id IS NULL)
           OR (stima_id IS NOT NULL AND agency_id IS NOT NULL)),
    CHECK (kind <> 'quick' OR detail_id IS NULL),
    CHECK (jsonb_typeof(checkpoints) = 'object' AND jsonb_typeof(frozen) = 'object')
);
CREATE INDEX public_submission_receipts_agency_status ON public_submission_receipts(agency_id,status,created_at);

CREATE FUNCTION enforce_public_submission_parent() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.stima_id IS NOT NULL THEN
        PERFORM 1 FROM stime WHERE id=NEW.stima_id AND agency_id=NEW.agency_id FOR SHARE;
        IF NOT FOUND THEN RAISE EXCEPTION 'submission agency mismatch'; END IF;
    END IF;
    IF NEW.detail_id IS NOT NULL THEN
        PERFORM 1 FROM stime_dettagliate WHERE id=NEW.detail_id
            AND stima_id=NEW.stima_id AND agency_id=NEW.agency_id FOR SHARE;
        IF NOT FOUND THEN RAISE EXCEPTION 'submission detail mismatch'; END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER public_submission_parent_guard BEFORE INSERT OR UPDATE OF stima_id,detail_id,agency_id
ON public_submission_receipts FOR EACH ROW EXECUTE FUNCTION enforce_public_submission_parent();

-- Keep a technical tombstone when an already-authorized hard purge removes a
-- parent. Erase the private snapshot, but never recycle the request identity.
CREATE FUNCTION void_public_submission_receipt() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_TABLE_NAME = 'stime' THEN
        UPDATE public_submission_receipts SET voided_at=NOW(), stima_id=NULL, detail_id=NULL,
            agency_id=NULL, request_payload='{}', checkpoints='{}', frozen='{}',
            response_payload=NULL, status='attention', updated_at=NOW()
        WHERE stima_id=OLD.id;
    ELSE
        UPDATE public_submission_receipts SET voided_at=NOW(), detail_id=NULL,
            request_payload='{}', checkpoints='{}', frozen='{}',
            response_payload=NULL, status='attention', updated_at=NOW()
        WHERE detail_id=OLD.id;
    END IF;
    RETURN OLD;
END;
$$;
CREATE TRIGGER public_submission_stima_purge BEFORE DELETE ON stime
FOR EACH ROW EXECUTE FUNCTION void_public_submission_receipt();
CREATE TRIGGER public_submission_detail_purge BEFORE DELETE ON stime_dettagliate
FOR EACH ROW EXECUTE FUNCTION void_public_submission_receipt();
