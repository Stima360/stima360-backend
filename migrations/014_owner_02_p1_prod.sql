-- OWNER 0.2 - P1
-- Migration additiva per ambiente PROD בלבד.
-- NON ESEGUIRE senza approvazione separata.

BEGIN;

-- Safety gate: questa versione è destinata esclusivamente al database PROD.
DO $$
BEGIN
    IF current_database() <> 'stima360_db' THEN
        RAISE EXCEPTION 'OWNER 0.2 P1 bloccata: database corrente %, atteso stima360_db', current_database();
    END IF;

    IF current_schema() <> 'public' THEN
        RAISE EXCEPTION 'OWNER 0.2 P1 bloccata: schema corrente %, atteso public', current_schema();
    END IF;

    IF to_regclass('public.owner_accounts') IS NULL
       OR to_regclass('public.owner_feedback') IS NULL
       OR to_regclass('public.owner_publications') IS NULL
       OR to_regclass('public.property_documents') IS NULL
       OR to_regclass('public.property_visits') IS NULL THEN
        RAISE EXCEPTION 'OWNER 0.2 P1 bloccata: baseline OWNER/PROPERTY incompleta';
    END IF;

    IF to_regclass('public.owner_shared_documents') IS NOT NULL
       OR to_regclass('public.owner_document_reads') IS NOT NULL
       OR to_regclass('public.owner_visit_feedback_publications') IS NOT NULL THEN
        RAISE EXCEPTION 'OWNER 0.2 P1 già applicata o schema parzialmente presente';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND (
              (table_name = 'owner_feedback' AND column_name IN ('availability_from','availability_to','public_response'))
              OR
              (table_name = 'owner_publications' AND column_name = 'acknowledgement_required')
          )
    ) THEN
        RAISE EXCEPTION 'OWNER 0.2 P1 bloccata: una o più colonne target sono già presenti';
    END IF;
END
$$;

CREATE TABLE owner_shared_documents (
    id BIGSERIAL PRIMARY KEY,
    property_document_id BIGINT NOT NULL
        REFERENCES property_documents(id) ON DELETE RESTRICT,
    owner_account_id BIGINT
        REFERENCES owner_accounts(id) ON DELETE CASCADE,
    public_title VARCHAR(200) NOT NULL,
    public_document_type VARCHAR(50) NOT NULL,
    version_number INTEGER NOT NULL DEFAULT 1,
    status VARCHAR(20) NOT NULL DEFAULT 'draft',
    published_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    acknowledgement_required BOOLEAN NOT NULL DEFAULT FALSE,
    supersedes_shared_document_id BIGINT
        REFERENCES owner_shared_documents(id) ON DELETE RESTRICT,
    superseded_by_shared_document_id BIGINT
        REFERENCES owner_shared_documents(id) ON DELETE RESTRICT,
    revoked_at TIMESTAMPTZ,
    revoked_by VARCHAR(200),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by VARCHAR(200),
    archived_at TIMESTAMPTZ,
    CONSTRAINT owner_shared_documents_title_chk
        CHECK (BTRIM(public_title) <> ''),
    CONSTRAINT owner_shared_documents_type_chk
        CHECK (BTRIM(public_document_type) <> ''),
    CONSTRAINT owner_shared_documents_version_chk
        CHECK (version_number >= 1),
    CONSTRAINT owner_shared_documents_status_chk
        CHECK (status IN ('draft','published','revoked','archived')),
    CONSTRAINT owner_shared_documents_published_chk
        CHECK (status <> 'published' OR published_at IS NOT NULL),
    CONSTRAINT owner_shared_documents_revoked_chk
        CHECK (status <> 'revoked' OR revoked_at IS NOT NULL),
    CONSTRAINT owner_shared_documents_expiry_chk
        CHECK (expires_at IS NULL OR published_at IS NULL OR expires_at > published_at),
    CONSTRAINT owner_shared_documents_supersedes_self_chk
        CHECK (supersedes_shared_document_id IS NULL OR supersedes_shared_document_id <> id),
    CONSTRAINT owner_shared_documents_superseded_by_self_chk
        CHECK (superseded_by_shared_document_id IS NULL OR superseded_by_shared_document_id <> id)
);

CREATE UNIQUE INDEX uq_owner_shared_documents_global_version
    ON owner_shared_documents(property_document_id, version_number)
    WHERE owner_account_id IS NULL;

CREATE UNIQUE INDEX uq_owner_shared_documents_account_version
    ON owner_shared_documents(property_document_id, owner_account_id, version_number)
    WHERE owner_account_id IS NOT NULL;

CREATE INDEX idx_owner_shared_documents_source_status
    ON owner_shared_documents(property_document_id, status, published_at DESC);

CREATE INDEX idx_owner_shared_documents_account_status
    ON owner_shared_documents(owner_account_id, status, published_at DESC)
    WHERE owner_account_id IS NOT NULL;

CREATE TABLE owner_document_reads (
    id BIGSERIAL PRIMARY KEY,
    shared_document_id BIGINT NOT NULL
        REFERENCES owner_shared_documents(id) ON DELETE CASCADE,
    owner_account_id BIGINT NOT NULL
        REFERENCES owner_accounts(id) ON DELETE CASCADE,
    first_viewed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_viewed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    view_count INTEGER NOT NULL DEFAULT 1,
    acknowledged_at TIMESTAMPTZ,
    CONSTRAINT owner_document_reads_unique
        UNIQUE (shared_document_id, owner_account_id),
    CONSTRAINT owner_document_reads_count_chk
        CHECK (view_count >= 1),
    CONSTRAINT owner_document_reads_time_chk
        CHECK (last_viewed_at >= first_viewed_at)
);

CREATE INDEX idx_owner_document_reads_account
    ON owner_document_reads(owner_account_id, last_viewed_at DESC);

CREATE TABLE owner_visit_feedback_publications (
    id BIGSERIAL PRIMARY KEY,
    property_visit_id BIGINT NOT NULL
        REFERENCES property_visits(id) ON DELETE RESTRICT,
    owner_account_id BIGINT
        REFERENCES owner_accounts(id) ON DELETE CASCADE,
    category VARCHAR(40) NOT NULL,
    public_summary TEXT NOT NULL,
    sentiment VARCHAR(20),
    version_number INTEGER NOT NULL DEFAULT 1,
    status VARCHAR(20) NOT NULL DEFAULT 'draft',
    published_at TIMESTAMPTZ,
    supersedes_feedback_publication_id BIGINT
        REFERENCES owner_visit_feedback_publications(id) ON DELETE RESTRICT,
    superseded_by_feedback_publication_id BIGINT
        REFERENCES owner_visit_feedback_publications(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by VARCHAR(200),
    archived_at TIMESTAMPTZ,
    CONSTRAINT owner_visit_feedback_category_chk
        CHECK (category IN ('price','state','layout','location','accessories','general')),
    CONSTRAINT owner_visit_feedback_summary_chk
        CHECK (BTRIM(public_summary) <> '' AND CHAR_LENGTH(public_summary) <= 5000),
    CONSTRAINT owner_visit_feedback_sentiment_chk
        CHECK (sentiment IS NULL OR sentiment IN ('positive','neutral','negative','mixed')),
    CONSTRAINT owner_visit_feedback_version_chk
        CHECK (version_number >= 1),
    CONSTRAINT owner_visit_feedback_status_chk
        CHECK (status IN ('draft','published','archived')),
    CONSTRAINT owner_visit_feedback_published_chk
        CHECK (status <> 'published' OR published_at IS NOT NULL),
    CONSTRAINT owner_visit_feedback_supersedes_self_chk
        CHECK (supersedes_feedback_publication_id IS NULL OR supersedes_feedback_publication_id <> id),
    CONSTRAINT owner_visit_feedback_superseded_by_self_chk
        CHECK (superseded_by_feedback_publication_id IS NULL OR superseded_by_feedback_publication_id <> id)
);

CREATE UNIQUE INDEX uq_owner_visit_feedback_global_version
    ON owner_visit_feedback_publications(property_visit_id, category, version_number)
    WHERE owner_account_id IS NULL;

CREATE UNIQUE INDEX uq_owner_visit_feedback_account_version
    ON owner_visit_feedback_publications(property_visit_id, owner_account_id, category, version_number)
    WHERE owner_account_id IS NOT NULL;

CREATE INDEX idx_owner_visit_feedback_visit_status
    ON owner_visit_feedback_publications(property_visit_id, status, published_at DESC);

CREATE INDEX idx_owner_visit_feedback_account_status
    ON owner_visit_feedback_publications(owner_account_id, status, published_at DESC)
    WHERE owner_account_id IS NOT NULL;

-- Estensione minima delle richieste proprietario già rappresentate da owner_feedback.
ALTER TABLE owner_feedback
    ADD COLUMN availability_from TIMESTAMPTZ,
    ADD COLUMN availability_to TIMESTAMPTZ,
    ADD COLUMN public_response TEXT;

ALTER TABLE owner_feedback
    ADD CONSTRAINT owner_feedback_availability_chk
        CHECK (
            availability_from IS NULL
            OR availability_to IS NULL
            OR availability_to > availability_from
        ),
    ADD CONSTRAINT owner_feedback_public_response_chk
        CHECK (public_response IS NULL OR CHAR_LENGTH(public_response) <= 5000);

-- Ampliamento semantico, senza nuova tabella richieste.
ALTER TABLE owner_feedback
    DROP CONSTRAINT owner_feedback_feedback_type_check;

ALTER TABLE owner_feedback
    ADD CONSTRAINT owner_feedback_feedback_type_check
        CHECK (feedback_type IN (
            'contact_request',
            'correction_request',
            'general_message',
            'strategy_feedback',
            'price_review',
            'availability_update',
            'document_question'
        ));

-- Il versioning esiste già in OWNER 0.1; si aggiunge solo il requisito di presa visione.
ALTER TABLE owner_publications
    ADD COLUMN acknowledgement_required BOOLEAN NOT NULL DEFAULT FALSE;

COMMIT;
