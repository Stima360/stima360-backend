-- P26-1 agency and operator identity foundation.
--
-- Additive, transactional, idempotent. This migration creates the four
-- identity tables and exactly one application row: the Default Agency.
-- It alters no existing table, touches no CORE table, and seeds no operator.
--
-- Scope boundary, deliberate:
--
--   * No operator user is created here. Operators carry credentials, and a
--     credential in a migration file is a credential in the repository and in
--     every environment the file is applied to. Seeding is done by
--     scripts/p26_seed_agencies_test.py, which is TEST-guarded and reads
--     passwords from the environment.
--
--   * No schema_migrations row is written here. scripts/p26_migrate.py
--     register() owns that insert: it supplies checksum_up, checksum_down,
--     down_available, transactional and execution_ms, none of which a file can
--     know about itself. Migration 026 sets the same precedent. A self-insert
--     would collide with register() on the version primary key.
--
-- Identity model, from the approved design:
--
--   agencies             one row per real agency; slug is the stable key
--   operator_users       one row per human; email is GLOBALLY unique
--   agency_memberships   which operator acts in which agency, in which role
--   operator_sessions    server-side sessions; only the token hash is stored
--
-- is_platform_admin lives on operator_users, never as a fourth agency role: a
-- platform admin has no membership row at all, which is precisely what
-- separates platform administration from the agency hierarchy.
--
-- Transaction ownership: this file opens no transaction and commits nothing.
-- scripts/p26_migrate.py executes this body, writes the schema_migrations row
-- through register(), and commits both in one transaction. A BEGIN/COMMIT here
-- would commit the schema change first and leave the ledger insert in a
-- second transaction, so a failure between them would change the schema
-- without recording it. 027 is the first migration under that rule; see
-- docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md. The paired _down.sql keeps its
-- own BEGIN/COMMIT, because it is executed manually rather than by the runner.

-- ---------------------------------------------------------------------------
-- agencies
--
-- settings holds non-sensitive display configuration only. P26-1 defines no
-- key in it and reads it nowhere; it exists so a later phase has a home for
-- preferences without another migration. Nothing confidential belongs here.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agencies (
    id         BIGSERIAL    PRIMARY KEY,
    name       VARCHAR(200) NOT NULL,
    slug       VARCHAR(100) NOT NULL,
    status     VARCHAR(20)  NOT NULL DEFAULT 'active',
    settings   JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT agencies_name_chk CHECK (BTRIM(name) <> ''),

    -- The slug is resolved by the public STIMA bridge and by the backfill in
    -- 029, so its shape is constrained rather than trusted.
    CONSTRAINT agencies_slug_chk CHECK (slug ~ '^[a-z0-9]([a-z0-9-]*[a-z0-9])?$'),

    CONSTRAINT agencies_status_chk CHECK (status IN ('active', 'suspended', 'archived')),

    CONSTRAINT agencies_slug_unq UNIQUE (slug)
);

-- ---------------------------------------------------------------------------
-- operator_users
--
-- email_normalized is GLOBALLY unique, not unique per agency. Login happens
-- before any agency context exists: the submitted credential is an email and
-- nothing else. A per-agency key would make the login lookup ambiguous and
-- would force an agency selector into an unauthenticated form, disclosing the
-- existence of agencies to anyone who can reach the login page.
--
-- password_hash is constrained to the PBKDF2 encoding produced by
-- operator_auth/security.py, so a plaintext password cannot be written even by
-- a direct SQL mistake.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS operator_users (
    id                BIGSERIAL    PRIMARY KEY,
    email             VARCHAR(320) NOT NULL,
    email_normalized  VARCHAR(320) NOT NULL,
    password_hash     TEXT         NOT NULL,
    first_name        VARCHAR(100),
    last_name         VARCHAR(100),
    status            VARCHAR(20)  NOT NULL DEFAULT 'active',
    is_platform_admin BOOLEAN      NOT NULL DEFAULT FALSE,
    last_login_at     TIMESTAMPTZ,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT operator_users_email_chk CHECK (BTRIM(email) <> ''),

    CONSTRAINT operator_users_status_chk CHECK (status IN ('invited', 'active', 'disabled')),

    CONSTRAINT operator_users_hash_chk CHECK (password_hash LIKE 'pbkdf2_sha256$%'),

    CONSTRAINT operator_users_email_unq UNIQUE (email_normalized)
);

-- ---------------------------------------------------------------------------
-- agency_memberships
--
-- Kept separate from operator_users so that multi-agency membership becomes a
-- row change rather than a schema demolition.
--
-- Exactly one UNIQUE constraint. An earlier draft proposed a second,
-- UNIQUE (agency_id, operator_user_id, id), on the belief that the composite
-- foreign keys in 030 needed it. They do not: PostgreSQL satisfies
-- FOREIGN KEY (agency_id, assigned_agent_id)
--   REFERENCES agency_memberships (agency_id, operator_user_id)
-- from a UNIQUE on exactly those two columns. A superset including id would
-- not satisfy the FK at all, and would add an index nothing reads.
--
-- agency_id is ON DELETE RESTRICT: an agency holding memberships must not
-- disappear by accident. operator_user_id is ON DELETE CASCADE: removing a
-- person removes their memberships, which are meaningless without them.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agency_memberships (
    id               BIGSERIAL   PRIMARY KEY,
    agency_id        BIGINT      NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    operator_user_id BIGINT      NOT NULL REFERENCES operator_users(id) ON DELETE CASCADE,
    role             VARCHAR(20) NOT NULL,
    status           VARCHAR(20) NOT NULL DEFAULT 'active',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT agency_memberships_role_chk CHECK (role IN ('agency_owner', 'agency_admin', 'agent')),

    CONSTRAINT agency_memberships_status_chk CHECK (status IN ('active', 'suspended', 'revoked')),

    CONSTRAINT agency_memberships_unq UNIQUE (agency_id, operator_user_id)
);

-- P26-1 permits at most one ACTIVE membership per operator. Dropping this one
-- index is the whole of the future multi-agency change.
CREATE UNIQUE INDEX IF NOT EXISTS uq_agency_memberships_single_active
    ON agency_memberships (operator_user_id) WHERE status = 'active';

-- An agency has exactly one active owner.
CREATE UNIQUE INDEX IF NOT EXISTS uq_agency_memberships_single_owner
    ON agency_memberships (agency_id) WHERE role = 'agency_owner' AND status = 'active';

-- ---------------------------------------------------------------------------
-- operator_sessions
--
-- Carries NO agency_id, deliberately. Membership, membership status, user
-- status and agency status are re-read on every request. Pinning the agency at
-- login would leave a suspended membership usable until the session expired;
-- resolving per request makes revocation take effect on the next call.
--
-- Only the SHA-256 of the session token is persisted. The raw token exists
-- solely in the Set-Cookie header and in the browser.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS operator_sessions (
    id               BIGSERIAL   PRIMARY KEY,
    operator_user_id BIGINT      NOT NULL REFERENCES operator_users(id) ON DELETE CASCADE,
    token_hash       CHAR(64)    NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at       TIMESTAMPTZ NOT NULL,
    revoked_at       TIMESTAMPTZ,

    CONSTRAINT operator_sessions_expiry_chk CHECK (expires_at > created_at),

    CONSTRAINT operator_sessions_hash_chk CHECK (token_hash ~ '^[0-9a-f]{64}$'),

    CONSTRAINT operator_sessions_token_unq UNIQUE (token_hash)
);

-- ---------------------------------------------------------------------------
-- Indexes.
--
-- operator_sessions.token_hash needs none of its own: UNIQUE creates one, and
-- it is the only lookup key on the session read path.
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_operator_sessions_user
    ON operator_sessions (operator_user_id);

CREATE INDEX IF NOT EXISTS idx_operator_sessions_expires_at
    ON operator_sessions (expires_at);

CREATE INDEX IF NOT EXISTS idx_agency_memberships_operator
    ON agency_memberships (operator_user_id);

CREATE INDEX IF NOT EXISTS idx_agency_memberships_agency
    ON agency_memberships (agency_id, role, status);

-- ---------------------------------------------------------------------------
-- The Default Agency.
--
-- Every legacy record is assigned to this agency by the backfill in 029, and
-- every public estimation routes to it. It must therefore exist before any
-- CORE scoping migration runs.
--
-- Identified by slug, never by id = 1: a restore, a re-seed or a future
-- environment could allocate a different serial, and a hard-coded id would
-- silently attach legacy data to the wrong agency.
--
-- The WHERE NOT EXISTS clause keeps re-execution a no-op.
-- ---------------------------------------------------------------------------
INSERT INTO agencies (name, slug, status, settings)
SELECT 'STIMA360', 'stima360', 'active', '{}'::jsonb
WHERE NOT EXISTS (SELECT 1 FROM agencies WHERE slug = 'stima360');
