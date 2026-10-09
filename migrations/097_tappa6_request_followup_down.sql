-- Rifiuta rollback con fatti o ledger v2: nessuna perdita implicita.
BEGIN;
DO $fn$ BEGIN
 IF EXISTS(SELECT 1 FROM communication_journeys WHERE trigger_type='stima_request_registered')
 OR EXISTS(SELECT 1 FROM communication_enrollments WHERE stop_reason='request_too_old')
 OR EXISTS(SELECT 1 FROM communication_messages WHERE enrollment_id IS NOT NULL AND status<>'cancelled' GROUP BY enrollment_id,step_no HAVING count(*)>1)
 OR EXISTS(SELECT 1 FROM communication_messages WHERE reason_code IN ('m6','m7','m8','m9','m10')) THEN
  RAISE EXCEPTION 'T6: down blocked by retained v2 data';
 END IF;
END; $fn$;
DROP TRIGGER trg_t6_appointments_fence ON appointments;
DROP TRIGGER trg_t6_acquisitions_fence ON acquisitions;
DROP TRIGGER trg_t6_stima_acquisitions_fence ON stima_acquisitions;
DROP TRIGGER trg_t6_stima_inspections_fence ON stima_inspections;
DROP TRIGGER trg_t6_seller_timeline_events_fence ON seller_timeline_events;
DROP TRIGGER trg_t6_leads_fence ON leads;
DROP TRIGGER trg_t6_lead_stime_fence ON lead_stime;
DROP TRIGGER trg_t6_communication_automation_controls_fence ON communication_automation_controls;
DROP TRIGGER trg_t6_property_contacts_fence ON property_contacts;
DROP FUNCTION communication_contact_stop_fence();
DROP TRIGGER trg_t6_messages_contract ON communication_messages;
DROP FUNCTION communication_tappa6_message_contract();
DROP INDEX uq_communication_messages_step_alive;
ALTER TABLE communication_messages DROP COLUMN tappa6_cycle;
CREATE UNIQUE INDEX uq_communication_messages_step_alive ON communication_messages(enrollment_id,step_no)
 WHERE enrollment_id IS NOT NULL AND status <> 'cancelled';
CREATE OR REPLACE FUNCTION communication_enrollments_guard() RETURNS trigger AS $fn$
DECLARE
    a_stima   BIGINT;
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
        SELECT agency_id, sent_at, reason_code, status
          INTO a_msg, v_sent_at, v_reason, v_status
          FROM communication_messages WHERE id = NEW.trigger_message_id;
        IF a_msg IS NULL OR a_msg <> NEW.agency_id THEN
            RAISE EXCEPTION 'P29-3 tenancy: trigger message % is not in agency %', NEW.trigger_message_id, NEW.agency_id;
        END IF;
        IF v_reason <> 'stima_pdf' OR v_status <> 'sent' OR v_sent_at IS NULL THEN
            RAISE EXCEPTION 'P29-3: trigger message % is not a sent stima_pdf', NEW.trigger_message_id;
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
ALTER TABLE communication_enrollments ALTER COLUMN trigger_message_id SET NOT NULL;
DROP TRIGGER trg_t6_crm_registration_guard ON stime;
DROP FUNCTION communication_crm_registration_guard();
ALTER TABLE stime DROP COLUMN crm_registered_at;
ALTER TABLE communication_messages DROP CONSTRAINT communication_messages_reason_code_chk;
ALTER TABLE communication_messages ADD CONSTRAINT communication_messages_reason_code_chk CHECK(reason_code IN ('stima_pdf','operator_manual','operator_reply','owner_login_link','appointment_reminder','m1','m2','m3','m4','m5'));
ALTER TABLE communication_journey_steps DROP CONSTRAINT comm_steps_reason_chk;
ALTER TABLE communication_journey_steps ADD CONSTRAINT comm_steps_reason_chk CHECK(reason_code IN ('m1','m2','m3','m4','m5'));
ALTER TABLE communication_journeys DROP CONSTRAINT comm_journeys_trigger_chk;
ALTER TABLE communication_journeys ADD CONSTRAINT comm_journeys_trigger_chk CHECK(trigger_type IN ('stima_pdf_sent'));
DELETE FROM schema_migrations WHERE version='097_tappa6_request_followup';
ALTER TABLE communication_enrollments DROP CONSTRAINT comm_enroll_stop_reason_chk;
ALTER TABLE communication_enrollments ADD CONSTRAINT comm_enroll_stop_reason_chk CHECK(stop_reason IS NULL OR stop_reason IN (
 'mandate_signed','acquisition_linked','inspection','consultation_requested','lead_closed','contact_inactive',
 'consent_revoked','consent_not_granted','consent_inconsistent','expired_on_resume','operator'));
COMMIT;
