-- BLOCCO-1B - il logo dell'agenzia per i report.
--
-- I DATI dell'agenzia (nome per i report, ragione sociale, partita IVA, codice
-- fiscale, sede legale, recapiti) non hanno bisogno di schema: vivono in
-- `agencies.settings` (027) sotto la chiave `report_profile`, accanto a
-- timezone e locale. Questa migrazione aggiunge SOLO il logo, un file per
-- agenzia.
--
-- Perche' una tabella e non lo storage documentale del proprietario (R2,
-- owner/document_storage.py): quello e' legato ai documenti condivisi con i
-- proprietari, ai loro permessi e a una configurazione esterna. Un logo di al
-- massimo 2 MB, uno per agenzia, sta come i PDF privati delle stime (093):
-- BYTEA con impronta, nella stessa transazione dei dati.
--
-- Additiva: una tabella nuova, nessun backfill, nessuna riga esistente
-- toccata. Un'agenzia senza riga qui semplicemente non ha un logo.
-- La transazione e' del runner.

CREATE TABLE agency_logos (
    agency_id BIGINT PRIMARY KEY REFERENCES agencies(id),
    content_type TEXT NOT NULL CHECK (content_type IN ('image/png', 'image/jpeg')),
    width INTEGER NOT NULL CHECK (width BETWEEN 1 AND 4096),
    height INTEGER NOT NULL CHECK (height BETWEEN 1 AND 4096),
    image_bytes BYTEA NOT NULL CHECK (octet_length(image_bytes) BETWEEN 1 AND 2097152),
    sha256 TEXT NOT NULL CHECK (sha256 ~ '^[a-f0-9]{64}$'),
    uploaded_by VARCHAR(200),
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
