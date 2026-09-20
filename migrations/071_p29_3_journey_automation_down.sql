-- Down for 071. Toglie la fondazione delle journey.
--
-- RIFIUTA SE ESISTE ANCHE UNA SOLA ISCRIZIONE O UN SOLO CONTROLLO. Le journey
-- senza iscrizioni sono definizioni e si possono togliere; le iscrizioni sono
-- fatti - chi e' entrato in una sequenza, quando, perche' si e' fermato - e
-- nessun'altra tabella li conserva. Un messaggio del ledger che porta
-- `enrollment_id` e' un fatto a meta': le tre colonne sparirebbero e la
-- provenienza con loro, quindi anche quello blocca.
--
-- LA GUARDIA DEL LEDGER TORNA ALLA VERSIONE POST-065, NON A QUELLA DELLA 064.
-- La 065 ha cambiato la politica di purge - `contact_id` nullable, genitore di
-- lifecycle - e questa down non deve far regredire quella semantica: il testo
-- qui sotto e' COPIATO dalla 065, e un test lo confronta con l'originale.

BEGIN;

DO $$
DECLARE
    v_enr INTEGER := 0;
    v_ctl INTEGER := 0;
    v_msg INTEGER := 0;
BEGIN
    IF to_regclass('public.communication_enrollments') IS NOT NULL THEN
        SELECT count(*) INTO v_enr FROM communication_enrollments;
    END IF;
    IF to_regclass('public.communication_automation_controls') IS NOT NULL THEN
        SELECT count(*) INTO v_ctl FROM communication_automation_controls;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'communication_messages'
                  AND column_name = 'enrollment_id') THEN
        SELECT count(*) INTO v_msg FROM communication_messages WHERE enrollment_id IS NOT NULL;
    END IF;
    IF v_enr > 0 OR v_ctl > 0 OR v_msg > 0 THEN
        RAISE EXCEPTION
            'P29-3 071 down: % enrollment(s), % automation control(s) and % journey message(s) would lose their history. Nothing has been changed.',
            v_enr, v_ctl, v_msg;
    END IF;
END
$$;

-- 1. Il ledger: prima la guardia (che nomina le colonne), poi gli indici, poi
--    le colonne. L'ordine inverso lascerebbe una funzione che cita colonne
--    inesistenti.
CREATE OR REPLACE FUNCTION communication_messages_guard()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_genitore_sparito BOOLEAN;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.id                   IS DISTINCT FROM OLD.id
        OR NEW.agency_id            IS DISTINCT FROM OLD.agency_id
        OR NEW.contact_id           IS DISTINCT FROM OLD.contact_id
        OR NEW.channel              IS DISTINCT FROM OLD.channel
        OR NEW.communication_type   IS DISTINCT FROM OLD.communication_type
        OR NEW.mode                 IS DISTINCT FROM OLD.mode
        OR NEW.reason_code          IS DISTINCT FROM OLD.reason_code
        OR NEW.rendered_body        IS DISTINCT FROM OLD.rendered_body
        OR NEW.subject_snapshot     IS DISTINCT FROM OLD.subject_snapshot
        OR NEW.destination_snapshot IS DISTINCT FROM OLD.destination_snapshot
        OR NEW.idempotency_key      IS DISTINCT FROM OLD.idempotency_key
        OR NEW.created_at           IS DISTINCT FROM OLD.created_at
        THEN
            RAISE EXCEPTION
                'communication_messages: identity and snapshot columns are immutable (id=%). What a message says it was is not rewritable; record a new message instead.',
                OLD.id;
        END IF;
        RETURN NEW;
    END IF;

    v_genitore_sparito :=
        (OLD.contact_id IS NOT NULL
         AND NOT EXISTS (SELECT 1 FROM contacts WHERE id = OLD.contact_id))
        OR OLD.contact_id IS NULL;

    IF pg_trigger_depth() > 1 AND v_genitore_sparito THEN
        -- Purge: un genitore di questo messaggio sta sparendo. Il contenuto
        -- comunicativo personale lo segue.
        RETURN OLD;
    END IF;

    RAISE EXCEPTION
        'communication_messages: DELETE is refused (id=%). A message is removed only by physically purging a lifecycle parent it belongs to.',
        OLD.id;
END
$fn$;

DROP INDEX IF EXISTS uq_communication_messages_step_alive;
DROP INDEX IF EXISTS uq_communication_messages_step_run;
ALTER TABLE communication_messages
    DROP CONSTRAINT IF EXISTS communication_messages_enrollment_triple_chk;
ALTER TABLE communication_messages
    DROP COLUMN IF EXISTS run_no,
    DROP COLUMN IF EXISTS step_no,
    DROP COLUMN IF EXISTS enrollment_id;

-- 2. Le tabelle, figlie prima dei genitori.
DROP TRIGGER IF EXISTS trg_communication_enrollments_guard ON communication_enrollments;
DROP FUNCTION IF EXISTS communication_enrollments_guard();
DROP FUNCTION IF EXISTS comm_enrollment_actor_ok(BIGINT, BIGINT, TEXT);
DROP TABLE IF EXISTS communication_automation_controls;
DROP TABLE IF EXISTS communication_enrollments;
DROP TABLE IF EXISTS communication_journey_steps;
DROP TABLE IF EXISTS communication_journeys;

-- NON tocca: communication_messages_reason_code_chk, consent_*.

DELETE FROM schema_migrations WHERE version = '071_p29_3_journey_automation';

COMMIT;
