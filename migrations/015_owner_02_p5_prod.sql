-- OWNER 0.2 - P5
-- Inbox notifiche e preferenze in-app, esclusivamente ambiente PROD.
-- PREPARATA, NON ESEGUIRE senza approvazione separata.

BEGIN;

DO $$
BEGIN
    IF current_database() <> 'stima360_db' THEN
        RAISE EXCEPTION 'OWNER 0.2 P5 bloccata: database corrente %, atteso stima360_db', current_database();
    END IF;

    IF current_schema() <> 'public' THEN
        RAISE EXCEPTION 'OWNER 0.2 P5 bloccata: schema corrente %, atteso public', current_schema();
    END IF;

    IF to_regclass('public.owner_accounts') IS NULL
       OR to_regclass('public.owner_property_access') IS NULL
       OR to_regclass('public.owner_publications') IS NULL
       OR to_regclass('public.owner_feedback') IS NULL
       OR to_regclass('public.owner_shared_documents') IS NULL
       OR to_regclass('public.owner_visit_feedback_publications') IS NULL
       OR to_regclass('public.owner_audit_log') IS NULL THEN
        RAISE EXCEPTION 'OWNER 0.2 P5 bloccata: baseline OWNER P1-P4 incompleta';
    END IF;

    IF to_regclass('public.owner_notifications') IS NOT NULL
       OR to_regclass('public.owner_notification_preferences') IS NOT NULL THEN
        RAISE EXCEPTION 'OWNER 0.2 P5 già applicata o schema parzialmente presente';
    END IF;
END
$$;

CREATE TABLE owner_notifications (
    id BIGSERIAL PRIMARY KEY,
    owner_account_id BIGINT NOT NULL
        REFERENCES owner_accounts(id) ON DELETE CASCADE,
    property_id BIGINT NOT NULL
        REFERENCES properties(id) ON DELETE CASCADE,
    notification_type VARCHAR(40) NOT NULL,
    title VARCHAR(200) NOT NULL,
    body TEXT NOT NULL,
    target_type VARCHAR(40) NOT NULL,
    target_id BIGINT NOT NULL,
    idempotency_key VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    read_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '365 days'),
    CONSTRAINT owner_notifications_type_chk CHECK (
        notification_type IN (
            'publication_published',
            'visit_feedback_published',
            'shared_document_published',
            'request_handled'
        )
    ),
    CONSTRAINT owner_notifications_target_type_chk CHECK (
        target_type IN (
            'owner_publication',
            'owner_visit_feedback',
            'owner_shared_document',
            'owner_feedback'
        )
    ),
    CONSTRAINT owner_notifications_title_chk
        CHECK (BTRIM(title) <> ''),
    CONSTRAINT owner_notifications_body_chk
        CHECK (BTRIM(body) <> '' AND CHAR_LENGTH(body) <= 5000),
    CONSTRAINT owner_notifications_idempotency_chk
        CHECK (BTRIM(idempotency_key) <> ''),
    CONSTRAINT owner_notifications_read_time_chk
        CHECK (read_at IS NULL OR read_at >= created_at),
    CONSTRAINT owner_notifications_expiry_chk
        CHECK (expires_at > created_at),
    CONSTRAINT owner_notifications_idempotency_unique
        UNIQUE (idempotency_key)
);

CREATE INDEX idx_owner_notifications_account_created
    ON owner_notifications(owner_account_id, created_at DESC, id DESC);

CREATE INDEX idx_owner_notifications_account_unread
    ON owner_notifications(owner_account_id, created_at DESC, id DESC)
    WHERE read_at IS NULL;

CREATE INDEX idx_owner_notifications_property_created
    ON owner_notifications(property_id, created_at DESC, id DESC);

CREATE TABLE owner_notification_preferences (
    owner_account_id BIGINT PRIMARY KEY
        REFERENCES owner_accounts(id) ON DELETE CASCADE,
    in_app_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    publication_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    visit_feedback_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    document_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    request_update_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMIT;
