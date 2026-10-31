-- BLOCCO-6 - lacune operative. Additiva: una tabella nuova, due colonne
-- facoltative. Nessun dato esistente cambia significato.
--
--   contact_do_not_contact   il DIVIETO TOTALE DI CONTATTO di un contatto
--       dell'agenzia: nessuna comunicazione in uscita, di nessun tipo
--       (servizio compreso) e su nessun canale, finche' non viene revocato.
--       E' DISTINTO dalla rinuncia al marketing (consent_events,
--       communication_optouts): quella ferma il marketing su un canale, questo
--       ferma tutto. Chi, quando, perche'; la revoca chiude la riga (con chi,
--       quando e una nota), non la cancella. Al piu' UN divieto aperto per
--       contatto.
--   property_visit_booking_links.disabled_reason / disabled_note
--       perche' un collegamento e' stato disattivato: 'operator' (scelta
--       dell'agenzia), 'owner_authorization_revoked' (il proprietario ha
--       revocato l'autorizzazione alle visite: nota obbligatoria),
--       'parent_disabled' (collegamento dedicato chiuso con il suo padre).
--       Le righe disattivate prima della 124 restano senza motivo (NULL):
--       non si inventa una ragione a posteriori.
--
-- La transazione e' del runner.

CREATE TABLE contact_do_not_contact (
    id                  BIGSERIAL    PRIMARY KEY,
    agency_id           BIGINT       NOT NULL REFERENCES agencies(id),
    contact_id          BIGINT       NOT NULL REFERENCES contacts(id),
    source              VARCHAR(30)  NOT NULL,
    reason              TEXT         NOT NULL,
    set_by_user_id      BIGINT       NOT NULL REFERENCES operator_users(id),
    set_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    lifted_by_user_id   BIGINT       REFERENCES operator_users(id),
    lifted_at           TIMESTAMPTZ,
    lift_note           TEXT,
    CONSTRAINT contact_do_not_contact_source_chk CHECK (source IN ('subject_request', 'agency_decision')),
    CONSTRAINT contact_do_not_contact_reason_chk CHECK (length(btrim(reason)) >= 5),
    CONSTRAINT contact_do_not_contact_lift_chk CHECK (
        (lifted_at IS NULL AND lifted_by_user_id IS NULL AND lift_note IS NULL)
        OR (lifted_at IS NOT NULL AND lifted_by_user_id IS NOT NULL AND length(btrim(COALESCE(lift_note, ''))) >= 5)),
    CONSTRAINT contact_do_not_contact_order_chk CHECK (lifted_at IS NULL OR lifted_at >= set_at)
);

CREATE UNIQUE INDEX uq_contact_do_not_contact_open
    ON contact_do_not_contact (agency_id, contact_id) WHERE lifted_at IS NULL;

CREATE FUNCTION contact_do_not_contact_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_agency BIGINT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'contact do-not-contact % cannot be deleted', OLD.id;
    END IF;
    IF TG_OP = 'UPDATE' AND (
        NEW.agency_id IS DISTINCT FROM OLD.agency_id OR NEW.contact_id IS DISTINCT FROM OLD.contact_id
        OR NEW.source IS DISTINCT FROM OLD.source OR NEW.reason IS DISTINCT FROM OLD.reason
        OR NEW.set_by_user_id IS DISTINCT FROM OLD.set_by_user_id OR NEW.set_at IS DISTINCT FROM OLD.set_at
        OR OLD.lifted_at IS NOT NULL) THEN
        RAISE EXCEPTION 'contact do-not-contact %: only lifting an open ban is allowed', OLD.id;
    END IF;
    SELECT agency_id INTO v_agency FROM contacts WHERE id = NEW.contact_id;
    IF v_agency IS DISTINCT FROM NEW.agency_id THEN
        RAISE EXCEPTION 'contact do-not-contact: contact % belongs to another agency', NEW.contact_id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_contact_do_not_contact_guard
    BEFORE INSERT OR UPDATE OR DELETE ON contact_do_not_contact
    FOR EACH ROW EXECUTE FUNCTION contact_do_not_contact_guard();

ALTER TABLE property_visit_booking_links
    ADD COLUMN disabled_reason VARCHAR(30),
    ADD COLUMN disabled_note   TEXT,
    ADD CONSTRAINT property_visit_booking_links_disabled_reason_chk CHECK (
        disabled_reason IS NULL
        OR (status = 'disabled' AND disabled_reason IN ('operator', 'owner_authorization_revoked', 'parent_disabled'))),
    ADD CONSTRAINT property_visit_booking_links_revocation_note_chk CHECK (
        disabled_reason IS DISTINCT FROM 'owner_authorization_revoked'
        OR length(btrim(COALESCE(disabled_note, ''))) >= 5);
