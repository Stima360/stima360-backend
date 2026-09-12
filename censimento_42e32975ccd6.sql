-- ===========================================================================
-- CENSIMENTO del run P26-6  42e32975ccd6  +  MANIFEST DELLE CHIAVI STORAGE
--
-- SOLA LETTURA SUL DATABASE. Nessuna INSERT, UPDATE, DELETE, nessuna DDL,
-- nessuna tabella temporanea. Le uniche scritture sono due file LOCALI - il
-- manifest e il suo file di controllo - prodotti da `COPY ... TO STDOUT`
-- dirottato su file da `\g`, che e' un comando del client psql.
--
-- "NESSUN LOCK" ERA SBAGLIATO, ed e' corretto qui.
--
-- Una SELECT non e' priva di lock: prende un ACCESS SHARE su ogni tabella che
-- tocca. Non blocca letture ne' scritture ordinarie, ma blocca - ed e' bloccata
-- da - un ACCESS EXCLUSIVE, cioe' un `ALTER TABLE`, un `DROP`, un `VACUUM
-- FULL`, una migration. Di qui il timeout: se una migration sta girando, questo
-- censimento deve rinunciare in fretta invece di restare appeso e trattenere a
-- sua volta la coda.
--
-- ISTANTANEA CONSISTENTE. Tutte le domande vengono fatte dentro UNA
-- transazione REPEATABLE READ: il perimetro che ne esce e' lo stato del
-- database a un singolo istante. Senza, il punto 5 potrebbe contare figlie di
-- una riga che il punto 1 ha visto e che nel frattempo e' cambiata, e il
-- perimetro sarebbe la fotografia di due momenti diversi.
--
-- PERCHE' SERVE PRIMA DEL RECUPERO
--
-- Il run si e' fermato con il cleanup BLOCCATO dal preflight, quindi il report
-- non ha stampato gli id derivati: si leggono solo le RADICI.
--
--   agenzie dedicate   24, 25        conti OWNER         14, 15
--   contatti           98, 99        immobili            50, 51
--   richieste          36, 37        eventi venditore    114, 115
--   match              34, 35        proposte            25, 26
--   vendite            20, 21        attivita'           46, 47
--   eventi FLOW        13, 14        stime               19, 20
--   documenti condivisi 1, 2
--
-- Tutto il resto - documenti dell'immobile, storico, risultati per criterio,
-- concessioni, token, sessioni, audit, NOTIFICHE e LETTURE - esiste senza che
-- nessuno ne abbia scritto l'id. Qui si RICAVA, ognuno dalla propria radice.
--
-- I SEI AUDIT (560, 562, 563, 564, 566, 567) NON appartengono a questo run e
-- non vanno toccati: i punti 10 e 10b li guardano, nient'altro.
-- ===========================================================================

\set ON_ERROR_STOP on
\pset pager off

-- ---------------------------------------------------------------------------
-- GUARDIA: il database deve essere il TEST. Prima di tutto, fuori dalla
-- transazione, cosi' che un errore qui non lasci nulla aperto.
--
-- `\if` non puo' valutare SQL, quindi la guardia e' un DO che SOLLEVA: con
-- ON_ERROR_STOP la sessione termina e nessuna delle query successive parte.
-- Un DO non scrive nulla - non e' una DDL e non modifica dati.
-- ---------------------------------------------------------------------------
DO $guardia$
BEGIN
    IF current_database() <> 'stima360_db_test' THEN
        RAISE EXCEPTION
            'RIFIUTO: questo censimento gira solo su stima360_db_test, non su %',
            current_database();
    END IF;
END
$guardia$;

BEGIN TRANSACTION READ ONLY ISOLATION LEVEL REPEATABLE READ;

-- Se una migration tiene un ACCESS EXCLUSIVE, si rinuncia in dieci secondi.
SET LOCAL statement_timeout = '10s';
SET LOCAL lock_timeout = '5s';
SET LOCAL idle_in_transaction_session_timeout = '60s';

\echo '--- 0. DATABASE E ISTANTANEA ---'
SELECT current_database() AS db,
       current_setting('transaction_read_only') AS sola_lettura,
       current_setting('transaction_isolation')  AS isolamento,
       now() AS istante_istantanea;

\echo '--- 1. RADICI dichiarate dal report ---'
SELECT 'contacts' AS tabella, c.id, c.agency_id,
       c.display_name LIKE 'P26-6-42e32975ccd6-%' AS ha_marcatore
  FROM contacts c WHERE c.id IN (98, 99)
UNION ALL
SELECT 'properties', p.id, p.agency_id, p.title LIKE 'P26-6-42e32975ccd6-%'
  FROM properties p WHERE p.id IN (50, 51)
UNION ALL
SELECT 'buy_requests', b.id, b.agency_id, b.title LIKE 'P26-6-42e32975ccd6-%'
  FROM buy_requests b WHERE b.id IN (36, 37)
UNION ALL
SELECT 'seller_timeline_events', s.id, s.agency_id,
       s.event_type LIKE 'P26-6-42e32975ccd6-%'
  FROM seller_timeline_events s WHERE s.id IN (114, 115)
UNION ALL
SELECT 'tasks', t.id, t.agency_id, NULL FROM tasks t WHERE t.id IN (46, 47)
UNION ALL
SELECT 'stime', st.id, st.agency_id, st.comune LIKE 'P26-6-42e32975ccd6-%'
  FROM stime st WHERE st.id IN (19, 20)
UNION ALL
SELECT 'flow_events', fe.id, fe.agency_id, NULL
  FROM flow_events fe WHERE fe.id IN (13, 14)
UNION ALL
SELECT 'agencies', a.id, a.id, a.slug LIKE 'p26-6-cert-42e32975ccd6-%'
  FROM agencies a WHERE a.id IN (24, 25)
ORDER BY 1, 2;

\echo '--- 2. RADICI senza agency_id proprio ---'
SELECT 'matches' AS tabella, m.id, m.buy_request_id AS rif_a, m.property_id AS rif_b
  FROM matches m WHERE m.id IN (34, 35)
UNION ALL
SELECT 'property_proposals', pp.id, pp.match_id, NULL
  FROM property_proposals pp WHERE pp.id IN (25, 26)
UNION ALL
SELECT 'property_sales', ps.id, ps.proposal_id, ps.property_id
  FROM property_sales ps WHERE ps.id IN (20, 21)
UNION ALL
SELECT 'owner_accounts', oa.id, oa.contact_id, NULL
  FROM owner_accounts oa WHERE oa.id IN (14, 15)
UNION ALL
SELECT 'owner_shared_documents', sd.id, sd.property_document_id, sd.owner_account_id
  FROM owner_shared_documents sd WHERE sd.id IN (1, 2)
ORDER BY 1, 2;

\echo '--- 3. MARCATORE: qualunque riga lo porti, anche non prevista ---'
SELECT 'contacts' AS tabella, array_agg(id ORDER BY id) AS ids
  FROM contacts WHERE display_name LIKE 'P26-6-42e32975ccd6-%'
UNION ALL
SELECT 'properties', array_agg(id ORDER BY id)
  FROM properties WHERE title LIKE 'P26-6-42e32975ccd6-%'
UNION ALL
SELECT 'buy_requests', array_agg(id ORDER BY id)
  FROM buy_requests WHERE title LIKE 'P26-6-42e32975ccd6-%'
UNION ALL
SELECT 'seller_timeline_events', array_agg(id ORDER BY id)
  FROM seller_timeline_events WHERE event_type LIKE 'P26-6-42e32975ccd6-%'
UNION ALL
SELECT 'stime (via comune)', array_agg(id ORDER BY id)
  FROM stime WHERE comune LIKE 'P26-6-42e32975ccd6-%'
UNION ALL
SELECT 'owner_shared_documents (titolo)', array_agg(id ORDER BY id)
  FROM owner_shared_documents WHERE public_title LIKE 'P26-6-42e32975ccd6-%'
ORDER BY 1;

\echo '--- 4. AGENZIE DEDICATE 24/25: tutto quello che contengono ---'
SELECT 'contacts' AS tabella, count(*) AS righe, array_agg(id ORDER BY id) AS ids
  FROM contacts WHERE agency_id IN (24, 25)
UNION ALL SELECT 'leads', count(*), array_agg(id ORDER BY id)
  FROM leads WHERE agency_id IN (24, 25)
UNION ALL SELECT 'tasks', count(*), array_agg(id ORDER BY id)
  FROM tasks WHERE agency_id IN (24, 25)
UNION ALL SELECT 'followup_actions', count(*), array_agg(id ORDER BY id)
  FROM followup_actions WHERE agency_id IN (24, 25)
UNION ALL SELECT 'stime', count(*), array_agg(id ORDER BY id)
  FROM stime WHERE agency_id IN (24, 25)
UNION ALL SELECT 'seller_timeline_events', count(*), array_agg(id ORDER BY id)
  FROM seller_timeline_events WHERE agency_id IN (24, 25)
UNION ALL SELECT 'property_watches', count(*), array_agg(id ORDER BY id)
  FROM property_watches WHERE agency_id IN (24, 25)
UNION ALL SELECT 'flow_events', count(*), array_agg(id ORDER BY id)
  FROM flow_events WHERE agency_id IN (24, 25)
UNION ALL SELECT 'flow_executions', count(*), array_agg(id ORDER BY id)
  FROM flow_executions WHERE agency_id IN (24, 25)
UNION ALL SELECT 'flow_suppressions', count(*), array_agg(id ORDER BY id)
  FROM flow_suppressions WHERE agency_id IN (24, 25)
UNION ALL SELECT 'next_best_actions', count(*), array_agg(id ORDER BY id)
  FROM next_best_actions WHERE agency_id IN (24, 25)
UNION ALL SELECT 'agency_memberships', count(*), array_agg(id ORDER BY id)
  FROM agency_memberships WHERE agency_id IN (24, 25)
UNION ALL SELECT 'properties', count(*), array_agg(id ORDER BY id)
  FROM properties WHERE agency_id IN (24, 25)
UNION ALL SELECT 'buy_requests', count(*), array_agg(id ORDER BY id)
  FROM buy_requests WHERE agency_id IN (24, 25)
ORDER BY 1;

\echo '--- 4b. FIGLIE SENZA agency_id delle righe dedicate ---'
SELECT 'flow_action_records' AS tabella, count(*) AS righe,
       array_agg(ar.id ORDER BY ar.id) AS ids
  FROM flow_action_records ar
  JOIN flow_executions fx ON fx.id = ar.execution_id
 WHERE fx.agency_id IN (24, 25)
UNION ALL
SELECT 'property_watch_observations', count(*), array_agg(o.id ORDER BY o.id)
  FROM property_watch_observations o
  JOIN property_watches w ON w.id = o.watch_id
 WHERE w.agency_id IN (24, 25)
UNION ALL
SELECT 'invisible_sale_opportunities (RESTRICT: atteso 0)', count(*),
       array_agg(io.id ORDER BY io.id)
  FROM invisible_sale_opportunities io
  JOIN property_watches w ON w.id = io.watch_id
 WHERE w.agency_id IN (24, 25)
ORDER BY 1;

\echo '--- 4c. flow_action_records: hanno ALTRI riferimenti fuori perimetro? ---'
-- La terza condizione di OWNED_BY_PARENT. Se una di queste righe puntasse
-- anche altrove, sarebbe MISTA e non andrebbe cancellata.
SELECT ar.id, ar.target_entity_type, ar.target_entity_id, ar.status
  FROM flow_action_records ar
  JOIN flow_executions fx ON fx.id = ar.execution_id
 WHERE fx.agency_id IN (24, 25)
 ORDER BY ar.id;

\echo '--- 5. DIPENDENZE ricavate dalle radici ---'
SELECT 'property_contacts' AS tabella, count(*) AS righe,
       array_agg(id ORDER BY id) AS ids
  FROM property_contacts
 WHERE property_id IN (50, 51) AND contact_id IN (98, 99)
UNION ALL SELECT 'property_status_history', count(*), array_agg(id ORDER BY id)
  FROM property_status_history WHERE property_id IN (50, 51)
UNION ALL SELECT 'buy_request_history', count(*), array_agg(id ORDER BY id)
  FROM buy_request_history WHERE buy_request_id IN (36, 37)
UNION ALL SELECT 'match_runs', count(*), array_agg(id ORDER BY id)
  FROM match_runs WHERE buy_request_id IN (36, 37) OR property_id IN (50, 51)
UNION ALL SELECT 'match_requirement_results', count(*), array_agg(r.id ORDER BY r.id)
  FROM match_requirement_results r JOIN match_runs mr ON mr.id = r.match_run_id
 WHERE mr.buy_request_id IN (36, 37) OR mr.property_id IN (50, 51)
UNION ALL SELECT 'property_documents', count(*), array_agg(id ORDER BY id)
  FROM property_documents WHERE property_id IN (50, 51)
UNION ALL SELECT 'property_sale_sellers', count(*), array_agg(id ORDER BY id)
  FROM property_sale_sellers WHERE sale_id IN (20, 21) AND contact_id IN (98, 99)
UNION ALL SELECT 'owner_property_access', count(*), array_agg(id ORDER BY id)
  FROM owner_property_access
 WHERE owner_account_id IN (14, 15) AND property_id IN (50, 51)
UNION ALL SELECT 'owner_access_tokens', count(*), array_agg(id ORDER BY id)
  FROM owner_access_tokens WHERE owner_account_id IN (14, 15)
UNION ALL SELECT 'owner_sessions', count(*), array_agg(id ORDER BY id)
  FROM owner_sessions WHERE owner_account_id IN (14, 15)
UNION ALL SELECT 'owner_audit_log (del run)', count(*), array_agg(id ORDER BY id)
  FROM owner_audit_log
 WHERE (owner_account_id IS NOT NULL OR property_id IS NOT NULL)
   AND (owner_account_id IS NULL OR owner_account_id IN (14, 15))
   AND (property_id IS NULL OR property_id IN (50, 51))
ORDER BY 1;

\echo '--- 6. owner_notifications: LA PRIMA CAUSA DEL BLOCCO ---'
SELECT n.id, n.owner_account_id, n.property_id, n.notification_type,
       n.target_type, n.target_id,
       n.owner_account_id IN (14, 15) AS conto_del_run,
       n.property_id IN (50, 51)      AS immobile_del_run,
       n.idempotency_key LIKE 'owner-p5:v1:shared_document_published:owner_shared_document:%'
           AS chiave_della_pubblicazione
  FROM owner_notifications n
 WHERE n.owner_account_id IN (14, 15) OR n.property_id IN (50, 51)
 ORDER BY n.id;

\echo '--- 7. owner_document_reads: LA SECONDA CAUSA DEL BLOCCO ---'
SELECT dr.id, dr.shared_document_id, dr.owner_account_id, dr.view_count,
       dr.owner_account_id IN (14, 15) AS conto_del_run,
       dr.shared_document_id IN (1, 2) AS documento_del_run
  FROM owner_document_reads dr
 WHERE dr.owner_account_id IN (14, 15) OR dr.shared_document_id IN (1, 2)
 ORDER BY dr.id;

\echo '--- 8. STORAGE: conteggio a video, CHIAVI nel manifest ---'
-- LE CHIAVI NON SI STAMPANO. Una chiave di storage localizza un oggetto in un
-- bucket: nel log di una shell resta, e il log gira. A video va il numero; le
-- chiavi vanno nel manifest, che e' un file con permessi propri.
SELECT count(*) FILTER (WHERE storage_key IS NOT NULL) AS oggetti_da_rimuovere,
       count(*) FILTER (WHERE storage_key IS NULL)     AS documenti_senza_oggetto,
       count(*)                                        AS documenti_totali
  FROM property_documents
 WHERE property_id IN (50, 51);

\echo '--- 8b. MANIFEST: scritto PRIMA di qualunque cancellazione ---'
-- PERCHE' UN FILE E NON UNA COLONNA.
--
-- `property_documents.storage_key` e' l'UNICO posto in cui quelle chiavi
-- esistono. Il recupero cancella quella riga: da quel momento l'oggetto nel
-- bucket non e' piu' raggiungibile per nessuna via - nessun censimento SQL lo
-- vedrebbe, perche' non e' sul database. Se il processo si interrompe fra il
-- COMMIT e la pulizia del bucket, senza manifest l'oggetto resta li' per
-- sempre e nessuno sa piu' quale sia.
--
-- PERCHE' `COPY ... TO STDOUT` E NON `\copy`.
--
-- La versione precedente scriveva `\copy (...) TO :'parziale'`, e NON
-- FUNZIONA. psql documenta l'eccezione: per `\copy` l'intero resto della riga
-- e' preso alla lettera come argomento, "neither variable interpolation nor
-- backquote expansion are performed". Il file si sarebbe chiamato
-- letteralmente `:'parziale'`, e il verificatore avrebbe cercato un nome che
-- non esiste - oppure, peggio, avrebbe trovato il manifest di ieri.
--
-- Qui si usa il percorso documentato: `COPY (...) TO STDOUT`, che e' una
-- lettura lato server, e `\g` che ne dirotta l'uscita su un file del CLIENT.
-- Il nome e' un LETTERALE, senza variabili: l'unicita' la da' la directory,
-- che il wrapper crea nuova a ogni esecuzione e in cui entra prima di
-- lanciare psql. Cosi' il wrapper CONOSCE il percorso esatto e non deve
-- cercarlo con `ls | head` - che avrebbe potuto pescare l'esecuzione di
-- qualcun altro.
--
-- La transazione resta READ ONLY: `COPY TO STDOUT` legge e basta.
\echo 'manifest in scrittura: manifest.csv.parziale (nella directory corrente)'

COPY (
    SELECT id, property_id, storage_key, md5(storage_key) AS impronta,
           'da_rimuovere' AS stato
      FROM property_documents
     WHERE property_id IN (50, 51) AND storage_key IS NOT NULL
     ORDER BY id
) TO STDOUT WITH (FORMAT csv, HEADER true) \g manifest.csv.parziale

\echo '--- 8c. CONTROLLO in formato macchina, per il verificatore ---'
-- DUE COLONNE, non una stringa con un TAB dentro.
--
-- La versione precedente concatenava: `count(*)::text || E'\t' || md5(...)`.
-- Il TAB finiva DENTRO un unico valore, e il formato non lo distingueva piu'
-- da un tab che facesse parte del dato: `COPY ... TO STDOUT` lo avrebbe
-- dovuto proteggere come carattere qualsiasi. Qui le colonne sono due e il
-- TAB e' il DELIMITATORE - cioe' la cosa che separa i campi, che e' un'altra
-- cosa dall'essere un carattere in un campo.
--
-- `md5('')` invece di stringa vuota nel caso ZERO RIGHE. Con `string_agg` su
-- un insieme vuoto il risultato e' NULL, e in formato testo un NULL esce come
-- `\N`: il verificatore avrebbe letto `\N` come impronta e non avrebbe potuto
-- confrontarla con nulla. `md5('')` e' l'impronta della concatenazione vuota,
-- ed e' esattamente cio' che il verificatore Python calcola quando le righe
-- sono zero - le due parti restano d'accordo anche sul caso vuoto.
--
-- Le chiavi NON compaiono qui: solo quante sono e l'impronta d'insieme.
COPY (
    SELECT count(*) AS conteggio,
           coalesce(
               md5(string_agg(md5(storage_key), ',' ORDER BY id)),
               md5('')
           ) AS impronta
      FROM property_documents
     WHERE property_id IN (50, 51)
       AND storage_key IS NOT NULL
) TO STDOUT WITH (FORMAT text, DELIMITER E'\t')
\g controllo_manifest.tsv

\echo '--- 8d. Gli stessi due valori a video, per il registro ---'
SELECT count(*) AS righe_attese_nel_manifest,
       md5(string_agg(md5(storage_key), ',' ORDER BY id)) AS impronta_insieme
  FROM property_documents
 WHERE property_id IN (50, 51) AND storage_key IS NOT NULL;

\echo '--- 9. IDENTITA E SESSIONI: il run le dichiara rimosse. Si verifica. ---'
SELECT 'operator_users col prefisso' AS cosa, count(*) AS righe
  FROM operator_users WHERE email LIKE '%42e32975ccd6%'
UNION ALL
SELECT 'agency_memberships nelle dedicate', count(*)
  FROM agency_memberships WHERE agency_id IN (24, 25)
UNION ALL
SELECT 'operator_sessions di quelle identita', count(*)
  FROM operator_sessions s
  JOIN operator_users u ON u.id = s.operator_user_id
 WHERE u.email LIKE '%42e32975ccd6%'
ORDER BY 1;

\echo '--- 10. I SEI AUDIT SENZA RADICE: si guardano, non si toccano ---'
SELECT id, entity_type, entity_id, action, result,
       owner_account_id IS NULL AS conto_nullo,
       property_id IS NULL      AS immobile_nullo,
       created_at
  FROM owner_audit_log
 WHERE id IN (560, 562, 563, 564, 566, 567)
 ORDER BY id;

\echo '--- 10b. Gli audit VICINI, per collocare i sei nel loro contesto ---'
SELECT id, entity_type, entity_id, action,
       owner_account_id, property_id, created_at
  FROM owner_audit_log
 WHERE id BETWEEN 555 AND 572
 ORDER BY id;

\echo '--- 10c. `entity_id` dei sei esiste ancora? (ipotesi SET NULL) ---'
-- Se il conto/sessione/token nominato da `entity_id` NON esiste piu', la riga
-- e' compatibile con un SET NULL avvenuto. Se esiste ancora ed e' di
-- un''agenzia viva, l''ipotesi SET NULL e' piu' debole. NESSUNA delle due
-- risposte chiude da sola la questione: e' un indizio, e va detto come tale.
SELECT a.id, a.entity_type, a.entity_id,
       CASE a.entity_type
           WHEN 'owner_account' THEN EXISTS (SELECT 1 FROM owner_accounts o
                                              WHERE o.id::text = a.entity_id)
           WHEN 'owner_session' THEN EXISTS (SELECT 1 FROM owner_sessions s
                                              WHERE s.id::text = a.entity_id)
           WHEN 'owner_token'   THEN EXISTS (SELECT 1 FROM owner_access_tokens t
                                              WHERE t.id::text = a.entity_id)
       END AS entita_ancora_presente
  FROM owner_audit_log a
 WHERE a.id IN (560, 562, 563, 564, 566, 567)
 ORDER BY a.id;

\echo '--- 11. CONFIGURAZIONE TEST che serve alle fixture ancora aperte ---'
-- FLOW: un''esecuzione nasce solo se una REGOLA ATTIVA corrisponde
-- all''evento. Quali siano attive su questo TEST e' configurazione, non
-- codice: senza questo elenco una fixture deterministica sulle esecuzioni non
-- si puo' scrivere.
SELECT code, is_active, scope, version
  FROM flow_rules
 ORDER BY code;

\echo '--- 11b. NEXT_BEST_ACTION: i lead aperti delle agenzie dedicate ---'
-- Il segnale piu' semplice e deterministico e' `next_action_overdue`, che
-- esige un lead APERTO con `next_action_at` nel passato. Qui si verifica che
-- nelle agenzie dedicate non ce ne siano di preesistenti, cosi' che la
-- fixture futura parta da zero e il confronto A/B resti pulito.
SELECT agency_id, count(*) AS lead_aperti,
       count(*) FILTER (WHERE next_action_at < now()) AS gia_scaduti
  FROM leads
 WHERE agency_id IN (24, 25) AND status = 'open'
 GROUP BY agency_id
 ORDER BY agency_id;

\echo '--- 12. TOTALE osservato (da confrontare con il cleanup) ---'
SELECT (SELECT count(*) FROM contacts WHERE id IN (98, 99))
     + (SELECT count(*) FROM properties WHERE id IN (50, 51))
     + (SELECT count(*) FROM buy_requests WHERE id IN (36, 37))
     + (SELECT count(*) FROM matches WHERE id IN (34, 35))
     + (SELECT count(*) FROM property_proposals WHERE id IN (25, 26))
     + (SELECT count(*) FROM property_sales WHERE id IN (20, 21))
     + (SELECT count(*) FROM owner_accounts WHERE id IN (14, 15))
     + (SELECT count(*) FROM agencies WHERE id IN (24, 25))
       AS radici_ancora_presenti;

COMMIT;

\echo '--- FINE ---'
\echo 'Il manifest e manifest.csv.parziale, il controllo controllo_manifest.tsv,'
\echo 'entrambi nella directory corrente. Diventa definitivo solo dopo la'
\echo 'verifica con scripts/p26_6_verifica_manifest.py.'
\echo 'Nessun oggetto e stato rimosso dal bucket: questo file non lo fa.'
