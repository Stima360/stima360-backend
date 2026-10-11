-- STIMA Voice Fase 7 - passo 1/3: fotografia del CRM TEST PRIMA di ogni intervento.
-- SOLA LETTURA (BEGIN READ ONLY ... ROLLBACK). Nessun nome di persona stampato.
-- Uso: psql ... -v ON_ERROR_STOP=1 -v pilot=<id operatore pilota> -f questo_file
BEGIN READ ONLY;

\echo '== 1. database e orologio'
SELECT current_database() AS database, now() AS adesso, current_setting('TimeZone') AS fuso,
       split_part(version(), ' ', 2) AS postgres;

\echo '== 2. ultime migrazioni (atteso: ultima 097, 098 e 099 assenti)'
SELECT version, applied_at, rolled_back_at FROM schema_migrations ORDER BY version DESC LIMIT 5;
SELECT count(*) AS voice_in_registro FROM schema_migrations WHERE version LIKE '09%voice%';

\echo '== 3. oggetti Voice gia'' presenti (atteso: tutti 0)'
SELECT (SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name LIKE 'voice\_%') AS tabelle,
       (SELECT count(*) FROM pg_proc WHERE proname LIKE 'voice\_%') AS funzioni,
       (SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'trg\_voice\_%') AS trigger;

\echo '== 4. tabelle e colonne su cui poggiano 098, 099 e le operazioni vocali (atteso: mancanti = 0)'
WITH attese(tabella, colonna) AS (VALUES
  ('agencies', 'id'), ('operator_users', 'id'), ('agency_memberships', 'role'),
  ('contacts', 'deleted_at'), ('contacts', 'assigned_agent_id'), ('contacts', 'phone_normalized'),
  ('properties', 'record_kind'), ('properties', 'client_request_id'), ('properties', 'deleted_at'),
  ('properties', 'assigned_agent_id'), ('properties', 'building_id'),
  ('buildings', 'units_declared'), ('buildings', 'client_request_id'), ('buildings', 'deleted_at'),
  ('appointments', 'assigned_user_id'), ('appointments', 'appointment_type'), ('leads', 'pipeline'),
  ('property_contacts', 'role'), ('activities', 'metadata'), ('tasks', 'metadata'),
  ('record_lifecycle_events', 'entity_type'), ('calendar_connections', 'status'))
SELECT count(*) FILTER (WHERE c.column_name IS NULL) AS mancanti,
       string_agg(a.tabella || '.' || a.colonna, ', ') FILTER (WHERE c.column_name IS NULL) AS elenco
  FROM attese a LEFT JOIN information_schema.columns c
    ON c.table_schema = 'public' AND c.table_name = a.tabella AND c.column_name = a.colonna;

\echo '== 5. agenzie e operatori attivi per ruolo (solo conteggi)'
SELECT a.id, a.slug, m.role, count(*) AS operatori_attivi
  FROM agencies a JOIN agency_memberships m ON m.agency_id = a.id AND m.status = 'active'
  JOIN operator_users u ON u.id = m.operator_user_id AND u.status = 'active'
 GROUP BY a.id, a.slug, m.role ORDER BY a.id, m.role;

\echo '== 6. il pilota (atteso: una riga, attivo, Google non collegato, agenda libera domani 15-16)'
SELECT u.id, u.status AS utente, m.agency_id, m.role, m.status AS membership,
       (SELECT count(*) FROM calendar_connections c WHERE c.user_id = u.id AND c.status <> 'disconnected') AS google_collegato,
       (SELECT count(*) FROM appointments ap WHERE ap.assigned_user_id = u.id AND ap.agency_id = m.agency_id
           AND ap.status IN ('requested', 'scheduled', 'confirmed')
           AND ap.start_at < (((now() AT TIME ZONE 'Europe/Rome')::date + 1) + time '16:00') AT TIME ZONE 'Europe/Rome'
           AND ap.end_at > (((now() AT TIME ZONE 'Europe/Rome')::date + 1) + time '15:00') AT TIME ZONE 'Europe/Rome') AS impegni_domani_15_16
  FROM operator_users u JOIN agency_memberships m ON m.operator_user_id = u.id
 WHERE u.id = :pilot;

\echo '== 7. salute del CRM: conteggi da confrontare dopo ogni passo'
SELECT (SELECT count(*) FROM contacts) AS contatti, (SELECT count(*) FROM properties) AS immobili,
       (SELECT count(*) FROM buildings) AS edifici, (SELECT count(*) FROM appointments) AS appuntamenti,
       (SELECT count(*) FROM leads) AS lead, (SELECT count(*) FROM property_contacts) AS legami_immobile,
       (SELECT count(*) FROM activities) AS attivita, (SELECT count(*) FROM tasks) AS task,
       (SELECT count(*) FROM operator_sessions WHERE revoked_at IS NULL AND expires_at > now()) AS sessioni_vive;

\echo '== 8. residui sintetici di run precedenti (atteso: 0 e 0)'
SELECT (SELECT count(*) FROM contacts WHERE last_name ILIKE 'Collaudovoce%' AND deleted_at IS NULL) AS contatti_attivi,
       (SELECT count(*) FROM buildings WHERE address ILIKE 'Via Collaudo Voce%'
           AND (to_jsonb(buildings)->>'deleted_at') IS NULL) AS edifici_attivi;

ROLLBACK;
