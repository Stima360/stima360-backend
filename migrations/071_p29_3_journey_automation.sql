-- 071 - P29-3B: il motore delle journey commerciali, la sua FONDAZIONE.
--
-- COSA C'E' QUI, E COSA NON C'E'
--
-- Quattro tabelle e un ALTER. Nessun seed, nessun backfill, nessun cron: qui
-- si crea il posto in cui una sequenza commerciale puo' esistere, non la
-- sequenza. Il testo dei messaggi, lo scanner che iscrive, il tick che avanza
-- sono fasi successive e hanno i loro gate.
--
-- IL LEDGER RESTA L'UNICA COSA CHE SPEDISCE. Un messaggio pianificato di una
-- journey E' una riga `queued` di `communication_messages` con `scheduled_at`
-- nel futuro; non esiste una tabella di "azioni pianificate" che duplicherebbe
-- quello stato. Al ledger si aggiungono tre colonne di PROVENIENZA -
-- `enrollment_id`, `step_no`, `run_no` - scritte all'INSERT e mai piu'.
--
-- LE MATRICI DI STATO SONO NEL DATABASE, non nel codice: uno stato terminale
-- con campi di avanzamento ancora scritti, una pausa senza chi l'ha decisa,
-- un `stopped` senza ragione sono IRRAPPRESENTABILI, come per la 070.
--
-- LA TENANCY DELLE ENROLLMENT E' DERIVATA E VERIFICATA: `agency_id` c'e' (come
-- sul ledger, con la FK composita verso `contacts`), e un trigger impone che
-- stima e messaggio-trigger appartengano alla stessa agenzia dell'iscrizione.
-- Gli attori si verificano QUANDO VENGONO SCRITTI, non a ogni UPDATE: e' la
-- lezione del `SET NULL` di LMC-15.
--
-- LA `down` RIPRISTINA LA GUARDIA DEL LEDGER ALLA VERSIONE POST-065, non a
-- quella della 064: la 065 ha cambiato la politica di purge (genitore di
-- lifecycle, `contact_id` nullable) e un rollback di questa migration non
-- deve far regredire quella.

-- ---------------------------------------------------------------------------
-- communication_journeys: la definizione, versionata, per agenzia.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS communication_journeys (
    id              BIGSERIAL    PRIMARY KEY,
    agency_id       BIGINT       NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,
    journey_key     VARCHAR(60)  NOT NULL,
    version         INTEGER      NOT NULL,
    trigger_type    VARCHAR(40)  NOT NULL,
    status          VARCHAR(20)  NOT NULL DEFAULT 'draft',
    name            VARCHAR(120) NOT NULL,
    -- V1: il fuso in cui si valuta la finestra di invio. Non e' quello della
    -- macchina, che su Render e' UTC e su un portatile e' quello del
    -- portatile.
    send_timezone   VARCHAR(64)  NOT NULL DEFAULT 'Europe/Rome',

    -- Chi l'ha creata e chi l'ha attivata: una persona O il sistema. La
    -- journey predefinita viene provisionata dal sistema, e inventare un
    -- operatore per firmarla sarebbe un audit falso.
    created_by_type                 VARCHAR(20) NOT NULL,
    created_by_operator_user_id     BIGINT REFERENCES operator_users(id) ON DELETE RESTRICT,
    activated_at                    TIMESTAMPTZ,
    activated_by_type               VARCHAR(20),
    activated_by_operator_user_id   BIGINT REFERENCES operator_users(id) ON DELETE RESTRICT,
    retired_at                      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT comm_journeys_key_chk       CHECK (journey_key ~ '^[a-z][a-z0-9_]{1,59}$'),
    CONSTRAINT comm_journeys_version_chk   CHECK (version >= 1),
    CONSTRAINT comm_journeys_trigger_chk   CHECK (trigger_type IN ('stima_pdf_sent')),
    CONSTRAINT comm_journeys_status_chk    CHECK (status IN ('draft', 'active', 'retired')),
    CONSTRAINT comm_journeys_name_chk      CHECK (BTRIM(name) <> ''),
    CONSTRAINT comm_journeys_tz_chk        CHECK (BTRIM(send_timezone) <> ''),
    CONSTRAINT comm_journeys_created_by_type_chk
        CHECK (created_by_type IN ('system', 'operator')),
    CONSTRAINT comm_journeys_created_actor_chk
        CHECK ((created_by_type = 'operator') = (created_by_operator_user_id IS NOT NULL)),
    CONSTRAINT comm_journeys_activated_by_type_chk
        CHECK (activated_by_type IS NULL OR activated_by_type IN ('system', 'operator')),
    CONSTRAINT comm_journeys_activated_actor_chk
        CHECK ((activated_by_type = 'operator') = (activated_by_operator_user_id IS NOT NULL)),
    -- LA MATRICE DELL'ATTIVAZIONE. `draft`: niente. `active`: quando e da
    -- chi. `retired`: e' stata attiva (quindi conserva quando e da chi) e sa
    -- quando e' finita.
    CONSTRAINT comm_journeys_status_matrix_chk CHECK (
        (status = 'draft'
            AND activated_at IS NULL AND activated_by_type IS NULL AND retired_at IS NULL)
     OR (status = 'active'
            AND activated_at IS NOT NULL AND activated_by_type IS NOT NULL AND retired_at IS NULL)
     OR (status = 'retired'
            AND activated_at IS NOT NULL AND activated_by_type IS NOT NULL AND retired_at IS NOT NULL
            AND retired_at >= activated_at)),

    CONSTRAINT comm_journeys_version_unq UNIQUE (agency_id, journey_key, version)
);

-- Una sola versione attiva per chiave: la successiva si pubblica ritirando
-- la precedente, e le iscrizioni in corso restano sulla loro.
CREATE UNIQUE INDEX IF NOT EXISTS uq_comm_journeys_active
    ON communication_journeys (agency_id, journey_key) WHERE status = 'active';

-- ---------------------------------------------------------------------------
-- communication_journey_steps: i passi, ordinati, con il loro template.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS communication_journey_steps (
    id                  BIGSERIAL   PRIMARY KEY,
    journey_id          BIGINT      NOT NULL REFERENCES communication_journeys(id) ON DELETE CASCADE,
    step_no             INTEGER     NOT NULL,
    step_key            VARCHAR(10) NOT NULL,
    -- I codici che il CHECK del ledger gia' ammette (064/067): la 071 NON lo
    -- tocca, e questo CHECK ne e' il sottoinsieme commerciale.
    reason_code         VARCHAR(60) NOT NULL,
    channel             VARCHAR(20) NOT NULL,
    communication_type  VARCHAR(20) NOT NULL,
    default_mode        VARCHAR(20) NOT NULL,
    delay_from          VARCHAR(20) NOT NULL,
    delay_seconds       INTEGER     NOT NULL,
    send_window         JSONB       NOT NULL DEFAULT '{}'::jsonb,
    template_key        VARCHAR(80) NOT NULL,
    template_version    INTEGER     NOT NULL,
    stop_on             JSONB       NOT NULL DEFAULT '[]'::jsonb,
    active              BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT comm_steps_no_chk        CHECK (step_no >= 1),
    CONSTRAINT comm_steps_key_chk       CHECK (step_key ~ '^[A-Z][A-Z0-9]{0,9}$'),
    CONSTRAINT comm_steps_reason_chk    CHECK (reason_code IN ('m1', 'm2', 'm3', 'm4', 'm5')),
    CONSTRAINT comm_steps_channel_chk   CHECK (channel IN ('email', 'whatsapp')),
    CONSTRAINT comm_steps_type_chk      CHECK (communication_type IN ('service', 'marketing')),
    -- `manual` non e' un modo di un passo pianificato: un passo o parte da
    -- solo o aspetta l'agente.
    CONSTRAINT comm_steps_mode_chk      CHECK (default_mode IN ('automatic', 'assisted')),
    CONSTRAINT comm_steps_delay_from_chk CHECK (delay_from IN ('trigger', 'previous_step_sent')),
    CONSTRAINT comm_steps_delay_chk     CHECK (delay_seconds >= 0),
    CONSTRAINT comm_steps_template_chk  CHECK (BTRIM(template_key) <> '' AND template_version >= 1),
    CONSTRAINT comm_steps_stop_on_chk   CHECK (jsonb_typeof(stop_on) = 'array'),
    CONSTRAINT comm_steps_window_chk    CHECK (jsonb_typeof(send_window) = 'object'),
    CONSTRAINT comm_steps_no_unq        UNIQUE (journey_id, step_no),
    CONSTRAINT comm_steps_key_unq       UNIQUE (journey_id, step_key)
);

-- ---------------------------------------------------------------------------
-- communication_enrollments: il fatto - questo contatto, in questa journey,
-- per questa stima, a partire da questo messaggio.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS communication_enrollments (
    id                  BIGSERIAL   PRIMARY KEY,
    agency_id           BIGINT      NOT NULL,
    journey_id          BIGINT      NOT NULL REFERENCES communication_journeys(id) ON DELETE RESTRICT,
    contact_id          BIGINT      NOT NULL,
    lead_id             BIGINT      REFERENCES leads(id) ON DELETE SET NULL,
    -- La stima, con la disciplina di LMC-15: obbligatoria all'INSERT,
    -- SET NULL se cancellata, e lo snapshot - assegnato dal database, mai dal
    -- chiamante - conserva il numero.
    stima_id            INTEGER     REFERENCES stime(id) ON DELETE SET NULL,
    stima_id_snapshot   INTEGER     NOT NULL,
    -- Il fatto autorevole da cui nasce: la mail della stima, `sent`.
    trigger_message_id  BIGINT      NOT NULL REFERENCES communication_messages(id) ON DELETE RESTRICT,
    trigger_sent_at     TIMESTAMPTZ NOT NULL,

    status              VARCHAR(20) NOT NULL DEFAULT 'active',
    next_step_no        INTEGER,
    next_action_at      TIMESTAMPTZ,
    next_action_kind    VARCHAR(20),
    awaiting_since      TIMESTAMPTZ,
    run_no              INTEGER     NOT NULL DEFAULT 1,

    stop_reason         VARCHAR(40),
    stop_event_id       BIGINT      REFERENCES seller_timeline_events(id) ON DELETE SET NULL,

    enrolled_by_type                VARCHAR(20) NOT NULL,
    enrolled_by_operator_user_id    BIGINT REFERENCES operator_users(id) ON DELETE RESTRICT,
    paused_at                       TIMESTAMPTZ,
    paused_source                   VARCHAR(20),
    paused_by_operator_user_id      BIGINT REFERENCES operator_users(id) ON DELETE RESTRICT,
    stopped_at                      TIMESTAMPTZ,
    stopped_by_operator_user_id     BIGINT REFERENCES operator_users(id) ON DELETE RESTRICT,
    completed_at                    TIMESTAMPTZ,

    idempotency_key     VARCHAR(200) NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT comm_enroll_contact_same_agency_fk
        FOREIGN KEY (agency_id, contact_id) REFERENCES contacts (agency_id, id) ON DELETE CASCADE,
    CONSTRAINT comm_enroll_status_chk
        CHECK (status IN ('active', 'paused', 'completed', 'stopped')),
    CONSTRAINT comm_enroll_kind_chk
        CHECK (next_action_kind IS NULL OR next_action_kind IN ('enqueue', 'await_operator')),
    CONSTRAINT comm_enroll_run_chk       CHECK (run_no >= 1),
    CONSTRAINT comm_enroll_step_chk      CHECK (next_step_no IS NULL OR next_step_no >= 1),
    CONSTRAINT comm_enroll_snapshot_chk  CHECK (stima_id IS NULL OR stima_id = stima_id_snapshot),
    CONSTRAINT comm_enroll_idem_chk      CHECK (BTRIM(idempotency_key) <> ''),
    CONSTRAINT comm_enroll_stop_reason_chk CHECK (stop_reason IS NULL OR stop_reason IN (
        'mandate_signed', 'acquisition_linked', 'inspection', 'consultation_requested',
        'lead_closed', 'contact_inactive',
        'consent_revoked', 'consent_not_granted', 'consent_inconsistent',
        'expired_on_resume', 'operator')),
    CONSTRAINT comm_enroll_paused_source_chk
        CHECK (paused_source IS NULL OR paused_source IN ('enrollment', 'contact_control')),
    CONSTRAINT comm_enroll_enrolled_by_type_chk
        CHECK (enrolled_by_type IN ('system', 'operator')),
    CONSTRAINT comm_enroll_enrolled_actor_chk
        CHECK ((enrolled_by_type = 'operator') = (enrolled_by_operator_user_id IS NOT NULL)),

    -- LA MATRICE, STATO PER STATO. Ogni colonna di stato compare in ogni
    -- ramo: cio' che non e' nominato come NOT NULL e' NULL.
    CONSTRAINT comm_enroll_matrix_chk CHECK (
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
            AND completed_at IS NULL)),
    -- L'attesa dell'agente esiste esattamente quando il prossimo passo la
    -- richiede.
    CONSTRAINT comm_enroll_awaiting_chk
        CHECK ((next_action_kind = 'await_operator') = (awaiting_since IS NOT NULL)),
    -- Uno stop deciso da una persona porta la persona; uno stop di sistema
    -- non ne inventa una.
    CONSTRAINT comm_enroll_stop_actor_chk
        CHECK ((stop_reason = 'operator') = (stopped_by_operator_user_id IS NOT NULL)),

    CONSTRAINT comm_enroll_idem_unq     UNIQUE (agency_id, idempotency_key),
    CONSTRAINT comm_enroll_stima_unq    UNIQUE (agency_id, journey_id, stima_id),
    -- Lo stesso fatto non iscrive due volte alla STESSA journey; una journey
    -- diversa puo' partire dallo stesso fatto.
    CONSTRAINT comm_enroll_trigger_unq  UNIQUE (journey_id, trigger_message_id)
);

-- L'anti-spam di V1: UNA sola journey commerciale aperta per contatto.
CREATE UNIQUE INDEX IF NOT EXISTS uq_comm_enroll_open_per_contact
    ON communication_enrollments (agency_id, contact_id) WHERE status IN ('active', 'paused');
CREATE INDEX IF NOT EXISTS idx_comm_enroll_due
    ON communication_enrollments (agency_id, next_action_at) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_comm_enroll_snapshot
    ON communication_enrollments (stima_id_snapshot);

-- ---------------------------------------------------------------------------
-- communication_automation_controls: lo STATO CORRENTE delle automazioni per
-- contatto. La storia sta sulla timeline.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS communication_automation_controls (
    id                          BIGSERIAL   PRIMARY KEY,
    agency_id                   BIGINT      NOT NULL,
    contact_id                  BIGINT      NOT NULL,
    paused                      BOOLEAN     NOT NULL DEFAULT FALSE,
    paused_at                   TIMESTAMPTZ,
    paused_by_operator_user_id  BIGINT REFERENCES operator_users(id) ON DELETE RESTRICT,
    pause_reason                VARCHAR(200),
    resumed_at                  TIMESTAMPTZ,
    resumed_by_operator_user_id BIGINT REFERENCES operator_users(id) ON DELETE RESTRICT,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT comm_controls_contact_same_agency_fk
        FOREIGN KEY (agency_id, contact_id) REFERENCES contacts (agency_id, id) ON DELETE CASCADE,
    CONSTRAINT comm_controls_contact_unq UNIQUE (agency_id, contact_id),
    CONSTRAINT comm_controls_reason_chk
        CHECK (pause_reason IS NULL OR BTRIM(pause_reason) <> ''),
    CONSTRAINT comm_controls_matrix_chk CHECK (
        (paused = TRUE
            AND paused_at IS NOT NULL AND paused_by_operator_user_id IS NOT NULL
            AND resumed_at IS NULL AND resumed_by_operator_user_id IS NULL)
     OR (paused = FALSE
            AND paused_at IS NULL AND paused_by_operator_user_id IS NULL AND pause_reason IS NULL
            AND ((resumed_at IS NULL) = (resumed_by_operator_user_id IS NULL))))
);

-- ---------------------------------------------------------------------------
-- Il ledger: tre colonne di PROVENIENZA.
-- ---------------------------------------------------------------------------
ALTER TABLE communication_messages
    ADD COLUMN IF NOT EXISTS enrollment_id BIGINT
        REFERENCES communication_enrollments(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS step_no INTEGER,
    ADD COLUMN IF NOT EXISTS run_no  INTEGER;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'communication_messages_enrollment_triple_chk'
                      AND conrelid = 'public.communication_messages'::regclass) THEN
        ALTER TABLE communication_messages
            ADD CONSTRAINT communication_messages_enrollment_triple_chk
            CHECK (num_nonnulls(enrollment_id, step_no, run_no) IN (0, 3));
    END IF;
END
$$;

-- Identita' del tentativo di passo: mai due righe per (passo, run).
CREATE UNIQUE INDEX IF NOT EXISTS uq_communication_messages_step_run
    ON communication_messages (enrollment_id, step_no, run_no)
    WHERE enrollment_id IS NOT NULL;
-- Mai due messaggi VIVI dello stesso passo: un `cancelled` (pausa) lascia il
-- posto al run successivo; un `sent` lo occupa per sempre.
CREATE UNIQUE INDEX IF NOT EXISTS uq_communication_messages_step_alive
    ON communication_messages (enrollment_id, step_no)
    WHERE enrollment_id IS NOT NULL AND status <> 'cancelled';

-- La guardia del ledger, con le tre colonne fra gli immutabili. Tutto il resto
-- e' IDENTICO alla versione della 065 (lifecycle parent, purge): la down la
-- ripristina esattamente.
CREATE OR REPLACE FUNCTION communication_messages_guard()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_genitore_sparito BOOLEAN;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        -- L'UNICO cambiamento ammesso alla provenienza: `enrollment_id` che
        -- va a NULL perche' l'iscrizione sta SPARENDO (ON DELETE SET NULL,
        -- quindi da dentro un'altra azione: pg_trigger_depth() > 1). Una
        -- iscrizione si cancella solo con il suo contatto o la sua agenzia -
        -- lo stesso purge che porta via il messaggio - ma la cascata puo'
        -- toccare il messaggio prima di cancellarlo, e la guardia non deve
        -- far fallire il purge. Un UPDATE diretto resta rifiutato.
        IF pg_trigger_depth() > 1
           AND OLD.enrollment_id IS NOT NULL AND NEW.enrollment_id IS NULL
           AND NOT EXISTS (SELECT 1 FROM communication_enrollments WHERE id = OLD.enrollment_id)
           AND NEW.step_no IS NOT DISTINCT FROM OLD.step_no
           AND NEW.run_no  IS NOT DISTINCT FROM OLD.run_no
        THEN
            NEW.step_no := NULL;
            NEW.run_no  := NULL;
            RETURN NEW;
        END IF;

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
        OR NEW.enrollment_id        IS DISTINCT FROM OLD.enrollment_id
        OR NEW.step_no              IS DISTINCT FROM OLD.step_no
        OR NEW.run_no               IS DISTINCT FROM OLD.run_no
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
        RETURN OLD;
    END IF;

    RAISE EXCEPTION
        'communication_messages: DELETE is refused (id=%). A message is removed only by physically purging a lifecycle parent it belongs to.',
        OLD.id;
END
$fn$;

-- ---------------------------------------------------------------------------
-- La guardia delle enrollment: snapshot dal database, tenancy derivata,
-- attori verificati quando scritti.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION comm_enrollment_actor_ok(p_actor_id BIGINT, p_agency_id BIGINT, p_campo TEXT)
RETURNS void AS $fn$
DECLARE
    v_platform BOOLEAN;
BEGIN
    IF p_actor_id IS NULL THEN RETURN; END IF;
    SELECT is_platform_admin INTO v_platform FROM operator_users WHERE id = p_actor_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'P29-3 actor: operator % (%) does not exist', p_actor_id, p_campo;
    END IF;
    IF v_platform THEN RETURN; END IF;
    IF NOT EXISTS (SELECT 1 FROM agency_memberships
                    WHERE operator_user_id = p_actor_id AND agency_id = p_agency_id
                      AND status = 'active') THEN
        RAISE EXCEPTION 'P29-3 actor: operator % (%) has no active membership in agency %',
            p_actor_id, p_campo, p_agency_id;
    END IF;
END;
$fn$ LANGUAGE plpgsql;

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

DROP TRIGGER IF EXISTS trg_communication_enrollments_guard ON communication_enrollments;
CREATE TRIGGER trg_communication_enrollments_guard
    BEFORE INSERT OR UPDATE ON communication_enrollments
    FOR EACH ROW EXECUTE FUNCTION communication_enrollments_guard();

-- ---------------------------------------------------------------------------
-- Verifica: le tabelle sono quelle che questo file dice.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE v_n integer;
BEGIN
    SELECT count(*) INTO v_n FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'communication_enrollments';
    IF v_n <> 28 THEN RAISE EXCEPTION 'P29-3 071: communication_enrollments has % columns, expected 28', v_n; END IF;
    SELECT count(*) INTO v_n FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'communication_journeys';
    IF v_n <> 16 THEN RAISE EXCEPTION 'P29-3 071: communication_journeys has % columns, expected 16', v_n; END IF;
    SELECT count(*) INTO v_n FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'communication_automation_controls';
    IF v_n <> 11 THEN RAISE EXCEPTION 'P29-3 071: communication_automation_controls has % columns, expected 11', v_n; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'uq_communication_messages_step_alive')
    OR NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'uq_communication_messages_step_run')
    OR NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'uq_comm_enroll_open_per_contact') THEN
        RAISE EXCEPTION 'P29-3 071: a unique index of the ledger link or the anti-spam rule is missing';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_communication_enrollments_guard') THEN
        RAISE EXCEPTION 'P29-3 071: the enrollment guard trigger is missing';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'communication_messages_guard'
                    AND prosrc LIKE '%enrollment_id%') THEN
        RAISE EXCEPTION 'P29-3 071: the ledger guard does not protect the provenance columns';
    END IF;
END
$do$;
