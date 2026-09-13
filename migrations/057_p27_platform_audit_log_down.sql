-- Down for 057. Removes the platform audit foundation and nothing else.
--
-- 057 created one table, three indexes, two functions and two triggers, and
-- altered nothing that existed before it. The inverse is therefore exact: drop
-- what it created, in dependency order, and leave every P26 object untouched.
--
-- WHAT DROPPING THIS TABLE MEANS
--
-- It destroys the register. That is the honest description, and it is why this
-- file exists rather than refusing: 057 is reversible in the schema sense, and
-- a down that lies about being reversible is worse than one that deletes.
--
-- Before running it on an environment where the platform surface has actually
-- been used, export the table. The rows cannot be reconstructed from anything
-- else in the database - that is the entire point of an append-only register.
--
-- What it does NOT mean is any effect on another table. The register holds no
-- foreign key at all (see the up file), so nothing anywhere references it and
-- nothing it references is disturbed by its removal. Dropping it subtracts the
-- history and touches no other row in the database.
--
-- The triggers are dropped before the functions, and the functions before the
-- table: DROP TABLE would take its own triggers with it, but not the two
-- functions, which are schema-level objects and would survive as orphans that
-- the next attempt to apply 057 would then CREATE OR REPLACE rather than
-- create - a difference no one would notice until it mattered.
--
-- Down files bracket their own transaction; the runner owns none here.

BEGIN;

DROP TRIGGER IF EXISTS trg_platform_audit_log_append_only ON platform_audit_log;
DROP TRIGGER IF EXISTS trg_platform_audit_log_no_truncate ON platform_audit_log;

DROP FUNCTION IF EXISTS platform_audit_log_append_only();
DROP FUNCTION IF EXISTS platform_audit_log_no_truncate();

-- The three indexes go with the table; naming them would be noise. The table
-- is dropped without CASCADE deliberately: nothing should depend on it, and if
-- something does, this file must fail and say so rather than quietly take that
-- something with it.
DROP TABLE IF EXISTS platform_audit_log;

DELETE FROM schema_migrations WHERE version = '057_p27_platform_audit_log';

COMMIT;
