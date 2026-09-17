-- P29-2.6E: una comunicazione SERVICE puo' non avere un contatto, e allora
-- il suo genitore di lifecycle e' la stima.
--
-- Additive. Non crea tabelle, non inserisce righe, non cancella nulla.
-- Altera UNA tabella preesistente - `communication_messages` della 064 - e
-- aggiunge UN trigger a `stime`.
--
-- Transaction ownership: the runner owns the UP transaction (version >= 027),
-- so this file carries no BEGIN/COMMIT. See
-- docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.
--
-- ---------------------------------------------------------------------------
-- PERCHE' QUESTA MIGRATION ESISTE, E PERCHE' NON ERA PREVISTA
--
-- Il design P29-2.0 prevedeva ZERO migration dopo la 064. Questa nasce da un
-- audit del flusso pubblico reale, non da un ripensamento di schema.
--
-- `POST /api/salva_stima` chiama `core_service.bridge_public_stima(...)` per
-- risolvere il contatto CRM. Quel bridge PUO' non produrne uno, e in quattro
-- modi che esistono nel codice:
--
--     core/repository.py:632   conflict, ambiguous_identity
--     core/repository.py:637   conflict, identity_conflict
--     core/repository.py:646   skipped,  insufficient_contact_identity
--     main.py:883              il bridge solleva, l'eccezione e' catturata
--
-- Un conflitto di identita' - due contatti con la stessa email - e' ordinario
-- in un CRM: e' la persona che ha gia' scritto in passato. Oggi non ha
-- conseguenze, perche' `invia_mail` non ha bisogno di un contatto e l'email
-- parte comunque. Con il cutover di P29-2.6E la mail passera' da
-- `communication.service.enqueue(...)`, e con `contact_id NOT NULL` quella
-- persona semplicemente NON riceverebbe la sua stima.
--
-- ---------------------------------------------------------------------------
-- LA REGOLA, E PERCHE' NON E' "CONTACT_ID DIVENTA OPZIONALE"
--
--     MARKETING            contact_id OBBLIGATORIO
--     SERVICE con contatto contact_id valorizzato
--     SERVICE senza        contact_id NULL, e allora stima_id OBBLIGATORIO
--
-- Il marketing richiede un contatto perche' il consenso si interroga su una
-- persona del CRM: senza contatto non esiste una decisione da leggere, e un
-- invio marketing senza consenso verificabile non deve poter nascere.
--
-- Il service no: il destinatario effettivo e' gia' congelato in
-- `destination_snapshot` (C12), e la comunicazione e' l'esecuzione di una
-- richiesta dell'interessato, non un'iniziativa commerciale.
--
-- Ma un messaggio SENZA NESSUN GENITORE non deve poter esistere, ed e' il
-- punto dell'intera migration. `contact_id NOT NULL` non era solo una
-- restrizione: era l'UNICO genitore di queste righe, e su di esso poggiavano
-- due decisioni gia' certificate della 064:
--
--   R4  nessuna FK verso `agencies`, perche' l'esistenza dell'agenzia e'
--       implicata TRANSITIVAMENTE da `contacts.agency_id`;
--   C6  `communication_messages` non entra in `DEDICATED_TABLES` del purge
--       P26-6, perche' il purge cancella i contatti prima delle agenzie e la
--       catena si chiude da sola.
--
-- Togliere `NOT NULL` e basta avrebbe prodotto righe senza genitore: misurate
-- su PostgreSQL 16, sopravvivevano alla cancellazione della propria agenzia,
-- con `agency_id` danglante, e NON erano cancellabili da nessuno - il guard
-- della 064 rifiuta il DELETE diretto e nessuna cascata le raggiungeva piu'.
-- Questa migration ridona un genitore a entrambe le forme, e per questo le tre
-- aggiunte sotto stanno insieme.
--
-- ---------------------------------------------------------------------------
-- PERCHE' UN TRIGGER SU `stime` E NON UNA SECONDA FK
--
-- La semantica richiesta e' CONDIZIONALE:
--
--     contatto presente -> cancellare la stima AZZERA stima_id, il messaggio resta
--     contatto assente  -> cancellare la stima ELIMINA il messaggio
--
-- La prima meta' e' gia' la 064 (`stima_id REFERENCES stime(id) ON DELETE SET
-- NULL`), con una motivazione che resta valida: sparire il contesto
-- commerciale non deve cancellare una comunicazione realmente scambiata con
-- una persona. Quella FK NON si tocca.
--
-- La seconda meta' non e' esprimibile con una FK, e il tentativo e' stato
-- fatto e misurato: una colonna generata
--
--     lifecycle_stima_id GENERATED ALWAYS AS
--         (CASE WHEN contact_id IS NULL THEN stima_id END) STORED
--
-- con una FK composita CASCADE su di essa e' legale su PostgreSQL 16, ma con
-- le DUE chiavi verso `stime` coesistenti un `DELETE FROM stime` applica
-- ENTRAMBE le azioni referenziali: il SET NULL azzera `stima_id` e la riga
-- viola il CHECK del genitore prima che il CASCADE la porti via. Quale delle
-- due vinca dipende dall'ordine dei trigger RI, che non e' un fondamento.
--
-- Il trigger e' BEFORE DELETE proprio per questo: agisce PRIMA che il SET NULL
-- della 064 faccia perdere il legame, e tocca SOLO le righe senza contatto.
-- `stime` non aveva alcun trigger prima di oggi (033 ne mette uno su
-- `lead_stime`, 051 su `stime_dettagliate`), quindi non esiste un ordinamento
-- da negoziare con nessuno.
--
-- ---------------------------------------------------------------------------
-- COSA QUESTA MIGRATION NON FA
--
-- Non introduce un'erasure per interessato. Non esiste oggi in questa
-- piattaforma - `core/router.py` non ha una DELETE del contatto, solo dei suoi
-- RUOLI, e lo dichiara gia' `scripts/p26_6_live_cert.py:271` - ed e' debito
-- preesistente, non aperto qui. Cio' che questa migration garantisce e' che il
-- messaggio senza contatto abbia ESATTAMENTE il ciclo di vita del dato che
-- duplica: `stime` contiene gia' nome, cognome, email e telefono del
-- richiedente, e `POST /api/admin/stime/delete` e' oggi l'unico modo di
-- cancellarli. Dopo questa migration quella stessa operazione porta via anche
-- il messaggio.
-- ---------------------------------------------------------------------------


-- ---------------------------------------------------------------------------
-- 1. `contact_id` diventa nullable.
-- ---------------------------------------------------------------------------
ALTER TABLE communication_messages
    ALTER COLUMN contact_id DROP NOT NULL;


-- ---------------------------------------------------------------------------
-- 2. La FK diretta verso `agencies`, che R4 aveva escluso.
--
-- R4 la escludeva perche' ridondante: l'esistenza dell'agenzia era implicata
-- da `contacts.agency_id`. Con `contact_id` nullable quell'implicazione cade
-- per le righe senza contatto, e serve un backstop esplicito.
--
-- CASCADE e non RESTRICT: RESTRICT bloccherebbe il purge dell'agenzia, che e'
-- precisamente cio' che R4 voleva evitare. CASCADE dice che un messaggio non
-- sopravvive all'agenzia che lo ha prodotto - che e' vero e che oggi non era
-- garantito.
--
-- Non cambia l'ordine di P26-6: `contacts.agency_id` e `stime.agency_id` sono
-- RESTRICT verso `agencies`, quindi questa cascata puo' scattare solo quando
-- contatti e stime sono gia' spariti.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'communication_messages_agency_fk'
           AND conrelid = 'public.communication_messages'::regclass
    ) THEN
        ALTER TABLE communication_messages
            ADD CONSTRAINT communication_messages_agency_fk
            FOREIGN KEY (agency_id) REFERENCES agencies (id) ON DELETE CASCADE;
    END IF;
END
$$;


-- ---------------------------------------------------------------------------
-- 3. Il marketing esige un contatto.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'communication_messages_marketing_contact_chk'
           AND conrelid = 'public.communication_messages'::regclass
    ) THEN
        ALTER TABLE communication_messages
            ADD CONSTRAINT communication_messages_marketing_contact_chk
            CHECK (communication_type <> 'marketing' OR contact_id IS NOT NULL);
    END IF;
END
$$;


-- ---------------------------------------------------------------------------
-- 4. Nessun messaggio senza genitore di lifecycle.
--
-- E' il vincolo che rende sicura la nullabilita' del punto 1: una riga deve
-- avere almeno uno fra contatto e stima, e ciascuno dei due sa portarsela via.
-- Stessa forma di `activities_reference_chk` (001:104), che nel repository
-- esprime gia' questa idea.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'communication_messages_lifecycle_parent_chk'
           AND conrelid = 'public.communication_messages'::regclass
    ) THEN
        ALTER TABLE communication_messages
            ADD CONSTRAINT communication_messages_lifecycle_parent_chk
            CHECK (contact_id IS NOT NULL OR stima_id IS NOT NULL);
    END IF;
END
$$;


-- ---------------------------------------------------------------------------
-- 5. Il genitore alternativo: la stima si porta via i propri messaggi senza
--    contatto, e SOLO quelli.
--
-- `agency_id` nel WHERE insieme a `stima_id`: la coppia, non l'id da solo, per
-- la stessa ragione per cui ogni lettura del dominio porta il predicato di
-- tenancy - e perche' `stime.id` e' un SERIAL globale.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION stime_purge_contactless_messages()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $fn$
BEGIN
    DELETE FROM communication_messages
     WHERE agency_id  = OLD.agency_id
       AND stima_id   = OLD.id
       AND contact_id IS NULL;
    RETURN OLD;
END
$fn$;

DROP TRIGGER IF EXISTS trg_stime_purge_contactless_messages ON stime;
CREATE TRIGGER trg_stime_purge_contactless_messages
    BEFORE DELETE ON stime
    FOR EACH ROW EXECUTE FUNCTION stime_purge_contactless_messages();

-- ENABLE ALWAYS, per la ragione misurata in 062 e riusata in 064: un trigger
-- ordinario (tgenabled='O') non scatta quando session_replication_role e'
-- 'replica'. Un genitore di lifecycle che un parametro di sessione puo'
-- spegnere non e' un genitore.
ALTER TABLE stime
    ENABLE ALWAYS TRIGGER trg_stime_purge_contactless_messages;


-- ---------------------------------------------------------------------------
-- 6. Il guard C6, riscritto per conoscere il secondo genitore.
--
-- IL RAMO `UPDATE` E' QUELLO DELLA 064, PAROLA PER PAROLA.
--
-- Sostituire una funzione la riscrive tutta: una colonna dimenticata qui
-- sarebbe un'immutabilita' persa in silenzio. Le dodici colonne protette sono
-- le stesse, nello stesso ordine, con lo stesso messaggio.
-- `tests/test_p29_2_6e_lifecycle_parent.py` le confronta una per una con il
-- testo della 064.
--
-- IL RAMO `DELETE` CAMBIA DI UN RAMO SOLO, E LO DICHIARO
--
-- La 064 chiedeva: "il contatto e' sparito?". Con `contact_id` nullable quella
-- domanda non ha risposta per le righe senza contatto - `WHERE id = NULL` non
-- trova nulla e `NOT EXISTS` sarebbe TRUE per costruzione, che e' una risposta
-- accidentale e non una risposta. La formula qui e' NULL-aware: il primo ramo
-- vale solo quando un contatto c'e' davvero.
--
-- Il secondo ramo e' l'allentamento, ed e' dichiarato invece che nascosto: per
-- una riga SENZA contatto non si verifica QUALE genitore sia sparito. Non si
-- puo': il trigger del punto 5 e' BEFORE DELETE, quindi quando arriviamo qui
-- la stima esiste ancora. Regge su un'enumerazione chiusa - a profondita' > 1
-- questa tabella e' raggiungibile SOLO dai suoi genitori:
--
--     CASCADE da `contacts`   (FK composita della 064)
--     CASCADE da `agencies`   (punto 2 di questo file)
--     trigger da `stime`      (punto 5 di questo file)
--
-- Nessun'altra chiave esterna punta a `communication_messages`; e' solo
-- `communication_attempts` a puntare a lei.
-- `tests/test_p29_2_6e_lifecycle_parent.py` fissa quell'enumerazione sul
-- catalogo e fallisce se un percorso nuovo compare senza che la certificazione
-- venga aggiornata deliberatamente.
--
-- CIO' CHE NON CAMBIA: il DELETE diretto resta rifiutato, sempre. La
-- condizione `pg_trigger_depth() > 1` e' la prima meta' della congiunzione e
-- non si tocca, e nessuna variabile di sessione entra qui - l'esistenza del
-- genitore si legge, non si dichiara.
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


-- ---------------------------------------------------------------------------
-- 7. La migration rilegge il catalogo e prova i propri effetti.
--
-- Stessa disciplina della 064: il testo dice cosa si voleva, il catalogo dice
-- cosa c'e'.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    v_notnull BOOLEAN;
    v_fk      "char";
    v_tg      "char";
    v_checks  INTEGER;
BEGIN
    SELECT attnotnull INTO v_notnull
      FROM pg_attribute
     WHERE attrelid = 'public.communication_messages'::regclass
       AND attname  = 'contact_id';
    IF v_notnull THEN
        RAISE EXCEPTION 'P29-2.6E 065: communication_messages.contact_id is still NOT NULL';
    END IF;

    SELECT confdeltype INTO v_fk
      FROM pg_constraint
     WHERE conname  = 'communication_messages_agency_fk'
       AND conrelid = 'public.communication_messages'::regclass;
    IF v_fk IS NULL THEN
        RAISE EXCEPTION 'P29-2.6E 065: the direct foreign key towards agencies is missing';
    END IF;
    IF v_fk <> 'c' THEN
        RAISE EXCEPTION
            'P29-2.6E 065: communication_messages_agency_fk must be ON DELETE CASCADE, found %', v_fk;
    END IF;

    SELECT count(*) INTO v_checks
      FROM pg_constraint
     WHERE conrelid = 'public.communication_messages'::regclass
       AND contype  = 'c'
       AND conname IN ('communication_messages_marketing_contact_chk',
                       'communication_messages_lifecycle_parent_chk');
    IF v_checks <> 2 THEN
        RAISE EXCEPTION
            'P29-2.6E 065: expected both new CHECK constraints, found %', v_checks;
    END IF;

    SELECT tgenabled INTO v_tg
      FROM pg_trigger
     WHERE tgname   = 'trg_stime_purge_contactless_messages'
       AND tgrelid  = 'public.stime'::regclass;
    IF v_tg IS NULL THEN
        RAISE EXCEPTION 'P29-2.6E 065: the lifecycle trigger on stime is missing';
    END IF;
    IF v_tg <> 'A' THEN
        RAISE EXCEPTION
            'P29-2.6E 065: trg_stime_purge_contactless_messages must be ENABLE ALWAYS (A), found %', v_tg;
    END IF;
END
$$;
