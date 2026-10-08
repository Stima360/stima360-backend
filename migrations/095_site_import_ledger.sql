-- SITE-IMPORT-1: il registro dell'importazione dal database del sito (sola lettura)
-- nel database del CRM. Una riga per record del sito: provenienza + id originale.
--
-- Additiva: una tabella nuova, nessuna riga esistente toccata, nessun backfill.
-- Nessuna FK verso `stime`/`stime_dettagliate` del CRM: se un operatore cancella
-- o mette nel Cestino un record importato, il registro ricorda che e' gia' stato
-- importato e il sincronizzatore NON lo ricrea.
CREATE TABLE site_import_records (
    id BIGSERIAL PRIMARY KEY,
    source VARCHAR(40) NOT NULL DEFAULT 'stima360_site'
        CHECK (source ~ '^[a-z][a-z0-9_]{1,39}$'),
    source_table VARCHAR(40) NOT NULL
        CHECK (source_table IN ('stime', 'stime_dettagliate')),
    source_id INTEGER NOT NULL CHECK (source_id > 0),
    -- L'identita' originale e' conservata anche nel CRM: crm_id = source_id.
    crm_id INTEGER,
    agency_id BIGINT REFERENCES agencies(id),
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'imported', 'partial', 'orphan', 'conflict', 'failed')),
    -- Esito di ciascun passo idempotente (bridge, property, pdf, agenda, ...).
    steps JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(steps) = 'object'),
    source_sha256 CHAR(64) CHECK (source_sha256 IS NULL OR source_sha256 ~ '^[a-f0-9]{64}$'),
    source_created_at TIMESTAMP,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error VARCHAR(200),
    imported_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT site_import_records_source_unq UNIQUE (source, source_table, source_id),
    CHECK (crm_id IS NULL OR crm_id = source_id),
    CHECK (status <> 'imported' OR (crm_id IS NOT NULL AND agency_id IS NOT NULL AND imported_at IS NOT NULL))
);
CREATE INDEX site_import_records_retry
    ON site_import_records (source, source_table, status, updated_at)
    WHERE status IN ('pending', 'partial', 'orphan', 'failed');
