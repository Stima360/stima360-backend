-- BLOCCO-5A - prenotazione online delle visite di UN immobile con incarico.
--
-- Due tabelle nuove, additive. Nessuna tabella esistente cambia: le visite
-- restano righe di `appointments` (l'unica agenda), tipo `buyer_visit`,
-- origine `booking_link`, collegate all'immobile; la proiezione sull'immobile
-- (`property_visits`) e i promemoria restano quelli di sempre. I link
-- generici di `public_booking_links` continuano a NON prenotare visite.
--
--   property_visit_booking_links   un collegamento pubblico per UN immobile e
--       UN agente. Token conservato solo come SHA-256. Durata, passo degli
--       orari, tempo di spostamento (prima e dopo), preavviso minimo e
--       orizzonte sono configurati qui. L'autorizzazione del proprietario e'
--       ESPLICITA e registrata con autore e data:
--           preauthorized  il proprietario ha autorizzato le visite negli
--                          orari dell'agente: la prenotazione e' confermata;
--           per_visit      ogni visita richiede il suo assenso: la
--                          prenotazione resta una RICHIESTA, mai confermata.
--       Senza autorizzazione registrata il link non si crea.
--   property_visit_bookings        una prenotazione per riga: link, visita
--       in agenda (unica), chiave di invio (idempotenza) e hash del token di
--       gestione (annullamento e riprogrammazione dal cliente).
--   property_visit_booking_rate_limits  limiti di frequenza per link e per
--       IP (hash), finestra fissa: stessa forma di `public_booking_rate_limits`.
--
-- La transazione e' del runner.

CREATE TABLE property_visit_booking_links (
    id                                   BIGSERIAL    PRIMARY KEY,
    agency_id                            BIGINT       NOT NULL REFERENCES agencies(id),
    property_id                          BIGINT       NOT NULL REFERENCES properties(id),
    assigned_user_id                     BIGINT       NOT NULL REFERENCES operator_users(id),
    token_hash                           CHAR(64)     NOT NULL,
    status                               VARCHAR(10)  NOT NULL DEFAULT 'active',
    duration_minutes                     INTEGER      NOT NULL,
    step_minutes                         INTEGER      NOT NULL DEFAULT 30,
    travel_minutes                       INTEGER      NOT NULL DEFAULT 0,
    min_notice_minutes                   INTEGER      NOT NULL DEFAULT 720,
    horizon_days                         INTEGER      NOT NULL DEFAULT 21,
    owner_authorization                  VARCHAR(15)  NOT NULL,
    owner_authorization_note             TEXT,
    owner_authorization_recorded_by      BIGINT       NOT NULL REFERENCES operator_users(id),
    owner_authorization_recorded_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_by_user_id                   BIGINT       NOT NULL REFERENCES operator_users(id),
    created_at                           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    expires_at                           TIMESTAMPTZ,
    disabled_at                          TIMESTAMPTZ,
    disabled_by_user_id                  BIGINT       REFERENCES operator_users(id),
    CONSTRAINT property_visit_booking_links_token_unq UNIQUE (token_hash),
    CONSTRAINT property_visit_booking_links_token_chk CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT property_visit_booking_links_status_chk CHECK (status IN ('active', 'disabled')),
    CONSTRAINT property_visit_booking_links_disabled_chk
        CHECK ((status = 'disabled') = (disabled_at IS NOT NULL)),
    CONSTRAINT property_visit_booking_links_duration_chk CHECK (duration_minutes BETWEEN 15 AND 240),
    CONSTRAINT property_visit_booking_links_step_chk CHECK (step_minutes IN (15, 30, 60)),
    CONSTRAINT property_visit_booking_links_travel_chk CHECK (travel_minutes BETWEEN 0 AND 120),
    CONSTRAINT property_visit_booking_links_notice_chk CHECK (min_notice_minutes BETWEEN 60 AND 10080),
    CONSTRAINT property_visit_booking_links_horizon_chk CHECK (horizon_days BETWEEN 1 AND 60),
    CONSTRAINT property_visit_booking_links_owner_chk CHECK (owner_authorization IN ('preauthorized', 'per_visit')),
    CONSTRAINT property_visit_booking_links_agent_fk
        FOREIGN KEY (agency_id, assigned_user_id) REFERENCES agency_memberships (agency_id, operator_user_id)
);

CREATE INDEX idx_property_visit_booking_links_property
    ON property_visit_booking_links (agency_id, property_id, created_at DESC);

CREATE FUNCTION property_visit_booking_links_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_agency BIGINT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'property visit booking link % cannot be deleted', OLD.id;
    END IF;
    IF TG_OP = 'UPDATE' AND (
        NEW.agency_id IS DISTINCT FROM OLD.agency_id
        OR NEW.property_id IS DISTINCT FROM OLD.property_id
        OR NEW.assigned_user_id IS DISTINCT FROM OLD.assigned_user_id
        OR NEW.token_hash IS DISTINCT FROM OLD.token_hash
        OR NEW.owner_authorization IS DISTINCT FROM OLD.owner_authorization
        OR NEW.created_at IS DISTINCT FROM OLD.created_at
        OR (OLD.status = 'disabled' AND NEW.status <> 'disabled')) THEN
        RAISE EXCEPTION 'property visit booking link %: only disabling is allowed', OLD.id;
    END IF;
    SELECT agency_id INTO v_agency FROM properties WHERE id = NEW.property_id;
    IF v_agency IS DISTINCT FROM NEW.agency_id THEN
        RAISE EXCEPTION 'property visit booking link: property % belongs to another agency', NEW.property_id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_property_visit_booking_links_guard
    BEFORE INSERT OR UPDATE OR DELETE ON property_visit_booking_links
    FOR EACH ROW EXECUTE FUNCTION property_visit_booking_links_guard();

CREATE TABLE property_visit_bookings (
    id                    BIGSERIAL    PRIMARY KEY,
    agency_id             BIGINT       NOT NULL REFERENCES agencies(id),
    link_id               BIGINT       NOT NULL REFERENCES property_visit_booking_links(id),
    appointment_id        BIGINT       NOT NULL REFERENCES appointments(id),
    submission_key        VARCHAR(80)  NOT NULL,
    manage_token_hash     CHAR(64)     NOT NULL,
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    replaced_by_id        BIGINT       REFERENCES property_visit_bookings(id),
    CONSTRAINT property_visit_bookings_appointment_unq UNIQUE (appointment_id),
    CONSTRAINT property_visit_bookings_submission_unq UNIQUE (link_id, submission_key),
    CONSTRAINT property_visit_bookings_manage_unq UNIQUE (manage_token_hash),
    CONSTRAINT property_visit_bookings_manage_chk CHECK (manage_token_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT property_visit_bookings_submission_chk CHECK (BTRIM(submission_key) <> '')
);

CREATE FUNCTION property_visit_bookings_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_link_agency BIGINT;
    v_appointment_agency BIGINT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'property visit booking % cannot be deleted', OLD.id;
    END IF;
    IF TG_OP = 'UPDATE' AND (
        NEW.agency_id IS DISTINCT FROM OLD.agency_id OR NEW.link_id IS DISTINCT FROM OLD.link_id
        OR NEW.appointment_id IS DISTINCT FROM OLD.appointment_id
        OR NEW.submission_key IS DISTINCT FROM OLD.submission_key
        OR NEW.manage_token_hash IS DISTINCT FROM OLD.manage_token_hash
        OR (OLD.replaced_by_id IS NOT NULL AND NEW.replaced_by_id IS DISTINCT FROM OLD.replaced_by_id)) THEN
        RAISE EXCEPTION 'property visit booking %: only the replacement may be recorded, once', OLD.id;
    END IF;
    SELECT agency_id INTO v_link_agency FROM property_visit_booking_links WHERE id = NEW.link_id;
    SELECT agency_id INTO v_appointment_agency FROM appointments WHERE id = NEW.appointment_id;
    IF v_link_agency IS DISTINCT FROM NEW.agency_id OR v_appointment_agency IS DISTINCT FROM NEW.agency_id THEN
        RAISE EXCEPTION 'property visit booking: link and appointment must belong to agency %', NEW.agency_id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_property_visit_bookings_guard
    BEFORE INSERT OR UPDATE OR DELETE ON property_visit_bookings
    FOR EACH ROW EXECUTE FUNCTION property_visit_bookings_guard();

CREATE TABLE property_visit_booking_rate_limits (
    id                BIGSERIAL    PRIMARY KEY,
    link_id           BIGINT       NOT NULL REFERENCES property_visit_booking_links(id),
    scope             VARCHAR(20)  NOT NULL,
    client_ip_hash    CHAR(64)     NOT NULL DEFAULT '',
    window_start      TIMESTAMPTZ  NOT NULL,
    request_count     INTEGER      NOT NULL DEFAULT 0,
    CONSTRAINT property_visit_booking_rate_limits_scope_chk
        CHECK (scope IN ('get_link', 'get_ip', 'post_link', 'post_ip')),
    CONSTRAINT property_visit_booking_rate_limits_unique
        UNIQUE (link_id, scope, client_ip_hash, window_start)
);
