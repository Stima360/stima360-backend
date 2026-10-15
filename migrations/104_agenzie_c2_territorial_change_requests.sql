-- AGENZIE-TERRITORI C2: proposte di modifica TERRITORIALI con approvazione del
-- Super Admin.
--
-- Additiva. Nessun backfill, nessuna tabella esistente modificata. Due tabelle:
--
--   1. `property_territorial_change_requests` - la PROPOSTA del Supervisore
--      dell'agenzia territorialmente competente su un immobile di un'ALTRA
--      agenzia. Nasce `pending`; l'immobile non cambia finche' il Super Admin
--      non approva. Contiene: immobile, agenzia d'origine, agenzia competente
--      e territorio (decisi dal server), proponente, data, campi richiesti con
--      valore precedente e nuovo (`changes` = {campo: {"old", "new"}}),
--      impronta della versione dell'immobile su cui e' costruita
--      (`base_fingerprint`), decisione (chi, quando, motivazione) ed esito
--      dell'applicazione. Mai cancellata: il guard rifiuta DELETE, rende
--      immutabili i dati della proposta e definitiva la decisione.
--      Una sola proposta `pending` per immobile (indice unico parziale).
--
--   2. `property_territorial_change_events` - il REGISTRO, append-only: una
--      riga per ogni passaggio (proposta, approvazione con applicazione,
--      rifiuto, approvazione bloccata da una verifica). Nessun UPDATE/DELETE.
--
-- `property_id` NON ha chiave esterna (come `building_deletion_requests`.
-- `building_id`): lo storico deve sopravvivere a una cancellazione definitiva
-- dell'immobile (CESTINO-GENERALE-3), e una FK la bloccherebbe o la
-- cancellerebbe. `property_label` conserva come si chiamava. Le agenzie sono
-- FK ON DELETE RESTRICT come ogni riferimento d'agenzia da P26; le persone
-- ON DELETE SET NULL come in 101.
--
-- Transaction ownership: il runner possiede la transazione UP (versione >= 027),
-- quindi questo file non ha BEGIN/COMMIT.

CREATE TABLE IF NOT EXISTS property_territorial_change_requests (
    id                     BIGSERIAL    PRIMARY KEY,
    property_id            BIGINT       NOT NULL,
    property_label         TEXT         NOT NULL,
    origin_agency_id       BIGINT       NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    competent_agency_id    BIGINT       NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,
    territory_id           BIGINT       NOT NULL,
    territory_label        TEXT         NOT NULL,
    status                 VARCHAR(12)  NOT NULL DEFAULT 'pending',
    changes                JSONB        NOT NULL,
    base_fingerprint       VARCHAR(64)  NOT NULL,
    request_note           TEXT,
    requested_by_user_id   BIGINT       REFERENCES operator_users(id) ON DELETE SET NULL,
    requested_by_role      VARCHAR(30)  NOT NULL,
    requested_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    decided_by_user_id     BIGINT       REFERENCES operator_users(id) ON DELETE SET NULL,
    decided_at             TIMESTAMPTZ,
    decision_note          TEXT,
    applied_at             TIMESTAMPTZ,
    apply_result           JSONB,
    updated_at             TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT property_territorial_change_requests_status_chk
        CHECK (status IN ('pending', 'approved', 'rejected')),
    CONSTRAINT property_territorial_change_requests_agencies_chk
        CHECK (origin_agency_id <> competent_agency_id),
    CONSTRAINT property_territorial_change_requests_changes_chk
        CHECK (jsonb_typeof(changes) = 'object' AND changes <> '{}'::jsonb),
    CONSTRAINT property_territorial_change_requests_fingerprint_chk
        CHECK (base_fingerprint ~ '^[0-9a-f]{64}$'),
    CONSTRAINT property_territorial_change_requests_labels_chk
        CHECK (btrim(property_label) <> '' AND btrim(territory_label) <> ''),
    CONSTRAINT property_territorial_change_requests_notes_chk
        CHECK ((request_note IS NULL OR char_length(request_note) <= 500)
               AND (decision_note IS NULL OR char_length(decision_note) <= 500)),
    CONSTRAINT property_territorial_change_requests_role_chk
        CHECK (requested_by_role = 'agency_owner'),
    CONSTRAINT property_territorial_change_requests_decision_chk
        CHECK ((status = 'pending') = (decided_at IS NULL)),
    CONSTRAINT property_territorial_change_requests_reject_note_chk
        CHECK (status <> 'rejected' OR btrim(coalesce(decision_note, '')) <> ''),
    CONSTRAINT property_territorial_change_requests_applied_chk
        CHECK ((status = 'approved') = (applied_at IS NOT NULL AND apply_result IS NOT NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_property_territorial_change_pending
    ON property_territorial_change_requests (property_id) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_property_territorial_change_status
    ON property_territorial_change_requests (status, requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_property_territorial_change_property
    ON property_territorial_change_requests (property_id, requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_property_territorial_change_competent
    ON property_territorial_change_requests (competent_agency_id, status, requested_at DESC);

-- La proposta: nasce pending, i suoi dati non cambiano, la decisione e' una
-- sola e definitiva, nessuna riga si cancella.
CREATE OR REPLACE FUNCTION property_territorial_change_requests_guard() RETURNS trigger AS $fn$
DECLARE
    v_fissi TEXT[] := ARRAY['id', 'property_id', 'property_label', 'origin_agency_id', 'competent_agency_id',
                            'territory_id', 'territory_label', 'changes', 'base_fingerprint', 'request_note',
                            'requested_by_role', 'requested_at'];
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'AGENZIE-TERRITORI 104: territorial change requests are history (DELETE refused, id=%)', OLD.id;
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'pending' OR NEW.decided_at IS NOT NULL OR NEW.decided_by_user_id IS NOT NULL
           OR NEW.applied_at IS NOT NULL OR NEW.apply_result IS NOT NULL OR NEW.decision_note IS NOT NULL THEN
            RAISE EXCEPTION 'AGENZIE-TERRITORI 104: a territorial change request is born pending';
        END IF;
        RETURN NEW;
    END IF;
    IF (SELECT jsonb_object_agg(k, to_jsonb(OLD) -> k) FROM unnest(v_fissi) k)
       IS DISTINCT FROM (SELECT jsonb_object_agg(k, to_jsonb(NEW) -> k) FROM unnest(v_fissi) k) THEN
        RAISE EXCEPTION 'AGENZIE-TERRITORI 104: request % data are immutable', OLD.id;
    END IF;
    IF OLD.status <> 'pending' AND (to_jsonb(NEW) - 'updated_at' - 'requested_by_user_id' - 'decided_by_user_id')
                                IS DISTINCT FROM (to_jsonb(OLD) - 'updated_at' - 'requested_by_user_id' - 'decided_by_user_id') THEN
        RAISE EXCEPTION 'AGENZIE-TERRITORI 104: the decision of request % is final (%)', OLD.id, OLD.status;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_property_territorial_change_requests_guard'
                      AND tgrelid = 'public.property_territorial_change_requests'::regclass) THEN
        CREATE TRIGGER trg_property_territorial_change_requests_guard
            BEFORE INSERT OR UPDATE OR DELETE ON property_territorial_change_requests
            FOR EACH ROW EXECUTE FUNCTION property_territorial_change_requests_guard();
    END IF;
END
$do$;

CREATE TABLE IF NOT EXISTS property_territorial_change_events (
    id               BIGSERIAL    PRIMARY KEY,
    request_id       BIGINT       NOT NULL REFERENCES property_territorial_change_requests(id) ON DELETE RESTRICT,
    property_id      BIGINT       NOT NULL,
    action           VARCHAR(30)  NOT NULL,
    actor_user_id    BIGINT       REFERENCES operator_users(id) ON DELETE SET NULL,
    actor_agency_id  BIGINT       REFERENCES agencies(id) ON DELETE RESTRICT,
    occurred_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    metadata         JSONB        NOT NULL DEFAULT '{}'::jsonb,

    CONSTRAINT property_territorial_change_events_action_chk
        CHECK (action IN ('proposed', 'approved', 'rejected', 'approval_blocked')),
    CONSTRAINT property_territorial_change_events_metadata_chk
        CHECK (jsonb_typeof(metadata) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_property_territorial_change_events_request
    ON property_territorial_change_events (request_id, occurred_at, id);

CREATE OR REPLACE FUNCTION property_territorial_change_events_append_only() RETURNS trigger AS $fn$
BEGIN
    RAISE EXCEPTION 'AGENZIE-TERRITORI 104: property_territorial_change_events is append-only (% refused, id=%)', TG_OP, OLD.id;
END;
$fn$ LANGUAGE plpgsql;

DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_property_territorial_change_events_append_only'
                      AND tgrelid = 'public.property_territorial_change_events'::regclass) THEN
        CREATE TRIGGER trg_property_territorial_change_events_append_only
            BEFORE UPDATE OR DELETE ON property_territorial_change_events
            FOR EACH ROW EXECUTE FUNCTION property_territorial_change_events_append_only();
    END IF;
END
$do$;
