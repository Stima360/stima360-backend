-- Down for 058. Removes the territory domain and nothing else.
--
-- 058 created two tables and four indexes, and altered nothing that existed
-- before it. The inverse is therefore exact: drop what it created, in
-- dependency order, and leave every P26 and P27-1 object untouched.
--
-- WHAT DROPPING THESE TABLES MEANS
--
-- It destroys the map of the network: which territories were declared and who
-- held them. `platform_audit_log` keeps the record of the ADMINISTRATIVE ACTS
-- that produced them - who assigned what, and when - so the history of the
-- decisions survives this file. The current state does not.
--
-- Nothing outside these two tables is affected. No other table references
-- them, and the two foreign keys they hold point OUTWARD, at
-- network_territories and agencies: dropping the child releases them and
-- changes nothing in `agencies`, which is why the assignments go first.
--
-- Down files bracket their own transaction; the runner owns none here.

BEGIN;

-- The child first. Dropping it releases both foreign keys; doing it the other
-- way round would require CASCADE, and CASCADE in a down file is how a table
-- nobody meant to touch disappears.
--
-- The indexes go with their tables; naming them would be noise. Neither DROP
-- carries CASCADE deliberately: nothing should depend on these tables, and if
-- something does, this file must fail and say so rather than quietly take that
-- something with it.
DROP TABLE IF EXISTS agency_territory_assignments;

DROP TABLE IF EXISTS network_territories;

DELETE FROM schema_migrations WHERE version = '058_p27_network_territories';

COMMIT;
