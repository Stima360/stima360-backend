-- ===========================================================================
-- CENSIMENTO READ-ONLY del run P26-6  38e341f68f8a
--
-- SOLA LETTURA. Nessuna INSERT, UPDATE, DELETE, nessuna tabella temporanea.
--
-- PERCHE' QUESTO CENSIMENTO DERIVA INVECE DI DICHIARARE
--
-- Il run si e' fermato con il cleanup BLOCCATO, quindi il report non ha
-- stampato gli id di cio' che aveva creato: si leggono solo le radici
-- (contatti 90/91, immobili 46/47, richieste 32/33, eventi 108/109, match
-- 30/31, proposte 21/22, vendite 16/17, conti 10/11, agenzie 20/21, attivita'
-- 42/43). Tutto il resto - documenti, storico, risultati, legami, token,
-- concessioni, stime - esisteva senza che nessuno ne scrivesse l'id.
--
-- Inventarli sarebbe il modo peggiore di cominciare. Qui si RICAVANO, ognuno
-- dalla propria radice, e il censimento stampa quello che trova: sara' quel
-- numero, non una mia previsione, a diventare il perimetro del cleanup.
--
-- IDENTITA' E SESSIONI risultano gia' rimosse dal run stesso (CLEAN-DB: 4
-- sessioni, 4 membership, 4 identita'; CLEAN-CHK: 0 residui). Il punto 6 lo
-- verifica invece di darlo per buono.
--
-- I SEI AUDIT SENZA RADICE non appartengono a questo run - hanno entrambe le
-- colonne NULL, cioe' origine non attribuita - e il punto 7 li tiene separati
-- dagli otto che il run ha scritto.
-- ===========================================================================

\echo '--- 0. DATABASE ---'
SELECT current_database() AS db,
       current_database() = 'stima360_db_test' AS e_il_test;

\echo '--- 1. RADICI dichiarate dal report del run ---'
SELECT 'contacts' AS tabella, c.id, c.agency_id,
       c.display_name LIKE 'P26-6-38e341f68f8a-%' AS ha_marcatore
  FROM contacts c WHERE c.id IN (90, 91)
UNION ALL
SELECT 'properties', p.id, p.agency_id, p.title LIKE 'P26-6-38e341f68f8a-%'
  FROM properties p WHERE p.id IN (46, 47)
UNION ALL
SELECT 'buy_requests', b.id, b.agency_id, b.title LIKE 'P26-6-38e341f68f8a-%'
  FROM buy_requests b WHERE b.id IN (32, 33)
UNION ALL
SELECT 'seller_timeline_events', s.id, s.agency_id,
       s.event_type LIKE 'P26-6-38e341f68f8a-%'
  FROM seller_timeline_events s WHERE s.id IN (108, 109)
UNION ALL
SELECT 'tasks', t.id, t.agency_id, NULL FROM tasks t WHERE t.id IN (42, 43)
UNION ALL
SELECT 'agencies', a.id, a.id, a.slug LIKE 'p26-6-cert-38e341f68f8a-%'
  FROM agencies a WHERE a.id IN (20, 21)
ORDER BY 1, 2;

\echo '--- 2. RADICI senza agency_id proprio ---'
SELECT 'matches' AS tabella, m.id, m.buy_request_id AS rif_a, m.property_id AS rif_b
  FROM matches m WHERE m.id IN (30, 31)
UNION ALL
SELECT 'property_proposals', pp.id, pp.match_id, NULL
  FROM property_proposals pp WHERE pp.id IN (21, 22)
UNION ALL
SELECT 'property_sales', ps.id, ps.proposal_id, ps.property_id
  FROM property_sales ps WHERE ps.id IN (16, 17)
UNION ALL
SELECT 'owner_accounts', oa.id, oa.contact_id, NULL
  FROM owner_accounts oa WHERE oa.id IN (10, 11)
ORDER BY 1, 2;

\echo '--- 3. MARCATORE: qualunque riga lo porti, anche non prevista ---'
-- Se qui compare un id che il punto 1 non elenca, il perimetro del cleanup
-- va allargato PRIMA di prepararlo.
SELECT 'contacts' AS tabella, array_agg(id ORDER BY id) AS ids
  FROM contacts WHERE display_name LIKE 'P26-6-38e341f68f8a-%'
UNION ALL
SELECT 'properties', array_agg(id ORDER BY id)
  FROM properties WHERE title LIKE 'P26-6-38e341f68f8a-%'
UNION ALL
SELECT 'buy_requests', array_agg(id ORDER BY id)
  FROM buy_requests WHERE title LIKE 'P26-6-38e341f68f8a-%'
UNION ALL
SELECT 'seller_timeline_events', array_agg(id ORDER BY id)
  FROM seller_timeline_events WHERE event_type LIKE 'P26-6-38e341f68f8a-%'
UNION ALL
SELECT 'stime (via comune)', array_agg(id ORDER BY id)
  FROM stime WHERE comune LIKE 'P26-6-38e341f68f8a-%'
ORDER BY 1;

\echo '--- 4. AGENZIE DEDICATE 20/21: tutto quello che contengono ---'
SELECT 'contacts' AS tabella, count(*) AS righe,
       array_agg(id ORDER BY id) AS ids
  FROM contacts WHERE agency_id IN (20, 21)
UNION ALL SELECT 'tasks', count(*), array_agg(id ORDER BY id)
  FROM tasks WHERE agency_id IN (20, 21)
UNION ALL SELECT 'followup_actions', count(*), array_agg(id ORDER BY id)
  FROM followup_actions WHERE agency_id IN (20, 21)
UNION ALL SELECT 'stime', count(*), array_agg(id ORDER BY id)
  FROM stime WHERE agency_id IN (20, 21)
UNION ALL SELECT 'seller_timeline_events', count(*), array_agg(id ORDER BY id)
  FROM seller_timeline_events WHERE agency_id IN (20, 21)
UNION ALL SELECT 'property_watches', count(*), array_agg(id ORDER BY id)
  FROM property_watches WHERE agency_id IN (20, 21)
UNION ALL SELECT 'flow_events', count(*), array_agg(id ORDER BY id)
  FROM flow_events WHERE agency_id IN (20, 21)
UNION ALL SELECT 'flow_executions', count(*), array_agg(id ORDER BY id)
  FROM flow_executions WHERE agency_id IN (20, 21)
UNION ALL SELECT 'next_best_actions', count(*), array_agg(id ORDER BY id)
  FROM next_best_actions WHERE agency_id IN (20, 21)
UNION ALL SELECT 'agency_memberships', count(*), array_agg(id ORDER BY id)
  FROM agency_memberships WHERE agency_id IN (20, 21)
UNION ALL SELECT 'properties', count(*), array_agg(id ORDER BY id)
  FROM properties WHERE agency_id IN (20, 21)
UNION ALL SELECT 'buy_requests', count(*), array_agg(id ORDER BY id)
  FROM buy_requests WHERE agency_id IN (20, 21)
ORDER BY 1;

\echo '--- 5. DIPENDENZE ricavate dalle radici ---'
SELECT 'property_contacts' AS tabella, count(*) AS righe,
       array_agg(id ORDER BY id) AS ids
  FROM property_contacts
 WHERE property_id IN (46, 47) AND contact_id IN (90, 91)
UNION ALL SELECT 'property_status_history', count(*), array_agg(id ORDER BY id)
  FROM property_status_history WHERE property_id IN (46, 47)
UNION ALL SELECT 'buy_request_history', count(*), array_agg(id ORDER BY id)
  FROM buy_request_history WHERE buy_request_id IN (32, 33)
UNION ALL SELECT 'match_runs', count(*), array_agg(id ORDER BY id)
  FROM match_runs WHERE buy_request_id IN (32, 33) OR property_id IN (46, 47)
UNION ALL SELECT 'match_requirement_results', count(*), array_agg(r.id ORDER BY r.id)
  FROM match_requirement_results r JOIN match_runs mr ON mr.id = r.match_run_id
 WHERE mr.buy_request_id IN (32, 33) OR mr.property_id IN (46, 47)
UNION ALL SELECT 'property_documents', count(*), array_agg(id ORDER BY id)
  FROM property_documents WHERE property_id IN (46, 47)
UNION ALL SELECT 'owner_shared_documents', count(*), array_agg(sd.id ORDER BY sd.id)
  FROM owner_shared_documents sd JOIN property_documents pd ON pd.id = sd.property_document_id
 WHERE pd.property_id IN (46, 47)
UNION ALL SELECT 'property_sale_sellers', count(*), array_agg(id ORDER BY id)
  FROM property_sale_sellers WHERE sale_id IN (16, 17) AND contact_id IN (90, 91)
UNION ALL SELECT 'owner_property_access', count(*), array_agg(id ORDER BY id)
  FROM owner_property_access
 WHERE owner_account_id IN (10, 11) AND property_id IN (46, 47)
UNION ALL SELECT 'owner_access_tokens', count(*), array_agg(id ORDER BY id)
  FROM owner_access_tokens WHERE owner_account_id IN (10, 11)
UNION ALL SELECT 'owner_sessions', count(*), array_agg(id ORDER BY id)
  FROM owner_sessions WHERE owner_account_id IN (10, 11)
UNION ALL SELECT 'owner_audit_log (del run)', count(*), array_agg(id ORDER BY id)
  FROM owner_audit_log
 WHERE (owner_account_id IS NOT NULL OR property_id IS NOT NULL)
   AND (owner_account_id IS NULL OR owner_account_id IN (10, 11))
   AND (property_id IS NULL OR property_id IN (46, 47))
ORDER BY 1;

\echo '--- 6. IDENTITA E SESSIONI: il run le dichiara rimosse. Si verifica. ---'
SELECT 'operator_users col prefisso' AS cosa, count(*) AS righe
  FROM operator_users WHERE email LIKE '%38e341f68f8a%'
UNION ALL SELECT 'operator_sessions di quelle identita', count(*)
  FROM operator_sessions s JOIN operator_users u ON u.id = s.operator_user_id
 WHERE u.email LIKE '%38e341f68f8a%'
UNION ALL SELECT 'agency_memberships di quelle identita', count(*)
  FROM agency_memberships m JOIN operator_users u ON u.id = m.operator_user_id
 WHERE u.email LIKE '%38e341f68f8a%'
UNION ALL SELECT 'owner_sessions dei conti 10/11', count(*)
  FROM owner_sessions WHERE owner_account_id IN (10, 11);

\echo '--- 7. AUDIT: i preesistenti senza radice NON sono di questo run ---'
SELECT CASE WHEN owner_account_id IS NULL AND property_id IS NULL
            THEN 'senza radice (PREESISTENTI, da conservare)'
            ELSE 'con radice' END AS classe,
       COALESCE(entity_type, '(nessuno)') AS entita,
       count(*) AS righe,
       array_agg(id ORDER BY id) AS ids
  FROM owner_audit_log
 WHERE (owner_account_id IS NULL AND property_id IS NULL)
    OR owner_account_id IN (10, 11) OR property_id IN (46, 47)
 GROUP BY 1, 2
 ORDER BY 1, 2;

\echo '--- 8. DIPENDENZE ESTRANEE: chi punta alle radici, dal catalogo ---'
SELECT con.conrelid::regclass::text  AS figlia,
       att.attname                   AS colonna,
       con.confrelid::regclass::text AS genitore,
       CASE con.confdeltype WHEN 'a' THEN 'NO ACTION' WHEN 'r' THEN 'RESTRICT'
                            WHEN 'c' THEN 'CASCADE'   WHEN 'n' THEN 'SET NULL'
                            WHEN 'd' THEN 'SET DEFAULT' END AS azione
  FROM pg_constraint con
  CROSS JOIN LATERAL unnest(con.conkey, con.confkey) AS k(figlia, genitore)
  JOIN pg_attribute att  ON att.attrelid  = con.conrelid  AND att.attnum = k.figlia
  JOIN pg_attribute patt ON patt.attrelid = con.confrelid AND patt.attnum = k.genitore
 WHERE con.contype = 'f' AND patt.attname = 'id'
   AND con.confrelid IN ('public.contacts'::regclass, 'public.properties'::regclass,
                         'public.buy_requests'::regclass, 'public.matches'::regclass,
                         'public.property_proposals'::regclass,
                         'public.property_sales'::regclass,
                         'public.owner_accounts'::regclass, 'public.agencies'::regclass,
                         'public.tasks'::regclass, 'public.stime'::regclass,
                         'public.seller_timeline_events'::regclass,
                         'public.match_runs'::regclass,
                         'public.property_documents'::regclass)
 ORDER BY 3, 1, 2;
