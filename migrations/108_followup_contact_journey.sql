-- 108 - FOLLOW-UP 180 GIORNI (Fase B): la journey SUL CONTATTO, la pausa di
-- sistema, la disiscrizione di piattaforma.
--
-- Additiva. Nessuna riga esistente viene riscritta, nessuna migration storica
-- cambia, nessuna journey nasce qui: una journey e' una decisione di
-- un'agenzia e si crea (in bozza) dal codice, mai da una migration.
--
--   1. `communication_email_optouts` (NUOVA, di piattaforma): chi ha detto
--      "basta marketing STIMA360" per TUTTE le agenzie. Append-only. Non e'
--      un evento di consenso di un'agenzia: quelli restano per agenzia e non
--      vengono mai scritti per conto di un'altra. La guardia centrale la
--      legge accanto al consenso.
--   2. `communication_journeys`: il trigger `contact_eligible` (la journey
--      parte dal contatto eleggibile, non da una stima) e `sequence_policy`:
--      `linear` finisce dopo l'ultimo passo, `repeat_last` ricomincia (il
--      comportamento della v2, che resta IDENTICO: backfill su quelle righe).
--   3. `communication_enrollments`: per una journey sul contatto non c'e' una
--      stima (`stima_id_snapshot` diventa nullable), c'e' l'email con cui si
--      e' iscritta (`email_normalized_snapshot`, UNICA fra le iscrizioni
--      aperte: una email, una sequenza, in tutta la piattaforma), e la pausa
--      puo' essere di SISTEMA (`appointment`, `commercial`, `consultation`),
--      senza operatore, con una data di ripresa (`resume_after`) e un
--      riferimento al fatto (`pause_ref`). Tre ragioni di stop in piu'.
--   4. I passi e i messaggi ammettono `m1`..`m20` (il calendario ne usa 17).
--   5. `communication_enrollments_guard` (097) riscritta con il ramo
--      `contact_eligible`: il contatto e' dell'agenzia, l'email dichiarata e'
--      quella del contatto, `trigger_sent_at` (il momento iniziale) lo passa
--      il chiamante e resta immutabile come prima.

-- ---------------------------------------------------------------------------
-- 1. Le disiscrizioni di piattaforma
-- ---------------------------------------------------------------------------
CREATE TABLE communication_email_optouts (
    id                 BIGSERIAL    PRIMARY KEY,
    email_normalized   VARCHAR(320) NOT NULL,
    opted_out_at       TIMESTAMPTZ  NOT NULL,
    source             VARCHAR(40)  NOT NULL,
    source_agency_id   BIGINT       REFERENCES agencies(id) ON DELETE SET NULL,
    source_contact_id  BIGINT,
    consent_event_id   BIGINT       REFERENCES consent_events(id) ON DELETE SET NULL,
    actor_type         VARCHAR(20)  NOT NULL,
    actor_ref          VARCHAR(120),
    note               TEXT,
    idempotency_key    VARCHAR(300) NOT NULL UNIQUE,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT comm_optouts_email_chk
        CHECK (email_normalized = LOWER(BTRIM(email_normalized)) AND POSITION('@' IN email_normalized) > 1),
    CONSTRAINT comm_optouts_source_chk
        CHECK (source IN ('unsubscribe_link', 'crm')),
    CONSTRAINT comm_optouts_actor_type_chk
        CHECK (actor_type IN ('subject', 'operator')),
    CONSTRAINT comm_optouts_operator_ref_chk
        CHECK (actor_type <> 'operator' OR (actor_ref IS NOT NULL AND BTRIM(actor_ref) <> '')),
    CONSTRAINT comm_optouts_idem_chk CHECK (BTRIM(idempotency_key) <> '')
);
CREATE INDEX idx_comm_optouts_email ON communication_email_optouts (email_normalized, opted_out_at DESC);

CREATE FUNCTION communication_email_optouts_append_only() RETURNS trigger AS $fn$
BEGIN
    RAISE EXCEPTION '108: communication_email_optouts is append-only (%)', TG_OP;
END;
$fn$ LANGUAGE plpgsql;
CREATE TRIGGER trg_comm_optouts_append_only
    BEFORE UPDATE OR DELETE ON communication_email_optouts
    FOR EACH ROW EXECUTE FUNCTION communication_email_optouts_append_only();

-- ---------------------------------------------------------------------------
-- 2. Le journey: il trigger sul contatto e la policy di sequenza
-- ---------------------------------------------------------------------------
ALTER TABLE communication_journeys DROP CONSTRAINT comm_journeys_trigger_chk;
ALTER TABLE communication_journeys ADD CONSTRAINT comm_journeys_trigger_chk
    CHECK (trigger_type IN ('stima_pdf_sent', 'stima_request_registered', 'contact_eligible'));
ALTER TABLE communication_journeys ADD COLUMN sequence_policy VARCHAR(20) NOT NULL DEFAULT 'linear';
ALTER TABLE communication_journeys ADD CONSTRAINT comm_journeys_policy_chk
    CHECK (sequence_policy IN ('linear', 'repeat_last'));
-- La v2 (Tappa 6) ricominciava dall'ultimo passo: resta cosi', dichiarato.
UPDATE communication_journeys SET sequence_policy = 'repeat_last'
 WHERE trigger_type = 'stima_request_registered';

-- ---------------------------------------------------------------------------
-- 3. Le iscrizioni
-- ---------------------------------------------------------------------------
ALTER TABLE communication_enrollments ALTER COLUMN stima_id_snapshot DROP NOT NULL;
ALTER TABLE communication_enrollments ADD COLUMN email_normalized_snapshot VARCHAR(320);
ALTER TABLE communication_enrollments ADD COLUMN resume_after TIMESTAMPTZ;
ALTER TABLE communication_enrollments ADD COLUMN pause_ref VARCHAR(120);
ALTER TABLE communication_enrollments ADD CONSTRAINT comm_enroll_email_snapshot_chk
    CHECK (email_normalized_snapshot IS NULL
           OR (email_normalized_snapshot = LOWER(BTRIM(email_normalized_snapshot))
               AND POSITION('@' IN email_normalized_snapshot) > 1));

ALTER TABLE communication_enrollments DROP CONSTRAINT comm_enroll_paused_source_chk;
ALTER TABLE communication_enrollments ADD CONSTRAINT comm_enroll_paused_source_chk
    CHECK (paused_source IS NULL OR paused_source IN
           ('enrollment', 'contact_control', 'appointment', 'commercial', 'consultation'));

ALTER TABLE communication_enrollments DROP CONSTRAINT comm_enroll_stop_reason_chk;
ALTER TABLE communication_enrollments ADD CONSTRAINT comm_enroll_stop_reason_chk
    CHECK (stop_reason IS NULL OR stop_reason IN (
        'mandate_signed', 'acquisition_linked', 'inspection', 'consultation_requested',
        'lead_closed', 'contact_inactive',
        'consent_revoked', 'consent_not_granted', 'consent_inconsistent',
        'expired_on_resume', 'operator', 'request_too_old',
        'platform_optout', 'email_invalid', 'duplicate_email'));

-- LA MATRICE, riscritta per UNA differenza: in pausa, l'operatore c'e' se e
-- solo se la pausa e' sua (`enrollment`, `contact_control`); una pausa di
-- sistema non ne inventa uno. `resume_after` e `pause_ref` esistono solo in
-- pausa.
ALTER TABLE communication_enrollments DROP CONSTRAINT comm_enroll_matrix_chk;
ALTER TABLE communication_enrollments ADD CONSTRAINT comm_enroll_matrix_chk CHECK (
    (status = 'active'
        AND next_step_no IS NOT NULL AND next_action_at IS NOT NULL AND next_action_kind IS NOT NULL
        AND paused_at IS NULL AND paused_source IS NULL AND paused_by_operator_user_id IS NULL
        AND resume_after IS NULL AND pause_ref IS NULL
        AND stopped_at IS NULL AND stop_reason IS NULL AND stopped_by_operator_user_id IS NULL
        AND completed_at IS NULL)
 OR (status = 'paused'
        AND next_step_no IS NOT NULL AND next_action_at IS NOT NULL AND next_action_kind IS NOT NULL
        AND paused_at IS NOT NULL AND paused_source IS NOT NULL
        AND ((paused_source IN ('enrollment', 'contact_control')) = (paused_by_operator_user_id IS NOT NULL))
        AND stopped_at IS NULL AND stop_reason IS NULL AND stopped_by_operator_user_id IS NULL
        AND completed_at IS NULL)
 OR (status = 'completed'
        AND next_step_no IS NULL AND next_action_at IS NULL AND next_action_kind IS NULL
        AND awaiting_since IS NULL
        AND paused_at IS NULL AND paused_source IS NULL AND paused_by_operator_user_id IS NULL
        AND resume_after IS NULL AND pause_ref IS NULL
        AND stopped_at IS NULL AND stop_reason IS NULL AND stopped_by_operator_user_id IS NULL
        AND completed_at IS NOT NULL)
 OR (status = 'stopped'
        AND next_step_no IS NULL AND next_action_at IS NULL AND next_action_kind IS NULL
        AND awaiting_since IS NULL
        AND paused_at IS NULL AND paused_source IS NULL AND paused_by_operator_user_id IS NULL
        AND resume_after IS NULL AND pause_ref IS NULL
        AND stopped_at IS NOT NULL AND stop_reason IS NOT NULL
        AND completed_at IS NULL));

-- UNA EMAIL, UNA SEQUENZA, in tutta la piattaforma: l'indice non ha
-- `agency_id` di proposito. Solo le iscrizioni sul contatto portano l'email
-- (le v1/v2 lasciano NULL e non ne sono toccate).
CREATE UNIQUE INDEX uq_comm_enroll_open_per_email
    ON communication_enrollments (email_normalized_snapshot)
    WHERE status IN ('active', 'paused') AND email_normalized_snapshot IS NOT NULL;
CREATE INDEX idx_comm_enroll_email_snapshot
    ON communication_enrollments (email_normalized_snapshot) WHERE email_normalized_snapshot IS NOT NULL;
CREATE INDEX idx_comm_enroll_resume
    ON communication_enrollments (agency_id, resume_after) WHERE status = 'paused' AND resume_after IS NOT NULL;

-- UN CICLO SOLO, PER SEMPRE, A LIVELLO DI DATABASE. L'indice unico sopra
-- vieta due sequenze APERTE sullo stesso indirizzo; questo trigger vieta
-- una sequenza NUOVA su un indirizzo che ne ha gia' COMPLETATA una, in
-- qualunque agenzia, in qualunque momento. Non e' una regola della platea
-- (che la applica per prima): e' un fatto che il database rifiuta anche a
-- chi la platea non la legge.
CREATE FUNCTION communication_contact_enrollment_once() RETURNS trigger AS $fn$
BEGIN
    IF NEW.email_normalized_snapshot IS NOT NULL AND EXISTS (
        SELECT 1 FROM communication_enrollments e
         WHERE e.email_normalized_snapshot = NEW.email_normalized_snapshot
           AND e.status = 'completed') THEN
        RAISE EXCEPTION '108: % has already completed a contact journey: no second sequence',
            NEW.email_normalized_snapshot;
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;
CREATE TRIGGER trg_comm_contact_enrollment_once
    BEFORE INSERT ON communication_enrollments
    FOR EACH ROW EXECUTE FUNCTION communication_contact_enrollment_once();

-- ---------------------------------------------------------------------------
-- 4. I passi: fino a m20
-- ---------------------------------------------------------------------------
ALTER TABLE communication_messages DROP CONSTRAINT communication_messages_reason_code_chk;
ALTER TABLE communication_messages ADD CONSTRAINT communication_messages_reason_code_chk
    CHECK (reason_code IN ('stima_pdf','operator_manual','operator_reply','owner_login_link','appointment_reminder',
        'm1','m2','m3','m4','m5','m6','m7','m8','m9','m10',
        'm11','m12','m13','m14','m15','m16','m17','m18','m19','m20'));
ALTER TABLE communication_journey_steps DROP CONSTRAINT comm_steps_reason_chk;
ALTER TABLE communication_journey_steps ADD CONSTRAINT comm_steps_reason_chk
    CHECK (reason_code IN ('m1','m2','m3','m4','m5','m6','m7','m8','m9','m10',
        'm11','m12','m13','m14','m15','m16','m17','m18','m19','m20'));

-- ---------------------------------------------------------------------------
-- 5. La guardia delle iscrizioni, con il ramo sul contatto
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION communication_enrollments_guard() RETURNS trigger AS $fn$
DECLARE
    a_stima   BIGINT;
    v_journey RECORD;
    a_msg     BIGINT;
    v_sent_at TIMESTAMPTZ;
    v_reason  TEXT;
    v_status  TEXT;
    v_email   TEXT;
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT * INTO v_journey FROM communication_journeys WHERE id = NEW.journey_id AND agency_id = NEW.agency_id;
        IF v_journey IS NULL THEN
            RAISE EXCEPTION 'P29-3 tenancy: journey % is not in agency %', NEW.journey_id, NEW.agency_id;
        END IF;

        IF v_journey.trigger_type = 'contact_eligible' THEN
            -- 108: la journey sul contatto. Nessuna stima richiesta, nessun
            -- messaggio di trigger; il momento iniziale lo dichiara il
            -- chiamante e l'email e' quella del contatto, ADESSO.
            IF NEW.trigger_message_id IS NOT NULL THEN
                RAISE EXCEPTION '108: a contact journey enrollment carries no trigger message';
            END IF;
            IF NEW.trigger_sent_at IS NULL THEN
                RAISE EXCEPTION '108: a contact journey enrollment requires trigger_sent_at (admission time)';
            END IF;
            IF NEW.email_normalized_snapshot IS NULL THEN
                RAISE EXCEPTION '108: a contact journey enrollment requires email_normalized_snapshot';
            END IF;
            SELECT c.email_normalized INTO v_email FROM contacts c
             WHERE c.id = NEW.contact_id AND c.agency_id = NEW.agency_id;
            IF v_email IS NULL OR v_email <> NEW.email_normalized_snapshot THEN
                RAISE EXCEPTION '108: email snapshot does not match contact % in agency %', NEW.contact_id, NEW.agency_id;
            END IF;
            NEW.stima_id_snapshot := NEW.stima_id;
            IF NEW.stima_id IS NOT NULL THEN
                SELECT agency_id INTO a_stima FROM stime WHERE id = NEW.stima_id;
                IF a_stima IS NULL OR a_stima <> NEW.agency_id THEN
                    RAISE EXCEPTION 'P29-3 tenancy: estimation % is not in agency %', NEW.stima_id, NEW.agency_id;
                END IF;
            END IF;
            PERFORM comm_enrollment_actor_ok(NEW.enrolled_by_operator_user_id, NEW.agency_id, 'enrolled_by');
            PERFORM comm_enrollment_actor_ok(NEW.paused_by_operator_user_id,   NEW.agency_id, 'paused_by');
            PERFORM comm_enrollment_actor_ok(NEW.stopped_by_operator_user_id,  NEW.agency_id, 'stopped_by');
            RETURN NEW;
        END IF;

        IF NEW.email_normalized_snapshot IS NOT NULL THEN
            RAISE EXCEPTION '108: only a contact journey enrollment carries email_normalized_snapshot';
        END IF;
        IF NEW.stima_id IS NULL THEN
            RAISE EXCEPTION 'P29-3: stima_id is required when creating an enrollment';
        END IF;
        -- ASSEGNATO, non validato.
        NEW.stima_id_snapshot := NEW.stima_id;

        SELECT agency_id INTO a_stima FROM stime WHERE id = NEW.stima_id;
        IF a_stima IS NULL OR a_stima <> NEW.agency_id THEN
            RAISE EXCEPTION 'P29-3 tenancy: estimation % is not in agency %', NEW.stima_id, NEW.agency_id;
        END IF;
        -- Il trigger e' la mail della stima, GIA' spedita, della stessa
        -- agenzia e della stessa stima. `trigger_sent_at` e' una copia del
        -- suo `sent_at`, assegnata qui e non dal chiamante.
        IF NEW.trigger_message_id IS NULL THEN
            IF v_journey.trigger_type IS DISTINCT FROM 'stima_request_registered' THEN
                RAISE EXCEPTION 'T6: request enrollment requires registered-request journey';
            END IF;
            SELECT crm_registered_at INTO v_sent_at FROM stime WHERE id=NEW.stima_id AND agency_id=NEW.agency_id;
            IF v_sent_at IS NULL THEN RAISE EXCEPTION 'T6: missing CRM registration time'; END IF;
            IF EXISTS(SELECT 1 FROM site_import_baselines b,jsonb_array_elements_text(b.ids->'stime') h WHERE h::integer=NEW.stima_id) THEN
                RAISE EXCEPTION 'T6: baseline request cannot enroll';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM lead_stime ls JOIN leads l ON l.id = ls.lead_id
                WHERE ls.stima_id = NEW.stima_id AND l.id = NEW.lead_id
                  AND l.contact_id = NEW.contact_id AND l.agency_id = NEW.agency_id) THEN
                RAISE EXCEPTION 'T6: request enrollment contact/lead mismatch';
            END IF;
        ELSE
            IF v_journey.trigger_type <> 'stima_pdf_sent' THEN
                RAISE EXCEPTION 'T6: request journey cannot use a PDF sent message';
            END IF;
        SELECT agency_id, sent_at, reason_code, status
          INTO a_msg, v_sent_at, v_reason, v_status
          FROM communication_messages WHERE id = NEW.trigger_message_id;
        IF a_msg IS NULL OR a_msg <> NEW.agency_id THEN
            RAISE EXCEPTION 'P29-3 tenancy: trigger message % is not in agency %', NEW.trigger_message_id, NEW.agency_id;
        END IF;
        IF v_reason <> 'stima_pdf' OR v_status <> 'sent' OR v_sent_at IS NULL THEN
            RAISE EXCEPTION 'P29-3: trigger message % is not a sent stima_pdf', NEW.trigger_message_id;
        END IF;
        END IF;
        NEW.trigger_sent_at := v_sent_at;
        PERFORM comm_enrollment_actor_ok(NEW.enrolled_by_operator_user_id, NEW.agency_id, 'enrolled_by');
        PERFORM comm_enrollment_actor_ok(NEW.paused_by_operator_user_id,   NEW.agency_id, 'paused_by');
        PERFORM comm_enrollment_actor_ok(NEW.stopped_by_operator_user_id,  NEW.agency_id, 'stopped_by');
        RETURN NEW;
    END IF;

    -- UPDATE: identita' immutabile; attori verificati solo se cambiano.
    IF NEW.stima_id_snapshot IS DISTINCT FROM OLD.stima_id_snapshot
    OR NEW.trigger_message_id IS DISTINCT FROM OLD.trigger_message_id
    OR NEW.trigger_sent_at    IS DISTINCT FROM OLD.trigger_sent_at
    OR NEW.journey_id         IS DISTINCT FROM OLD.journey_id
    OR NEW.agency_id          IS DISTINCT FROM OLD.agency_id
    OR NEW.contact_id         IS DISTINCT FROM OLD.contact_id
    OR NEW.email_normalized_snapshot IS DISTINCT FROM OLD.email_normalized_snapshot
    OR NEW.idempotency_key    IS DISTINCT FROM OLD.idempotency_key THEN
        RAISE EXCEPTION 'P29-3: enrollment identity columns are immutable (id=%)', OLD.id;
    END IF;
    IF NEW.stima_id IS NOT NULL AND OLD.stima_id IS NOT NULL
       AND NEW.stima_id IS DISTINCT FROM OLD.stima_id THEN
        RAISE EXCEPTION 'P29-3: stima_id cannot be reassigned on an enrollment';
    END IF;
    IF NEW.paused_by_operator_user_id IS DISTINCT FROM OLD.paused_by_operator_user_id THEN
        PERFORM comm_enrollment_actor_ok(NEW.paused_by_operator_user_id, NEW.agency_id, 'paused_by');
    END IF;
    IF NEW.stopped_by_operator_user_id IS DISTINCT FROM OLD.stopped_by_operator_user_id THEN
        PERFORM comm_enrollment_actor_ok(NEW.stopped_by_operator_user_id, NEW.agency_id, 'stopped_by');
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;
