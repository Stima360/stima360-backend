-- P29-2.1 communication foundation: il ledger dei messaggi e l'audit dei tentativi.
--
-- Additive, idempotent. Crea DUE tabelle e i guardiani che le rendono
-- append-oriented. Non altera alcuna tabella preesistente e non inserisce
-- alcuna riga: nessun seed, nessun backfill, nessun template, nessuna
-- configurazione di provider.
--
-- Transaction ownership: the runner owns the UP transaction (version >= 027),
-- so this file carries no BEGIN/COMMIT. See
-- docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.
--
-- ---------------------------------------------------------------------------
-- PERCHE' DUE TABELLE IN UNA SOLA MIGRATION
--
-- Decisione del design P29-2.0 (sezione 21), e non e' organizzativa. La chiave
-- esterna composita di `communication_attempts` dipende dallo UNIQUE
-- (agency_id, id) di `communication_messages`: separarle creerebbe uno stato
-- intermedio in cui il ledger esiste senza il suo audit. Un dispatcher scritto
-- contro quello stato sarebbe legittimo e sbagliato - registrerebbe l'esito
-- dell'ultimo tentativo e perderebbe i precedenti - ed e' esattamente il difetto
-- che C14 ha corretto.
--
-- ---------------------------------------------------------------------------
-- COSA SONO QUESTE DUE TABELLE
--
-- `communication_messages` e' la verita' su UNA comunicazione verso un contatto
-- CRM: cosa e' stato mandato, a chi, quando, con quale esito. E' insieme
-- ledger, coda di dispatch e sorgente dell'audit. Il design la definisce
--
--     durable communication ledger + dispatch queue
--
-- e NON "append-only": lo stato e i tentativi cambiano, e devono poterlo fare.
--
-- `communication_attempts` e' l'audit append-oriented di OGNI tentativo di
-- dispatch. NON e' una seconda coda: nessuna riga qui puo' far partire un
-- invio, e il claim non la interroga mai. Esiste perche' le colonne di riepilogo
-- sul messaggio conservano soltanto l'ULTIMO tentativo: una sequenza
-- timeout -> 503 -> sent finirebbe con un messaggio che dice `sent`,
-- `attempt_count = 3` e nessuna traccia dei primi due.
--
-- ---------------------------------------------------------------------------
-- COSA QUESTE TABELLE NON SONO
--
-- Non sono `activities`. Quella e' la proiezione leggibile dall'agente e non ha
-- stato operativo, errore, tentativo o id di provider. La decisione vincolante
-- del committente e' registrata anche in 062: gli stati operativi delle
-- comunicazioni non vivono ne' li' ne' in `seller_timeline_events`, ma qui.
--
-- Non sono `whatsapp_incoming`. Quella e' ingress grezzo su un numero di
-- piattaforma, senza agency e con tenancy derivata dal telefono. Un messaggio
-- di questo ledger ha sempre un contatto e sempre un'agenzia.
--
-- Non sono una `communication_outbox` separata: il design ha scelto una tabella
-- sola, e lo stato `queued` vive nella riga del ledger.
--
-- ---------------------------------------------------------------------------
-- P29-2.1 E' SOLO SCHEMA
--
-- Nessun repository, nessun service, nessun claim, nessun dispatcher, nessun
-- provider. Le colonne di claim e fencing esistono perche' la migration
-- successiva possa usarle, non perche' qualcosa le usi oggi.
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- 1. Il ledger.
--
-- TENANCY: UNA SOLA CHIAVE, COMPOSITA
--
-- `agency_id` e `contact_id` entrano insieme in una FK verso
-- `contacts (agency_id, id)`. Non sono due FK separate, ed e' il punto: la
-- composita dice in un vincolo solo che il contatto esiste E che appartiene a
-- quell'agenzia, quindi un messaggio cross-tenant non e' vietato dal codice, e'
-- IMPOSSIBILE. E' la stessa garanzia che 030 ha dato a `leads` con
-- `leads_contact_same_agency_fk`.
--
-- NON C'E' UNA FK SEPARATA VERSO `agencies`, ed e' deliberato. Sarebbe
-- ridondante - `contacts.agency_id` e' NOT NULL e vincolato ad `agencies`,
-- quindi l'esistenza dell'agenzia e' implicata transitivamente - e obbligherebbe
-- a scegliere fra RESTRICT, che bloccherebbe il purge, e CASCADE, che
-- duplicherebbe una semantica gia' espressa. La condizione perche' questo sia
-- corretto e' stata certificata sul catalogo LIVE di TEST prima di scrivere
-- questo file (gate R4): contacts.agency_id NOT NULL, FK verso agencies
-- RESTRICT, UNIQUE (agency_id, id) presente.
--
-- ON DELETE CASCADE sulla composita: il purge del soggetto porta via il
-- contenuto comunicativo personale, ed e' cio' che C6 chiede. Il cleanup P26-6
-- cancella i contatti PRIMA delle agenzie, quindi la catena si chiude da sola e
-- queste due tabelle non vanno aggiunte a DEDICATED_TABLES - esattamente come
-- `consent_events`, che infatti non ci compare.
--
-- I TRE CONTESTI COMMERCIALI SONO NULLABLE E SET NULL
--
-- `lead_id`, `stima_id`, `property_id` dicono a proposito di cosa si e'
-- scritto. Eliminare il contesto commerciale non deve cancellare il messaggio
-- scambiato con la persona: il messaggio resta, il riferimento si svuota.
--
-- `property_id` c'e' dalla fondazione (C5) perche' STIMA360 e' un CRM
-- immobiliare e un contatto ha piu' immobili. Aggiungerla dopo avrebbe
-- richiesto un backfill su righe il cui immobile non e' piu' ricostruibile.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS communication_messages (
    id                   BIGSERIAL    PRIMARY KEY,
    agency_id            BIGINT       NOT NULL,
    contact_id           BIGINT       NOT NULL,
    lead_id              BIGINT       REFERENCES leads(id)      ON DELETE SET NULL,
    stima_id             INTEGER      REFERENCES stime(id)      ON DELETE SET NULL,
    property_id          BIGINT       REFERENCES properties(id) ON DELETE SET NULL,

    channel              VARCHAR(20)  NOT NULL,
    direction            VARCHAR(10)  NOT NULL,
    communication_type   VARCHAR(20)  NOT NULL,
    mode                 VARCHAR(20)  NOT NULL,
    reason_code          VARCHAR(60)  NOT NULL,

    template_key         VARCHAR(80),
    template_version     INTEGER,
    subject_snapshot     VARCHAR(300),
    rendered_body        TEXT         NOT NULL,
    destination_snapshot VARCHAR(320) NOT NULL,

    status               VARCHAR(20)  NOT NULL DEFAULT 'queued',
    failure_class        VARCHAR(20),
    provider             VARCHAR(40),
    provider_message_id  VARCHAR(200),

    scheduled_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    claimed_at           TIMESTAMPTZ,
    claim_token          UUID,
    attempt_count        INTEGER      NOT NULL DEFAULT 0,
    last_attempt_at      TIMESTAMPTZ,
    sent_at              TIMESTAMPTZ,
    failed_at            TIMESTAMPTZ,

    error_code           VARCHAR(80),
    error_detail         TEXT,
    suppressed_reason    VARCHAR(60),

    actor_type           VARCHAR(20)  NOT NULL,
    actor_user_id        BIGINT,

    idempotency_key      VARCHAR(300) NOT NULL,
    metadata             JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- Lo UNIQUE che rende possibile la FK composita dei tentativi. Non e' un
    -- indice di comodo: senza, `communication_attempts` non puo' dichiarare la
    -- coppia (agency_id, message_id) e un tentativo potrebbe puntare a un
    -- messaggio di un'altra agenzia. Stessa forma di contacts_agency_scope_unq.
    CONSTRAINT communication_messages_agency_scope_unq UNIQUE (agency_id, id),

    -- La FK composita descritta sopra.
    CONSTRAINT communication_messages_contact_same_agency_fk
        FOREIGN KEY (agency_id, contact_id)
        REFERENCES contacts (agency_id, id) ON DELETE CASCADE,

    -- Due canali, e nessun terzo. `sms` e `push` si aggiungono con una riga qui
    -- quando esistera' un provider che li implementa: un insieme che contiene
    -- valori che nessun adapter sa mandare e' una promessa che il codice non
    -- mantiene.
    CONSTRAINT communication_messages_channel_chk
        CHECK (channel IN ('email', 'whatsapp')),

    -- `inbound` e' ammesso dal vincolo ma nessun percorso di P29-2 lo scrive.
    -- Sta qui adesso perche' l'inbound di domani non richieda una migration su
    -- un CHECK: la riga in arrivo entrera' in QUESTO ledger, non in un secondo.
    CONSTRAINT communication_messages_direction_chk
        CHECK (direction IN ('outbound', 'inbound')),

    -- Due, e governano il consent gate. `service` esegue una richiesta
    -- dell'interessato, `marketing` richiede il consenso. Non c'e' una terza
    -- categoria: `transactional` sarebbe un sinonimo di service, e
    -- `notification` descriveva l'alert all'amministratore, che C4 ha messo
    -- fuori dal perimetro del dominio.
    CONSTRAINT communication_messages_type_chk
        CHECK (communication_type IN ('service', 'marketing')),

    -- Chi ha premuto invia. Ortogonale al canale e al tipo: un WhatsApp puo'
    -- essere manuale, un marketing puo' essere assistito - e resta marketing.
    CONSTRAINT communication_messages_mode_chk
        CHECK (mode IN ('manual', 'assisted', 'automatic')),

    -- Perche' e' partito. Insieme chiuso: `admin_lead_alert` NON c'e', perche'
    -- l'alert all'amministratore e' fuori dal perimetro (C4) e continua a
    -- passare da invia_mail senza toccare questo ledger.
    CONSTRAINT communication_messages_reason_code_chk
        CHECK (reason_code IN (
            'stima_pdf', 'operator_manual', 'operator_reply',
            'm1', 'm2', 'm3', 'm4', 'm5'
        )),

    -- SETTE STATI, E `indeterminate` E' QUELLO CHE MANCAVA.
    --
    -- Chiamiamo il provider e la connessione cade prima della risposta: il
    -- messaggio PUO' essere partito. Non e' `failed`, e trattarlo come tale
    -- produce un doppione al primo retry o una perdita silenziosa. Ha uno stato
    -- proprio, ed e' terminale per l'automazione.
    --
    -- `scheduled`, `delivered` e `draft` NON ci sono: il primo sarebbe
    -- indistinguibile da `queued` con `scheduled_at` futuro, il secondo arriva
    -- con il webhook di stato che oggi non esiste, il terzo appartiene alla UI.
    CONSTRAINT communication_messages_status_chk
        CHECK (status IN (
            'queued', 'sending', 'sent', 'failed',
            'indeterminate', 'suppressed', 'cancelled'
        )),

    -- `failure_class` registra un FATTO osservato - sappiamo o non sappiamo
    -- cosa ha fatto il provider - e non una conclusione. La ritentabilita' si
    -- deriva nel codice da failure_class + error_code + attempt_count, dove una
    -- politica puo' cambiare senza riscrivere la storia.
    CONSTRAINT communication_messages_failure_class_chk
        CHECK (failure_class IS NULL OR failure_class IN ('definite', 'indeterminate')),

    -- I due stati che rappresentano un insuccesso devono dire QUALE insuccesso,
    -- e nessun altro stato puo' portare quella classificazione.
    CONSTRAINT communication_messages_failure_class_coherence_chk
        CHECK ((status IN ('failed', 'indeterminate')) = (failure_class IS NOT NULL)),

    -- L'insieme dei nostri codici. `error_detail` porta il testo grezzo del
    -- provider; questo campo porta il nostro, che resta stabile anche quando il
    -- provider cambia le sue stringhe.
    CONSTRAINT communication_messages_error_code_chk
        CHECK (error_code IS NULL OR error_code IN (
            'provider_unavailable', 'provider_rejected', 'invalid_destination',
            'missing_credentials', 'rendering_failed', 'timeout',
            'outcome_unknown', 'unknown'
        )),

    -- CLAIM E FENCING: LE DUE COLONNE STANNO INSIEME O NON STANNO.
    --
    -- Derivate dalle transazioni del design, non inventate qui: il claim
    -- (sezione 8.1) le scrive entrambe nello stesso UPDATE, la finalizzazione
    -- (9.3) e la recovery (10.3) le azzerano entrambe.
    CONSTRAINT communication_messages_claim_pair_chk
        CHECK ((claim_token IS NULL) = (claimed_at IS NULL)),

    -- Un token esiste se e solo se il messaggio e' reclamato. Un token su un
    -- messaggio gia' finalizzato sarebbe una chiave che apre una porta chiusa:
    -- il compare-and-set di 9.3 non deve poter riuscire due volte, e azzerare il
    -- token e' cio' che lo rende falso per costruzione.
    CONSTRAINT communication_messages_claim_status_chk
        CHECK ((status = 'sending') = (claim_token IS NOT NULL)),

    -- Tre tentativi al massimo, contati AL CLAIM e non all'esito: un processo
    -- che muore subito dopo il commit ha comunque consumato un tentativo, e non
    -- puo' esistere un ciclo infinito di claim-e-morte.
    CONSTRAINT communication_messages_attempt_count_chk
        CHECK (attempt_count BETWEEN 0 AND 3),

    -- Un atto di un operatore porta il nome dell'operatore. E' la stessa regola
    -- di consent_events_operator_ref_chk, e per la stessa ragione: senza,
    -- "inviato dal CRM" e' tutto cio' che resta.
    CONSTRAINT communication_messages_actor_type_chk
        CHECK (actor_type IN ('system', 'operator')),
    CONSTRAINT communication_messages_actor_user_chk
        CHECK ((actor_type = 'operator') = (actor_user_id IS NOT NULL)),

    -- Le due meta' dell'identita' del template stanno insieme o non stanno: una
    -- versione senza chiave non si sa leggere, una chiave senza versione non
    -- dice quale testo era in uso.
    CONSTRAINT communication_messages_template_chk
        CHECK ((template_key IS NULL) = (template_version IS NULL)),

    -- SNAPSHOT: CIO' CHE E' STATO REALMENTE MANDATO.
    --
    -- Un corpo vuoto o un destinatario vuoto non sono un messaggio. L'oggetto
    -- esiste per le email e non esiste per WhatsApp, e il vincolo lo dice invece
    -- di lasciarlo alla disciplina del chiamante.
    CONSTRAINT communication_messages_body_chk
        CHECK (BTRIM(rendered_body) <> ''),
    CONSTRAINT communication_messages_destination_chk
        CHECK (BTRIM(destination_snapshot) <> ''),
    CONSTRAINT communication_messages_subject_chk
        CHECK ((channel = 'email') = (subject_snapshot IS NOT NULL)),

    CONSTRAINT communication_messages_idempotency_chk
        CHECK (BTRIM(idempotency_key) <> '')
);

-- ---------------------------------------------------------------------------
-- 2. Idempotenza, PER TENANT.
--
-- La chiave identifica un'INTENZIONE di comunicazione, la genera il producer e
-- mai il dispatcher, ed e' unica nell'agenzia.
--
-- Non globale, e la ragione e' concreta. I producer futuri useranno chiavi che
-- contengono identificatori locali all'agenzia - un codice di campagna, un
-- riferimento importato da un gestionale. Con un vincolo globale l'agenzia B non
-- potrebbe accodare un messaggio perche' l'agenzia A ha gia' usato quella
-- stringa: un fallimento incomprensibile, e una fuga di informazione.
-- ---------------------------------------------------------------------------
CREATE UNIQUE INDEX IF NOT EXISTS uq_communication_messages_idempotency
    ON communication_messages (agency_id, idempotency_key);

-- ---------------------------------------------------------------------------
-- 3. Indici di lettura.
--
-- Uno per percorso reale, e nessuno "per sicurezza".
-- ---------------------------------------------------------------------------

-- La scheda contatto e la futura lista messaggi.
CREATE INDEX IF NOT EXISTS idx_communication_messages_contact
    ON communication_messages (agency_id, contact_id, created_at DESC);

-- Il claim. Parziale, cosi' resta piccolo quanto la coda e non quanto lo
-- storico: le righe terminali - che saranno la quasi totalita' - non ci entrano.
CREATE INDEX IF NOT EXISTS idx_communication_messages_da_inviare
    ON communication_messages (status, scheduled_at)
    WHERE status IN ('queued', 'failed');

-- Il recovery dei claim orfani.
CREATE INDEX IF NOT EXISTS idx_communication_messages_in_corso
    ON communication_messages (status, claimed_at)
    WHERE status = 'sending';

-- La correlazione di un inbound futuro con il messaggio che l'ha provocato.
-- Unico: due messaggi non possono dichiarare lo stesso id di provider.
CREATE UNIQUE INDEX IF NOT EXISTS uq_communication_messages_provider_message_id
    ON communication_messages (provider_message_id)
    WHERE provider_message_id IS NOT NULL;

-- I messaggi che riguardano un immobile.
CREATE INDEX IF NOT EXISTS idx_communication_messages_property
    ON communication_messages (agency_id, property_id)
    WHERE property_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 4. L'audit dei tentativi.
--
-- NON E' UNA SECONDA CODA. Il claim legge e scrive soltanto
-- `communication_messages`; nessuna riga di questa tabella puo' far partire un
-- invio, e nessun indice qui serve a selezionare lavoro da eseguire.
--
-- `attempt_no` e' il valore di `attempt_count` del messaggio al momento del
-- claim: i due nascono nello stesso commit e non possono divergere.
--
-- `late_result` E LA RAGIONE PER CUI ENTRA NELLO UNIQUE
--
-- Un worker che ha perso l'ownership - perche' la recovery ha gia' chiuso il
-- suo claim - puo' tornare vivo con una risposta tardiva del provider. Quella
-- risposta e' informazione, e va conservata; ma NON riscrive il tentativo
-- originale, che nel frattempo e' stato chiuso `indeterminate`. Riscriverlo
-- cancellerebbe il fatto che a un certo istante non sapevamo, che e'
-- precisamente l'informazione che lo stato `indeterminate` esiste per
-- conservare. Il risultato tardivo e' quindi una RIGA NUOVA con lo stesso
-- `attempt_no`, e lo UNIQUE a tre colonne ammette al massimo una riga regolare
-- e una tardiva per tentativo - continuando a impedire due claim con lo stesso
-- numero.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS communication_attempts (
    id                  BIGSERIAL    PRIMARY KEY,
    agency_id           BIGINT       NOT NULL,
    message_id          BIGINT       NOT NULL,
    attempt_no          INTEGER      NOT NULL,
    claim_token         UUID         NOT NULL,
    provider            VARCHAR(40)  NOT NULL,
    started_at          TIMESTAMPTZ  NOT NULL,
    finished_at         TIMESTAMPTZ,
    outcome             VARCHAR(20)  NOT NULL,
    provider_message_id VARCHAR(200),
    failure_class       VARCHAR(20),
    error_code          VARCHAR(80),
    error_detail        TEXT,
    late_result         BOOLEAN      NOT NULL DEFAULT FALSE,
    recovered_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- Vedi il commento sopra: tre colonne, non due.
    CONSTRAINT communication_attempts_no_unq
        UNIQUE (message_id, attempt_no, late_result),

    -- La coppia, non il solo message_id: un tentativo non puo' puntare a un
    -- messaggio di un'altra agenzia, e non per convenzione.
    --
    -- CASCADE: i tentativi sono l'esecuzione di QUEL messaggio e non hanno senso
    -- senza di lui. Il purge del soggetto li porta via per la catena
    -- contacts -> communication_messages -> communication_attempts.
    CONSTRAINT communication_attempts_message_same_agency_fk
        FOREIGN KEY (agency_id, message_id)
        REFERENCES communication_messages (agency_id, id) ON DELETE CASCADE,

    CONSTRAINT communication_attempts_outcome_chk
        CHECK (outcome IN ('in_progress', 'accepted', 'rejected', 'indeterminate')),

    -- Un tentativo aperto non ha una fine, e un tentativo chiuso ce l'ha. Il
    -- bicondizionale e' voluto: una delle due meta' da sola descriverebbe uno
    -- stato che non esiste.
    CONSTRAINT communication_attempts_finished_chk
        CHECK ((outcome = 'in_progress') = (finished_at IS NULL)),

    -- Un successo non ha una classe di fallimento.
    CONSTRAINT communication_attempts_accepted_chk
        CHECK (outcome <> 'accepted' OR failure_class IS NULL),

    -- Un insuccesso deve dire quale: certo, o a esito ignoto.
    CONSTRAINT communication_attempts_failure_class_chk
        CHECK (outcome NOT IN ('rejected', 'indeterminate') OR failure_class IS NOT NULL),

    CONSTRAINT communication_attempts_failure_class_values_chk
        CHECK (failure_class IS NULL OR failure_class IN ('definite', 'indeterminate')),

    CONSTRAINT communication_attempts_no_chk
        CHECK (attempt_no >= 1)
);

-- Dalla FK composita. Serve a leggere i tentativi di un messaggio, che e' la
-- sola lettura prevista su questa tabella.
CREATE INDEX IF NOT EXISTS idx_communication_attempts_message
    ON communication_attempts (agency_id, message_id);

-- I tentativi ancora aperti: e' cosi' che la recovery li trova. Parziale, e in
-- condizioni normali quasi vuoto.
CREATE INDEX IF NOT EXISTS idx_communication_attempts_aperti
    ON communication_attempts (outcome)
    WHERE outcome = 'in_progress';

-- ---------------------------------------------------------------------------
-- 5. Immutabilita' parziale del ledger.
--
-- Il messaggio NON e' append-only: lo stato, i tentativi e i timestamp di
-- dispatch cambiano, ed e' il loro mestiere. Ma cio' che il messaggio DICE di
-- essere stato non cambia mai.
--
-- Immutabili dopo l'INSERT: l'identita' (agency_id, contact_id,
-- idempotency_key, created_at), la natura (channel, communication_type, mode,
-- reason_code) e gli snapshot (rendered_body, subject_snapshot,
-- destination_snapshot).
--
-- Gli snapshot sono il punto di C12: il template puo' cambiare domani, la riga
-- storica no. Non e' una convenzione applicativa, e' il database che dice no.
--
-- Il DELETE e' rifiutato, con la sola eccezione del purge - due condizioni
-- entrambe necessarie, gia' misurate su PostgreSQL 16 in 062:
--
--   1. pg_trigger_depth() > 1: le azioni referenziali sono trigger interni,
--      quindi durante un CASCADE questo guardiano gira ANNIDATO;
--   2. il genitore non c'e' piu': il trigger RI del CASCADE e' un AFTER DELETE
--      sul genitore, quindi quando questo gira la riga di `contacts` E' GIA'
--      SPARITA.
--
-- Insieme, l'unico modo di soddisfarle e' cancellare davvero il contatto.
-- Nessuna variabile di sessione le apre: pg_trigger_depth() non e' assegnabile
-- e l'esistenza del genitore si legge, non si dichiara.
-- ---------------------------------------------------------------------------
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
        NOT EXISTS (SELECT 1 FROM contacts WHERE id = OLD.contact_id);

    IF pg_trigger_depth() > 1 AND v_genitore_sparito THEN
        -- Purge: il destinatario di questo messaggio non esiste piu'. Il
        -- contenuto comunicativo personale lo segue.
        RETURN OLD;
    END IF;

    RAISE EXCEPTION
        'communication_messages: DELETE is refused (id=%). A message is removed only by physically purging the contact it was addressed to.',
        OLD.id;
END
$fn$;

DROP TRIGGER IF EXISTS trg_communication_messages_guard ON communication_messages;
CREATE TRIGGER trg_communication_messages_guard
    BEFORE UPDATE OR DELETE ON communication_messages
    FOR EACH ROW EXECUTE FUNCTION communication_messages_guard();

-- ENABLE ALWAYS, per la ragione misurata in 062: un trigger ordinario
-- (tgenabled='O') non scatta quando session_replication_role e' 'replica', cioe'
-- esiste un parametro di sessione che lo spegne. 'A' chiude quella strada.
ALTER TABLE communication_messages
    ENABLE ALWAYS TRIGGER trg_communication_messages_guard;

-- ---------------------------------------------------------------------------
-- 6. Il tentativo chiuso non si riapre.
--
-- Una riga nasce `in_progress` al claim e si chiude UNA VOLTA SOLA alla
-- finalizzazione. Dopo, e' storia.
--
-- E' il vincolo che rende onesto il modello del risultato tardivo: se un
-- tentativo chiuso fosse riaprribile, un worker sopravvissuto potrebbe
-- riscrivere `indeterminate` in `accepted` e cancellare il fatto che a un certo
-- istante non sapevamo. Non potendo, deve inserire una riga nuova con
-- late_result = true - e la storia mostra entrambe le cose: cosa abbiamo
-- dichiarato allora, e cosa e' arrivato dopo.
--
-- Immutabili sempre: l'identita' del claim (message_id, agency_id, attempt_no,
-- claim_token, provider, started_at) e late_result. Una riga tardiva non puo'
-- diventare regolare, ne' viceversa.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION communication_attempts_guard()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_genitore_sparito BOOLEAN;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF OLD.outcome <> 'in_progress' THEN
            RAISE EXCEPTION
                'communication_attempts: a closed attempt cannot be reopened (id=%, outcome=%). Record a late provider result as a new row with late_result = true.',
                OLD.id, OLD.outcome;
        END IF;
        IF NEW.id          IS DISTINCT FROM OLD.id
        OR NEW.agency_id   IS DISTINCT FROM OLD.agency_id
        OR NEW.message_id  IS DISTINCT FROM OLD.message_id
        OR NEW.attempt_no  IS DISTINCT FROM OLD.attempt_no
        OR NEW.claim_token IS DISTINCT FROM OLD.claim_token
        OR NEW.provider    IS DISTINCT FROM OLD.provider
        OR NEW.started_at  IS DISTINCT FROM OLD.started_at
        OR NEW.late_result IS DISTINCT FROM OLD.late_result
        OR NEW.created_at  IS DISTINCT FROM OLD.created_at
        THEN
            RAISE EXCEPTION
                'communication_attempts: the identity of an attempt is immutable (id=%).',
                OLD.id;
        END IF;
        RETURN NEW;
    END IF;

    v_genitore_sparito :=
        NOT EXISTS (SELECT 1 FROM communication_messages WHERE id = OLD.message_id);

    IF pg_trigger_depth() > 1 AND v_genitore_sparito THEN
        RETURN OLD;
    END IF;

    RAISE EXCEPTION
        'communication_attempts: DELETE is refused (id=%). Dispatch history is removed only together with the message it belongs to.',
        OLD.id;
END
$fn$;

DROP TRIGGER IF EXISTS trg_communication_attempts_guard ON communication_attempts;
CREATE TRIGGER trg_communication_attempts_guard
    BEFORE UPDATE OR DELETE ON communication_attempts
    FOR EACH ROW EXECUTE FUNCTION communication_attempts_guard();

ALTER TABLE communication_attempts
    ENABLE ALWAYS TRIGGER trg_communication_attempts_guard;

-- ---------------------------------------------------------------------------
-- 7. TRUNCATE resta vietato, anche a cascata.
--
-- Stessa ragione di 062: `TRUNCATE contacts CASCADE` svuoterebbe queste tabelle
-- senza che nessun trigger di riga se ne accorga. Il cleanup di P26-6 non usa
-- TRUNCATE - cancella per predicato con DELETE - quindi vietarlo non toglie
-- niente a nessuno e chiude una strada larga.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION communication_messages_no_truncate()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $fn$
BEGIN
    RAISE EXCEPTION 'communication_messages: TRUNCATE is refused.';
END
$fn$;

DROP TRIGGER IF EXISTS trg_communication_messages_no_truncate ON communication_messages;
CREATE TRIGGER trg_communication_messages_no_truncate
    BEFORE TRUNCATE ON communication_messages
    FOR EACH STATEMENT EXECUTE FUNCTION communication_messages_no_truncate();

ALTER TABLE communication_messages
    ENABLE ALWAYS TRIGGER trg_communication_messages_no_truncate;

CREATE OR REPLACE FUNCTION communication_attempts_no_truncate()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $fn$
BEGIN
    RAISE EXCEPTION 'communication_attempts: TRUNCATE is refused.';
END
$fn$;

DROP TRIGGER IF EXISTS trg_communication_attempts_no_truncate ON communication_attempts;
CREATE TRIGGER trg_communication_attempts_no_truncate
    BEFORE TRUNCATE ON communication_attempts
    FOR EACH STATEMENT EXECUTE FUNCTION communication_attempts_no_truncate();

ALTER TABLE communication_attempts
    ENABLE ALWAYS TRIGGER trg_communication_attempts_no_truncate;

-- ---------------------------------------------------------------------------
-- 8. Prova, riletta dal catalogo.
--
-- Bit di pg_trigger.tgtype come in 057, 061 e 062:
--   trigger di riga      : ROW(1) + BEFORE(2) + DELETE(8) + UPDATE(16) = 27
--   trigger di statement : BEFORE(2) + TRUNCATE(32)                    = 34
--
-- Si verificano anche le FK e i due UNIQUE, perche' la loro assenza non si
-- noterebbe finche' non conta: una FK composita degradata a semplice si
-- scoprirebbe il giorno in cui un messaggio finisce nell'agenzia sbagliata, e
-- uno UNIQUE mancante il giorno di un doppio invio.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_riga        text[];
    v_tgtype      smallint;
    v_tgenabled   "char";
    v_funzione    text;
    v_atteso      smallint;
    v_deltype     "char";
    v_colonne     smallint[];
    v_riferita    text;
    v_n           integer;
BEGIN
    FOREACH v_riga SLICE 1 IN ARRAY ARRAY[
        ARRAY['trg_communication_messages_guard','communication_messages','communication_messages_guard','27'],
        ARRAY['trg_communication_attempts_guard','communication_attempts','communication_attempts_guard','27'],
        ARRAY['trg_communication_messages_no_truncate','communication_messages','communication_messages_no_truncate','34'],
        ARRAY['trg_communication_attempts_no_truncate','communication_attempts','communication_attempts_no_truncate','34']
    ]
    LOOP
        v_atteso := v_riga[4]::smallint;

        SELECT t.tgtype, t.tgenabled, p.proname
          INTO v_tgtype, v_tgenabled, v_funzione
          FROM pg_trigger t
          JOIN pg_proc p      ON p.oid = t.tgfoid
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE t.tgname  = v_riga[1]
           AND t.tgrelid = ('public.' || v_riga[2])::regclass
           AND n.nspname = 'public'
           AND NOT t.tgisinternal;

        IF v_tgtype IS NULL THEN
            RAISE EXCEPTION 'P29-2.1 064: % is not installed on %', v_riga[1], v_riga[2];
        END IF;
        IF v_funzione <> v_riga[3] THEN
            RAISE EXCEPTION 'P29-2.1 064: % calls %, expected %', v_riga[1], v_funzione, v_riga[3];
        END IF;
        -- 'A' = ENABLE ALWAYS, e non 'O'. Un trigger 'O' non scatta quando
        -- session_replication_role e' 'replica': esiste cioe' un parametro di
        -- sessione che lo spegne. Questa prova esige la forma che non si spegne.
        IF v_tgenabled <> 'A' THEN
            RAISE EXCEPTION
                'P29-2.1 064: % is not ENABLE ALWAYS (tgenabled=%); session_replication_role would switch it off',
                v_riga[1], v_tgenabled;
        END IF;
        IF (v_tgtype & 2) = 0 OR (v_tgtype & 64) <> 0 THEN
            RAISE EXCEPTION 'P29-2.1 064: % must fire BEFORE, not AFTER or INSTEAD OF', v_riga[1];
        END IF;
        IF (v_tgtype & v_atteso) <> v_atteso THEN
            RAISE EXCEPTION
                'P29-2.1 064: % does not cover the required operations (tgtype=%, wanted=%)',
                v_riga[1], v_tgtype, v_atteso;
        END IF;
        -- Nessuno dei quattro scatta su INSERT: le tabelle sono
        -- append-oriented, non di sola lettura.
        IF (v_tgtype & 4) <> 0 THEN
            RAISE EXCEPTION
                'P29-2.1 064: % must not fire on INSERT', v_riga[1];
        END IF;
    END LOOP;

    -- LE DUE FK COMPOSITE.
    --
    -- Si verifica che portino DUE colonne e non una: una composita degradata a
    -- FK semplice continuerebbe a garantire che il genitore esiste e smetterebbe
    -- di garantire che appartiene alla stessa agenzia - cioe' perderebbe
    -- esattamente la proprieta' per cui e' stata scelta, senza che nulla fallisca.
    SELECT c.conkey, c.confdeltype, cl.relname
      INTO v_colonne, v_deltype, v_riferita
      FROM pg_constraint c
      JOIN pg_class cl ON cl.oid = c.confrelid
     WHERE c.conrelid = 'public.communication_messages'::regclass
       AND c.conname  = 'communication_messages_contact_same_agency_fk';

    IF v_colonne IS NULL THEN
        RAISE EXCEPTION
            'P29-2.1 064: communication_messages carries no composite key towards contacts; a message could belong to another agency than its contact';
    END IF;
    IF array_length(v_colonne, 1) <> 2 THEN
        RAISE EXCEPTION
            'P29-2.1 064: the key towards contacts carries % column(s), expected 2 (agency_id, contact_id)',
            array_length(v_colonne, 1);
    END IF;
    IF v_riferita <> 'contacts' THEN
        RAISE EXCEPTION 'P29-2.1 064: the composite key points at %, expected contacts', v_riferita;
    END IF;
    -- 'c' = CASCADE. Asserita al positivo: riportarla a RESTRICT o a SET NULL
    -- farebbe fallire questa migration invece di far fallire, mesi dopo, il
    -- purge di un contatto.
    IF v_deltype <> 'c' THEN
        RAISE EXCEPTION
            'P29-2.1 064: the composite key towards contacts is not ON DELETE CASCADE (confdeltype=%); purging a contact would fail on its first message',
            v_deltype;
    END IF;

    SELECT c.conkey, c.confdeltype, cl.relname
      INTO v_colonne, v_deltype, v_riferita
      FROM pg_constraint c
      JOIN pg_class cl ON cl.oid = c.confrelid
     WHERE c.conrelid = 'public.communication_attempts'::regclass
       AND c.conname  = 'communication_attempts_message_same_agency_fk';

    IF v_colonne IS NULL THEN
        RAISE EXCEPTION
            'P29-2.1 064: communication_attempts carries no composite key towards communication_messages';
    END IF;
    IF array_length(v_colonne, 1) <> 2 THEN
        RAISE EXCEPTION
            'P29-2.1 064: the key towards communication_messages carries % column(s), expected 2 (agency_id, message_id)',
            array_length(v_colonne, 1);
    END IF;
    IF v_riferita <> 'communication_messages' THEN
        RAISE EXCEPTION 'P29-2.1 064: the attempts key points at %, expected communication_messages', v_riferita;
    END IF;
    IF v_deltype <> 'c' THEN
        RAISE EXCEPTION
            'P29-2.1 064: the attempts key is not ON DELETE CASCADE (confdeltype=%)', v_deltype;
    END IF;

    -- I TRE CONTESTI COMMERCIALI SONO SET NULL, E NON CASCADE.
    -- 'n' = SET NULL. Se uno di questi fosse CASCADE, cancellare un immobile
    -- porterebbe via i messaggi scambiati con la persona a proposito di quel
    -- immobile: il contesto se ne va, la comunicazione resta.
    FOREACH v_riga SLICE 1 IN ARRAY ARRAY[
        ARRAY['lead_id'], ARRAY['stima_id'], ARRAY['property_id']
    ]
    LOOP
        SELECT c.confdeltype INTO v_deltype
          FROM pg_constraint c
          JOIN pg_attribute a
            ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
         WHERE c.conrelid = 'public.communication_messages'::regclass
           AND c.contype  = 'f'
           AND array_length(c.conkey, 1) = 1
           AND a.attname  = v_riga[1];

        IF v_deltype IS NULL THEN
            RAISE EXCEPTION
                'P29-2.1 064: communication_messages.% carries no foreign key', v_riga[1];
        END IF;
        IF v_deltype <> 'n' THEN
            RAISE EXCEPTION
                'P29-2.1 064: the foreign key on communication_messages.% is not ON DELETE SET NULL (confdeltype=%); deleting a commercial context must not delete the message',
                v_riga[1], v_deltype;
        END IF;
    END LOOP;

    -- LO UNIQUE CHE REGGE LA FK COMPOSITA.
    SELECT COUNT(*) INTO v_n
      FROM pg_constraint c
     WHERE c.conrelid = 'public.communication_messages'::regclass
       AND c.contype  = 'u'
       AND c.conname  = 'communication_messages_agency_scope_unq'
       AND array_length(c.conkey, 1) = 2;
    IF v_n <> 1 THEN
        RAISE EXCEPTION
            'P29-2.1 064: UNIQUE (agency_id, id) is missing from communication_messages; the attempts composite key cannot stand without it';
    END IF;

    -- LO UNIQUE DELL'IDEMPOTENZA E' PER TENANT, NON GLOBALE.
    SELECT COUNT(*) INTO v_n
      FROM pg_index i
      JOIN pg_class c ON c.oid = i.indexrelid
     WHERE i.indrelid = 'public.communication_messages'::regclass
       AND c.relname  = 'uq_communication_messages_idempotency'
       AND i.indisunique
       AND i.indnatts = 2;
    IF v_n <> 1 THEN
        RAISE EXCEPTION
            'P29-2.1 064: the idempotency index is missing or is not the two-column (agency_id, idempotency_key) form';
    END IF;

    -- LO UNIQUE DEI TENTATIVI PORTA TRE COLONNE.
    -- A due, un risultato tardivo non potrebbe essere registrato e il worker
    -- sopravvissuto resterebbe senza alcun modo di dire cosa ha visto.
    SELECT COUNT(*) INTO v_n
      FROM pg_constraint c
     WHERE c.conrelid = 'public.communication_attempts'::regclass
       AND c.contype  = 'u'
       AND c.conname  = 'communication_attempts_no_unq'
       AND array_length(c.conkey, 1) = 3;
    IF v_n <> 1 THEN
        RAISE EXCEPTION
            'P29-2.1 064: UNIQUE (message_id, attempt_no, late_result) is missing or does not carry three columns';
    END IF;

    -- `metadata` NON ESISTE SUI TENTATIVI, ED E' UNA DECISIONE.
    -- C14 l'ha rimossa: una JSONB vuota su ogni riga e' un invito a metterci
    -- dati che avrebbero dovuto essere colonne.
    SELECT COUNT(*) INTO v_n
      FROM pg_attribute
     WHERE attrelid = 'public.communication_attempts'::regclass
       AND attname  = 'metadata'
       AND NOT attisdropped;
    IF v_n <> 0 THEN
        RAISE EXCEPTION
            'P29-2.1 064: communication_attempts carries a metadata column; C14 removed it deliberately';
    END IF;
END
$do$;

-- ---------------------------------------------------------------------------
-- 9. Prova: i vincoli MORDONO.
--
-- Come in 061, 062 e 063, la sonda scrive righe vere e le annulla con il
-- savepoint implicito di BEGIN ... EXCEPTION. Salta quando non c'e' un contatto
-- su cui appoggiarsi: un database appena costruito dal runner non ne ha, e una
-- migration che fallisse li' sarebbe inapplicabile a uno schema vuoto.
--
-- Si provano le regole la cui violazione sarebbe silenziosa e grave: gli
-- snapshot riscrivibili, il tentativo chiuso riaperto, il fencing incoerente,
-- l'idempotenza aggirabile, il tenant mismatch.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_contatto        BIGINT;
    v_agenzia         BIGINT;
    v_altra_agenzia   BIGINT;
    v_msg             BIGINT;
    v_tent            BIGINT;
    v_token           CONSTANT UUID := '11111111-1111-4111-8111-111111111111';
    v_insert_ok       BOOLEAN := FALSE;
    v_snapshot_bloc   BOOLEAN := FALSE;
    v_stato_ok        BOOLEAN := FALSE;
    v_delete_rifiut   BOOLEAN := FALSE;
    v_idem_rifiut     BOOLEAN := FALSE;
    v_tenant_rifiut   BOOLEAN := FALSE;
    v_stato_ignoto    BOOLEAN := FALSE;
    v_claim_incoer    BOOLEAN := FALSE;
    v_chiuso_bloc     BOOLEAN := FALSE;
    v_tardivo_ok      BOOLEAN := FALSE;
    v_tardivo_doppio  BOOLEAN := FALSE;
    v_sentinel        CONSTANT text := 'P29_064_PROBE_ROLLBACK';
BEGIN
    SELECT id, agency_id INTO v_contatto, v_agenzia
      FROM contacts WHERE agency_id IS NOT NULL ORDER BY id LIMIT 1;

    IF v_contatto IS NULL OR v_agenzia IS NULL THEN
        RETURN;
    END IF;

    SELECT id INTO v_altra_agenzia
      FROM agencies WHERE id <> v_agenzia ORDER BY id LIMIT 1;

    BEGIN
        INSERT INTO communication_messages (
            agency_id, contact_id, channel, direction, communication_type,
            mode, reason_code, subject_snapshot, rendered_body,
            destination_snapshot, actor_type, idempotency_key
        ) VALUES (
            v_agenzia, v_contatto, 'email', 'outbound', 'service',
            'automatic', 'stima_pdf', 'oggetto sonda', 'corpo sonda',
            'sonda@example.invalid', 'system', 'P29_064_PROBE_KEY'
        ) RETURNING id INTO v_msg;
        v_insert_ok := TRUE;

        -- Lo snapshot non si riscrive.
        BEGIN
            UPDATE communication_messages
               SET rendered_body = 'corpo riscritto' WHERE id = v_msg;
        EXCEPTION WHEN raise_exception THEN
            v_snapshot_bloc := TRUE;
        END;

        -- Lo stato invece si', ed e' il mestiere della colonna. Il claim scrive
        -- token e istante insieme.
        UPDATE communication_messages
           SET status = 'sending', claim_token = v_token, claimed_at = NOW(),
               attempt_count = attempt_count + 1, last_attempt_at = NOW()
         WHERE id = v_msg;
        v_stato_ok := TRUE;

        -- Un token su un messaggio non reclamato.
        BEGIN
            UPDATE communication_messages
               SET status = 'sent', sent_at = NOW()
             WHERE id = v_msg;
        EXCEPTION WHEN check_violation THEN
            v_claim_incoer := TRUE;
        END;

        -- Il tentativo, aperto.
        INSERT INTO communication_attempts (
            agency_id, message_id, attempt_no, claim_token, provider,
            started_at, outcome
        ) VALUES (
            v_agenzia, v_msg, 1, v_token, 'probe', NOW(), 'in_progress'
        ) RETURNING id INTO v_tent;

        -- La recovery lo chiude.
        UPDATE communication_attempts
           SET outcome = 'indeterminate', failure_class = 'indeterminate',
               error_code = 'outcome_unknown', finished_at = NOW(),
               recovered_at = NOW()
         WHERE id = v_tent;

        -- E chiuso resta.
        BEGIN
            UPDATE communication_attempts
               SET outcome = 'accepted', failure_class = NULL WHERE id = v_tent;
        EXCEPTION WHEN raise_exception THEN
            v_chiuso_bloc := TRUE;
        END;

        -- Il risultato tardivo entra come riga NUOVA, stesso attempt_no.
        INSERT INTO communication_attempts (
            agency_id, message_id, attempt_no, claim_token, provider,
            started_at, finished_at, outcome, provider_message_id, late_result
        ) VALUES (
            v_agenzia, v_msg, 1, v_token, 'probe', NOW(), NOW(),
            'accepted', 'probe.msgid', TRUE
        );
        v_tardivo_ok := TRUE;

        -- Ma uno solo.
        BEGIN
            INSERT INTO communication_attempts (
                agency_id, message_id, attempt_no, claim_token, provider,
                started_at, finished_at, outcome, late_result
            ) VALUES (
                v_agenzia, v_msg, 1, v_token, 'probe', NOW(), NOW(),
                'accepted', TRUE
            );
        EXCEPTION WHEN unique_violation THEN
            v_tardivo_doppio := TRUE;
        END;

        -- Il messaggio non si cancella.
        BEGIN
            DELETE FROM communication_messages WHERE id = v_msg;
        EXCEPTION WHEN raise_exception THEN
            v_delete_rifiut := TRUE;
        END;

        -- La stessa intenzione, due volte, nella stessa agenzia.
        BEGIN
            INSERT INTO communication_messages (
                agency_id, contact_id, channel, direction, communication_type,
                mode, reason_code, subject_snapshot, rendered_body,
                destination_snapshot, actor_type, idempotency_key
            ) VALUES (
                v_agenzia, v_contatto, 'email', 'outbound', 'service',
                'automatic', 'stima_pdf', 'oggetto sonda', 'corpo sonda',
                'sonda@example.invalid', 'system', 'P29_064_PROBE_KEY'
            );
        EXCEPTION WHEN unique_violation THEN
            v_idem_rifiut := TRUE;
        END;

        -- Uno stato che non esiste.
        BEGIN
            UPDATE communication_messages SET status = 'delivered' WHERE id = v_msg;
        EXCEPTION WHEN check_violation THEN
            v_stato_ignoto := TRUE;
        END;

        -- Il contatto di un'altra agenzia.
        IF v_altra_agenzia IS NOT NULL THEN
            BEGIN
                INSERT INTO communication_messages (
                    agency_id, contact_id, channel, direction, communication_type,
                    mode, reason_code, subject_snapshot, rendered_body,
                    destination_snapshot, actor_type, idempotency_key
                ) VALUES (
                    v_altra_agenzia, v_contatto, 'email', 'outbound', 'service',
                    'automatic', 'stima_pdf', 'oggetto sonda', 'corpo sonda',
                    'sonda@example.invalid', 'system', 'P29_064_PROBE_KEY_2'
                );
            EXCEPTION WHEN foreign_key_violation THEN
                v_tenant_rifiut := TRUE;
            END;
        ELSE
            v_tenant_rifiut := TRUE;
        END IF;

        RAISE EXCEPTION USING MESSAGE = v_sentinel;
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM <> v_sentinel THEN
            RAISE;
        END IF;
    END;

    IF NOT v_insert_ok THEN
        RAISE EXCEPTION 'P29-2.1 064: a legitimate message was refused';
    END IF;
    IF NOT v_snapshot_bloc THEN
        RAISE EXCEPTION 'P29-2.1 064: the rendered body of a message was rewritable';
    END IF;
    IF NOT v_stato_ok THEN
        RAISE EXCEPTION 'P29-2.1 064: a legitimate claim was refused';
    END IF;
    IF NOT v_claim_incoer THEN
        RAISE EXCEPTION 'P29-2.1 064: a message left sending while keeping its claim token';
    END IF;
    IF NOT v_chiuso_bloc THEN
        RAISE EXCEPTION 'P29-2.1 064: a closed attempt was reopened';
    END IF;
    IF NOT v_tardivo_ok THEN
        RAISE EXCEPTION 'P29-2.1 064: a late provider result could not be recorded';
    END IF;
    IF NOT v_tardivo_doppio THEN
        RAISE EXCEPTION 'P29-2.1 064: two late results were accepted for one attempt';
    END IF;
    IF NOT v_delete_rifiut THEN
        RAISE EXCEPTION 'P29-2.1 064: a message was deletable';
    END IF;
    IF NOT v_idem_rifiut THEN
        RAISE EXCEPTION 'P29-2.1 064: the same communication intent was accepted twice';
    END IF;
    IF NOT v_stato_ignoto THEN
        RAISE EXCEPTION 'P29-2.1 064: a status outside the closed set was accepted';
    END IF;
    IF NOT v_tenant_rifiut THEN
        RAISE EXCEPTION 'P29-2.1 064: a message was accepted for a contact of another agency';
    END IF;
END
$do$;
