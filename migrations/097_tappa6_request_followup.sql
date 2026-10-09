-- Tappa 6 REV 2: richiesta registrata nel CRM, indipendente dal PDF. Nessuna attivazione.
ALTER TABLE communication_messages DROP CONSTRAINT communication_messages_reason_code_chk;
ALTER TABLE communication_messages ADD CONSTRAINT communication_messages_reason_code_chk CHECK(reason_code IN ('stima_pdf','operator_manual','operator_reply','owner_login_link','appointment_reminder','m1','m2','m3','m4','m5','m6','m7','m8','m9','m10'));
ALTER TABLE communication_journey_steps DROP CONSTRAINT comm_steps_reason_chk;
ALTER TABLE communication_journey_steps ADD CONSTRAINT comm_steps_reason_chk CHECK(reason_code IN ('m1','m2','m3','m4','m5','m6','m7','m8','m9','m10'));
ALTER TABLE communication_journeys DROP CONSTRAINT comm_journeys_trigger_chk;
ALTER TABLE communication_journeys ADD CONSTRAINT comm_journeys_trigger_chk CHECK(trigger_type IN ('stima_pdf_sent','stima_request_registered'));

-- Existing rows are NOT stamped with migration/activation time. Only
-- agency-scoped CRM receipt/import ledgers can certify a prior registration.
ALTER TABLE stime ADD COLUMN crm_registered_at TIMESTAMPTZ;
UPDATE stime s SET crm_registered_at = registered.at FROM (
 SELECT stima_id,agency_id,min(created_at) AS at FROM (
  SELECT stima_id,agency_id,created_at FROM public_submission_receipts WHERE kind='quick' AND voided_at IS NULL
  UNION ALL
  SELECT crm_id AS stima_id,agency_id,created_at FROM site_import_records WHERE source_table='stime' AND crm_id IS NOT NULL
 ) facts GROUP BY stima_id,agency_id
) registered WHERE s.id=registered.stima_id AND s.agency_id=registered.agency_id
 AND NOT EXISTS(SELECT 1 FROM site_import_baselines b,jsonb_array_elements_text(b.ids->'stime') h WHERE h::integer=s.id);
ALTER TABLE stime ALTER COLUMN crm_registered_at SET DEFAULT clock_timestamp();
CREATE FUNCTION communication_crm_registration_guard() RETURNS trigger AS $fn$
BEGIN
 IF NEW.crm_registered_at IS DISTINCT FROM OLD.crm_registered_at THEN
  RAISE EXCEPTION 'T6: CRM registration timestamp is immutable';
 END IF;
 RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;
CREATE TRIGGER trg_t6_crm_registration_guard BEFORE UPDATE OF crm_registered_at ON stime
 FOR EACH ROW EXECUTE FUNCTION communication_crm_registration_guard();
ALTER TABLE communication_enrollments ALTER COLUMN trigger_message_id DROP NOT NULL;
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
ALTER TABLE communication_enrollments DROP CONSTRAINT comm_enroll_stop_reason_chk;
ALTER TABLE communication_enrollments ADD CONSTRAINT comm_enroll_stop_reason_chk CHECK(stop_reason IS NULL OR stop_reason IN (
 'mandate_signed','acquisition_linked','inspection','consultation_requested','lead_closed','contact_inactive',
 'consent_revoked','consent_not_granted','consent_inconsistent','expired_on_resume','operator','request_too_old'));

-- Lock writers individually; no existing protection is disabled.
CREATE FUNCTION communication_contact_stop_fence() RETURNS trigger AS $fn$
DECLARE data JSONB; old_data JSONB; cid BIGINT; agency BIGINT;
BEGIN
 data := to_jsonb(NEW);
 old_data := CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE '{}'::jsonb END;
 FOR cid,agency IN
   SELECT DISTINCT c.id,c.agency_id FROM contacts c WHERE
     (c.id IN ((data->>'contact_id')::bigint,(old_data->>'contact_id')::bigint,
               (data->>'owner_contact_id')::bigint,(old_data->>'owner_contact_id')::bigint)
       AND c.agency_id IN ((data->>'agency_id')::bigint,(old_data->>'agency_id')::bigint))
     OR EXISTS(SELECT 1 FROM leads l LEFT JOIN lead_stime ls ON ls.lead_id=l.id
               WHERE l.contact_id=c.id AND l.agency_id=c.agency_id AND
               (l.id IN ((data->>'lead_id')::bigint,(old_data->>'lead_id')::bigint)
                 OR ls.stima_id IN ((data->>'stima_id')::integer,(old_data->>'stima_id')::integer,
                   (data->>'stima_id_snapshot')::integer,(old_data->>'stima_id_snapshot')::integer)))
     OR (c.id IN ((data->>'contact_id')::bigint,(old_data->>'contact_id')::bigint)
         AND EXISTS(SELECT 1 FROM properties p WHERE p.agency_id=c.agency_id
             AND p.id IN ((data->>'property_id')::bigint,(old_data->>'property_id')::bigint)))
     OR EXISTS(SELECT 1 FROM property_contacts pc JOIN properties p ON p.id=pc.property_id
               WHERE pc.contact_id=c.id AND p.agency_id=c.agency_id AND pc.role IN ('owner','seller')
                 AND pc.property_id IN ((data->>'property_id')::bigint,(old_data->>'property_id')::bigint))
   ORDER BY c.id,c.agency_id
 LOOP
   PERFORM 1 FROM contacts c WHERE c.id=cid AND c.agency_id=agency FOR UPDATE;
 END LOOP;
 RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;
CREATE TRIGGER trg_t6_appointments_fence BEFORE INSERT OR UPDATE ON appointments
 FOR EACH ROW EXECUTE FUNCTION communication_contact_stop_fence();
CREATE TRIGGER trg_t6_acquisitions_fence BEFORE INSERT OR UPDATE ON acquisitions
 FOR EACH ROW EXECUTE FUNCTION communication_contact_stop_fence();
CREATE TRIGGER trg_t6_stima_acquisitions_fence BEFORE INSERT OR UPDATE ON stima_acquisitions
 FOR EACH ROW EXECUTE FUNCTION communication_contact_stop_fence();
CREATE TRIGGER trg_t6_stima_inspections_fence BEFORE INSERT OR UPDATE ON stima_inspections
 FOR EACH ROW EXECUTE FUNCTION communication_contact_stop_fence();
CREATE TRIGGER trg_t6_seller_timeline_events_fence BEFORE INSERT OR UPDATE ON seller_timeline_events
 FOR EACH ROW EXECUTE FUNCTION communication_contact_stop_fence();
CREATE TRIGGER trg_t6_leads_fence BEFORE INSERT OR UPDATE ON leads
 FOR EACH ROW EXECUTE FUNCTION communication_contact_stop_fence();
CREATE TRIGGER trg_t6_lead_stime_fence BEFORE INSERT OR UPDATE ON lead_stime
 FOR EACH ROW EXECUTE FUNCTION communication_contact_stop_fence();
CREATE TRIGGER trg_t6_communication_automation_controls_fence BEFORE INSERT OR UPDATE ON communication_automation_controls
 FOR EACH ROW EXECUTE FUNCTION communication_contact_stop_fence();

CREATE TRIGGER trg_t6_property_contacts_fence BEFORE INSERT OR UPDATE ON property_contacts
 FOR EACH ROW EXECUTE FUNCTION communication_contact_stop_fence();

-- V1 keeps its original live-step exclusion. The derived flag admits only
-- certified request journeys to repeat a step in another run (071 step_run).
ALTER TABLE communication_messages ADD COLUMN tappa6_cycle BOOLEAN NOT NULL DEFAULT FALSE;
CREATE FUNCTION communication_tappa6_message_contract() RETURNS trigger AS $fn$
DECLARE e RECORD; step RECORD;
BEGIN
 IF TG_OP='UPDATE' THEN
  IF NEW.tappa6_cycle IS DISTINCT FROM OLD.tappa6_cycle THEN
   RAISE EXCEPTION 'T6: cycle provenance is immutable';
  END IF;
  IF OLD.tappa6_cycle AND (
      NEW.template_key IS DISTINCT FROM OLD.template_key OR NEW.template_version IS DISTINCT FROM OLD.template_version
      OR (NEW.stima_id IS DISTINCT FROM OLD.stima_id AND NOT (pg_trigger_depth()>1 AND NEW.stima_id IS NULL))
      OR (NEW.lead_id IS DISTINCT FROM OLD.lead_id AND NOT (pg_trigger_depth()>1 AND NEW.lead_id IS NULL))) THEN
   RAISE EXCEPTION 'T6: message provenance is immutable (FK detach only)';
  END IF;
  RETURN NEW;
 END IF;
 SELECT * INTO e FROM communication_enrollments WHERE id=NEW.enrollment_id;
 NEW.tappa6_cycle := e.id IS NOT NULL AND e.trigger_message_id IS NULL;
 IF e.id IS NOT NULL AND e.trigger_message_id IS NULL THEN
  SELECT * INTO step FROM communication_journey_steps WHERE journey_id=e.journey_id AND step_no=NEW.step_no;
  IF step.id IS NULL OR NEW.agency_id IS DISTINCT FROM e.agency_id OR NEW.contact_id IS DISTINCT FROM e.contact_id
     OR NEW.stima_id IS DISTINCT FROM e.stima_id OR NEW.lead_id IS DISTINCT FROM e.lead_id
     OR NEW.reason_code IS DISTINCT FROM step.reason_code OR NEW.template_key IS DISTINCT FROM step.template_key
     OR NEW.template_version IS DISTINCT FROM step.template_version THEN
   RAISE EXCEPTION 'T6: invalid message provenance';
  END IF;
  IF NEW.status<>'queued' OR e.status<>'active' OR NEW.run_no<>e.run_no OR NEW.step_no<>e.next_step_no THEN
   RAISE EXCEPTION 'T6: message is not current active step/run';
  END IF;
 END IF;
 RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;
CREATE TRIGGER trg_t6_messages_contract BEFORE INSERT OR UPDATE ON communication_messages
 FOR EACH ROW EXECUTE FUNCTION communication_tappa6_message_contract();
DROP INDEX uq_communication_messages_step_alive;
CREATE UNIQUE INDEX uq_communication_messages_step_alive
 ON communication_messages(enrollment_id,step_no)
 WHERE enrollment_id IS NOT NULL AND status <> 'cancelled' AND NOT tappa6_cycle;
