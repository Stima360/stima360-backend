-- 072 - A30-1: l'AGENDA CRM, il MODELLO. `appointments` e' la fonte autorevole
-- degli appuntamenti degli agenti.
--
-- ADDITIVA. Crea un'estensione (se assente), due tabelle, cinque funzioni,
-- tre trigger e gli indici. Non tocca NESSUNA tabella esistente: non
-- `stime`, non `stime_dettagliate`, non `property_visits`, non
-- `stima_inspections`, non `tasks`/`activities`. Nessun seed, nessun backfill.
--
-- COSA NON C'E' QUI, DI PROPOSITO
--
--   * Nessuna API e nessuna UI (A30-2, A30-4).
--   * Nessun import legacy (A30-6) e nessun backfill di `stima_inspections`
--     (fase dedicata): `source` e `source_record_id` sono il posto in cui
--     arriveranno, idempotenti per costruzione.
--   * Nessuna integrazione Google (A30-9/10): le colonne `google_*` sono
--     predisposte e restano vuote; `google_sync_status` nasce `not_synced`.
--   * Nessun esito (A30-8): gli esiti si modellano prima di diventare colonne.
--
-- LA STIMA E' UN RIFERIMENTO MORBIDO
--
-- `stima_id` NON ha FK verso `stime` (decisione Q-A6b, regola P30: nessuna FK
-- dal CRM verso le tabelle del sito). Il database qui non legge MAI `stime`:
-- che la stima esista e sia della stessa agenzia lo verifica il SERVICE, una
-- volta, quando il riferimento viene scritto. Se la stima viene poi
-- cancellata, l'appuntamento resta e il numero resta: e' uno snapshot.
--
-- LA SOVRAPPOSIZIONE E' IRRAPPRESENTABILE
--
-- Il vincolo EXCLUDE e' la garanzia finale: due appuntamenti che BLOCCANO lo
-- stesso agente non possono avere intervalli (buffer compresi) che si
-- toccano, qualunque sia il percorso di scrittura - service, psql, un bug. Il
-- service aggiunge davanti un lock per agente e un controllo leggibile, ma la
-- verita' e' qui. Gli intervalli sono semiaperti `[)`: 10:00-11:00 e
-- 11:00-12:00 NON sono in conflitto.
--
-- Bloccano: scheduled, confirmed, completed, no_show.
-- Non bloccano: requested (richiesta da smistare), cancelled, rescheduled (la
-- riga vecchia; lo spostamento crea una riga nuova con `rescheduled_from_id`).
--
-- `blocked_range` e' una colonna VERA scritta dal trigger, non un'espressione
-- d'indice ne' una colonna generata: `timestamptz - interval` e' STABLE, non
-- IMMUTABLE, e PostgreSQL non lo ammette in nessuno dei due posti.
--
-- I DATI DI PROVA (Q7 del GATE A30-1)
--
-- A runtime un appuntamento non si cancella MAI. L'unica eccezione e' il
-- dato di prova: `source = 'a30_test'` con un `test_run_id` obbligatorio.
-- Lo cancella soltanto `a30_test_purge(run_id)`, che tocca solo quella corsa
-- e porta via i suoi eventi. Nessun altro percorso sblocca la DELETE.
--
-- L'INSERIMENTO di un dato di prova, la purge e ogni eccezione alla DELETE
-- sono in ALLOWLIST, non in blacklist:
-- funzionano SOLO se `current_database()` e' uno dei nomi TEST certificati
-- dal progetto (oggi `stima360_db_test`, lo stesso nome che esigono la
-- certificazione live P26-6 e le migration 010/011). Qualunque altro nome -
-- PROD, sconosciuto, simile - fallisce chiuso.
--
-- IL REGISTRO LO SCRIVE IL REPOSITORY, NON UN TRIGGER
--
-- `appointment_events` e' una tabella di tenant, e la certificazione P26-6
-- (serie 100, test 103d) vieta che un trigger scriva in una tabella di tenant:
-- sarebbe una scrittura che nessun sorgente Python nomina. Quindi ogni evento
-- lo scrive `appointments/repository.py`, in chiaro, nella stessa transazione
-- della modifica. Il database garantisce il resto: il registro e'
-- append-only, e un appuntamento non si cancella, si annulla.

CREATE EXTENSION IF NOT EXISTS btree_gist;

-- ---------------------------------------------------------------------------
-- appointments
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS appointments (
    id                  BIGSERIAL    PRIMARY KEY,

    agency_id           BIGINT       NOT NULL REFERENCES agencies(id) ON DELETE RESTRICT,

    -- L'agente. NULL solo finche' la richiesta non e' smistata (requested) o
    -- se viene annullata/spostata prima di esserlo. La FK composita impone
    -- che sia un membro DI QUESTA agenzia; che la membership sia ATTIVA lo
    -- verifica il trigger quando l'agente viene scritto.
    assigned_user_id    BIGINT,

    appointment_type    VARCHAR(30)  NOT NULL,
    status              VARCHAR(20)  NOT NULL DEFAULT 'scheduled',

    -- Istanti assoluti. `timezone` e' il fuso in cui l'appuntamento si
    -- legge e si mostra, non una correzione degli istanti.
    start_at            TIMESTAMPTZ  NOT NULL,
    end_at              TIMESTAMPTZ  NOT NULL,
    timezone            VARCHAR(64)  NOT NULL DEFAULT 'Europe/Rome',

    buffer_before_minutes SMALLINT   NOT NULL DEFAULT 0,
    buffer_after_minutes  SMALLINT   NOT NULL DEFAULT 0,
    -- Scritta SOLO dal trigger: [start_at - buffer_before, end_at + buffer_after).
    blocked_range       TSTZRANGE    NOT NULL,

    -- Riferimenti CRM, tutti facoltativi. Un appuntamento puo' nascere prima
    -- che esista un immobile, o anche prima di un lead.
    stima_id            INTEGER,                                   -- morbido, senza FK
    contact_id          BIGINT       REFERENCES contacts(id)   ON DELETE SET NULL,
    lead_id             BIGINT       REFERENCES leads(id)      ON DELETE SET NULL,
    property_id         BIGINT       REFERENCES properties(id) ON DELETE SET NULL,

    -- La proiezione verso LMC-15 (A30-2+): la riga di `stima_inspections` che
    -- questo appuntamento alimenta. Predisposta, non scritta da A30-1.
    stima_inspection_id BIGINT       REFERENCES stima_inspections(id) ON DELETE SET NULL,

    location_text       TEXT,
    notes               TEXT,

    -- Provenienza. `source_record_id` e' testo: e' l'identita' del record
    -- nel sistema di origine, qualunque forma abbia.
    source              VARCHAR(40)  NOT NULL DEFAULT 'crm_manual',
    source_record_id    VARCHAR(100),
    -- Solo per `source = 'a30_test'`: la corsa di prova a cui la riga
    -- appartiene, e l'unica chiave con cui la si puo' cancellare.
    test_run_id         VARCHAR(64),

    -- Lo spostamento: la riga nuova punta a quella che sostituisce. NO ACTION
    -- (il default) e non RESTRICT: il controllo avviene a fine istruzione,
    -- cosi' la purge di una corsa di prova puo' togliere un'intera catena di
    -- spostamenti in una sola DELETE. A runtime la DELETE e' comunque rifiutata.
    rescheduled_from_id BIGINT       REFERENCES appointments(id),

    -- Google (A30-9/10): predisposto, non implementato.
    google_calendar_id    VARCHAR(255),
    google_event_id       VARCHAR(255),
    google_sync_status    VARCHAR(20) NOT NULL DEFAULT 'not_synced',
    google_last_synced_at TIMESTAMPTZ,

    created_by_user_id  BIGINT       REFERENCES operator_users(id) ON DELETE RESTRICT,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- Lock ottimistico: il trigger lo incrementa a ogni UPDATE.
    version             INTEGER      NOT NULL DEFAULT 1,

    confirmed_at        TIMESTAMPTZ,
    -- Il momento REALE in cui l'appuntamento e' avvenuto (Q3): dichiarato
    -- dall'operatore o NOW() alla chiusura, mai ricavato da `start_at`.
    completed_at        TIMESTAMPTZ,
    no_show_at          TIMESTAMPTZ,
    cancelled_at        TIMESTAMPTZ,
    cancelled_reason    VARCHAR(300),
    rescheduled_at      TIMESTAMPTZ,

    CONSTRAINT appointments_agency_scope_unq UNIQUE (agency_id, id),

    CONSTRAINT appointments_agent_same_agency_fk
        FOREIGN KEY (agency_id, assigned_user_id)
        REFERENCES agency_memberships (agency_id, operator_user_id),

    CONSTRAINT appointments_type_chk CHECK (appointment_type IN (
        'call', 'video_call', 'seller_meeting', 'inspection', 'buyer_visit',
        'valuation_presentation', 'mandate_signing', 'proposal',
        'preliminary_contract', 'notary', 'technical', 'other')),

    CONSTRAINT appointments_status_chk CHECK (status IN (
        'requested', 'scheduled', 'confirmed', 'completed',
        'cancelled', 'no_show', 'rescheduled')),

    CONSTRAINT appointments_time_order_chk CHECK (end_at > start_at),
    -- Rete contro l'errore di battitura (un anno al posto di un'ora), non
    -- una regola di business: nessun appuntamento dura piu' di un giorno.
    CONSTRAINT appointments_max_duration_chk
        CHECK (end_at - start_at <= INTERVAL '24 hours'),

    CONSTRAINT appointments_timezone_chk CHECK (timezone = 'Europe/Rome'),

    CONSTRAINT appointments_buffer_chk CHECK (
        buffer_before_minutes BETWEEN 0 AND 240
        AND buffer_after_minutes BETWEEN 0 AND 240),

    CONSTRAINT appointments_blocked_range_chk CHECK (
        NOT isempty(blocked_range)
        AND lower_inc(blocked_range) AND NOT upper_inc(blocked_range)),

    -- L'AGENTE (Q4 del GATE A30-1). Obbligatorio per scheduled e confirmed.
    -- Puo' mancare: per una richiesta da smistare (requested); per un
    -- appuntamento chiuso senza essere mai stato attribuito (cancelled,
    -- rescheduled); e per un record STORICO importato, completed o no_show,
    -- che nessuno ha attribuito. Un agente non si inferisce mai (per esempio
    -- da chi ha creato la riga): se la fonte non lo dice, resta NULL.
    CONSTRAINT appointments_agent_required_chk CHECK (
        assigned_user_id IS NOT NULL
        OR status IN ('requested', 'cancelled', 'rescheduled')
        OR (status IN ('completed', 'no_show')
            AND source IN ('legacy_stime_dettagliate', 'stima_inspections_backfill'))),

    CONSTRAINT appointments_source_chk CHECK (source IN (
        'crm_manual', 'legacy_stime_dettagliate', 'stima_inspections_backfill',
        'booking_link', 'system', 'a30_test')),
    -- Un dato di prova porta SEMPRE la sua corsa, e solo lui ne porta una.
    CONSTRAINT appointments_test_run_chk CHECK (
        (source = 'a30_test') = (test_run_id IS NOT NULL)
        AND (test_run_id IS NULL OR test_run_id ~ '^[A-Za-z0-9_.:-]{1,64}$')),
    CONSTRAINT appointments_source_record_chk CHECK (
        source_record_id IS NULL OR BTRIM(source_record_id) <> ''),
    -- Le fonti importate DEVONO dire da quale record vengono: e' la chiave
    -- dell'idempotenza.
    CONSTRAINT appointments_imported_source_chk CHECK (
        source NOT IN ('legacy_stime_dettagliate', 'stima_inspections_backfill')
        OR source_record_id IS NOT NULL),

    CONSTRAINT appointments_rescheduled_self_chk
        CHECK (rescheduled_from_id IS NULL OR rescheduled_from_id <> id),

    -- La proiezione LMC-15 esiste solo per un sopralluogo legato a una stima.
    CONSTRAINT appointments_inspection_link_chk CHECK (
        stima_inspection_id IS NULL
        OR (appointment_type = 'inspection' AND stima_id IS NOT NULL)),

    CONSTRAINT appointments_google_status_chk CHECK (google_sync_status IN (
        'not_synced', 'pending', 'synced', 'error', 'disabled')),
    CONSTRAINT appointments_google_pair_chk CHECK (
        google_event_id IS NULL OR google_calendar_id IS NOT NULL),

    CONSTRAINT appointments_text_chk CHECK (
        (location_text IS NULL OR char_length(location_text) <= 500)
        AND (notes IS NULL OR char_length(notes) <= 5000)
        AND (cancelled_reason IS NULL OR BTRIM(cancelled_reason) <> '')),

    -- LA MATRICE, STATO PER STATO: cosa c'e' e cosa NON c'e'. Nessuno stato
    -- ambiguo, e nessun istante di chiusura appeso a un appuntamento aperto.
    CONSTRAINT appointments_open_states_chk CHECK (
        status NOT IN ('requested', 'scheduled')
        OR num_nonnulls(confirmed_at, completed_at, no_show_at,
                        cancelled_at, cancelled_reason, rescheduled_at) = 0),
    CONSTRAINT appointments_confirmed_chk CHECK (
        status <> 'confirmed' OR (
            confirmed_at IS NOT NULL
            AND num_nonnulls(completed_at, no_show_at, cancelled_at,
                             cancelled_reason, rescheduled_at) = 0)),
    CONSTRAINT appointments_completed_chk CHECK (
        status <> 'completed' OR (
            completed_at IS NOT NULL
            AND num_nonnulls(no_show_at, cancelled_at, cancelled_reason,
                             rescheduled_at) = 0)),
    CONSTRAINT appointments_no_show_chk CHECK (
        status <> 'no_show' OR (
            no_show_at IS NOT NULL
            AND num_nonnulls(completed_at, cancelled_at, cancelled_reason,
                             rescheduled_at) = 0)),
    CONSTRAINT appointments_cancelled_chk CHECK (
        status <> 'cancelled' OR (
            cancelled_at IS NOT NULL
            AND num_nonnulls(completed_at, no_show_at, rescheduled_at) = 0)),
    CONSTRAINT appointments_rescheduled_chk CHECK (
        status <> 'rescheduled' OR (
            rescheduled_at IS NOT NULL
            AND num_nonnulls(completed_at, no_show_at, cancelled_at,
                             cancelled_reason) = 0)),

    -- LA GARANZIA ANTI-SOVRAPPOSIZIONE.
    CONSTRAINT appointments_no_overlap_excl EXCLUDE USING gist (
        assigned_user_id WITH =,
        blocked_range    WITH &&
    ) WHERE (status IN ('scheduled', 'confirmed', 'completed', 'no_show'))
);

-- ---------------------------------------------------------------------------
-- appointment_events: il registro, append-only.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS appointment_events (
    id              BIGSERIAL    PRIMARY KEY,
    agency_id       BIGINT       NOT NULL,
    appointment_id  BIGINT       NOT NULL,
    event_type      VARCHAR(20)  NOT NULL,
    from_status     VARCHAR(20),
    to_status       VARCHAR(20)  NOT NULL,
    -- Chi, secondo il service (NULL = sistema/import), e con quale ruolo di
    -- database.
    actor_user_id   BIGINT       REFERENCES operator_users(id) ON DELETE RESTRICT,
    db_user         TEXT         NOT NULL DEFAULT CURRENT_USER,
    occurred_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    -- Solo le colonne cambiate: {"colonna": {"da": ..., "a": ...}}.
    changes         JSONB        NOT NULL DEFAULT '{}'::jsonb,

    CONSTRAINT appointment_events_appointment_fk
        FOREIGN KEY (agency_id, appointment_id)
        REFERENCES appointments (agency_id, id) ON DELETE RESTRICT,
    CONSTRAINT appointment_events_type_chk
        CHECK (event_type IN ('created', 'updated', 'status_changed'))
);

-- ---------------------------------------------------------------------------
-- TEST CERTIFICATO: l'ALLOWLIST dei dati di prova (INSERT), della purge e
-- delle eccezioni alla DELETE.
-- Confronto ESATTO (niente LIKE, niente marcatore nel nome): un nome
-- sconosciuto o solo somigliante non e' TEST. Un test controlla che coincida
-- con `REQUIRED_DB_NAME` di `scripts/p26_6_live_cert.py`.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION a30_is_certified_test_database() RETURNS boolean AS $fn$
    SELECT current_database() IN ('stima360_db_test');
$fn$ LANGUAGE sql STABLE;

-- ---------------------------------------------------------------------------
-- IL TRIGGER DI GUARDIA: tenancy dei riferimenti, attori, agente attivo,
-- intervallo bloccato, updated_at/version.
--
-- I riferimenti si verificano QUANDO VENGONO SCRITTI, non a ogni UPDATE: un
-- contatto passato a NULL da `ON DELETE SET NULL` non deve far fallire la
-- riga, e un operatore revocato oggi non deve rendere immodificabile un
-- appuntamento creato ieri (la lezione del SET NULL di LMC-15).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION appointments_guard() RETURNS trigger AS $fn$
DECLARE
    v_agency   BIGINT;
    v_contact  BIGINT;
    v_source   VARCHAR(40);
    v_run      VARCHAR(64);
    v_is_ins   BOOLEAN := (TG_OP = 'INSERT');
BEGIN
    IF NOT v_is_ins THEN
        IF NEW.agency_id IS DISTINCT FROM OLD.agency_id THEN
            RAISE EXCEPTION 'A30-1: appointments.agency_id is immutable (id=%)', OLD.id;
        END IF;
        IF NEW.source IS DISTINCT FROM OLD.source
           OR NEW.source_record_id IS DISTINCT FROM OLD.source_record_id
           OR NEW.test_run_id IS DISTINCT FROM OLD.test_run_id THEN
            RAISE EXCEPTION 'A30-1: appointments source/source_record_id/test_run_id are immutable (id=%)', OLD.id;
        END IF;
        IF NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id
           OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION 'A30-1: appointments creation fields are immutable (id=%)', OLD.id;
        END IF;
        IF NEW.stima_id IS DISTINCT FROM OLD.stima_id AND OLD.stima_id IS NOT NULL THEN
            RAISE EXCEPTION 'A30-1: appointments.stima_id cannot be reassigned (id=%)', OLD.id;
        END IF;
    END IF;

    -- Un dato di prova nasce SOLO su un TEST certificato (allowlist): PROD,
    -- un nome sconosciuto o solo somigliante falliscono chiusi.
    IF v_is_ins AND NEW.source = 'a30_test' AND NOT a30_is_certified_test_database() THEN
        RAISE EXCEPTION 'A30-1: a30_test data is refused on database %: not a certified TEST database',
            current_database();
    END IF;

    -- Contatto, lead, immobile: della stessa agenzia, quando vengono scritti.
    IF NEW.contact_id IS NOT NULL
       AND (v_is_ins OR NEW.contact_id IS DISTINCT FROM OLD.contact_id) THEN
        SELECT agency_id INTO v_agency FROM contacts WHERE id = NEW.contact_id;
        IF v_agency IS DISTINCT FROM NEW.agency_id THEN
            RAISE EXCEPTION 'A30-1 tenancy: contact % does not belong to agency %',
                NEW.contact_id, NEW.agency_id;
        END IF;
    END IF;

    IF NEW.lead_id IS NOT NULL
       AND (v_is_ins OR NEW.lead_id IS DISTINCT FROM OLD.lead_id
            OR NEW.contact_id IS DISTINCT FROM OLD.contact_id) THEN
        SELECT agency_id, contact_id INTO v_agency, v_contact FROM leads WHERE id = NEW.lead_id;
        IF v_agency IS DISTINCT FROM NEW.agency_id THEN
            RAISE EXCEPTION 'A30-1 tenancy: lead % does not belong to agency %',
                NEW.lead_id, NEW.agency_id;
        END IF;
        -- Un lead appartiene a UN contatto: i due riferimenti non possono
        -- raccontare due persone diverse.
        IF NEW.contact_id IS NOT NULL AND v_contact IS DISTINCT FROM NEW.contact_id THEN
            RAISE EXCEPTION 'A30-1: lead % belongs to contact %, not to contact %',
                NEW.lead_id, v_contact, NEW.contact_id;
        END IF;
    END IF;

    IF NEW.property_id IS NOT NULL
       AND (v_is_ins OR NEW.property_id IS DISTINCT FROM OLD.property_id) THEN
        SELECT agency_id INTO v_agency FROM properties WHERE id = NEW.property_id;
        IF v_agency IS DISTINCT FROM NEW.agency_id THEN
            RAISE EXCEPTION 'A30-1 tenancy: property % does not belong to agency %',
                NEW.property_id, NEW.agency_id;
        END IF;
    END IF;

    IF NEW.rescheduled_from_id IS NOT NULL
       AND (v_is_ins OR NEW.rescheduled_from_id IS DISTINCT FROM OLD.rescheduled_from_id) THEN
        SELECT agency_id, source, test_run_id INTO v_agency, v_source, v_run
          FROM appointments WHERE id = NEW.rescheduled_from_id;
        IF v_agency IS DISTINCT FROM NEW.agency_id THEN
            RAISE EXCEPTION 'A30-1 tenancy: appointment % does not belong to agency %',
                NEW.rescheduled_from_id, NEW.agency_id;
        END IF;
        -- Uno spostamento resta nel mondo del suo predecessore: un dato di
        -- prova genera solo dati di prova della stessa corsa, e viceversa.
        IF (v_source = 'a30_test') IS DISTINCT FROM (NEW.source = 'a30_test')
           OR v_run IS DISTINCT FROM NEW.test_run_id THEN
            RAISE EXCEPTION 'A30-1: a reschedule must keep the test marker of appointment %',
                NEW.rescheduled_from_id;
        END IF;
    END IF;

    -- L'agente: membership ATTIVA quando viene assegnato. La FK composita
    -- dice solo che la membership esiste.
    IF NEW.assigned_user_id IS NOT NULL
       AND (v_is_ins OR NEW.assigned_user_id IS DISTINCT FROM OLD.assigned_user_id) THEN
        IF NOT EXISTS (
            SELECT 1 FROM agency_memberships
             WHERE agency_id = NEW.agency_id
               AND operator_user_id = NEW.assigned_user_id
               AND status = 'active') THEN
            RAISE EXCEPTION 'A30-1: operator % has no active membership in agency %',
                NEW.assigned_user_id, NEW.agency_id;
        END IF;
    END IF;

    -- Chi crea: stessa regola degli attori LMC-15 (membership attiva, o
    -- platform admin). NULL = sistema/import.
    IF v_is_ins THEN
        PERFORM lmc15_assert_operator_may_act(NEW.created_by_user_id, NEW.agency_id, 'created_by');
    END IF;

    -- L'intervallo che blocca l'agenda: sempre ricalcolato, mai accettato
    -- dal chiamante.
    -- Con orari invertiti l'intervallo resta vuoto e a rifiutare la riga e'
    -- il CHECK `appointments_time_order_chk`, con il suo nome, invece di un
    -- errore generico di tstzrange.
    IF NEW.end_at > NEW.start_at THEN
        NEW.blocked_range := tstzrange(
            NEW.start_at - make_interval(mins => NEW.buffer_before_minutes),
            NEW.end_at   + make_interval(mins => NEW.buffer_after_minutes),
            '[)');
    ELSE
        NEW.blocked_range := 'empty'::tstzrange;
    END IF;

    IF v_is_ins THEN
        NEW.version := 1;
    ELSE
        NEW.version := OLD.version + 1;
        NEW.updated_at := NOW();
    END IF;

    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

-- Un appuntamento non si cancella: si annulla. E il registro non si riscrive.
--
-- L'UNICA ECCEZIONE e' la purge di una corsa di prova: la DELETE passa solo
-- se la riga e' `a30_test`, la sua corsa e' quella dichiarata dalla purge in
-- questa transazione (`stima360.a30_test_purge`) e il database e' un TEST
-- certificato (allowlist). Altrimenti fallisce chiuso.
CREATE OR REPLACE FUNCTION appointments_refuse_delete() RETURNS trigger AS $fn$
BEGIN
    IF OLD.source = 'a30_test'
       AND OLD.test_run_id = current_setting('stima360.a30_test_purge', true)
       AND a30_is_certified_test_database() THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION
        'A30-1: DELETE on appointments is refused (id=%). Cancel the appointment instead.',
        OLD.id;
END;
$fn$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION appointment_events_append_only() RETURNS trigger AS $fn$
BEGIN
    IF TG_OP = 'DELETE'
       AND a30_is_certified_test_database()
       AND EXISTS (
           SELECT 1 FROM appointments a
            WHERE a.id = OLD.appointment_id
              AND a.source = 'a30_test'
              AND a.test_run_id = current_setting('stima360.a30_test_purge', true)) THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'A30-1: appointment_events is append-only (% refused, id=%)',
        TG_OP, OLD.id;
END;
$fn$ LANGUAGE plpgsql;

-- LA PURGE DI UNA CORSA DI PROVA. Funziona solo su un TEST certificato
-- (allowlist: ogni altro database fallisce chiuso) e con una corsa valida;
-- tocca solo le righe `a30_test` di quella corsa e i loro eventi; dichiara la
-- corsa solo per la transazione corrente (`set_config(..., true)`).
CREATE OR REPLACE FUNCTION a30_test_purge(p_run_id TEXT,
                                          OUT appointments_deleted INTEGER,
                                          OUT events_deleted INTEGER) AS $fn$
BEGIN
    IF NOT a30_is_certified_test_database() THEN
        RAISE EXCEPTION 'A30-1: a30_test_purge is refused on database %: not a certified TEST database',
            current_database();
    END IF;
    IF p_run_id IS NULL OR p_run_id !~ '^[A-Za-z0-9_.:-]{1,64}$' THEN
        RAISE EXCEPTION 'A30-1: a30_test_purge needs a valid test run id';
    END IF;

    PERFORM set_config('stima360.a30_test_purge', p_run_id, true);

    DELETE FROM appointment_events e
     USING appointments a
     WHERE a.id = e.appointment_id
       AND a.source = 'a30_test' AND a.test_run_id = p_run_id;
    GET DIAGNOSTICS events_deleted = ROW_COUNT;

    DELETE FROM appointments
     WHERE source = 'a30_test' AND test_run_id = p_run_id;
    GET DIAGNOSTICS appointments_deleted = ROW_COUNT;

    PERFORM set_config('stima360.a30_test_purge', '', true);
END;
$fn$ LANGUAGE plpgsql;

DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_appointments_guard'
                    AND tgrelid = 'public.appointments'::regclass) THEN
        CREATE TRIGGER trg_appointments_guard
            BEFORE INSERT OR UPDATE ON appointments
            FOR EACH ROW EXECUTE FUNCTION appointments_guard();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_appointments_refuse_delete'
                    AND tgrelid = 'public.appointments'::regclass) THEN
        CREATE TRIGGER trg_appointments_refuse_delete
            BEFORE DELETE ON appointments
            FOR EACH ROW EXECUTE FUNCTION appointments_refuse_delete();
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_appointment_events_append_only'
                    AND tgrelid = 'public.appointment_events'::regclass) THEN
        CREATE TRIGGER trg_appointment_events_append_only
            BEFORE UPDATE OR DELETE ON appointment_events
            FOR EACH ROW EXECUTE FUNCTION appointment_events_append_only();
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- Indici. Il vincolo EXCLUDE porta gia' il suo indice GiST
-- (assigned_user_id, blocked_range) sugli stati che bloccano: e' anche quello
-- che serve al controllo di disponibilita'.
-- ---------------------------------------------------------------------------

-- Idempotenza degli import: un record di origine, un appuntamento.
CREATE UNIQUE INDEX IF NOT EXISTS uq_appointments_source_record
    ON appointments (source, source_record_id) WHERE source_record_id IS NOT NULL;

-- Uno spostamento ha un solo successore; una riga LMC-15 una sola origine.
CREATE UNIQUE INDEX IF NOT EXISTS uq_appointments_rescheduled_from
    ON appointments (rescheduled_from_id) WHERE rescheduled_from_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_appointments_stima_inspection
    ON appointments (stima_inspection_id) WHERE stima_inspection_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_appointments_google_event
    ON appointments (google_calendar_id, google_event_id) WHERE google_event_id IS NOT NULL;

-- Calendario: per agenzia e per agente, nel tempo.
CREATE INDEX IF NOT EXISTS idx_appointments_agency_start
    ON appointments (agency_id, start_at);
CREATE INDEX IF NOT EXISTS idx_appointments_agent_start
    ON appointments (assigned_user_id, start_at) WHERE assigned_user_id IS NOT NULL;
-- Le corse di prova, per la purge.
CREATE INDEX IF NOT EXISTS idx_appointments_test_run
    ON appointments (test_run_id) WHERE test_run_id IS NOT NULL;
-- Coda delle richieste da smistare.
CREATE INDEX IF NOT EXISTS idx_appointments_requested
    ON appointments (agency_id, created_at) WHERE status = 'requested';

-- Schede collegate.
CREATE INDEX IF NOT EXISTS idx_appointments_stima
    ON appointments (stima_id) WHERE stima_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_appointments_contact
    ON appointments (contact_id) WHERE contact_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_appointments_lead
    ON appointments (lead_id) WHERE lead_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_appointments_property
    ON appointments (property_id) WHERE property_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_appointment_events_appointment
    ON appointment_events (appointment_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_appointment_events_agency
    ON appointment_events (agency_id, occurred_at DESC);
