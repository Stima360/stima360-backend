BEGIN;

-- Rollback only the additive CORE module. Legacy tables and data are untouched.
DROP TABLE IF EXISTS tasks;
DROP TABLE IF EXISTS activities;
DROP TABLE IF EXISTS lead_stime;
DROP TABLE IF EXISTS leads;
DROP TABLE IF EXISTS contact_roles;
DROP TABLE IF EXISTS contacts;

COMMIT;
