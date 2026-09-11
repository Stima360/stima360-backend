-- ===========================================================================
-- CLEANUP DEL RUN P26-6  52f6d97b5214  (commit 3bc14b2)
--
-- NON ESEGUITO. Questo file e' per la review.
--
-- DUE MODALITA', E SI SCEGLIE IN CIMA
--
--   \set ESEGUI false   PROVA GENERALE (predefinita). Tutte le verifiche e
--                       tutte le DELETE girano, poi ROLLBACK. La verifica
--                       finale esige che le 71 righe siano TORNATE al loro
--                       posto: la prova dimostra di non aver rotto niente,
--                       invece di limitarsi a non rompere.
--   \set ESEGUI true    ESECUZIONE. COMMIT, e la verifica finale esige zero
--                       residui sugli ID originali.
--
-- Serve psql: \set e \if sono suoi.
--
-- IL PERIMETRO: 71 RIGHE, IN DUE MODI DIVERSI
--
-- 61 righe arrivano dal censimento, dichiarate una per una:
--
--   contacts                   4   86, 87 (agenzie condivise)
--                                  88, 89 (agenzie dedicate 18/19)
--   properties                 2   44, 45
--   buy_requests               2   30, 31
--   matches                    2   28, 29
--   property_proposals         2   19, 20
--   property_sales             2   14, 15
--   owner_accounts             2   8, 9
--   agencies                   2   18, 19
--   tasks                      2   40, 41
--   followup_actions           1   867
--   buy_request_history        8   121..128
--   match_runs                 2   29, 30
--   match_requirement_results 16   201..216
--   property_contacts          2   24, 25
--   property_status_history    2   87, 88
--   seller_timeline_events     2   106, 107
--   owner_audit_log            8   585..592
--
-- COSA MANCAVA AL PRIMO DRY-RUN, E PERCHE'
--
-- Il primo giro si e' fermato a 59 righe su 71, ed e' andata come doveva: il
-- conteggio non era un'etichetta, era un'asserzione, e ha fermato tutto prima
-- di qualsiasi DELETE. Mancavano dodici righe, di tre specie diverse:
--
--   contacts 88, 89          i contatti creati DENTRO le agenzie dedicate
--                            dalla fixture FOLLOWUP. Il censimento elencava
--                            solo 86/87, quelli delle agenzie condivise.
--   seller_timeline_events   la risorsa del dominio SELLER_INTENT, che porta
--   106, 107                 il marcatore in `event_type`.
--   owner_audit_log 585..592 lo storico scritto dalle azioni OWNER Admin.
--
-- Sono dichiarate, non ricavate: il censimento live adesso ne da' gli id.
--
-- `owner_shared_documents` compare a ZERO, e non e' una dimenticanza: le
-- condivisioni non sono mai state create, perche' la POST rispondeva 422 su
-- `public_document_type`. E' lo stesso guasto per cui esiste la correzione
-- del payload; qui si limita a non lasciare residui.
--
-- Le altre 10 righe NON hanno id nel censimento, e non li invento. Si
-- RICAVANO dalle radici, con una condizione di appartenenza esplicita per
-- ciascuna tabella:
--
--   owner_property_access    owner_account_id in (8,9) E property_id in (44,45)
--   owner_access_tokens      owner_account_id in (8,9)
--   owner_sessions           owner_account_id in (8,9)
--   property_documents       property_id in (44,45)
--   owner_shared_documents   property_document_id fra i property_documents sopra
--   property_sale_sellers    sale_id in (14,15) E contact_id in (86,87)
--
-- Il totale viene poi CONFRONTATO con 71: se non torna, il file si ferma e
-- stampa la ripartizione per tabella, cosi' che la differenza si veda subito.
-- "71" non e' un'etichetta, e' un'asserzione.
--
-- La derivazione avviene PRIMA del BEGIN - una tabella temporanea popolata
-- dentro la transazione sparirebbe con il ROLLBACK - e viene RICONTROLLATA
-- dentro, dopo i lock: se nel frattempo e' comparsa o sparita una riga, la
-- transazione muore.
--
-- COSA NON VIENE TOCCATO
--
-- Gli audit senza riferimenti 560, 562, 563, 564, 566, 567. Entrambe le
-- colonne NULL significa origine non attribuita, non SET NULL gia' avvenuto:
-- non sono residui di questo run. Vengono verificati per ID prima e dopo.
--
-- Ogni guardia e' una RAISE EXCEPTION dentro un blocco DO, non una SELECT:
-- una SELECT che "dice" che il database e' sbagliato non ferma la
-- transazione che segue.
-- ===========================================================================

\set ON_ERROR_STOP on

-- LA MODALITA'. Cambiare in true SOLO quando l'esecuzione e' autorizzata.
\set ESEGUI false


-- ---------------------------------------------------------------------------
-- 1. GUARDIA: il database giusto, e lo script muore se non lo e'.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF current_database() <> 'stima360_db_test' THEN
        RAISE EXCEPTION
            'DATABASE SBAGLIATO: % (atteso stima360_db_test). Nessuna modifica.',
            current_database();
    END IF;
END
$$;


-- ---------------------------------------------------------------------------
-- 2. IL PERIMETRO DICHIARATO: 61 righe, una per una.
--
-- `ON COMMIT PRESERVE ROWS` e creazione FUORI dalla transazione: gli id
-- servono anche dopo il COMMIT o il ROLLBACK, per la verifica finale.
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE perimetro (
    tabella  regclass NOT NULL,
    id       bigint   NOT NULL,
    ricavata boolean  NOT NULL DEFAULT false,
    PRIMARY KEY (tabella, id)
) ON COMMIT PRESERVE ROWS;

INSERT INTO perimetro (tabella, id) VALUES
    ('public.contacts'::regclass, 86),
    ('public.contacts'::regclass, 87),
    -- I contatti creati DENTRO le agenzie dedicate, dalla fixture FOLLOWUP.
    -- Mancavano al primo dry-run: il censimento elencava solo 86/87.
    ('public.contacts'::regclass, 88),
    ('public.contacts'::regclass, 89),
    ('public.properties'::regclass, 44),
    ('public.properties'::regclass, 45),
    ('public.buy_requests'::regclass, 30),
    ('public.buy_requests'::regclass, 31),
    ('public.matches'::regclass, 28),
    ('public.matches'::regclass, 29),
    ('public.property_proposals'::regclass, 19),
    ('public.property_proposals'::regclass, 20),
    ('public.property_sales'::regclass, 14),
    ('public.property_sales'::regclass, 15),
    ('public.owner_accounts'::regclass, 8),
    ('public.owner_accounts'::regclass, 9),
    ('public.agencies'::regclass, 18),
    ('public.agencies'::regclass, 19),
    ('public.tasks'::regclass, 40),
    ('public.tasks'::regclass, 41),
    ('public.followup_actions'::regclass, 867),
    ('public.buy_request_history'::regclass, 121),
    ('public.buy_request_history'::regclass, 122),
    ('public.buy_request_history'::regclass, 123),
    ('public.buy_request_history'::regclass, 124),
    ('public.buy_request_history'::regclass, 125),
    ('public.buy_request_history'::regclass, 126),
    ('public.buy_request_history'::regclass, 127),
    ('public.buy_request_history'::regclass, 128),
    ('public.match_runs'::regclass, 29),
    ('public.match_runs'::regclass, 30),
    ('public.match_requirement_results'::regclass, 201),
    ('public.match_requirement_results'::regclass, 202),
    ('public.match_requirement_results'::regclass, 203),
    ('public.match_requirement_results'::regclass, 204),
    ('public.match_requirement_results'::regclass, 205),
    ('public.match_requirement_results'::regclass, 206),
    ('public.match_requirement_results'::regclass, 207),
    ('public.match_requirement_results'::regclass, 208),
    ('public.match_requirement_results'::regclass, 209),
    ('public.match_requirement_results'::regclass, 210),
    ('public.match_requirement_results'::regclass, 211),
    ('public.match_requirement_results'::regclass, 212),
    ('public.match_requirement_results'::regclass, 213),
    ('public.match_requirement_results'::regclass, 214),
    ('public.match_requirement_results'::regclass, 215),
    ('public.match_requirement_results'::regclass, 216),
    ('public.property_contacts'::regclass, 24),
    ('public.property_contacts'::regclass, 25),
    ('public.property_status_history'::regclass, 87),
    ('public.property_status_history'::regclass, 88),
    -- La risorsa del dominio SELLER_INTENT: marcatore in `event_type`.
    ('public.seller_timeline_events'::regclass, 106),
    ('public.seller_timeline_events'::regclass, 107),
    -- Lo storico scritto dalle azioni OWNER Admin. NON sono i sei audit
    -- senza riferimenti: quelli hanno entrambe le colonne NULL e restano.
    ('public.owner_audit_log'::regclass, 585),
    ('public.owner_audit_log'::regclass, 586),
    ('public.owner_audit_log'::regclass, 587),
    ('public.owner_audit_log'::regclass, 588),
    ('public.owner_audit_log'::regclass, 589),
    ('public.owner_audit_log'::regclass, 590),
    ('public.owner_audit_log'::regclass, 591),
    ('public.owner_audit_log'::regclass, 592);


-- ---------------------------------------------------------------------------
-- 3. LE 22 RIGHE RICAVATE, ciascuna con la sua condizione di appartenenza.
--
-- Non "tutte le righe che pendono da una nostra radice": ogni riferimento
-- deve cadere nel perimetro. `owner_property_access` deve legare un conto
-- NOSTRO a un immobile NOSTRO; `property_sale_sellers` una vendita nostra a
-- un contatto nostro. Una riga che ne soddisfi solo meta' non e' nostra e
-- non viene raccolta - e, non essendo nel perimetro, fara' fermare il
-- controllo delle dipendenze al punto 7.
-- ---------------------------------------------------------------------------
INSERT INTO perimetro (tabella, id, ricavata)
SELECT 'public.owner_property_access'::regclass, x.id, true
  FROM owner_property_access x
 WHERE x.owner_account_id IN (8, 9) AND x.property_id IN (44, 45);

INSERT INTO perimetro (tabella, id, ricavata)
SELECT 'public.owner_access_tokens'::regclass, t.id, true
  FROM owner_access_tokens t WHERE t.owner_account_id IN (8, 9);

INSERT INTO perimetro (tabella, id, ricavata)
SELECT 'public.owner_sessions'::regclass, s.id, true
  FROM owner_sessions s WHERE s.owner_account_id IN (8, 9);

INSERT INTO perimetro (tabella, id, ricavata)
SELECT 'public.property_documents'::regclass, d.id, true
  FROM property_documents d WHERE d.property_id IN (44, 45);

INSERT INTO perimetro (tabella, id, ricavata)
SELECT 'public.owner_shared_documents'::regclass, sd.id, true
  FROM owner_shared_documents sd
 WHERE sd.property_document_id IN (
           SELECT id FROM perimetro WHERE tabella = 'public.property_documents'::regclass);

INSERT INTO perimetro (tabella, id, ricavata)
SELECT 'public.property_sale_sellers'::regclass, ss.id, true
  FROM property_sale_sellers ss
 WHERE ss.sale_id IN (14, 15) AND ss.contact_id IN (86, 87);


-- ---------------------------------------------------------------------------
-- 4. IL TOTALE: 71, o ci si ferma con la ripartizione sotto gli occhi.
-- ---------------------------------------------------------------------------
\echo '--- ripartizione del perimetro ---'
SELECT tabella::text AS tabella,
       count(*) FILTER (WHERE NOT ricavata) AS dichiarate,
       count(*) FILTER (WHERE ricavata)     AS ricavate,
       count(*)                             AS totale
  FROM perimetro GROUP BY tabella ORDER BY 1;

DO $$
DECLARE
    n_totale    bigint;
    n_dichiarate bigint;
BEGIN
    SELECT count(*), count(*) FILTER (WHERE NOT ricavata)
      INTO n_totale, n_dichiarate FROM perimetro;
    IF n_dichiarate <> 61 THEN
        RAISE EXCEPTION 'righe dichiarate: % invece di 61', n_dichiarate;
    END IF;
    IF n_totale <> 71 THEN
        RAISE EXCEPTION
            'PERIMETRO DI % RIGHE INVECE DI 71 (% dichiarate, % ricavate). '
            'La ripartizione per tabella e'' stampata qui sopra: la differenza '
            'va capita prima di cancellare qualsiasi cosa.',
            n_totale, n_dichiarate, n_totale - n_dichiarate;
    END IF;
    RAISE NOTICE 'perimetro: 71 righe (61 dichiarate, 10 ricavate)';
END
$$;


-- ---------------------------------------------------------------------------
-- 5. I SEI AUDIT DA NON TOCCARE, per ID.
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE audit_intatti ON COMMIT PRESERVE ROWS AS
SELECT id, owner_account_id, property_id
  FROM owner_audit_log
 WHERE id IN (560, 562, 563, 564, 566, 567);

CREATE TEMP TABLE modalita ON COMMIT PRESERVE ROWS AS
SELECT :ESEGUI::boolean AS esegui;

DO $$
DECLARE
    n bigint;
    e boolean;
BEGIN
    SELECT count(*) INTO n FROM audit_intatti;
    IF n <> 6 THEN
        RAISE EXCEPTION 'audit da conservare: trovati % dei 6 attesi '
                        '(560, 562, 563, 564, 566, 567)', n;
    END IF;
    SELECT count(*) INTO n FROM audit_intatti
     WHERE owner_account_id IS NOT NULL OR property_id IS NOT NULL;
    IF n <> 0 THEN
        RAISE EXCEPTION '% dei sei audit hanno un riferimento non nullo: non '
                        'sono quelli attesi', n;
    END IF;

    -- E NON SONO NEL PERIMETRO. Adesso che `owner_audit_log` compare fra le
    -- tabelle da cancellare, questa e' la guardia che tiene separate le due
    -- cose: gli otto del run (585..592) e i sei da conservare.
    SELECT count(*) INTO n FROM perimetro
     WHERE tabella = 'public.owner_audit_log'::regclass
       AND id IN (SELECT id FROM audit_intatti);
    IF n <> 0 THEN
        RAISE EXCEPTION
            'IL PERIMETRO CONTIENE % dei sei audit da conservare: non si '
            'cancella niente.', n;
    END IF;
    RAISE NOTICE 'sei audit senza riferimenti registrati e fuori dal '
                 'perimetro: non verranno toccati';

    SELECT esegui INTO e FROM modalita;
    IF e THEN
        RAISE NOTICE 'MODALITA ESECUZIONE: si chiudera'' con COMMIT';
    ELSE
        RAISE NOTICE 'MODALITA PROVA GENERALE: si chiudera'' con ROLLBACK, e la '
                     'verifica finale esigera'' le 71 righe ancora al loro posto';
    END IF;
END
$$;


BEGIN;

SET LOCAL statement_timeout = '120s';
SET LOCAL idle_in_transaction_session_timeout = '300s';


-- ---------------------------------------------------------------------------
-- 6. LOCK, PRIMA DI GUARDARE - e NOWAIT.
--
-- `FOR UPDATE NOWAIT` non aspetta: se anche una sola riga e' bloccata da
-- qualcun altro, la transazione muore subito. E' la scelta giusta su un
-- perimetro che si sta per cancellare - aspettare significherebbe sperare
-- che l'altra transazione non stia facendo proprio quello che ci
-- preoccupa - ed e' anche il motivo per cui qui non serve `lock_timeout`.
--
-- I lock chiudono anche la finestra fra il controllo delle dipendenze e la
-- DELETE: per inserire una figlia con una FK, PostgreSQL prende un
-- `FOR KEY SHARE` sulla riga genitore, e `FOR KEY SHARE` confligge con
-- `FOR UPDATE`. Finche' questa transazione tiene il lock, un INSERT
-- concorrente che referenzi queste righe non riesce a infilarsi.
--
-- Ordine genitori -> figlie, lo stesso che segue l'applicazione quando
-- scrive: prenderli al contrario sarebbe un invito al deadlock.
-- ---------------------------------------------------------------------------
SELECT id FROM agencies                 WHERE id IN (18, 19) ORDER BY id FOR UPDATE NOWAIT;
SELECT id FROM contacts                 WHERE id IN (86, 87, 88, 89) ORDER BY id FOR UPDATE NOWAIT;
SELECT id FROM properties               WHERE id IN (44, 45) ORDER BY id FOR UPDATE NOWAIT;
SELECT id FROM buy_requests             WHERE id IN (30, 31) ORDER BY id FOR UPDATE NOWAIT;
SELECT id FROM tasks                    WHERE id IN (40, 41) ORDER BY id FOR UPDATE NOWAIT;
SELECT id FROM matches                  WHERE id IN (28, 29) ORDER BY id FOR UPDATE NOWAIT;
SELECT id FROM property_proposals       WHERE id IN (19, 20) ORDER BY id FOR UPDATE NOWAIT;
SELECT id FROM property_sales           WHERE id IN (14, 15) ORDER BY id FOR UPDATE NOWAIT;
SELECT id FROM owner_accounts           WHERE id IN (8, 9)   ORDER BY id FOR UPDATE NOWAIT;
SELECT id FROM followup_actions         WHERE id = 867       FOR UPDATE NOWAIT;
SELECT id FROM match_runs               WHERE id IN (29, 30) ORDER BY id FOR UPDATE NOWAIT;
SELECT id FROM match_requirement_results WHERE id BETWEEN 201 AND 216 ORDER BY id FOR UPDATE NOWAIT;
SELECT id FROM buy_request_history      WHERE id BETWEEN 121 AND 128 ORDER BY id FOR UPDATE NOWAIT;
SELECT id FROM property_contacts        WHERE id IN (24, 25) ORDER BY id FOR UPDATE NOWAIT;
SELECT id FROM property_status_history  WHERE id IN (87, 88) ORDER BY id FOR UPDATE NOWAIT;
SELECT id FROM seller_timeline_events   WHERE id IN (106, 107) ORDER BY id FOR UPDATE NOWAIT;
SELECT id FROM owner_audit_log          WHERE id BETWEEN 585 AND 592 ORDER BY id FOR UPDATE NOWAIT;

SELECT d.id FROM property_documents d
 WHERE d.id IN (SELECT id FROM perimetro WHERE tabella = 'public.property_documents'::regclass)
 ORDER BY d.id FOR UPDATE NOWAIT;
SELECT sd.id FROM owner_shared_documents sd
 WHERE sd.id IN (SELECT id FROM perimetro WHERE tabella = 'public.owner_shared_documents'::regclass)
 ORDER BY sd.id FOR UPDATE NOWAIT;
SELECT x.id FROM owner_property_access x
 WHERE x.id IN (SELECT id FROM perimetro WHERE tabella = 'public.owner_property_access'::regclass)
 ORDER BY x.id FOR UPDATE NOWAIT;
SELECT t.id FROM owner_access_tokens t
 WHERE t.id IN (SELECT id FROM perimetro WHERE tabella = 'public.owner_access_tokens'::regclass)
 ORDER BY t.id FOR UPDATE NOWAIT;
SELECT s.id FROM owner_sessions s
 WHERE s.id IN (SELECT id FROM perimetro WHERE tabella = 'public.owner_sessions'::regclass)
 ORDER BY s.id FOR UPDATE NOWAIT;
SELECT ss.id FROM property_sale_sellers ss
 WHERE ss.id IN (SELECT id FROM perimetro WHERE tabella = 'public.property_sale_sellers'::regclass)
 ORDER BY ss.id FOR UPDATE NOWAIT;


-- ---------------------------------------------------------------------------
-- 7. LE RIGHE RICAVATE SONO ANCORA QUELLE. La derivazione e' avvenuta prima
--    del BEGIN, cioe' prima dei lock: fra quel momento e questo poteva
--    comparirne una nuova, o sparirne una. Si rifa' la stessa domanda e si
--    esige la stessa risposta.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    scarti text[] := '{}';
    n bigint;
BEGIN
    SELECT count(*) INTO n FROM (
        SELECT id FROM owner_property_access
         WHERE owner_account_id IN (8, 9) AND property_id IN (44, 45)
        EXCEPT SELECT id FROM perimetro WHERE tabella = 'public.owner_property_access'::regclass
    ) d;
    IF n > 0 THEN scarti := scarti || format('owner_property_access: %s nuove', n); END IF;

    SELECT count(*) INTO n FROM (
        SELECT id FROM owner_access_tokens WHERE owner_account_id IN (8, 9)
        EXCEPT SELECT id FROM perimetro WHERE tabella = 'public.owner_access_tokens'::regclass
    ) d;
    IF n > 0 THEN scarti := scarti || format('owner_access_tokens: %s nuove', n); END IF;

    SELECT count(*) INTO n FROM (
        SELECT id FROM owner_sessions WHERE owner_account_id IN (8, 9)
        EXCEPT SELECT id FROM perimetro WHERE tabella = 'public.owner_sessions'::regclass
    ) d;
    IF n > 0 THEN scarti := scarti || format('owner_sessions: %s nuove', n); END IF;

    SELECT count(*) INTO n FROM (
        SELECT id FROM property_documents WHERE property_id IN (44, 45)
        EXCEPT SELECT id FROM perimetro WHERE tabella = 'public.property_documents'::regclass
    ) d;
    IF n > 0 THEN scarti := scarti || format('property_documents: %s nuove', n); END IF;

    SELECT count(*) INTO n FROM (
        SELECT sd.id FROM owner_shared_documents sd
         WHERE sd.property_document_id IN (
             SELECT id FROM perimetro WHERE tabella = 'public.property_documents'::regclass)
        EXCEPT SELECT id FROM perimetro WHERE tabella = 'public.owner_shared_documents'::regclass
    ) d;
    IF n > 0 THEN scarti := scarti || format('owner_shared_documents: %s nuove', n); END IF;

    SELECT count(*) INTO n FROM (
        SELECT id FROM property_sale_sellers
         WHERE sale_id IN (14, 15) AND contact_id IN (86, 87)
        EXCEPT SELECT id FROM perimetro WHERE tabella = 'public.property_sale_sellers'::regclass
    ) d;
    IF n > 0 THEN scarti := scarti || format('property_sale_sellers: %s nuove', n); END IF;

    IF array_length(scarti, 1) > 0 THEN
        RAISE EXCEPTION
            'IL PERIMETRO E'' CAMBIATO fra la derivazione e i lock: %. '
            'Nessuna cancellazione: il censimento va rifatto.',
            array_to_string(scarti, '; ');
    END IF;
    RAISE NOTICE 'righe ricavate confermate dopo i lock';
END
$$;


-- ---------------------------------------------------------------------------
-- 8. LE CORRISPONDENZE ESATTE.
--
-- Non "esistono 71 righe con questi id": ogni riga deve essere QUELLA riga.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    r       record;
    n       bigint;
    mancanti text[] := '{}';
BEGIN
    -- 8a. Ogni riga del perimetro esiste davvero.
    FOR r IN SELECT DISTINCT tabella FROM perimetro ORDER BY 1 LOOP
        EXECUTE format(
            'SELECT count(*) FROM %s WHERE id IN '
            '(SELECT id FROM perimetro WHERE tabella = %L::regclass)',
            r.tabella, r.tabella) INTO n;
        IF n <> (SELECT count(*) FROM perimetro WHERE tabella = r.tabella) THEN
            mancanti := mancanti || format('%s: %s presenti su %s attese',
                r.tabella, n, (SELECT count(*) FROM perimetro WHERE tabella = r.tabella));
        END IF;
    END LOOP;
    IF array_length(mancanti, 1) > 0 THEN
        RAISE EXCEPTION 'righe del censimento non trovate: %',
            array_to_string(mancanti, '; ');
    END IF;

    -- 8b. Il marcatore, dove esiste una colonna che lo porta.
    SELECT count(*) INTO n FROM contacts
     WHERE id IN (86, 87, 88, 89) AND display_name LIKE 'P26-6-52f6d97b5214-%';
    IF n <> 4 THEN RAISE EXCEPTION 'contacts: % con il marcatore, attese 4', n; END IF;
    -- 88 e 89 stanno DENTRO le agenzie dedicate: e' cio' che li distingue da
    -- 86/87, che vivono nelle agenzie condivise e non vanno confusi con loro.
    SELECT count(*) INTO n FROM contacts
     WHERE id IN (88, 89) AND agency_id IN (18, 19);
    IF n <> 2 THEN
        RAISE EXCEPTION 'contacts 88/89: % nelle agenzie 18/19, attese 2', n;
    END IF;
    SELECT count(*) INTO n FROM contacts
     WHERE id IN (86, 87) AND agency_id IN (18, 19);
    IF n <> 0 THEN
        RAISE EXCEPTION 'contacts 86/87: % risultano nelle agenzie dedicate, '
                        'attese 0', n;
    END IF;

    SELECT count(*) INTO n FROM seller_timeline_events
     WHERE id IN (106, 107) AND event_type LIKE 'P26-6-52f6d97b5214-%';
    IF n <> 2 THEN
        RAISE EXCEPTION 'seller_timeline_events: % con il marcatore, attese 2', n;
    END IF;

    -- Gli otto audit del run: ogni riferimento non nullo dentro il perimetro,
    -- e almeno uno che ci punti. E' la condizione che li separa dai sei da
    -- conservare, che hanno entrambe le colonne NULL.
    SELECT count(*) INTO n FROM owner_audit_log
     WHERE id BETWEEN 585 AND 592
       AND (owner_account_id IS NOT NULL OR property_id IS NOT NULL)
       AND (owner_account_id IS NULL OR owner_account_id IN (8, 9))
       AND (property_id IS NULL OR property_id IN (44, 45));
    IF n <> 8 THEN
        RAISE EXCEPTION
            'owner_audit_log 585..592: % righe appartengono al run, attese 8. '
            'Le altre hanno un riferimento fuori perimetro, o non ne hanno '
            'nessuno - e in quel caso non sono residui di questo run.', n;
    END IF;
    SELECT count(*) INTO n FROM properties
     WHERE id IN (44, 45) AND title LIKE 'P26-6-52f6d97b5214-%';
    IF n <> 2 THEN RAISE EXCEPTION 'properties: % con il marcatore, attese 2', n; END IF;
    SELECT count(*) INTO n FROM buy_requests
     WHERE id IN (30, 31) AND title LIKE 'P26-6-52f6d97b5214-%';
    IF n <> 2 THEN RAISE EXCEPTION 'buy_requests: % con il marcatore, attese 2', n; END IF;

    -- 8c. Le catene, esatte.
    SELECT count(*) INTO n FROM property_contacts
     WHERE id IN (24, 25) AND property_id IN (44, 45) AND contact_id IN (86, 87);
    IF n <> 2 THEN RAISE EXCEPTION 'property_contacts: % coerenti, attese 2', n; END IF;

    SELECT count(*) INTO n FROM property_status_history
     WHERE id IN (87, 88) AND property_id IN (44, 45);
    IF n <> 2 THEN RAISE EXCEPTION 'property_status_history: % coerenti, attese 2', n; END IF;

    SELECT count(*) INTO n FROM buy_request_history
     WHERE id BETWEEN 121 AND 128 AND buy_request_id IN (30, 31)
       AND (property_id IS NULL OR property_id IN (44, 45))
       AND (match_id IS NULL OR match_id IN (28, 29))
       AND (task_id IS NULL OR task_id IN (40, 41));
    IF n <> 8 THEN RAISE EXCEPTION 'buy_request_history: % coerenti, attese 8', n; END IF;

    SELECT count(*) INTO n FROM match_runs
     WHERE id IN (29, 30) AND buy_request_id IN (30, 31) AND property_id IN (44, 45);
    IF n <> 2 THEN RAISE EXCEPTION 'match_runs: % coerenti, attese 2', n; END IF;

    SELECT count(*) INTO n FROM match_requirement_results
     WHERE id BETWEEN 201 AND 216 AND match_run_id IN (29, 30);
    IF n <> 16 THEN RAISE EXCEPTION 'match_requirement_results: % coerenti, attese 16', n; END IF;

    SELECT count(*) INTO n FROM matches
     WHERE id IN (28, 29) AND buy_request_id IN (30, 31) AND property_id IN (44, 45);
    IF n <> 2 THEN RAISE EXCEPTION 'matches: % coerenti, attese 2', n; END IF;

    SELECT count(*) INTO n FROM property_proposals
     WHERE id IN (19, 20) AND match_id IN (28, 29);
    IF n <> 2 THEN RAISE EXCEPTION 'property_proposals: % coerenti, attese 2', n; END IF;

    SELECT count(*) INTO n FROM property_sales
     WHERE id IN (14, 15) AND proposal_id IN (19, 20)
       AND property_id IN (44, 45) AND buy_request_id IN (30, 31);
    IF n <> 2 THEN RAISE EXCEPTION 'property_sales: % coerenti, attese 2', n; END IF;

    SELECT count(*) INTO n FROM owner_accounts
     WHERE id IN (8, 9) AND contact_id IN (86, 87);
    IF n <> 2 THEN RAISE EXCEPTION 'owner_accounts: % coerenti, attese 2', n; END IF;

    SELECT count(*) INTO n FROM tasks WHERE id IN (40, 41) AND agency_id IN (18, 19);
    IF n <> 2 THEN RAISE EXCEPTION 'tasks: % nelle agenzie 18/19, attese 2', n; END IF;

    -- 8d. L'azione FOLLOWUP 867: quella diagnosticata, e nessun'altra.
    SELECT count(*) INTO n FROM followup_actions
     WHERE id = 867 AND agency_id = 18 AND status = 'failed' AND task_id IS NULL;
    IF n <> 1 THEN
        RAISE EXCEPTION 'followup_actions 867: non e'' la riga diagnosticata '
                        '(attesa agency_id 18, status failed, task_id NULL)';
    END IF;

    RAISE NOTICE 'corrispondenze verificate: 71 righe, ciascuna al suo posto';
END
$$;


-- ---------------------------------------------------------------------------
-- 9. DIPENDENZE FUORI PERIMETRO, DAL CATALOGO.
--
-- Si legge `pg_constraint`, cosi' che una FK aggiunta da una migration
-- futura compaia qui invece di farsi scoprire dalla transazione che cade.
--
-- OGNI azione conta, non solo RESTRICT:
--   RESTRICT / NO ACTION  la DELETE fallisce e la transazione muore;
--   CASCADE               la riga altrui viene CANCELLATA con la nostra;
--   SET NULL              la riga altrui sopravvive con un campo azzerato,
--                         e nessuno se ne accorge.
-- Le ultime due sono le pericolose: non fanno rumore.
--
-- `%L::regclass` e non `%L`: `perimetro.tabella` e' di tipo regclass, e un
-- letterale testuale verrebbe risolto con `oid = oid`, cioe' leggendo
-- 'contacts' come un numero - `invalid input syntax for type oid`.
--
-- Una figlia senza colonna `id` non puo' essere nel perimetro, quindi ogni
-- sua riga che ci referenzi e' per definizione estranea e viene contata tutta.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    r         record;
    n         bigint;
    ha_id     boolean;
    fuori     text[] := '{}';
    esaminate int := 0;
BEGIN
    FOR r IN
        SELECT con.conrelid::regclass  AS figlia,
               att.attname             AS colonna,
               con.confrelid::regclass AS genitore,
               CASE con.confdeltype WHEN 'a' THEN 'NO ACTION' WHEN 'r' THEN 'RESTRICT'
                                    WHEN 'c' THEN 'CASCADE'   WHEN 'n' THEN 'SET NULL'
                                    WHEN 'd' THEN 'SET DEFAULT' END AS azione
          FROM pg_constraint con
          CROSS JOIN LATERAL unnest(con.conkey, con.confkey)
               AS k(attnum_figlia, attnum_genitore)
          JOIN pg_attribute att  ON att.attrelid  = con.conrelid
                                AND att.attnum    = k.attnum_figlia
          JOIN pg_attribute patt ON patt.attrelid = con.confrelid
                                AND patt.attnum   = k.attnum_genitore
         WHERE con.contype = 'f'
           AND patt.attname = 'id'
           AND con.confrelid IN (SELECT DISTINCT tabella FROM perimetro)
         ORDER BY 3, 1, 2
    LOOP
        esaminate := esaminate + 1;
        SELECT EXISTS (
            SELECT 1 FROM pg_attribute
             WHERE attrelid = r.figlia AND attname = 'id'
               AND attnum > 0 AND NOT attisdropped) INTO ha_id;

        IF ha_id THEN
            EXECUTE format(
                'SELECT count(*) FROM %s f '
                ' WHERE f.%I IN (SELECT id FROM perimetro WHERE tabella = %L::regclass) '
                '   AND NOT EXISTS (SELECT 1 FROM perimetro p '
                '                    WHERE p.tabella = %L::regclass AND p.id = f.id)',
                r.figlia, r.colonna, r.genitore, r.figlia) INTO n;
        ELSE
            EXECUTE format(
                'SELECT count(*) FROM %s f '
                ' WHERE f.%I IN (SELECT id FROM perimetro WHERE tabella = %L::regclass)',
                r.figlia, r.colonna, r.genitore) INTO n;
        END IF;

        IF n > 0 THEN
            fuori := fuori || format('%s.%s -> %s: %s righe (ON DELETE %s)',
                                     r.figlia, r.colonna, r.genitore, n, r.azione);
        END IF;
    END LOOP;

    RAISE NOTICE 'chiavi esterne entranti esaminate: %', esaminate;

    IF array_length(fuori, 1) > 0 THEN
        RAISE EXCEPTION
            'DIPENDENZE FUORI PERIMETRO: %. Nessuna cancellazione e nessuna '
            'modifica: vanno esaminate prima. Il catalogo serve a RILEVARE una '
            'dipendenza imprevista, non ad autorizzarne la rimozione.',
            array_to_string(fuori, '; ');
    END IF;
    RAISE NOTICE 'nessuna riga fuori perimetro referenzia le 71 righe';
END
$$;


-- ---------------------------------------------------------------------------
-- 10. LE CANCELLAZIONI, IN ORDINE DI CHIAVE ESTERNA.
--
-- Figlie -> genitori, e l'ordine e' obbligato dai vincoli:
--   match_requirement_results -> match_runs                    CASCADE
--   owner_shared_documents    -> property_documents            RESTRICT
--   property_sale_sellers     -> property_sales CASCADE, contacts RESTRICT
--   property_sales -> property_proposals / buy_requests / properties  RESTRICT
--   property_proposals        -> matches                       RESTRICT
--   owner_accounts            -> contacts                      RESTRICT
--   buy_requests              -> contacts                      RESTRICT
--   properties/buy_requests/tasks/contacts -> agencies         RESTRICT
-- Per questo `agencies` e' l'ultima e `contacts` la penultima.
--
-- Ogni DELETE si limita agli id del perimetro: non c'e' una sola condizione
-- per prefisso, per data o per intervallo.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    r        record;
    n        bigint;
    atteso   bigint;
    ordine   text[] := ARRAY[
        'public.match_requirement_results',
        'public.match_runs',
        'public.buy_request_history',
        -- `owner_audit_log` PRIMA di owner_accounts e properties: le sue due
        -- colonne sono ON DELETE SET NULL, quindi cancellare i genitori per
        -- primi le azzererebbe - e la riga sopravviverebbe senza piu' un
        -- riferimento che dica di chi era. Si cancella finche' il legame c'e'.
        'public.owner_audit_log',
        'public.owner_shared_documents',
        'public.property_documents',
        'public.owner_property_access',
        'public.owner_access_tokens',
        'public.owner_sessions',
        'public.property_sale_sellers',
        'public.property_sales',
        'public.property_proposals',
        'public.matches',
        'public.owner_accounts',
        'public.property_status_history',
        'public.property_contacts',
        -- `seller_timeline_events` referenzia contatti, immobili e stime in
        -- SET NULL, e l'agenzia in RESTRICT: va prima di tutti e quattro.
        'public.seller_timeline_events',
        'public.followup_actions',
        'public.tasks',
        'public.buy_requests',
        'public.properties',
        'public.contacts',
        'public.agencies'];
    -- `nome_tabella`, NON `tabella`: la tabella temporanea ha una colonna con
    -- quel nome, e una variabile PL/pgSQL omonima rende ambiguo ogni
    -- riferimento nudo - `column reference "tabella" is ambiguous`, che e'
    -- esattamente dove si e' fermato il dry-run. Il nome diverso toglie
    -- l'ambiguita' alla radice; l'alias esplicito qui sotto la toglie anche a
    -- chi leggera' senza questo contesto.
    nome_tabella text;
BEGIN
    -- Ogni tabella del perimetro compare nell'ordine, e viceversa: una
    -- dimenticata resterebbe sul TEST senza che nessuno lo noti.
    SELECT count(*) INTO n FROM (
        SELECT p.tabella::text FROM perimetro AS p
        EXCEPT SELECT unnest(ordine)) d;
    IF n > 0 THEN
        RAISE EXCEPTION 'tabelle nel perimetro ma non nell''ordine di '
                        'cancellazione: %', n;
    END IF;

    FOREACH nome_tabella IN ARRAY ordine LOOP
        SELECT count(*) INTO atteso FROM perimetro AS p
         WHERE p.tabella = nome_tabella::regclass;
        IF atteso = 0 THEN
            CONTINUE;
        END IF;
        EXECUTE format(
            'DELETE FROM %s WHERE id IN '
            '(SELECT id FROM perimetro WHERE tabella = %L::regclass)',
            nome_tabella, nome_tabella);
        GET DIAGNOSTICS n = ROW_COUNT;
        IF n <> atteso THEN
            RAISE EXCEPTION '%: cancellate % righe su % del perimetro',
                nome_tabella, n, atteso;
        END IF;
        RAISE NOTICE '  % : % righe', nome_tabella, n;
    END LOOP;
    SELECT count(*) INTO n FROM perimetro;
    RAISE NOTICE 'cancellate % righe, nell''ordine delle chiavi esterne', n;
END
$$;


-- ---------------------------------------------------------------------------
-- 11. ZERO RESIDUI, PRIMA DELLA CHIUSURA.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    r       record;
    n       bigint;
    residui text[] := '{}';
BEGIN
    FOR r IN SELECT DISTINCT tabella FROM perimetro ORDER BY 1 LOOP
        EXECUTE format(
            'SELECT count(*) FROM %s WHERE id IN '
            '(SELECT id FROM perimetro WHERE tabella = %L::regclass)',
            r.tabella, r.tabella) INTO n;
        IF n > 0 THEN
            residui := residui || format('%s: %s righe', r.tabella, n);
        END IF;
    END LOOP;
    IF array_length(residui, 1) > 0 THEN
        RAISE EXCEPTION 'RESIDUI PRIMA DELLA CHIUSURA: %. Transazione annullata.',
            array_to_string(residui, '; ');
    END IF;

    SELECT count(*) INTO n FROM owner_audit_log
     WHERE id IN (SELECT id FROM audit_intatti);
    IF n <> 6 THEN
        RAISE EXCEPTION 'i sei audit da conservare sono diventati %: questo '
                        'cleanup non doveva toccarli', n;
    END IF;
    RAISE NOTICE 'verifica pre-chiusura: 0 righe del run, 6 audit intatti';
END
$$;


-- ---------------------------------------------------------------------------
-- LA CHIUSURA, SECONDO LA MODALITA' SCELTA IN CIMA.
-- ---------------------------------------------------------------------------
\if :ESEGUI
COMMIT;
\else
ROLLBACK;
\endif


-- ---------------------------------------------------------------------------
-- 12. LA VERIFICA FINALE, DIVERSA PER LE DUE MODALITA'.
--
--   ESECUZIONE       zero righe del run.
--   PROVA GENERALE   TUTTE E 71 al loro posto, tabella per tabella. Una in
--                    meno significa che il ROLLBACK non ha riportato
--                    indietro qualcosa, ed e' un fatto da sapere PRIMA di
--                    autorizzare l'esecuzione.
--
-- Una sola verifica "zero residui" per entrambe fallirebbe in prova per la
-- ragione giusta dicendo la cosa sbagliata: che il cleanup non funziona.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    r       record;
    n       bigint;
    atteso  bigint;
    totale  bigint := 0;
    esegui  boolean;
    scarti  text[] := '{}';
    n_audit bigint;
BEGIN
    SELECT modalita.esegui INTO esegui FROM modalita;

    FOR r IN SELECT DISTINCT tabella FROM perimetro ORDER BY 1 LOOP
        EXECUTE format(
            'SELECT count(*) FROM %s WHERE id IN '
            '(SELECT id FROM perimetro WHERE tabella = %L::regclass)',
            r.tabella, r.tabella) INTO n;
        totale := totale + n;
        SELECT count(*) INTO atteso FROM perimetro WHERE tabella = r.tabella;
        IF esegui AND n > 0 THEN
            scarti := scarti || format('%s: %s ancora presenti', r.tabella, n);
        ELSIF NOT esegui AND n <> atteso THEN
            scarti := scarti || format('%s: %s su %s attese', r.tabella, n, atteso);
        END IF;
    END LOOP;

    IF array_length(scarti, 1) > 0 THEN
        IF esegui THEN
            RAISE EXCEPTION 'RESIDUI DOPO IL COMMIT: %. Il cleanup non e'' completo.',
                array_to_string(scarti, '; ');
        ELSE
            RAISE EXCEPTION
                'IL ROLLBACK NON HA RIPORTATO TUTTO: %. Il database non e'' '
                'tornato com''era: da esaminare prima di autorizzare l''esecuzione.',
                array_to_string(scarti, '; ');
        END IF;
    END IF;

    SELECT count(*) INTO n_audit FROM owner_audit_log a
      JOIN audit_intatti i ON i.id = a.id
     WHERE a.owner_account_id IS NOT DISTINCT FROM i.owner_account_id
       AND a.property_id IS NOT DISTINCT FROM i.property_id;
    IF n_audit <> 6 THEN
        RAISE EXCEPTION
            'audit 560/562/563/564/566/567: % dei sei sono ancora identici a '
            'prima. Questo cleanup non doveva toccarli.', n_audit;
    END IF;

    IF esegui THEN
        RAISE NOTICE 'ESECUZIONE conclusa: 0 righe del run, 6 audit intatti. '
                     'Run 52f6d97b5214 rimosso.';
    ELSE
        RAISE NOTICE 'PROVA GENERALE conclusa: tutte le % righe sono tornate al '
                     'loro posto e i 6 audit sono intatti. Il database non e'' '
                     'cambiato; verifiche e DELETE hanno girato davvero.', totale;
    END IF;
END
$$;

DROP TABLE IF EXISTS perimetro;
DROP TABLE IF EXISTS audit_intatti;
DROP TABLE IF EXISTS modalita;
