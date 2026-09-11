-- ===========================================================================
-- CLEANUP DEL RUN P26-6  38e341f68f8a
--
-- NON ESEGUITO. Questo file e' per la review.
--
-- DUE MODALITA', E SI SCEGLIE IN CIMA
--
--   \set ESEGUI false   PROVA GENERALE (predefinita). Tutte le verifiche e
--                       tutte le DELETE girano, poi ROLLBACK. La verifica
--                       finale esige che OGNI riga del perimetro sia TORNATA
--                       al suo posto.
--   \set ESEGUI true    ESECUZIONE. COMMIT, e la verifica finale esige zero
--                       residui sugli ID originali.
--
-- Serve psql: \set e \if sono suoi.
--
-- IL PERIMETRO E' DERIVATO, NON DICHIARATO
--
-- Il run si e' fermato con il cleanup BLOCCATO, quindi il suo report non ha
-- stampato gli id di cio' che aveva creato: si conoscono solo le RADICI -
-- contatti 90/91, immobili 46/47, richieste 32/33, eventi 108/109, match
-- 30/31, proposte 21/22, vendite 16/17, conti 10/11, agenzie 20/21, attivita'
-- 42/43. Documenti, storico, risultati, legami, token, concessioni, stime e
-- azioni FOLLOWUP esistono senza che nessuno ne abbia scritto l'id.
--
-- Non li invento. Il punto 3 li RICAVA, ciascuno con una condizione di
-- appartenenza esplicita, e il punto 4 stampa la ripartizione: quella tabella
-- va CONFRONTATA CON IL CENSIMENTO prima di mettere ESEGUI true. Il file non
-- puo' verificare da solo un totale che nessuno gli ha dichiarato - dirlo e'
-- piu' onesto che inventare un numero e chiamarlo asserzione.
--
-- COSA NON VIENE TOCCATO
--
--   * gli audit con entrambi i riferimenti NULL: origine non attribuita, non
--     residui di questo run. Il punto 2 li registra per id e il punto 12 li
--     riconfronta uno per uno;
--   * identita' e sessioni operatore: il run le ha gia' rimosse (CLEAN-DB).
--     Il punto 5 lo verifica invece di darlo per buono, e se ne trova si
--     ferma: significherebbe che il report diceva il falso.
--
-- Ogni guardia e' una RAISE EXCEPTION dentro un blocco DO, non una SELECT.
-- ===========================================================================

\set ON_ERROR_STOP on

-- LA MODALITA'. Cambiare in true SOLO quando l'esecuzione e' autorizzata.
\set ESEGUI false


-- ---------------------------------------------------------------------------
-- 1. GUARDIA: il database giusto, e lo script muore se non lo e'.
--
-- Fuori dalla transazione, insieme al perimetro: le tabelle temporanee create
-- dentro una transazione spariscono con un ROLLBACK, e la prova generale non
-- arriverebbe alla verifica finale.
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
-- 2. LE RADICI, e gli audit da conservare.
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE perimetro (
    tabella  regclass NOT NULL,
    id       bigint   NOT NULL,
    ricavata boolean  NOT NULL DEFAULT false,
    PRIMARY KEY (tabella, id)
) ON COMMIT PRESERVE ROWS;

INSERT INTO perimetro (tabella, id) VALUES
    ('public.contacts'::regclass, 90),
    ('public.contacts'::regclass, 91),
    ('public.properties'::regclass, 46),
    ('public.properties'::regclass, 47),
    ('public.buy_requests'::regclass, 32),
    ('public.buy_requests'::regclass, 33),
    ('public.seller_timeline_events'::regclass, 108),
    ('public.seller_timeline_events'::regclass, 109),
    ('public.matches'::regclass, 30),
    ('public.matches'::regclass, 31),
    ('public.property_proposals'::regclass, 21),
    ('public.property_proposals'::regclass, 22),
    ('public.property_sales'::regclass, 16),
    ('public.property_sales'::regclass, 17),
    ('public.owner_accounts'::regclass, 10),
    ('public.owner_accounts'::regclass, 11),
    ('public.tasks'::regclass, 42),
    ('public.tasks'::regclass, 43),
    ('public.agencies'::regclass, 20),
    ('public.agencies'::regclass, 21);

-- Gli audit senza radice, PER ID, come stanno adesso. Non appartengono a
-- questo run: entrambe le colonne NULL significa origine non attribuita.
CREATE TEMP TABLE audit_intatti ON COMMIT PRESERVE ROWS AS
SELECT id, owner_account_id, property_id, entity_type
  FROM owner_audit_log
 WHERE owner_account_id IS NULL AND property_id IS NULL;

CREATE TEMP TABLE modalita ON COMMIT PRESERVE ROWS AS
SELECT :ESEGUI::boolean AS esegui;


-- ---------------------------------------------------------------------------
-- 3. LE RIGHE RICAVATE, ciascuna con la sua condizione di appartenenza.
--
-- Non "tutto cio' che pende da una nostra radice": ogni riferimento deve
-- cadere nel perimetro. `property_sale_sellers` deve legare una vendita
-- NOSTRA a un contatto NOSTRO; `owner_property_access` un conto nostro a un
-- immobile nostro. Una riga che ne soddisfi solo meta' non viene raccolta -
-- e, non essendo nel perimetro, fara' fermare il controllo del punto 8.
--
-- Le agenzie dedicate sono l'eccezione dichiarata: la' dentro OGNI riga e'
-- del run per costruzione, perche' l'agenzia l'ha creata il run.
-- ---------------------------------------------------------------------------
INSERT INTO perimetro (tabella, id, ricavata)
SELECT 'public.property_contacts'::regclass, pc.id, true
  FROM property_contacts pc
 WHERE pc.property_id IN (46, 47) AND pc.contact_id IN (90, 91);

INSERT INTO perimetro (tabella, id, ricavata)
SELECT 'public.property_status_history'::regclass, h.id, true
  FROM property_status_history h WHERE h.property_id IN (46, 47);

INSERT INTO perimetro (tabella, id, ricavata)
SELECT 'public.buy_request_history'::regclass, h.id, true
  FROM buy_request_history h
 WHERE h.buy_request_id IN (32, 33)
   AND (h.property_id IS NULL OR h.property_id IN (46, 47))
   AND (h.match_id IS NULL OR h.match_id IN (30, 31))
   AND (h.task_id IS NULL OR h.task_id IN (42, 43));

INSERT INTO perimetro (tabella, id, ricavata)
SELECT 'public.match_runs'::regclass, mr.id, true
  FROM match_runs mr
 WHERE mr.buy_request_id IN (32, 33) AND mr.property_id IN (46, 47);

INSERT INTO perimetro (tabella, id, ricavata)
SELECT 'public.match_requirement_results'::regclass, r.id, true
  FROM match_requirement_results r
 WHERE r.match_run_id IN (
     SELECT id FROM perimetro WHERE tabella = 'public.match_runs'::regclass);

INSERT INTO perimetro (tabella, id, ricavata)
SELECT 'public.property_documents'::regclass, d.id, true
  FROM property_documents d WHERE d.property_id IN (46, 47);

INSERT INTO perimetro (tabella, id, ricavata)
SELECT 'public.owner_shared_documents'::regclass, sd.id, true
  FROM owner_shared_documents sd
 WHERE sd.property_document_id IN (
     SELECT id FROM perimetro WHERE tabella = 'public.property_documents'::regclass);

INSERT INTO perimetro (tabella, id, ricavata)
SELECT 'public.property_sale_sellers'::regclass, ss.id, true
  FROM property_sale_sellers ss
 WHERE ss.sale_id IN (16, 17) AND ss.contact_id IN (90, 91);

INSERT INTO perimetro (tabella, id, ricavata)
SELECT 'public.owner_property_access'::regclass, x.id, true
  FROM owner_property_access x
 WHERE x.owner_account_id IN (10, 11) AND x.property_id IN (46, 47);

INSERT INTO perimetro (tabella, id, ricavata)
SELECT 'public.owner_access_tokens'::regclass, t.id, true
  FROM owner_access_tokens t WHERE t.owner_account_id IN (10, 11);

INSERT INTO perimetro (tabella, id, ricavata)
SELECT 'public.owner_sessions'::regclass, s.id, true
  FROM owner_sessions s WHERE s.owner_account_id IN (10, 11);

-- Gli audit del run: almeno un riferimento dentro il perimetro, e nessuno
-- fuori. I sei preesistenti hanno ENTRAMBE le colonne NULL e non passano
-- questa condizione: e' cosi' che restano fuori, non per una lista di id.
INSERT INTO perimetro (tabella, id, ricavata)
SELECT 'public.owner_audit_log'::regclass, a.id, true
  FROM owner_audit_log a
 WHERE (a.owner_account_id IS NOT NULL OR a.property_id IS NOT NULL)
   AND (a.owner_account_id IS NULL OR a.owner_account_id IN (10, 11))
   AND (a.property_id IS NULL OR a.property_id IN (46, 47));

-- Le agenzie dedicate: ogni riga e' del run per costruzione.
INSERT INTO perimetro (tabella, id, ricavata)
SELECT 'public.contacts'::regclass, c.id, true
  FROM contacts c WHERE c.agency_id IN (20, 21)
   AND NOT EXISTS (SELECT 1 FROM perimetro p
                    WHERE p.tabella = 'public.contacts'::regclass AND p.id = c.id);

INSERT INTO perimetro (tabella, id, ricavata)
SELECT 'public.followup_actions'::regclass, f.id, true
  FROM followup_actions f WHERE f.agency_id IN (20, 21);

INSERT INTO perimetro (tabella, id, ricavata)
SELECT 'public.stime'::regclass, s.id, true
  FROM stime s WHERE s.agency_id IN (20, 21);

INSERT INTO perimetro (tabella, id, ricavata)
SELECT 'public.seller_timeline_events'::regclass, e.id, true
  FROM seller_timeline_events e WHERE e.agency_id IN (20, 21)
   AND NOT EXISTS (SELECT 1 FROM perimetro p
                    WHERE p.tabella = 'public.seller_timeline_events'::regclass
                      AND p.id = e.id);

INSERT INTO perimetro (tabella, id, ricavata)
SELECT 'public.property_watches'::regclass, w.id, true
  FROM property_watches w WHERE w.agency_id IN (20, 21);

INSERT INTO perimetro (tabella, id, ricavata)
SELECT 'public.flow_events'::regclass, e.id, true
  FROM flow_events e WHERE e.agency_id IN (20, 21);

INSERT INTO perimetro (tabella, id, ricavata)
SELECT 'public.flow_executions'::regclass, x.id, true
  FROM flow_executions x WHERE x.agency_id IN (20, 21);

INSERT INTO perimetro (tabella, id, ricavata)
SELECT 'public.next_best_actions'::regclass, n.id, true
  FROM next_best_actions n WHERE n.agency_id IN (20, 21);

INSERT INTO perimetro (tabella, id, ricavata)
SELECT 'public.agency_memberships'::regclass, m.id, true
  FROM agency_memberships m WHERE m.agency_id IN (20, 21);

INSERT INTO perimetro (tabella, id, ricavata)
SELECT 'public.tasks'::regclass, t.id, true
  FROM tasks t WHERE t.agency_id IN (20, 21)
   AND NOT EXISTS (SELECT 1 FROM perimetro p
                    WHERE p.tabella = 'public.tasks'::regclass AND p.id = t.id);


-- ---------------------------------------------------------------------------
-- 4. LA RIPARTIZIONE: da confrontare con il censimento PRIMA di eseguire.
-- ---------------------------------------------------------------------------
\echo '--- perimetro: confrontare con il censimento prima di ESEGUI true ---'
SELECT p.tabella::regclass::text AS tabella,
       count(*) FILTER (WHERE NOT p.ricavata) AS radici,
       count(*) FILTER (WHERE p.ricavata)     AS ricavate,
       count(*)                               AS totale,
       array_agg(p.id ORDER BY p.id)          AS ids
  FROM perimetro p GROUP BY 1 ORDER BY 1;

DO $$
DECLARE
    n_totale bigint;
    n_radici bigint;
BEGIN
    SELECT count(*), count(*) FILTER (WHERE NOT ricavata)
      INTO n_totale, n_radici FROM perimetro;
    IF n_radici <> 20 THEN
        RAISE EXCEPTION 'radici dichiarate: % invece di 20', n_radici;
    END IF;
    IF n_totale = n_radici THEN
        RAISE EXCEPTION
            'nessuna riga ricavata: le dipendenze del run non sono state '
            'trovate. O il cleanup e'' gia'' avvenuto, o le radici non sono '
            'quelle giuste: in entrambi i casi non si cancella niente.';
    END IF;
    RAISE NOTICE 'perimetro: % righe (% radici, % ricavate). CONFRONTARE con '
                 'il censimento.', n_totale, n_radici, n_totale - n_radici;
END
$$;


-- ---------------------------------------------------------------------------
-- 5. IDENTITA' E SESSIONI: il run le dichiara rimosse. Si verifica.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    n bigint;
BEGIN
    SELECT count(*) INTO n FROM operator_users WHERE email LIKE '%38e341f68f8a%';
    IF n > 0 THEN
        RAISE EXCEPTION
            '% identita'' del run sono ancora presenti, mentre il report '
            'dichiarava CLEAN-DB riuscito. Il disaccordo va capito prima di '
            'cancellare qualsiasi cosa.', n;
    END IF;
    SELECT count(*) INTO n FROM audit_intatti;
    RAISE NOTICE 'identita'' gia'' rimosse; % audit senza radice da conservare', n;
END
$$;


BEGIN;

SET LOCAL statement_timeout = '120s';
SET LOCAL idle_in_transaction_session_timeout = '300s';


-- ---------------------------------------------------------------------------
-- 6. LOCK, PRIMA DI GUARDARE - e NOWAIT.
--
-- `FOR UPDATE NOWAIT` non aspetta: se anche una sola riga e' bloccata da
-- qualcun altro, la transazione muore subito. Su un perimetro che si sta per
-- cancellare, aspettare significherebbe sperare che l'altra transazione non
-- stia facendo proprio cio' che ci preoccupa.
--
-- I lock chiudono anche la finestra fra il controllo delle dipendenze e la
-- DELETE: per inserire una figlia con una FK, PostgreSQL prende un
-- `FOR KEY SHARE` sul genitore, e quel lock confligge con `FOR UPDATE`.
--
-- Si bloccano TUTTE le righe del perimetro, non solo le radici: le ricavate
-- sono altrettanto cancellabili e altrettanto modificabili da altri.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    r record;
BEGIN
    FOR r IN SELECT DISTINCT p.tabella FROM perimetro p ORDER BY 1 LOOP
        EXECUTE format(
            'SELECT id FROM %s WHERE id IN '
            '(SELECT id FROM perimetro WHERE tabella = %L::regclass) '
            'ORDER BY id FOR UPDATE NOWAIT',
            r.tabella, r.tabella);
    END LOOP;
    RAISE NOTICE 'lock presi su tutte le tabelle del perimetro';
END
$$;


-- ---------------------------------------------------------------------------
-- 7. LE RIGHE RICAVATE SONO ANCORA QUELLE.
--
-- La derivazione e' avvenuta prima del BEGIN, cioe' prima dei lock: fra quel
-- momento e questo poteva comparirne una nuova. Si rifa' la stessa domanda su
-- ogni condizione e si esige la stessa risposta.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    scarti text[] := '{}';
    n bigint;
BEGIN
    SELECT count(*) INTO n FROM (
        SELECT id FROM property_contacts
         WHERE property_id IN (46, 47) AND contact_id IN (90, 91)
        EXCEPT SELECT id FROM perimetro
         WHERE tabella = 'public.property_contacts'::regclass) d;
    IF n > 0 THEN scarti := scarti || format('property_contacts: %s nuove', n); END IF;

    SELECT count(*) INTO n FROM (
        SELECT id FROM property_documents WHERE property_id IN (46, 47)
        EXCEPT SELECT id FROM perimetro
         WHERE tabella = 'public.property_documents'::regclass) d;
    IF n > 0 THEN scarti := scarti || format('property_documents: %s nuove', n); END IF;

    SELECT count(*) INTO n FROM (
        SELECT id FROM owner_access_tokens WHERE owner_account_id IN (10, 11)
        EXCEPT SELECT id FROM perimetro
         WHERE tabella = 'public.owner_access_tokens'::regclass) d;
    IF n > 0 THEN scarti := scarti || format('owner_access_tokens: %s nuove', n); END IF;

    SELECT count(*) INTO n FROM (
        SELECT id FROM owner_sessions WHERE owner_account_id IN (10, 11)
        EXCEPT SELECT id FROM perimetro
         WHERE tabella = 'public.owner_sessions'::regclass) d;
    IF n > 0 THEN scarti := scarti || format('owner_sessions: %s nuove', n); END IF;

    SELECT count(*) INTO n FROM (
        SELECT id FROM followup_actions WHERE agency_id IN (20, 21)
        EXCEPT SELECT id FROM perimetro
         WHERE tabella = 'public.followup_actions'::regclass) d;
    IF n > 0 THEN scarti := scarti || format('followup_actions: %s nuove', n); END IF;

    SELECT count(*) INTO n FROM (
        SELECT id FROM owner_audit_log
         WHERE (owner_account_id IS NOT NULL OR property_id IS NOT NULL)
           AND (owner_account_id IS NULL OR owner_account_id IN (10, 11))
           AND (property_id IS NULL OR property_id IN (46, 47))
        EXCEPT SELECT id FROM perimetro
         WHERE tabella = 'public.owner_audit_log'::regclass) d;
    IF n > 0 THEN scarti := scarti || format('owner_audit_log: %s nuove', n); END IF;

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
-- 8. DIPENDENZE FUORI PERIMETRO, DAL CATALOGO.
--
-- OGNI azione conta, non solo RESTRICT:
--   RESTRICT / NO ACTION  la DELETE fallisce e la transazione muore;
--   CASCADE               la riga altrui viene CANCELLATA con la nostra;
--   SET NULL              la riga altrui sopravvive con un campo azzerato,
--                         e nessuno se ne accorge.
-- Le ultime due sono le pericolose: non fanno rumore.
--
-- `%L::regclass` e non `%L`: `perimetro.tabella` e' regclass, e un letterale
-- testuale verrebbe risolto con `oid = oid`, cioe' leggendo 'contacts' come
-- un numero.
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
         WHERE con.contype = 'f' AND patt.attname = 'id'
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
            'modifica: vanno esaminate prima. Il catalogo serve a RILEVARE '
            'una dipendenza imprevista, non ad autorizzarne la rimozione.',
            array_to_string(fuori, '; ');
    END IF;
    RAISE NOTICE 'nessuna riga fuori perimetro referenzia le righe del run';
END
$$;


-- ---------------------------------------------------------------------------
-- 9. GLI AUDIT DA CONSERVARE NON SONO NEL PERIMETRO.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    n bigint;
BEGIN
    SELECT count(*) INTO n FROM perimetro p
     WHERE p.tabella = 'public.owner_audit_log'::regclass
       AND p.id IN (SELECT id FROM audit_intatti);
    IF n <> 0 THEN
        RAISE EXCEPTION
            'IL PERIMETRO CONTIENE % audit senza radice, che non sono di '
            'questo run. Nessuna cancellazione.', n;
    END IF;
    RAISE NOTICE 'audit senza radice: fuori dal perimetro, non verranno toccati';
END
$$;


-- ---------------------------------------------------------------------------
-- 10. LE CANCELLAZIONI, IN ORDINE DI CHIAVE ESTERNA.
--
-- Figlie -> genitori, ordine obbligato dai vincoli:
--   match_requirement_results -> match_runs                   CASCADE
--   owner_audit_log -> owner_accounts / properties            SET NULL
--     (prima dei genitori: dopo, le colonne sarebbero NULL e la riga
--      sopravviverebbe senza piu' dire di chi era)
--   owner_shared_documents -> property_documents              RESTRICT
--   property_sale_sellers -> property_sales CASCADE, contacts RESTRICT
--   property_sales -> proposals / buy_requests / properties   RESTRICT
--   property_proposals -> matches                             RESTRICT
--   owner_accounts -> contacts                                RESTRICT
--   buy_requests -> contacts                                  RESTRICT
--   properties / buy_requests / tasks / contacts / stime /
--   seller_timeline_events / property_watches -> agencies     RESTRICT
-- Per questo `agencies` e' l'ultima.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    n            bigint;
    atteso       bigint;
    mancanti     text[];
    nome_tabella text;
    ordine       text[] := ARRAY[
        'public.match_requirement_results',
        'public.match_runs',
        'public.buy_request_history',
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
        'public.next_best_actions',
        'public.flow_executions',
        'public.flow_events',
        'public.property_watches',
        'public.seller_timeline_events',
        'public.followup_actions',
        'public.stime',
        'public.tasks',
        'public.buy_requests',
        'public.properties',
        'public.contacts',
        'public.agency_memberships',
        'public.agencies'];
BEGIN
    -- Ogni tabella del perimetro compare nell'ordine. I due lati passano per
    -- la STESSA trasformazione: `regclass::text` da sola renderebbe
    -- `contacts` mentre l'array contiene `public.contacts`, e il confronto
    -- non troverebbe mai una corrispondenza.
    SELECT array_agg(d.nome ORDER BY d.nome) INTO mancanti FROM (
        SELECT p.tabella::regclass::text AS nome FROM perimetro AS p
        EXCEPT
        SELECT unnest(ordine)::regclass::text) d;
    IF mancanti IS NOT NULL THEN
        RAISE EXCEPTION
            'tabelle nel perimetro ma non nell''ordine di cancellazione: %. '
            'Resterebbero sul TEST senza che nessuno lo noti.',
            array_to_string(mancanti, ', ');
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
    FOR r IN SELECT DISTINCT p.tabella FROM perimetro p ORDER BY 1 LOOP
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
    IF n <> (SELECT count(*) FROM audit_intatti) THEN
        RAISE EXCEPTION 'gli audit da conservare sono cambiati: questo cleanup '
                        'non doveva toccarli';
    END IF;
    RAISE NOTICE 'verifica pre-chiusura: 0 righe del run, audit intatti';
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
--   PROVA GENERALE   TUTTE al loro posto, tabella per tabella. Una in meno
--                    significa che il ROLLBACK non ha riportato indietro
--                    qualcosa, ed e' un fatto da sapere PRIMA di autorizzare
--                    l'esecuzione.
--
-- Gli audit senza radice si confrontano CAMPO PER CAMPO, non si contano: un
-- SET NULL che ne avesse azzerato uno lascerebbe il numero invariato.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    r        record;
    n        bigint;
    atteso   bigint;
    totale   bigint := 0;
    esegui   boolean;
    scarti   text[] := '{}';
    n_audit  bigint;
    n_attesi bigint;
BEGIN
    SELECT modalita.esegui INTO esegui FROM modalita;

    FOR r IN SELECT DISTINCT p.tabella FROM perimetro p ORDER BY 1 LOOP
        EXECUTE format(
            'SELECT count(*) FROM %s WHERE id IN '
            '(SELECT id FROM perimetro WHERE tabella = %L::regclass)',
            r.tabella, r.tabella) INTO n;
        totale := totale + n;
        SELECT count(*) INTO atteso FROM perimetro AS p WHERE p.tabella = r.tabella;
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

    SELECT count(*) INTO n_attesi FROM audit_intatti;
    SELECT count(*) INTO n_audit FROM owner_audit_log a
      JOIN audit_intatti i ON i.id = a.id
     WHERE a.owner_account_id IS NOT DISTINCT FROM i.owner_account_id
       AND a.property_id IS NOT DISTINCT FROM i.property_id
       AND a.entity_type IS NOT DISTINCT FROM i.entity_type;
    IF n_audit <> n_attesi THEN
        RAISE EXCEPTION
            'audit senza radice: % dei % sono ancora identici a prima. Questo '
            'cleanup non doveva toccarli.', n_audit, n_attesi;
    END IF;

    IF esegui THEN
        RAISE NOTICE 'ESECUZIONE conclusa: 0 righe del run, % audit senza '
                     'radice intatti. Run 38e341f68f8a rimosso.', n_audit;
    ELSE
        RAISE NOTICE 'PROVA GENERALE conclusa: tutte le % righe sono tornate al '
                     'loro posto e % audit sono intatti. Il database non e'' '
                     'cambiato; verifiche e DELETE hanno girato davvero.',
                     totale, n_audit;
    END IF;
END
$$;

DROP TABLE IF EXISTS perimetro;
DROP TABLE IF EXISTS audit_intatti;
DROP TABLE IF EXISTS modalita;
