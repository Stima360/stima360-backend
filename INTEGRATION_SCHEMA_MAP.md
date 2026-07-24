# INTEGRATION 0.1 — Schema atteso e dipendenze statiche

> Analisi dei file SQL 001–009. Lo schema reale sarà scritto dallo script read-only eseguito sul DB TEST.

## `001_core_contacts_leads.sql`
- Tabelle create: `activities`, `contact_roles`, `contacts`, `lead_stime`, `leads`, `tasks`
- Tabelle alterate: nessuna

## `002_property_01.sql`
- Tabelle create: `properties`, `property_contacts`, `property_documents`, `property_leads`, `property_photos`, `property_visits`
- Tabelle alterate: nessuna

## `003_property_02.sql`
- Tabelle create: `property_price_history`, `property_status_history`
- Tabelle alterate: nessuna

## `004_buy_01.sql`
- Tabelle create: `buy_request_features`, `buy_request_locations`, `buy_request_typologies`, `buy_requests`
- Tabelle alterate: nessuna

## `005_match_01.sql`
- Tabelle create: `match_exclusions`, `match_requirement_results`, `match_runs`, `matches`
- Tabelle alterate: nessuna

## `006_buy_02.sql`
- Tabelle create: `buy_request_history`, `buy_request_interactions`, `buy_request_task_links`
- Tabelle alterate: `buy_requests`

## `007_match_02.sql`
- Tabelle create: `match_feedback`, `match_refresh_history`
- Tabelle alterate: `matches`

## `008_flow_01.sql`
- Tabelle create: `flow_action_records`, `flow_events`, `flow_executions`, `flow_rules`, `flow_suppressions`
- Tabelle alterate: nessuna

## `009_owner_01.sql`
- Tabelle create: `owner_access_tokens`, `owner_accounts`, `owner_audit_log`, `owner_feedback`, `owner_property_access`, `owner_publication_reads`, `owner_publications`, `owner_sessions`
- Tabelle alterate: nessuna

## Riferimenti FK dichiarati nei SQL

- `001_core_contacts_leads.sql` → `contacts.id`
- `001_core_contacts_leads.sql` → `contacts.id`
- `001_core_contacts_leads.sql` → `leads.id`
- `001_core_contacts_leads.sql` → `stime.id`
- `001_core_contacts_leads.sql` → `contacts.id`
- `001_core_contacts_leads.sql` → `leads.id`
- `001_core_contacts_leads.sql` → `stime.id`
- `001_core_contacts_leads.sql` → `contacts.id`
- `001_core_contacts_leads.sql` → `leads.id`
- `001_core_contacts_leads.sql` → `stime.id`
- `002_property_01.sql` → `properties.id`
- `002_property_01.sql` → `contacts.id`
- `002_property_01.sql` → `properties.id`
- `002_property_01.sql` → `leads.id`
- `002_property_01.sql` → `properties.id`
- `002_property_01.sql` → `properties.id`
- `002_property_01.sql` → `properties.id`
- `002_property_01.sql` → `contacts.id`
- `002_property_01.sql` → `leads.id`
- `003_property_02.sql` → `properties.id`
- `003_property_02.sql` → `properties.id`
- `004_buy_01.sql` → `contacts.id`
- `004_buy_01.sql` → `leads.id`
- `004_buy_01.sql` → `buy_requests.id`
- `004_buy_01.sql` → `buy_requests.id`
- `004_buy_01.sql` → `buy_requests.id`
- `005_match_01.sql` → `buy_requests.id`
- `005_match_01.sql` → `properties.id`
- `005_match_01.sql` → `buy_requests.id`
- `005_match_01.sql` → `properties.id`
- `005_match_01.sql` → `match_runs.id`
- `005_match_01.sql` → `match_runs.id`
- `005_match_01.sql` → `buy_requests.id`
- `005_match_01.sql` → `properties.id`
- `006_buy_02.sql` → `buy_requests.id`
- `006_buy_02.sql` → `matches.id`
- `006_buy_02.sql` → `properties.id`
- `006_buy_02.sql` → `property_visits.id`
- `006_buy_02.sql` → `buy_requests.id`
- `006_buy_02.sql` → `tasks.id`
- `006_buy_02.sql` → `buy_requests.id`
- `006_buy_02.sql` → `matches.id`
- `006_buy_02.sql` → `properties.id`
- `006_buy_02.sql` → `tasks.id`
- `007_match_02.sql` → `matches.id`
- `007_match_02.sql` → `match_runs.id`
- `007_match_02.sql` → `match_runs.id`
- `007_match_02.sql` → `matches.id`
- `008_flow_01.sql` → `flow_events.id`
- `008_flow_01.sql` → `flow_rules.id`
- `008_flow_01.sql` → `flow_executions.id`
- `008_flow_01.sql` → `flow_executions.id`
- `008_flow_01.sql` → `flow_rules.id`
- `009_owner_01.sql` → `contacts.id`
- `009_owner_01.sql` → `owner_accounts.id`
- `009_owner_01.sql` → `properties.id`
- `009_owner_01.sql` → `owner_accounts.id`
- `009_owner_01.sql` → `owner_accounts.id`
- `009_owner_01.sql` → `properties.id`
- `009_owner_01.sql` → `owner_publications.id`
- `009_owner_01.sql` → `owner_publications.id`
- `009_owner_01.sql` → `owner_publications.id`
- `009_owner_01.sql` → `owner_accounts.id`
- `009_owner_01.sql` → `owner_accounts.id`
- `009_owner_01.sql` → `properties.id`
- `009_owner_01.sql` → `activities.id`
- `009_owner_01.sql` → `owner_accounts.id`
- `009_owner_01.sql` → `properties.id`

## Confini intermodulo preliminari

- CORE è il nucleo per contatti, lead, attività e task.
- PROPERTY dipende da CORE per proprietari/lead.
- BUY dipende da CORE per contatti/lead e può collegare task.
- MATCH dipende da BUY e PROPERTY.
- FLOW legge CORE/PROPERTY/BUY/MATCH e scrive azioni tracciate in CORE.
- OWNER dipende da CORE e PROPERTY; non deve dipendere direttamente da BUY/MATCH in 0.1.

## Schema reale

- `NON VERIFICATO LIVE`: eseguire lo script sul DB TEST.
