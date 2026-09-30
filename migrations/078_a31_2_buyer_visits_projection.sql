-- 078 - A31-2: BUYER VISITS PROJECTION FOUNDATION.
--
-- Il gate A31-1 (DESIGN FREEZE, D1-D8) ha deciso che una visita acquirente
-- nasce in `appointments` (`appointment_type = 'buyer_visit'`, fonte
-- autorevole) e che `property_visits` ne e' la PROIEZIONE, scritta nella
-- STESSA transazione dal package `buyer_visits/`. Questa migration aggiunge
-- solo il COLLEGAMENTO e la sua guardia:
--
--   property_visits.appointment_id  BIGINT NULL
--       FK -> appointments(id) ON DELETE RESTRICT
--       UNIQUE (una proiezione per appuntamento; l'indice e' quello del
--       vincolo, nessun altro indice serve)
--
--   property_visits_appointment_guard()  + trigger SEPARATO
--       non tocca `property_agency_integrity()` ne' il suo trigger P26.
--
-- NON tocca `appointments` (schema, `appointments_source_chk`, stati),
-- `appointment_events`, `appointment_calendar_sync`, `buy_request_interactions`,
-- `owner_visit_feedback_publications`, `matches`. NESSUN BACKFILL: le righe
-- legacy restano `appointment_id IS NULL` e il loro comportamento non cambia
-- (LEGACY STRATEGY = LEAVE AS IS, census A31-1).
--
-- LA GUARDIA (solo quando `appointment_id` e' valorizzato):
--   * l'appuntamento esiste ed e' un `buyer_visit`;
--   * `appointments.property_id` = `property_visits.property_id` (non NULL);
--   * l'agenzia dell'appuntamento = l'agenzia dell'immobile (property_visits
--     non ha `agency_id`: la eredita dall'immobile, CHILD-DERIVED P26);
--   * in UPDATE un `appointment_id` gia' valorizzato si sposta SOLO verso il
--     successore diretto di uno spostamento (`rescheduled_from_id` = il
--     vecchio id): e' cosi' che la stessa visita segue il reschedule A30 (la
--     riga vecchia diventa `rescheduled`, ne nasce una nuova);
--   * un collegamento esistente non si azzera (NULL): una proiezione staccata
--     sarebbe una deriva silenziosa.
--
-- ON DELETE RESTRICT: `appointments` non si cancella mai (trigger 072), salvo
-- la purge dei dati `a30_test` sul solo database TEST certificato: se una
-- riga di prova fosse proiettata, la purge fallirebbe in modo visibile invece
-- di staccare la visita in silenzio (SET NULL).
--
-- La transazione e la riga in `schema_migrations` le gestisce il runner
-- P26: questo file non apre ne' chiude una transazione.

ALTER TABLE property_visits
    ADD COLUMN appointment_id BIGINT;

ALTER TABLE property_visits
    ADD CONSTRAINT property_visits_appointment_fk
        FOREIGN KEY (appointment_id) REFERENCES appointments (id) ON DELETE RESTRICT;

ALTER TABLE property_visits
    ADD CONSTRAINT property_visits_appointment_unq UNIQUE (appointment_id);

CREATE FUNCTION property_visits_appointment_guard() RETURNS trigger AS $fn$
DECLARE
    v_tipo        VARCHAR(30);
    v_agenzia     BIGINT;
    v_immobile    BIGINT;
    v_precedente  BIGINT;
    v_agenzia_imm BIGINT;
BEGIN
    IF NEW.appointment_id IS NULL THEN
        IF TG_OP = 'UPDATE' AND OLD.appointment_id IS NOT NULL THEN
            RAISE EXCEPTION
                'A31-2: property_visits % is the projection of appointment % and cannot be unlinked',
                OLD.id, OLD.appointment_id;
        END IF;
        RETURN NEW;
    END IF;

    SELECT appointment_type, agency_id, property_id, rescheduled_from_id
      INTO v_tipo, v_agenzia, v_immobile, v_precedente
      FROM appointments
     WHERE id = NEW.appointment_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'A31-2: appointment % does not exist', NEW.appointment_id;
    END IF;
    IF v_tipo <> 'buyer_visit' THEN
        RAISE EXCEPTION 'A31-2: appointment % is a %, not a buyer_visit',
            NEW.appointment_id, v_tipo;
    END IF;
    IF NEW.property_id IS NULL OR v_immobile IS NULL OR v_immobile <> NEW.property_id THEN
        RAISE EXCEPTION 'A31-2: appointment % is for property %, the visit for property %',
            NEW.appointment_id, v_immobile, NEW.property_id;
    END IF;
    SELECT agency_id INTO v_agenzia_imm FROM properties WHERE id = NEW.property_id;
    IF v_agenzia_imm IS DISTINCT FROM v_agenzia THEN
        RAISE EXCEPTION 'A31-2 tenancy: appointment % is in agency %, property % in agency %',
            NEW.appointment_id, v_agenzia, NEW.property_id, v_agenzia_imm;
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.appointment_id IS NOT NULL
       AND NEW.appointment_id <> OLD.appointment_id
       AND v_precedente IS DISTINCT FROM OLD.appointment_id THEN
        RAISE EXCEPTION
            'A31-2: property_visits % can move from appointment % only to its reschedule successor, not to %',
            OLD.id, OLD.appointment_id, NEW.appointment_id;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

CREATE TRIGGER trg_property_visits_appointment_guard
    BEFORE INSERT OR UPDATE OF appointment_id, property_id ON property_visits
    FOR EACH ROW EXECUTE FUNCTION property_visits_appointment_guard();
