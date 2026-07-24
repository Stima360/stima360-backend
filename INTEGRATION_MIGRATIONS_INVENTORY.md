# INTEGRATION 0.1 — Inventario migrazioni (analisi statica)

> Fonte: pacchetti validati disponibili. Lo stato di applicazione nel DB TEST non è dedotto dall’esistenza delle tabelle.

## Ordine effettivo rilevato

1. `001_core_contacts_leads.sql` — crea: activities, contact_roles, contacts, lead_stime, leads, tasks; altera: —; rollback: presente
2. `002_property_01.sql` — crea: properties, property_contacts, property_documents, property_leads, property_photos, property_visits; altera: —; rollback: presente
3. `003_property_02.sql` — crea: property_price_history, property_status_history; altera: —; rollback: presente
4. `004_buy_01.sql` — crea: buy_request_features, buy_request_locations, buy_request_typologies, buy_requests; altera: —; rollback: presente
5. `005_match_01.sql` — crea: match_exclusions, match_requirement_results, match_runs, matches; altera: —; rollback: presente
6. `006_buy_02.sql` — crea: buy_request_history, buy_request_interactions, buy_request_task_links; altera: buy_requests; rollback: presente
7. `007_match_02.sql` — crea: match_feedback, match_refresh_history; altera: matches; rollback: presente
8. `008_flow_01.sql` — crea: flow_action_records, flow_events, flow_executions, flow_rules, flow_suppressions; altera: —; rollback: presente
9. `009_owner_01.sql` — crea: owner_access_tokens, owner_accounts, owner_audit_log, owner_feedback, owner_property_access, owner_publication_reads, owner_publications, owner_sessions; altera: —; rollback: presente

## Anomalia nominale rilevata

- La sequenza numerica è continua 001–009, ma l’ordine funzionale è: `004_buy_01`, `005_match_01`, `006_buy_02`, `007_match_02`.
- Non è un errore di per sé; va confrontato con l’eventuale ledger reale.

## Stato di applicazione

- `NON VERIFICATO LIVE`: richiede esecuzione read-only di `run_integration_01_schema_check.py` sul backend TEST.
- Nessun ledger migrazioni è presente nei pacchetti esaminati; se assente anche nel DB, l’applicazione storica non sarà dimostrabile con certezza.
