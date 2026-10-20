-- Down for 108. Toglie la journey sul contatto, la pausa di sistema e le
-- disiscrizioni di piattaforma; riporta guardia, matrice e CHECK a 097/071.
--
-- Si FERMA, senza modifiche, se esiste anche una sola journey
-- `contact_eligible`, una disiscrizione di piattaforma o una iscrizione con
-- l'email: toglierle significherebbe cancellare decisioni reali.

BEGIN;

DO $do$
BEGIN
    IF EXISTS (SELECT 1 FROM communication_journeys WHERE trigger_type = 'contact_eligible')
    OR EXISTS (SELECT 1 FROM communication_email_optouts)
    OR EXISTS (SELECT 1 FROM communication_enrollments
                WHERE email_normalized_snapshot IS NOT NULL OR stima_id_snapshot IS NULL
                   OR paused_source IN ('appointment', 'commercial', 'consultation')
                   OR stop_reason IN ('platform_optout', 'email_invalid', 'duplicate_email')) THEN
        RAISE EXCEPTION '108 down: esistono dati della journey sul contatto; nessuna modifica eseguita';
    END IF;
END
$do$;

DROP TRIGGER IF EXISTS trg_comm_contact_enrollment_once ON communication_enrollments;
DROP FUNCTION IF EXISTS communication_contact_enrollment_once();
DROP INDEX IF EXISTS idx_comm_enroll_resume;
DROP INDEX IF EXISTS idx_comm_enroll_email_snapshot;
DROP INDEX IF EXISTS uq_comm_enroll_open_per_email;

ALTER TABLE communication_enrollments DROP CONSTRAINT comm_enroll_matrix_chk;
ALTER TABLE communication_enrollments ADD CONSTRAINT comm_enroll_matrix_chk CHECK (
    (status = 'active'
        AND next_step_no IS NOT NULL AND next_action_at IS NOT NULL AND next_action_kind IS NOT NULL
        AND paused_at IS NULL AND paused_source IS NULL AND paused_by_operator_user_id IS NULL
        AND stopped_at IS NULL AND stop_reason IS NULL AND stopped_by_operator_user_id IS NULL
        AND completed_at IS NULL)
 OR (status = 'paused'
        AND next_step_no IS NOT NULL AND next_action_at IS NOT NULL AND next_action_kind IS NOT NULL
        AND paused_at IS NOT NULL AND paused_source IS NOT NULL AND paused_by_operator_user_id IS NOT NULL
        AND stopped_at IS NULL AND stop_reason IS NULL AND stopped_by_operator_user_id IS NULL
        AND completed_at IS NULL)
 OR (status = 'completed'
        AND next_step_no IS NULL AND next_action_at IS NULL AND next_action_kind IS NULL
        AND awaiting_since IS NULL
        AND paused_at IS NULL AND paused_source IS NULL AND paused_by_operator_user_id IS NULL
        AND stopped_at IS NULL AND stop_reason IS NULL AND stopped_by_operator_user_id IS NULL
        AND completed_at IS NOT NULL)
 OR (status = 'stopped'
        AND next_step_no IS NULL AND next_action_at IS NULL AND next_action_kind IS NULL
        AND awaiting_since IS NULL
        AND paused_at IS NULL AND paused_source IS NULL AND paused_by_operator_user_id IS NULL
        AND stopped_at IS NOT NULL AND stop_reason IS NOT NULL
        AND completed_at IS NULL));

ALTER TABLE communication_enrollments DROP CONSTRAINT comm_enroll_stop_reason_chk;
ALTER TABLE communication_enrollments ADD CONSTRAINT comm_enroll_stop_reason_chk CHECK(stop_reason IS NULL OR stop_reason IN (
 'mandate_signed','acquisition_linked','inspection','consultation_requested','lead_closed','contact_inactive',
 'consent_revoked','consent_not_granted','consent_inconsistent','expired_on_resume','operator','request_too_old'));
ALTER TABLE communication_enrollments DROP CONSTRAINT comm_enroll_paused_source_chk;
ALTER TABLE communication_enrollments ADD CONSTRAINT comm_enroll_paused_source_chk
    CHECK (paused_source IS NULL OR paused_source IN ('enrollment', 'contact_control'));
ALTER TABLE communication_enrollments DROP CONSTRAINT comm_enroll_email_snapshot_chk;
ALTER TABLE communication_enrollments DROP COLUMN pause_ref;
ALTER TABLE communication_enrollments DROP COLUMN resume_after;
ALTER TABLE communication_enrollments DROP COLUMN email_normalized_snapshot;
ALTER TABLE communication_enrollments ALTER COLUMN stima_id_snapshot SET NOT NULL;

ALTER TABLE communication_journeys DROP CONSTRAINT comm_journeys_policy_chk;
ALTER TABLE communication_journeys DROP COLUMN sequence_policy;
ALTER TABLE communication_journeys DROP CONSTRAINT comm_journeys_trigger_chk;
ALTER TABLE communication_journeys ADD CONSTRAINT comm_journeys_trigger_chk CHECK(trigger_type IN ('stima_pdf_sent','stima_request_registered'));

ALTER TABLE communication_messages DROP CONSTRAINT communication_messages_reason_code_chk;
ALTER TABLE communication_messages ADD CONSTRAINT communication_messages_reason_code_chk CHECK(reason_code IN ('stima_pdf','operator_manual','operator_reply','owner_login_link','appointment_reminder','m1','m2','m3','m4','m5','m6','m7','m8','m9','m10'));
ALTER TABLE communication_journey_steps DROP CONSTRAINT comm_steps_reason_chk;
ALTER TABLE communication_journey_steps ADD CONSTRAINT comm_steps_reason_chk CHECK(reason_code IN ('m1','m2','m3','m4','m5','m6','m7','m8','m9','m10'));

DROP TRIGGER IF EXISTS trg_comm_optouts_append_only ON communication_email_optouts;
DROP FUNCTION IF EXISTS communication_email_optouts_append_only();
DROP TABLE communication_email_optouts;

-- La guardia delle iscrizioni com'era in 097, parola per parola.
CREATE OR REPLACE FUNCTION communication_enrollments_guard() RETURNS trigger AS $fn$
DECLARE
    a_stima   BIGINT;
    v_journey RECORD;
    a_msg     BIGINT;
    v_sent_at TIMESTAMPTZ;
    v_reason  TEXT;
    v_status  TEXT;
BEGIN
    IF TG_OP = 'INSERT' THEN
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
        SELECT * INTO v_journey FROM communication_journeys WHERE id = NEW.journey_id AND agency_id = NEW.agency_id;
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
        IF NOT EXISTS (SELECT 1 FROM communication_journeys
                        WHERE id = NEW.journey_id AND agency_id = NEW.agency_id) THEN
            RAISE EXCEPTION 'P29-3 tenancy: journey % is not in agency %', NEW.journey_id, NEW.agency_id;
        END IF;
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

COMMIT;
