-- P29-1.1 consent events: the append-only history of consent.
--
-- Additive, idempotent. Creates ONE table and the guards that make it
-- append-only. It alters no existing table and seeds no row: non esiste un
-- consenso di default, e il censimento P29-1.0 ha stabilito che NON si fa
-- backfill storico.
--
-- Transaction ownership: the runner owns the UP transaction (version >= 027),
-- so this file carries no BEGIN/COMMIT. See
-- docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.
--
-- ---------------------------------------------------------------------------
-- COS'E' QUESTA TABELLA
--
-- La verita' sul consenso. Lo stato corrente vive sulla proiezione in
-- `contacts` (063) perche' e' li' che serve leggerlo in fretta e perche' P24
-- lo legge gia' da anni; ma se le due cose divergono, vince questa.
--
-- Regola di scrittura, una sola: non si scrive lo stato senza aver prima
-- scritto l'evento, nella stessa transazione. Il modulo `consent/` e' l'unico
-- percorso che lo fa.
--
-- Regola di derivazione, una sola: lo stato corrente di (contact, purpose) e'
-- la `decision` dell'evento piu' recente per `decided_at`, tie-break su `id`
-- crescente. Nessun evento significa `never_given`, che NON e' `revoked`.
--
-- ---------------------------------------------------------------------------
-- COSA QUESTA TABELLA NON E'
--
-- Non e' `seller_timeline_events`. Quella registra i FATTI del percorso
-- venditore ed e' letta da FLOW e P18 per decidere cosa far fare a un agente.
-- Questa registra le DECISIONI di una persona sul proprio consenso, ed e'
-- letta per decidere se il software ha il diritto di scriverle. Due domande
-- diverse, due tabelle.
--
-- Non e' `activities`. Quella e' la proiezione leggibile dall'agente nella
-- scheda contatto. Un consenso non e' un'attivita' commerciale, e gli stati
-- operativi delle comunicazioni (scheduled, sending, failed) non vivranno ne'
-- qui ne' li' ma nel dominio Communications - decisione vincolante del
-- committente, registrata qui perche' questa e' la tabella che sarebbe stata
-- la scorciatoia sbagliata.
--
-- ---------------------------------------------------------------------------
-- PERCHE' E' TENANT-SCOPED E IL REGISTRO NO
--
-- Qui ci sono dati personali riferiti al contatto di UNA agenzia. `agency_id`
-- e' NOT NULL e nessuna lettura lo attraversa: il modulo `consent/` compone il
-- predicato con la stessa disciplina di `core/scope.py`, dove il nome della
-- tabella e il suo scope escono dalla stessa espressione.
--
-- Il registro delle notice (061) non ha agency perche' contiene testi, non
-- persone. Le due tabelle hanno tenancy diverse perche' contengono cose
-- diverse, non per distrazione.

-- ---------------------------------------------------------------------------
-- 1. La tabella.
--
-- LE CHIAVI ESTERNE, E PERCHE' SONO CASCADE
--
-- 057 non ne ha nessuna, e l'argomento era: un registro immutabile non deve
-- tenere pretese referenziali sulle entita' che descrive. Qui la conclusione e'
-- diversa, e la differenza e' reale: `platform_audit_log` descrive atti
-- amministrativi su entita' con un ciclo di vita proprio; questa tabella e'
-- parte del DATO del contatto. Un evento di consenso senza il contatto a cui si
-- riferisce non e' una storia incompleta, e' un dato privo di soggetto.
--
-- La prima stesura le aveva messe RESTRICT, per non perdere mai la prova. E'
-- stata CAMBIATA dopo che la sentinella P26-6
-- (tests/test_p26_6_live_cert_script.py::test_86i) ha mostrato cosa
-- significasse davvero: il cleanup di agenzia gia' certificato cancella righe
-- da `contacts` e da `agencies`, e un RESTRICT qui avrebbe fatto cadere
-- l'intera transazione di cleanup al primo contatto con un consenso
-- registrato. Le alternative erano tutte peggiori: una procedura speciale per
-- cancellare a mano questa tabella, un bypass del trigger, oppure righe orfane
-- e tenant-tombstone tenuti in vita solo per P29.
--
-- LA DECISIONE, DICHIARATA: LA CANCELLAZIONE FISICA E' UN PURGE ECCEZIONALE.
--
-- Durante il normale ciclo di vita applicativo lo storico resta append-only
-- senza eccezioni: nessun UPDATE, nessuna DELETE diretta, nessuna modifica
-- retroattiva. Ma la cancellazione FISICA del contatto o dell'intero tenant e'
-- un'operazione eccezionale, e in quel caso lo storico collegato se ne va con
-- il soggetto a cui apparteneva.
--
-- Il CASCADE da solo non bastava, e il motivo e' stato MISURATO su PostgreSQL
-- 16, non supposto: un trigger BEFORE DELETE di riga SCATTA ANCHE SULLA
-- CANCELLAZIONE GENERATA DAL CASCADE, quindi il guardiano append-only avrebbe
-- fatto fallire il purge esattamente come il RESTRICT. Come il guardiano
-- distingue i due casi e' spiegato al punto 3.
--
-- `notice_id` e' NULLABLE, e l'assenza ha un significato preciso e onesto:
-- consenso raccolto prima che il registro delle notice esistesse. I 12
-- contatti legacy del censimento P29-1.0 sono esattamente questo. Non si
-- inventa una notice per riempirlo.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS consent_events (
    id              BIGSERIAL    PRIMARY KEY,
    agency_id       BIGINT       NOT NULL REFERENCES agencies(id) ON DELETE CASCADE,
    contact_id      BIGINT       NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    purpose         VARCHAR(20)  NOT NULL,
    decision        VARCHAR(20)  NOT NULL,
    decided_at      TIMESTAMPTZ  NOT NULL,
    source          VARCHAR(40)  NOT NULL,
    notice_id       BIGINT       REFERENCES consent_notices(id) ON DELETE RESTRICT,
    actor_type      VARCHAR(20)  NOT NULL,
    actor_ref       VARCHAR(120),
    evidence_type   VARCHAR(40),
    evidence_ref    VARCHAR(100),
    note            TEXT,
    idempotency_key VARCHAR(300) UNIQUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- Gli stessi due scopi di 061, e nessun terzo. `marketing` copre Email e
    -- WhatsApp INSIEME: la separazione non e' rappresentabile per costruzione.
    CONSTRAINT consent_events_purpose_chk
        CHECK (purpose IN ('privacy_terms', 'marketing')),

    -- Due decisioni, e nessuna terza. In particolare NON esiste un valore per
    -- "non ha spuntato la casella": quello non e' una decisione, e' l'assenza
    -- di una. Il caso D del design (casella vuota su una nuova stima) non
    -- produce alcuna riga qui, e questo vincolo e' il motivo per cui non puo'
    -- produrla nemmeno per errore.
    CONSTRAINT consent_events_decision_chk
        CHECK (decision IN ('granted', 'revoked')),

    CONSTRAINT consent_events_actor_type_chk
        CHECK (actor_type IN ('subject', 'operator', 'system')),

    -- La provenienza e' obbligatoria. Un consenso di cui non si sa da dove
    -- venga non e' auditabile, e un evento non auditabile e' rumore in una
    -- tabella che esiste per essere riletta in tribunale.
    CONSTRAINT consent_events_source_chk CHECK (BTRIM(source) <> ''),

    -- UNA REVOCA E' SEMPRE RICONDUCIBILE A CHI L'HA VOLUTA.
    --
    -- `system` e' ammesso per un grant importato o migrato, dove l'attore e' un
    -- processo e lo si dichiara. Non e' ammesso per una revoca: una revoca
    -- nasce da un'azione - la persona (unsubscribe, STOP su WhatsApp) o un
    -- operatore che la registra per lei. Un processo che revoca da solo e' una
    -- perdita di consenso che nessuno ha chiesto e nessuno puo' spiegare.
    CONSTRAINT consent_events_revoked_actor_chk
        CHECK (decision <> 'revoked' OR actor_type IN ('subject', 'operator')),

    -- Un atto di un operatore porta il nome dell'operatore. Senza, "modificato
    -- dal CRM" e' tutto cio' che resta, ed e' il buco che P29-1 esiste per
    -- chiudere (oggi core/repository.py::update_contact cambia il consenso
    -- senza lasciare traccia ne' timestamp).
    CONSTRAINT consent_events_operator_ref_chk
        CHECK (actor_type <> 'operator' OR (actor_ref IS NOT NULL AND BTRIM(actor_ref) <> '')),

    -- Le due meta' della prova stanno insieme o non stanno: un riferimento
    -- senza tipo non si sa leggere, un tipo senza riferimento non punta a
    -- niente.
    CONSTRAINT consent_events_evidence_chk
        CHECK ((evidence_type IS NULL) = (evidence_ref IS NULL)),

    CONSTRAINT consent_events_idempotency_chk
        CHECK (idempotency_key IS NULL OR BTRIM(idempotency_key) <> '')
);

-- ---------------------------------------------------------------------------
-- 2. Indici.
--
-- Due percorsi di lettura, e non di piu'.
--
-- Il primo e' LA query del modulo: lo stato corrente di un contatto per uno
-- scopo, cioe' l'ultimo evento. Le colonne sono nell'ordine in cui la regola
-- di derivazione le usa - contatto, scopo, poi il tempo decrescente e l'id
-- come tie-break - cosi' la derivazione e' una lettura di indice e non un
-- ordinamento.
--
-- Il secondo e' il percorso di audit per agenzia.
--
-- Nessun indice su `purpose` da solo o su `decision`: pochissimi valori
-- distinti, costerebbero scritture e non farebbero risparmiare niente.
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_consent_events_current
    ON consent_events (contact_id, purpose, decided_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_consent_events_agency
    ON consent_events (agency_id, decided_at DESC);

-- ---------------------------------------------------------------------------
-- 3. Append-only, e l'unica eccezione: il purge.
--
-- COSA DISTINGUE UN PURGE DA UNA CANCELLAZIONE APPLICATIVA
--
-- Due condizioni, entrambe necessarie, entrambe verificate su PostgreSQL 16
-- prima di essere scritte qui:
--
--   1. pg_trigger_depth() > 1
--      Le azioni referenziali sono implementate da trigger interni, quindi
--      durante un CASCADE questo guardiano gira ANNIDATO. Misurato: una DELETE
--      diretta arriva qui a profondita' 1 (siamo dentro il nostro stesso
--      trigger), una DELETE generata dal CASCADE a profondita' 2.
--
--   2. IL GENITORE NON C'E' PIU'
--      Il trigger RI del CASCADE e' un AFTER DELETE sul genitore: quando
--      questo guardiano gira, la riga di `contacts` (o di `agencies`) E' GIA'
--      SPARITA. Misurato: parent_exists = false durante il cascade, true su
--      una DELETE diretta.
--
-- PERCHE' SERVONO TUTTE E DUE
--
-- La prima da sola sarebbe fragile: basterebbe che un qualsiasi trigger futuro
-- cancellasse da questa tabella perche' la profondita' salga e il guardiano si
-- apra. La seconda da sola sarebbe insufficiente a dire che la cancellazione
-- e' PARTE di quel comando. Insieme, l'unico modo di soddisfarle e' cancellare
-- davvero il contatto o l'agenzia - cioe' fare il purge.
--
-- E NON C'E' UN INTERRUTTORE
--
-- Nessuna variabile di sessione, nessun GUC, nessun flag applicativo, nessun
-- `SET LOCAL` apre questa porta. `pg_trigger_depth()` non e' assegnabile, e
-- l'esistenza del genitore non e' dichiarabile: si legge. Un'applicazione che
-- volesse cancellare uno storico senza cancellare la persona non ha alcun modo
-- di farlo, ed e' esattamente la proprieta' che serviva.
--
-- L'UPDATE non ha alcuna eccezione, in nessun caso. Una decisione presa e'
-- stata presa; un errore si corregge scrivendo l'evento successivo, che e' cio'
-- che il modello gia' prevede.
--
-- Cio' che un superuser puo' ancora fare: togliere il trigger e poi le righe.
-- E' onesto dirlo: il guardiano difende lo storico dall'applicazione, da uno
-- script sbagliato e da un ruolo ordinario, non da chi possiede il database.
-- Da quello difendono backup e restore, gia' certificati in P26.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION consent_events_append_only()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_genitore_sparito BOOLEAN;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION
            'consent_events is append-only: UPDATE is refused (id=%). Record the new decision as a new event.',
            OLD.id;
    END IF;

    v_genitore_sparito :=
        NOT EXISTS (SELECT 1 FROM contacts WHERE id = OLD.contact_id)
        OR NOT EXISTS (SELECT 1 FROM agencies WHERE id = OLD.agency_id);

    IF pg_trigger_depth() > 1 AND v_genitore_sparito THEN
        -- Purge: il soggetto di questo consenso non esiste piu'. Lo storico lo
        -- segue.
        RETURN OLD;
    END IF;

    RAISE EXCEPTION
        'consent_events is append-only: DELETE is refused (id=%). Consent history is removed only by physically purging the contact or the agency it belongs to.',
        OLD.id;
END
$fn$;

DROP TRIGGER IF EXISTS trg_consent_events_append_only ON consent_events;
CREATE TRIGGER trg_consent_events_append_only
    BEFORE UPDATE OR DELETE ON consent_events
    FOR EACH ROW EXECUTE FUNCTION consent_events_append_only();

-- ENABLE ALWAYS, E NON E' UN DETTAGLIO.
--
-- Un trigger ordinario ha tgenabled='O': scatta in `session_replication_role`
-- = origin, e NON scatta in 'replica'. Misurato su PostgreSQL 16 contro questa
-- stessa migration: con
--
--     SET LOCAL session_replication_role = 'replica';
--     DELETE FROM consent_events WHERE id = ...;
--
-- la riga spariva. Il guardiano c'era, la porta era aperta lo stesso.
--
-- `ENABLE ALWAYS` (tgenabled='A') lo fa scattare in ogni ruolo di replica, e
-- chiude quella strada. Resta vero - ed e' scritto sopra - che chi possiede il
-- database puo' togliere il trigger: da quello difendono backup e restore. La
-- differenza e' che togliere un trigger e' un atto che si vede, mentre un
-- parametro di sessione e' una riga in mezzo a una transazione.
ALTER TABLE consent_events ENABLE ALWAYS TRIGGER trg_consent_events_append_only;

-- ---------------------------------------------------------------------------
-- 3b. TRUNCATE resta vietato, anche a cascata.
--
-- `TRUNCATE contacts CASCADE` svuoterebbe questa tabella senza che nessun
-- trigger di riga se ne accorga, e senza che nessuna riga di `contacts` venga
-- esaminata una per una: non e' un purge mirato, e' un azzeramento. Il cleanup
-- di P26-6 non la usa - cancella per predicato con DELETE (verificato: zero
-- occorrenze di TRUNCATE in scripts/p26_6_live_cert.py) - quindi vietarla non
-- toglie niente a nessuno e chiude una strada larga.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION consent_events_no_truncate()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $fn$
BEGIN
    RAISE EXCEPTION 'consent_events is append-only: TRUNCATE is refused.';
END
$fn$;

DROP TRIGGER IF EXISTS trg_consent_events_no_truncate ON consent_events;
CREATE TRIGGER trg_consent_events_no_truncate
    BEFORE TRUNCATE ON consent_events
    FOR EACH STATEMENT EXECUTE FUNCTION consent_events_no_truncate();

ALTER TABLE consent_events ENABLE ALWAYS TRIGGER trg_consent_events_no_truncate;

-- ---------------------------------------------------------------------------
-- 4. Prova, riletta dal catalogo.
--
-- Bit di pg_trigger.tgtype come in 057 e 061:
--   trigger di riga      : ROW(1) + BEFORE(2) + DELETE(8) + UPDATE(16) = 27
--   trigger di statement : BEFORE(2) + TRUNCATE(32)                    = 34
--
-- Si verificano anche le due FK, perche' la loro assenza non si noterebbe
-- finche' non conta, e il loro `confdeltype` sbagliato si noterebbe solo il
-- giorno in cui qualcuno cancella un contatto.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_riga      text[];
    v_tgtype    smallint;
    v_tgenabled "char";
    v_funzione  text;
    v_atteso    smallint;
    v_deltype   "char";
BEGIN
    FOREACH v_riga SLICE 1 IN ARRAY ARRAY[
        ARRAY['trg_consent_events_append_only','consent_events','consent_events_append_only','27'],
        ARRAY['trg_consent_events_no_truncate','consent_events','consent_events_no_truncate','34']
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
            RAISE EXCEPTION 'P29-1.1 062: % is not installed on %', v_riga[1], v_riga[2];
        END IF;
        IF v_funzione <> v_riga[3] THEN
            RAISE EXCEPTION 'P29-1.1 062: % calls %, expected %', v_riga[1], v_funzione, v_riga[3];
        END IF;
        -- 'A' = ENABLE ALWAYS, e non 'O'. Un trigger 'O' non scatta quando
        -- session_replication_role e' 'replica', cioe' esiste un parametro di
        -- sessione che lo spegne. Questa prova esige la forma che quel
        -- parametro non spegne.
        IF v_tgenabled <> 'A' THEN
            RAISE EXCEPTION
                'P29-1.1 062: % is not ENABLE ALWAYS (tgenabled=%); session_replication_role would switch it off',
                v_riga[1], v_tgenabled;
        END IF;
        IF (v_tgtype & 2) = 0 OR (v_tgtype & 64) <> 0 THEN
            RAISE EXCEPTION 'P29-1.1 062: % must fire BEFORE, not AFTER or INSTEAD OF', v_riga[1];
        END IF;
        IF (v_tgtype & v_atteso) <> v_atteso THEN
            RAISE EXCEPTION
                'P29-1.1 062: % does not cover the required operations (tgtype=%, wanted=%)',
                v_riga[1], v_tgtype, v_atteso;
        END IF;
        IF (v_tgtype & 4) <> 0 THEN
            RAISE EXCEPTION
                'P29-1.1 062: % must not fire on INSERT; the table is append-only, not read-only',
                v_riga[1];
        END IF;
    END LOOP;

    -- Le due FK non distruttive.
    FOREACH v_riga SLICE 1 IN ARRAY ARRAY[
        ARRAY['consent_events_agency_id_fkey','agency_id'],
        ARRAY['consent_events_contact_id_fkey','contact_id']
    ]
    LOOP
        SELECT c.confdeltype INTO v_deltype
          FROM pg_constraint c
          JOIN pg_attribute a
            ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
         WHERE c.conrelid = 'public.consent_events'::regclass
           AND c.contype  = 'f'
           AND a.attname  = v_riga[2];

        IF v_deltype IS NULL THEN
            RAISE EXCEPTION
                'P29-1.1 062: consent_events.% carries no foreign key; a consent event could point at a row that does not exist',
                v_riga[2];
        END IF;
        -- 'c' = CASCADE. E' la decisione di C1, ed e' asserita al positivo:
        -- riportarla a RESTRICT o a SET NULL farebbe fallire questa migration
        -- invece di far fallire, mesi dopo, il cleanup di un'agenzia.
        IF v_deltype <> 'c' THEN
            RAISE EXCEPTION
                'P29-1.1 062: the foreign key on consent_events.% is not ON DELETE CASCADE (confdeltype=%); the P26-6 agency cleanup would fail on the first contact carrying a consent event',
                v_riga[2], v_deltype;
        END IF;
    END LOOP;
END
$do$;

-- ---------------------------------------------------------------------------
-- 5. Prova: i vincoli MORDONO.
--
-- Come in 061 e 060, la sonda scrive righe vere e le annulla con il savepoint
-- implicito di BEGIN ... EXCEPTION. Salta quando non c'e' un contatto o
-- un'agenzia su cui appoggiarsi: un database appena costruito dal runner non
-- ne ha, e una migration che fallisse li' sarebbe inapplicabile a uno schema
-- vuoto.
--
-- Si provano le quattro regole la cui violazione sarebbe silenziosa e grave:
-- append-only in UPDATE e DELETE, la revoca di `system`, l'operatore anonimo.
-- ---------------------------------------------------------------------------
DO $do$
DECLARE
    v_contatto      BIGINT;
    v_agenzia       BIGINT;
    v_id            BIGINT;
    v_insert_ok     BOOLEAN := FALSE;
    v_update_rifiut BOOLEAN := FALSE;
    v_delete_rifiut BOOLEAN := FALSE;
    v_sys_revoca    BOOLEAN := FALSE;
    v_op_anonimo    BOOLEAN := FALSE;
    v_decisione_ko  BOOLEAN := FALSE;
    v_sentinel      CONSTANT text := 'P29_062_PROBE_ROLLBACK';
BEGIN
    SELECT id, agency_id INTO v_contatto, v_agenzia
      FROM contacts WHERE agency_id IS NOT NULL ORDER BY id LIMIT 1;

    IF v_contatto IS NULL OR v_agenzia IS NULL THEN
        RETURN;
    END IF;

    BEGIN
        INSERT INTO consent_events
            (agency_id, contact_id, purpose, decision, decided_at, source, actor_type)
        VALUES
            (v_agenzia, v_contatto, 'marketing', 'granted', NOW(), 'probe', 'subject')
        RETURNING id INTO v_id;
        v_insert_ok := TRUE;

        BEGIN
            UPDATE consent_events SET decision = 'revoked' WHERE id = v_id;
        EXCEPTION WHEN raise_exception THEN
            v_update_rifiut := TRUE;
        END;

        BEGIN
            DELETE FROM consent_events WHERE id = v_id;
        EXCEPTION WHEN raise_exception THEN
            v_delete_rifiut := TRUE;
        END;

        -- Una revoca che nessuno ha voluto.
        BEGIN
            INSERT INTO consent_events
                (agency_id, contact_id, purpose, decision, decided_at, source, actor_type)
            VALUES
                (v_agenzia, v_contatto, 'marketing', 'revoked', NOW(), 'probe', 'system');
        EXCEPTION WHEN check_violation THEN
            v_sys_revoca := TRUE;
        END;

        -- Un operatore senza nome.
        BEGIN
            INSERT INTO consent_events
                (agency_id, contact_id, purpose, decision, decided_at, source, actor_type)
            VALUES
                (v_agenzia, v_contatto, 'marketing', 'granted', NOW(), 'probe', 'operator');
        EXCEPTION WHEN check_violation THEN
            v_op_anonimo := TRUE;
        END;

        -- Una terza decisione.
        BEGIN
            INSERT INTO consent_events
                (agency_id, contact_id, purpose, decision, decided_at, source, actor_type)
            VALUES
                (v_agenzia, v_contatto, 'marketing', 'not_given', NOW(), 'probe', 'subject');
        EXCEPTION WHEN check_violation THEN
            v_decisione_ko := TRUE;
        END;

        RAISE EXCEPTION USING MESSAGE = v_sentinel;
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM <> v_sentinel THEN
            RAISE;
        END IF;
    END;

    IF NOT v_insert_ok THEN
        RAISE EXCEPTION 'P29-1.1 062: a legitimate consent event was refused';
    END IF;
    IF NOT v_update_rifiut THEN
        RAISE EXCEPTION 'P29-1.1 062: a consent event was rewritable';
    END IF;
    IF NOT v_delete_rifiut THEN
        RAISE EXCEPTION 'P29-1.1 062: a consent event was deletable';
    END IF;
    IF NOT v_sys_revoca THEN
        RAISE EXCEPTION 'P29-1.1 062: a system-authored revocation was accepted';
    END IF;
    IF NOT v_op_anonimo THEN
        RAISE EXCEPTION 'P29-1.1 062: an operator event without actor_ref was accepted';
    END IF;
    IF NOT v_decisione_ko THEN
        RAISE EXCEPTION 'P29-1.1 062: a decision outside granted/revoked was accepted';
    END IF;
END
$do$;
