-- IMPORTAZIONE RUBRICA (fase B) - tutti i numeri di un contatto, contatti
-- riservati, registro delle importazioni.
--
--   contacts.private_user_id   il contatto e' RISERVATO a quell'operatore: lo
--       vede solo lui (titolare e amministratori compresi: e' la regola scelta
--       per la rubrica importata dal telefono). NULL = contatto dell'agenzia,
--       come tutti quelli di oggi. Si condivide con l'agenzia solo per scelta
--       di chi l'ha importato.
--   contact_phones             TUTTI i numeri di un contatto (la rubrica ne
--       porta anche tre o quattro): valore come scritto e valore normalizzato
--       (formato internazionale, solo cifre). `phone` e `secondary_phone` del
--       contatto restano i primi due; il riconoscimento WhatsApp
--       (inbound_messages/contact_match.py) guarda anche qui.
--   contact_imports            un'importazione: chi, quando, da che file;
--       le voci analizzate restano solo finche' e' in anteprima (`entries`),
--       dopo la conferma resta il riepilogo (`summary`). Nessun invio parte
--       da un'importazione.
--
-- Additiva, nessun backfill. La transazione e' del runner.

ALTER TABLE contacts ADD COLUMN private_user_id BIGINT REFERENCES operator_users(id);
CREATE INDEX idx_contacts_private_user ON contacts (private_user_id) WHERE private_user_id IS NOT NULL;

CREATE TABLE contact_phones (
    id                BIGSERIAL    PRIMARY KEY,
    agency_id         BIGINT       NOT NULL REFERENCES agencies(id),
    contact_id        BIGINT       NOT NULL,
    phone             TEXT         NOT NULL,
    phone_normalized  TEXT         NOT NULL,
    label             VARCHAR(30),
    position          SMALLINT     NOT NULL DEFAULT 0,
    source            VARCHAR(20)  NOT NULL DEFAULT 'import',
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT contact_phones_unq UNIQUE (contact_id, phone_normalized),
    CONSTRAINT contact_phones_normalized_chk CHECK (phone_normalized ~ '^[0-9]{6,15}$'),
    CONSTRAINT contact_phones_phone_chk CHECK (BTRIM(phone) <> ''),
    CONSTRAINT contact_phones_source_chk CHECK (source IN ('import', 'manual', 'whatsapp')),
    -- Il numero e' SEMPRE del contatto della stessa agenzia.
    CONSTRAINT contact_phones_contact_fk FOREIGN KEY (agency_id, contact_id) REFERENCES contacts (agency_id, id)
        ON DELETE CASCADE
);

CREATE INDEX idx_contact_phones_agency_number ON contact_phones (agency_id, phone_normalized);
CREATE INDEX idx_contact_phones_contact ON contact_phones (contact_id, position, id);

CREATE TABLE contact_imports (
    id            BIGSERIAL    PRIMARY KEY,
    agency_id     BIGINT       NOT NULL REFERENCES agencies(id),
    user_id       BIGINT       NOT NULL REFERENCES operator_users(id),
    source        VARCHAR(20)  NOT NULL DEFAULT 'vcf',
    file_name     VARCHAR(200),
    status        VARCHAR(10)  NOT NULL DEFAULT 'preview',
    entries       JSONB,
    summary       JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    committed_at  TIMESTAMPTZ,
    CONSTRAINT contact_imports_status_chk CHECK (status IN ('preview', 'done', 'abandoned')),
    CONSTRAINT contact_imports_source_chk CHECK (source IN ('vcf', 'csv', 'picker', 'whatsapp')),
    CONSTRAINT contact_imports_summary_chk CHECK (jsonb_typeof(summary) = 'object'),
    CONSTRAINT contact_imports_entries_chk CHECK (entries IS NULL OR jsonb_typeof(entries) = 'array'),
    CONSTRAINT contact_imports_done_chk CHECK ((status = 'done') = (committed_at IS NOT NULL))
);

CREATE INDEX idx_contact_imports_agency ON contact_imports (agency_id, created_at DESC);
