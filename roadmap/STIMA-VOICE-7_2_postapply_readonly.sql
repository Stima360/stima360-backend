-- STIMA Voice Fase 7 - passo 2/3: verifica DOPO l'applicazione di 098 e 099.
-- SOLA LETTURA. Atteso: due righe di registro, 5 tabelle, 6 trigger, nessuna
-- agenzia accesa (STIMA Voice resta spento), conteggi CRM uguali al passo 1.
BEGIN READ ONLY;

\echo '== 1. registro delle migrazioni'
SELECT version, applied_at, applied_by_operator, rolled_back_at FROM schema_migrations
 WHERE version IN ('098_voice_commands', '099_voice_undo') ORDER BY version;

\echo '== 2. oggetti creati (atteso: 5 tabelle, 6 trigger)'
SELECT string_agg(table_name, ', ' ORDER BY table_name) AS tabelle, count(*) AS n
  FROM information_schema.tables WHERE table_schema = 'public' AND table_name LIKE 'voice\_%';
SELECT string_agg(tgname, ', ' ORDER BY tgname) AS trigger, count(*) AS n
  FROM pg_trigger WHERE tgname LIKE 'trg\_voice\_%';

\echo '== 3. STIMA Voice spento ovunque (atteso: 0 agenzie accese, 0 comandi)'
SELECT (SELECT count(*) FROM voice_agency_settings WHERE enabled) AS agenzie_accese,
       (SELECT count(*) FROM voice_commands) AS comandi;

\echo '== 4. salute del CRM: confrontare con il passo 1'
SELECT (SELECT count(*) FROM contacts) AS contatti, (SELECT count(*) FROM properties) AS immobili,
       (SELECT count(*) FROM buildings) AS edifici, (SELECT count(*) FROM appointments) AS appuntamenti,
       (SELECT count(*) FROM leads) AS lead, (SELECT count(*) FROM property_contacts) AS legami_immobile,
       (SELECT count(*) FROM activities) AS attivita, (SELECT count(*) FROM tasks) AS task;

ROLLBACK;
