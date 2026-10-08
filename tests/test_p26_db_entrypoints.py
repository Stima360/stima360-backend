"""H11 - the database connection choke point must not regress.

The P26 roadmap ends with PostgreSQL Row Level Security as a final layer of
defence. RLS is only meaningful if the sessions it applies to are the sessions
the application actually opens, which means the set of places that build a
connection has to stay known and small.

This module pins that set. Every file permitted to call ``psycopg2.connect`` is
listed below with the reason it is allowed. A new connection site anywhere else
fails these tests, and the fix is to route it through
``database.get_connection`` or to add it here with an explicit justification
and a matching entry in ``docs/P26_DB_ENTRYPOINTS.md``.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT_DOC = ROOT / "docs" / "P26_DB_ENTRYPOINTS.md"

# The single runtime choke point. Everything the application serves goes here.
RUNTIME_CHOKE_POINT = "database.py"

# Files allowed to build their own connection, each with its justification.
# Adding an entry is a deliberate act that must be mirrored in the inventory
# document, which the tests below verify.
ALLOWED_CONNECTION_SITES: dict[str, str] = {
    "database.py": (
        "the runtime choke point itself; every application path resolves here"
    ),
    "integration_p2_support.py": (
        "guarded diagnostic helper; require_test_environment() checks database "
        "name, backend host and branch before connecting"
    ),
    "run_flow_01_e2e.py": (
        "TEST-only end-to-end script; refuses a DB_NAME without the test marker"
    ),
    "run_buy_021_e2e.py": (
        "TEST-only end-to-end script; verifies current_database() and the "
        "backend endpoint before doing anything"
    ),
    "migrate_add_token.py": (
        "legacy, unguarded, and known dead: it targets a table named 'stima' "
        "which does not exist. Recorded as a risk to neutralise before RLS; "
        "explicitly NOT modified in P26-0 until Render, cron and runbook usage "
        "have been ruled out"
    ),
    "scripts/p26_schema_snapshot.py": (
        "privileged migration channel, read-only; opens a read-only "
        "transaction against TEST and refuses production database names"
    ),
    "scripts/p26_migrate.py": (
        "privileged migration channel; the migrator role legitimately sits "
        "outside RLS, and the runner refuses production database names"
    ),
    "tests/conftest.py": (
        "test bootstrap; replaces psycopg2.connect with a stub and opens no "
        "connection"
    ),
    "tests/test_core_service_regressions.py": (
        "test stub; replaces psycopg2.connect and opens no connection"
    ),
    "tests/test_p26_6c_flow_immutability_pg.py": (
        "TEST-only live isolation proof; opts in through P26_PG_DSN and skips "
        "entirely without it, asserts current_database() contains 'test' before "
        "touching anything, writes only negative fixture ids inside a SAVEPOINT "
        "and rolls the connection back. P26-6C's agency-immutability guards are "
        "PL/pgSQL triggers, so nothing but a real PostgreSQL can prove them"
    ),
    "tests/test_p27_6_postgres_real.py": (
        "TEST-only throwaway cluster; it does not connect to any deployed "
        "database at all. It runs initdb in a temporary directory, starts a "
        "server on a unix socket with listen_addresses='', creates its own "
        "database, applies four migrations and deletes the whole directory at "
        "teardown. It reads no connection environment variable, so it cannot "
        "reach TEST or PROD even by accident, and it skips entirely when the "
        "PostgreSQL binaries are absent. A unique index on an expression, a "
        "RESTRICT foreign key and COUNT(*) OVER () are things only a real "
        "PostgreSQL can prove"
    ),
    "tests/test_p28_acting_postgres_real.py": (
        "TEST-only throwaway cluster, identical in shape to "
        "tests/test_p27_6_postgres_real.py: initdb in a temporary directory, a "
        "server on a unix socket with listen_addresses='', its own database, "
        "and the whole directory deleted at teardown. It reads no connection "
        "environment variable, so it cannot reach TEST or PROD even by "
        "accident, and it skips entirely when the PostgreSQL binaries are "
        "absent. A CHECK across two columns, a RESTRICT foreign key proven by "
        "actually deleting the parent, and an idempotent ALTER TABLE are "
        "things only a real PostgreSQL can prove"
    ),
    "tests/test_p29_1_consent_postgres.py": (
        "TEST-only integration connection. Disabled unless P29_TEST_DSN is "
        "explicitly supplied. Connects only to a disposable PostgreSQL used to "
        "verify P29 migrations and database invariants; it is not an "
        "application/RLS database entrypoint. It creates and drops its own "
        "database on that server and touches nothing else, and it must not go "
        "through database.get_connection(): that is the application choke "
        "point and must stay uncoupled from a throwaway test database. What "
        "only a real PostgreSQL can prove here is that a BEFORE DELETE row "
        "trigger also fires on an ON DELETE CASCADE, that the purge exception "
        "in 062 tells the two cases apart, and that ENABLE ALWAYS survives "
        "session_replication_role"
    ),
    "tests/test_p29_2_1_communication_postgres.py": (
        "TEST-only integration connection. Disabled unless P29_TEST_DSN is "
        "explicitly supplied, exactly like the P29-1.1 module above: without "
        "that variable the whole module is skipped, so it never reaches TEST "
        "or PROD from a development machine. Connects only to a disposable "
        "PostgreSQL used to verify migration 064 and its database invariants; "
        "it is not an application/RLS database entrypoint. It creates and "
        "drops its own database on that server and touches nothing else, and "
        "it must not go through database.get_connection(): that is the "
        "application choke point and must stay uncoupled from a throwaway test "
        "database. What only a real PostgreSQL can prove here is that the "
        "purge cascade crosses TWO BEFORE DELETE guards in a row - contacts to "
        "communication_messages to communication_attempts - and that the "
        "three-column UNIQUE on the attempts admits exactly one regular and "
        "one late row per attempt"
    ),
    "tests/test_p29_2_2_communication_postgres.py": (
        "TEST-only integration connection. Disabled unless P29_TEST_DSN is "
        "explicitly supplied, exactly like the two P29 modules above: without "
        "that variable the whole module is skipped, so it never reaches TEST "
        "or PROD from a development machine. Connects only to a disposable "
        "PostgreSQL where it applies 064 to verify the P29-2.2 repository and "
        "service against a real database; it is not an application/RLS "
        "database entrypoint. It creates and drops its own database on that "
        "server and touches nothing else, and it must not go through "
        "database.get_connection(): that is the application choke point and "
        "must stay uncoupled from a throwaway test database. What only a real "
        "PostgreSQL can prove here is that enqueue composes into the caller's "
        "transaction - a rollback takes the message with it - and that ON "
        "CONFLICT on the per-tenant unique index gives one message for a "
        "repeated key and two for the same key in two agencies. The companion "
        "module tests/test_p29_2_2_communication_service.py opens no "
        "connection and is deliberately NOT listed here"
    ),
    "tests/test_p29_2_3_claim_postgres.py": (
        "TEST-only integration connection. Disabled unless P29_TEST_DSN is "
        "explicitly supplied, exactly like the P29 modules above: without that "
        "variable the whole module is skipped, so it never reaches TEST or PROD "
        "from a development machine. Connects only to a disposable PostgreSQL "
        "where it applies 064 to verify the P29-2.3 claim, fencing and stale "
        "recovery; it is not an application/RLS database entrypoint. It creates "
        "and drops its own database on that server and touches nothing else, "
        "and it must not go through database.get_connection(): that is the "
        "application choke point and must stay uncoupled from a throwaway test "
        "database. It opens SEVERAL connections to that database on purpose - "
        "two concurrent workers cannot be simulated on one connection, because "
        "a lock never blocks itself - which is the only way to prove FOR UPDATE "
        "SKIP LOCKED, the compare-and-set rowcount and the trigger that refuses "
        "to reopen a closed attempt. Its companion "
        "tests/test_p29_2_3_claim_sentinels.py opens no connection and is "
        "deliberately NOT listed here"
    ),
    "tests/test_p29_2_4_dispatch_postgres.py": (
        "TEST-only integration connection. Disabled unless P29_TEST_DSN is "
        "explicitly supplied, exactly like the P29 modules above: without that "
        "variable the whole module is skipped, so it never reaches TEST or PROD "
        "from a development machine. Connects only to a disposable PostgreSQL "
        "where it applies 061-064 to verify the P29-2.4 consent gate: the "
        "closure criterion of the design - a revocation between enqueue and "
        "dispatch suppresses the message, with the exact reason and through its "
        "own compare-and-set - can only be proved against a real database, "
        "because it is the consent projection and the CAS rowcount that decide "
        "it. It is not an application/RLS database entrypoint. It creates and "
        "drops its own database on that server and touches nothing else, and it "
        "must not go through database.get_connection(): that is the application "
        "choke point and must stay uncoupled from a throwaway test database. It "
        "monkeypatches communication_cursor and consent_cursor onto its own "
        "connection precisely so that no domain module opens a second one "
        "towards a real environment. Its companion "
        "tests/test_p29_2_4_dispatch_sentinels.py opens no connection and is "
        "deliberately NOT listed here"
    ),
    "tests/test_p29_2_5e_email_postgres.py": (
        "TEST-only integration connection. Disabled unless P29_TEST_DSN is "
        "explicitly supplied, exactly like the P29 modules above: without that "
        "variable the whole module is skipped, so it never reaches TEST or PROD "
        "from a development machine. It is registered in its own right rather "
        "than reusing the P29-2.4 entry: P29-2.4 is closed, and an allow-list "
        "shared between phases would let a new entrypoint arrive unannounced. "
        "Connects only to a disposable PostgreSQL where it applies 061-064 to "
        "verify the P29-2.5E email adapter end to end: that the arguments handed "
        "to database.invia_mail are exactly the three columns of a real claimed "
        "row, and that an unsuccessful send lands as indeterminate and never as "
        "failed. It is not an application/RLS database entrypoint. It creates and "
        "drops its own database on that server and touches nothing else, and it "
        "must not go through database.get_connection(): that is the application "
        "choke point and must stay uncoupled from a throwaway test database. It "
        "monkeypatches communication_cursor and consent_cursor onto its own "
        "connection so that no domain module opens a second one, and it always "
        "replaces database.invia_mail with a spy, so no email is ever sent. Its "
        "companion tests/test_p29_2_5e_email_adapter.py opens no connection and "
        "is deliberately NOT listed here"
    ),
    "tests/test_p29_2_6e_channel_postgres.py": (
        "TEST-only integration connection. Disabled unless P29_TEST_DSN is "
        "explicitly supplied, exactly like the P29 modules above: without that "
        "variable the whole module is skipped, so it never reaches TEST or PROD "
        "from a development machine. It is registered in its own right rather "
        "than reusing an earlier P29 entry: those phases are closed, and an "
        "allow-list shared between phases would let a new entrypoint arrive "
        "unannounced. Connects only to a disposable PostgreSQL where it applies "
        "061-064 to verify the P29-2.6E channel filter: that a worker claiming "
        "channel='email' leaves a queued whatsapp message completely untouched - "
        "status, attempt_count, claim_token and the absence of any attempt row - "
        "which can only be measured against real rows and a real FOR UPDATE SKIP "
        "LOCKED. It is not an application/RLS database entrypoint. It creates and "
        "drops its own database on that server and touches nothing else, and it "
        "must not go through database.get_connection(): that is the application "
        "choke point and must stay uncoupled from a throwaway test database. It "
        "monkeypatches communication_cursor and consent_cursor onto its own "
        "connection and always replaces database.invia_mail with a spy, so no "
        "email is ever sent. Its companion "
        "tests/test_p29_2_6e_channel_routing.py opens no connection and is "
        "deliberately NOT listed here"
    ),
    "tests/test_p29_2_6e_lifecycle_postgres.py": (
        "TEST-only integration connection. Disabled unless P29_TEST_DSN is "
        "explicitly supplied, exactly like the P29 modules above: without that "
        "variable the whole module is skipped, so it never reaches TEST or PROD "
        "from a development machine. It is registered in its own right rather "
        "than reusing an earlier P29 entry: an allow-list shared between phases "
        "would let a new entrypoint arrive unannounced. Connects only to "
        "disposable PostgreSQL databases where it applies 064 and 065 to certify "
        "the lifecycle parent model: that a direct DELETE on the ledger stays "
        "refused with and without a contact, that deleting a stima leaves a "
        "linked message alive with stima_id NULL but takes a contactless one "
        "away, that the tenant purge leaves no residue and no dangling "
        "agency_id, and that the down migration refuses instead of destroying. "
        "None of that can be measured anywhere but on real rows with real "
        "triggers. It creates SEVERAL databases on purpose - the down migration "
        "dismantles the schema, so it runs on one of its own - and drops each at "
        "teardown, touching nothing else. It must not go through "
        "database.get_connection(): that is the application choke point and must "
        "stay uncoupled from a throwaway test database. Its companion "
        "tests/test_p29_2_6e_lifecycle_parent.py opens no connection and is "
        "deliberately NOT listed here"
    ),
    "tests/test_p29_2_6e_dispatch_ops_postgres.py": (
        "TEST-only integration connection. Disabled unless P29_TEST_DSN is "
        "explicitly supplied: without that variable the whole module is "
        "skipped, so it never reaches TEST or PROD from a development machine. "
        "Registered in its own right rather than reusing an earlier P29 entry: "
        "an allow-list shared between phases would let a new entrypoint arrive "
        "unannounced. It is the only P29 module that drives the REAL "
        "application - main.app with its routers mounted - over HTTP, because "
        "what it certifies is the operational chain that no unit test can "
        "reach: a least-privilege agent operator logs in, the session cookie "
        "carries the agency, the dispatch route claims one channel and the "
        "email adapter sends, then the session is revoked. It applies 061-065 "
        "to a disposable PostgreSQL it creates and drops itself, touching "
        "nothing else, and it must not go through database.get_connection(): "
        "that is the application choke point and must stay uncoupled from a "
        "throwaway test database. It monkeypatches the communication, consent "
        "and operator_auth cursors onto its own connection - operator_cursor in "
        "all four modules that import it by name, or the login would open a "
        "second connection towards a real environment - and always replaces "
        "database.invia_mail with a spy, so no email is ever sent"
    ),
    "tests/test_p20_property_watch_postgres.py": (
        "TEST-only integration connection. Disabled unless P29_TEST_DSN is "
        "explicitly supplied: without that variable the whole module is "
        "skipped, so it never reaches TEST or PROD from a development machine. "
        "Registered in its own right rather than reusing a P29 entry: an "
        "allow-list shared between phases would let a new entrypoint arrive "
        "unannounced. Connects only to a disposable PostgreSQL that it creates "
        "and drops itself, where it applies 022-025 and 046-048 to prove the "
        "PW-FIX: that the ctx-less watch writer the public funnel reaches "
        "through safe_ensure_watch_for_stima derives agency_id from the "
        "persisted stima, is idempotent on a second call, and that the 048 "
        "trigger still refuses a watch in another agency. A NOT NULL refusal "
        "swallowed by a fail-open wrapper is exactly what a fake cursor cannot "
        "show. It must not go through database.get_connection(): that is the "
        "application choke point and must stay uncoupled from a throwaway "
        "test database"
    ),
    "tests/test_lmc1a_owner_provisioning_postgres.py": (
        "TEST-only integration connection. Disabled unless P29_TEST_DSN is "
        "explicitly supplied: without that variable the whole module is "
        "skipped, so it never reaches TEST or PROD from a development machine. "
        "Registered in its own right rather than reusing another entry: an "
        "allow-list shared between phases would let a new entrypoint arrive "
        "unannounced. Connects only to a disposable PostgreSQL that it creates "
        "and drops itself, where it applies 009 and 066 to prove LMC-1A: that "
        "the owner/stima grant trigger refuses a contact of one agency and an "
        "estimation of another, that the UNIQUE denies a duplicate grant, that "
        "the automatic provisioning is idempotent on a second run - one "
        "account, one grant, one audit row each - and that the 066 down "
        "removes exactly what the up created. It must not go through "
        "database.get_connection(): that is the application choke point and "
        "must stay uncoupled from a throwaway test database"
    ),
    "tests/test_lmc1b_owner_login_link_postgres.py": (
        "TEST-only integration connection. Disabled unless P29_TEST_DSN is "
        "explicitly supplied: without that variable the whole module is "
        "skipped, so it never reaches TEST or PROD from a development machine. "
        "Registered in its own right rather than reusing another entry: an "
        "allow-list shared between phases would let a new entrypoint arrive "
        "unannounced. Connects only to a disposable PostgreSQL that it creates "
        "and drops itself, where it applies 009, 064, 065, 066 and 067 to "
        "prove LMC-1B: that the database admits owner_login_link only after "
        "067 and still refuses an invented reason code, that a login token "
        "lasts thirty minutes and is stored as a hash, that the rate limit "
        "counts real rows, that a failed enqueue takes its token down with it "
        "because both live in one transaction, and that the down refuses while "
        "login messages exist. It must not go through "
        "database.get_connection(): that is the application choke point and "
        "must stay uncoupled from a throwaway test database"
    ),
    "tests/test_lmc2_owner_homes_postgres.py": (
        "TEST-only integration connection. Disabled unless P29_TEST_DSN is "
        "explicitly supplied: without that variable the whole module is "
        "skipped, so it never reaches TEST or PROD from a development machine. "
        "Registered in its own right rather than reusing another entry: an "
        "allow-list shared between phases would let a new entrypoint arrive "
        "unannounced. Connects only to a disposable PostgreSQL that it creates "
        "and drops itself, where it applies 009, 022 and 066 to prove LMC-2: "
        "that a revoked, expired, foreign-owner or foreign-tenant home is "
        "refused with the SAME neutral 404 as one that does not exist, that "
        "the initial value comes from the watch baseline and falls back to the "
        "tenant-scoped stima_completata event, that reading writes nothing, "
        "and that no buyer or CRM datum reaches the owner. It must not go "
        "through database.get_connection(): that is the application choke "
        "point and must stay uncoupled from a throwaway test database"
    ),
    "tests/test_lmc9_consultation_request_postgres.py": (
        "TEST-only LMC-9 proof; opts in through P29_TEST_DSN and skips "
        "entirely without it. Creates and drops its own throwaway database "
        "and never touches an existing schema. The connection exists to seed "
        "grants and then read seller_timeline_events back, which is the only "
        "way to prove that a double submit on the same UTC day leaves one "
        "row, that the next day opens a new one, that every shape of missing "
        "access writes nothing and answers identically, and - the point of "
        "the phase - that a failed write answers 503 rather than a false "
        "success"
    ),
    "tests/test_lmc13_home_metrics_postgres.py": (
        "TEST-only LMC-13 proof; opts in through P29_TEST_DSN and skips "
        "entirely without it. Creates and drops its own throwaway database "
        "and never touches an existing schema. The connection exists to seed "
        "grants, owner accounts and owner_portal timeline events across two "
        "agencies and read the aggregate back, which is the only way to prove "
        "six things a double cannot: that a home with owner, co-owner and "
        "delegate counts as ONE home while its people count as three in "
        "activated_owners, that the cohort anchor is MIN(created_at) over the "
        "home's grants and the UTC window is half-open, that two distinct UTC "
        "days of the SAME owner make a return while one day each from two "
        "different owners does not, that one agency never sees the other's "
        "grants or events and a grant whose two roots stop agreeing leaves "
        "every count, that a revoked or expired grant leaves the historical "
        "cohort untouched while dropping out of active_homes_now, and that an "
        "empty cohort yields zero counts with null rates. It also proves the "
        "aggregate is a single query with twenty homes, so no N+1 can appear "
        "unnoticed. It must not go through database.get_connection(): that is "
        "the application choke point and must stay uncoupled from a throwaway "
        "test database"
    ),
    "tests/test_lmc15_acquisition_bridge_postgres.py": (
        "TEST-only LMC-15 proof; opts in through P29_TEST_DSN and skips "
        "entirely without it. Creates and drops its own throwaway database "
        "and never touches an existing schema. The connection exists to "
        "apply migration 070 and then exercise the acquisition bridge "
        "across two agencies, which is the only way to prove seven things a "
        "double cannot: that stima_id_snapshot is assigned by the database "
        "and ignores whatever the caller passed, and is immutable "
        "afterwards; that a property has exactly ONE active origin, "
        "enforced by a partial unique index and not by application code, "
        "while one estimation may legitimately produce several properties; "
        "that tenancy is derived - an estimation and a property of "
        "different agencies cannot be linked, an operator of another agency "
        "cannot act, and a platform admin in acting, who by design holds NO "
        "agency_membership, still can; that a half-revocation and a mandate "
        "without an author are UNREPRESENTABLE rather than merely "
        "discouraged; that hard-deleting an estimation neither fails nor "
        "removes the register, leaves the snapshot behind and keeps the "
        "orphaned link revocable, while a linked property and a signing "
        "operator cannot be deleted at all; that the fact and its timeline "
        "projection commit or fail together; and that the two new LMC-13 "
        "numerators count the right homes inside the right boundaries and "
        "only for cohorts that begin after measurement_started_at, which it "
        "reads from the migration ledger rather than from a hardcoded date. "
        "It must not go through database.get_connection(): that is the "
        "application choke point and must stay uncoupled from a throwaway "
        "test database"
    ),
    "tests/test_lmc12_home_notifications_postgres.py": (
        "TEST-only LMC-12 proof; opts in through P29_TEST_DSN and skips "
        "entirely without it. Creates and drops its own throwaway database "
        "and never touches an existing schema. The connection exists to "
        "apply migration 069 and then seed grants, valuation snapshots and "
        "buyer-pressure readings across two agencies and read "
        "owner_home_notifications and owner_audit_log back, which is the only "
        "way to prove six things a double cannot: that the down migration "
        "refuses to destroy real notifications and the up re-applies after an "
        "empty down, that the 069 trigger rejects an owner and an estimation "
        "of two different agencies even on a hand-written INSERT, that the "
        "idempotency key is refused by the database on the second run and that "
        "identical daily snapshots never add a row, that a run of one tenant "
        "never reads another tenant's grants and a revoked or expired grant "
        "makes the notification unreadable and unmarkable, that the "
        "owner:home_alerts session lock keeps a second run out while the "
        "LMC-11 lock does not, and that keyset pagination walks every grant "
        "with no starvation and no loop on a failing last element. It must "
        "not go through database.get_connection(): that is the application "
        "choke point and must stay uncoupled from a throwaway test database"
    ),
    "tests/test_lmc11_valuation_cron_postgres.py": (
        "TEST-only LMC-11 proof; opts in through P29_TEST_DSN and skips "
        "entirely without it. Creates and drops its own throwaway database "
        "and never touches an existing schema. The connection exists to seed "
        "watches across two agencies and then read property_watch_observations, "
        "property_watches, stime, owner_home_overrides and "
        "seller_timeline_events back, which is the only way to prove five "
        "things a double cannot: that a watch of another tenant - or one "
        "pointing at another tenant's estimation - is never read, that two "
        "runs on the same UTC day leave one snapshot while the next day opens "
        "a new one even at an identical price, that a real session-level "
        "advisory lock keeps a second run out and is released on the way out "
        "and on error, that the estimation, the owner overrides, the baseline "
        "and every snapshot already written stay byte-identical across a run, "
        "and that the owner's history really grows so change_30d turns from "
        "null into a number. It must not go through "
        "database.get_connection(): that is the application choke point and "
        "must stay uncoupled from a throwaway test database"
    ),
    "tests/test_lmc10_owner_home_update_postgres.py": (
        "TEST-only LMC-10 proof; opts in through P29_TEST_DSN and skips "
        "entirely without it. Creates and drops its own throwaway database "
        "and never touches an existing schema. The connection exists to "
        "apply migration 068 and then read information_schema, "
        "owner_home_overrides, property_watch_observations and "
        "seller_timeline_events back, which is the only way to prove four "
        "things a double cannot: that the override columns carry the same "
        "types as the columns of stime they override (locali is INTEGER "
        "there and VARCHAR in the namesake stime_dettagliate, so the wrong "
        "source would pass unnoticed), that two concurrent saves from the "
        "same version leave one written and one refused with nothing "
        "changed, that the down migration refuses to drop a table holding "
        "real owner corrections and succeeds on an empty one, and that the "
        "snapshot born after an update is computed by "
        "valuation.compute_from_payload on the EFFECTIVE profile while "
        "every snapshot already written stays byte-identical. It must not "
        "go through database.get_connection(): that is the application "
        "choke point and must stay uncoupled from a throwaway test database"
    ),
    "tests/test_lmc8_crm_radar_postgres.py": (
        "TEST-only LMC-8 proof; opts in through P29_TEST_DSN and skips "
        "entirely without it. Creates and drops its own throwaway database "
        "and never touches an existing schema. The connection exists to seed "
        "grants, estimations and owner events and then read the Contact 360 "
        "block back, which is the only way to prove that a namesake contact "
        "in another agency brings in no home, that a revoked grant removes "
        "one, and that the level shown is the one LMC-7 computes on the real "
        "rows rather than a copy that can drift"
    ),
    "tests/test_lmc7_owner_radar_postgres.py": (
        "TEST-only LMC-7 proof; opts in through P29_TEST_DSN and skips "
        "entirely without it. Creates and drops its own throwaway database "
        "and never touches an existing schema. The connection exists to seed "
        "leads, grants and Property Watch observations as fixtures and to "
        "read seller_timeline_events back, which is the only way to prove "
        "that a second view on the same UTC day writes no second row and "
        "does not alter the first, and that a revoked or expired grant "
        "records nothing at all"
    ),
    "tests/test_lmc4_buyer_demand_postgres.py": (
        "TEST-only LMC-4 proof; opts in through P29_TEST_DSN and skips "
        "entirely without it. Creates and drops its own throwaway database "
        "and never touches an existing schema. LMC-4 is read-only, so the "
        "connection exists to seed Buyer Pressure observations as fixtures "
        "and to count rows before and after a portal read, which is how the "
        "no-write guarantee and the tenancy boundary are proven at all"
    ),
    "tests/test_lmc3_valuation_snapshot_postgres.py": (
        "TEST-only integration connection. Disabled unless P29_TEST_DSN is "
        "explicitly supplied: without that variable the whole module is "
        "skipped, so it never reaches TEST or PROD from a development machine. "
        "Registered in its own right rather than reusing another entry: an "
        "allow-list shared between phases would let a new entrypoint arrive "
        "unannounced. Connects only to a disposable PostgreSQL that it creates "
        "and drops itself, where it applies 009, 022 and 066 to prove LMC-3: "
        "that a refresh writes one valuation_snapshot computed by the official "
        "engine, that repeating it the same day with the same input and the "
        "same algorithm writes nothing and overwrites nothing, that changed "
        "input or a changed fingerprint writes a NEW row beside the old one, "
        "that agency A cannot snapshot agency B's estimation, and that the "
        "owner read model reads those snapshots back. It must not go through "
        "database.get_connection(): that is the application choke point and "
        "must stay uncoupled from a throwaway test database"
    ),
    "tests/test_p29_cutover_email_cliente.py": (
        "TEST-only integration connection. Disabled unless P29_TEST_DSN is "
        "explicitly supplied: without that variable the whole module is "
        "skipped, so it never reaches TEST or PROD from a development machine. "
        "Registered in its own right rather than reusing the P29-2.6E OPS entry "
        "whose fixtures it borrows: an allow-list shared between modules would "
        "let a new entrypoint arrive unannounced. It opens exactly ONE extra "
        "connection, and the reason is the whole point of the module: the "
        "P29 cutover requires the `sent` of the customer email and the Seller "
        "Intelligence event to share ONE transaction, and a second connection "
        "is the only honest way to prove it - from inside the hook, that "
        "observer must see NEITHER row, because neither is committed yet. "
        "Proving atomicity from the writing connection would prove nothing, "
        "since it sees its own uncommitted work. The observer is read-only, "
        "autocommit, opened on the DSN of the disposable database the borrowed "
        "fixture created, and closed in a finally. It must not go through "
        "database.get_connection() for the same reason as the module above"
    ),
    "tests/test_p29_3c_orchestrator_postgres.py": (
        "TEST-only P29-3C proof; opts in through P29_TEST_DSN and skips "
        "entirely without it. Creates and drops its own throwaway database "
        "and never touches an existing schema. Registered in its own right "
        "rather than reusing the P29-3B entry: that phase is closed, and an "
        "allow-list shared between phases would let a new entrypoint arrive "
        "unannounced. It applies 061-067, then 070 and 071, because the "
        "journey engine can only be proven against the tables that really "
        "hold the facts it reacts to - the acquisition bridge for mandates "
        "and inspections, the ledger for sent steps, the consent domain for "
        "permission. It opens connections for three things a double cannot "
        "show: that two SIMULTANEOUS ticks on real connections neither "
        "enroll a contact twice nor queue a step twice, that two concurrent "
        "assisted sends produce one message, and that a stop and an enqueue "
        "racing on the same enrollment never leave a live message behind; "
        "that the whole chain works over HTTP against the real app - a real "
        "operator logs in, the session cookie carries the agency, an agent "
        "is refused a tick while an admin gets one, and a platform admin "
        "gets one only while acting; and that with migration 071 absent the "
        "journey routes answer 503 feature_not_migrated while every other "
        "P29 path keeps working. It monkeypatches get_connection in the "
        "database, communication, consent, core and operator_auth modules "
        "onto its own connection, and always replaces the email transport "
        "with a spy, so no message ever leaves. It must not go through "
        "database.get_connection(): that is the application choke point and "
        "must stay uncoupled from a throwaway test database"
    ),
    "tests/test_p29_3_journey_foundation_postgres.py": (
        "TEST-only P29-3B proof; opts in through P29_TEST_DSN and skips "
        "entirely without it. Creates and drops its own throwaway database "
        "and never touches an existing schema. Registered in its own right "
        "rather than reusing an earlier P29 entry: an allow-list shared "
        "between phases would let a new entrypoint arrive unannounced. The "
        "connection exists to apply 061-067 and then 071 and to prove what a "
        "double cannot: that the 071 down restores the post-065 ledger guard "
        "TEXTUALLY and refuses to run while an enrollment, a control or a "
        "journey message exists; that the enrollment and control state "
        "matrices, the one-open-enrollment-per-contact rule, the "
        "(journey, trigger) uniqueness and the step/run uniqueness of journey "
        "messages are enforced by CHECKs and partial unique indexes and not "
        "by application code; that stima_id_snapshot and the message "
        "provenance are assigned by the database and immutable afterwards; "
        "that tenancy is derived so an enrollment of another agency cannot be "
        "seen, paused or stopped, while a platform admin in acting still can; "
        "and that the public unsubscribe revokes the marketing consent once, "
        "idempotently, through the consent domain and never the service one. "
        "It monkeypatches get_connection in the database, communication, "
        "consent and core modules onto its own connection so no domain "
        "module opens a second one. It must not go through "
        "database.get_connection(): that is the application choke point and "
        "must stay uncoupled from a throwaway test database"
    ),
    "tests/test_a30_1_appointments_postgres.py": (
        "TEST-only A30-1 proof; opts in through P29_TEST_DSN and skips "
        "entirely without it. Creates and drops its own throwaway database "
        "and never touches an existing schema. Registered in its own right "
        "rather than reusing an earlier entry: an allow-list shared between "
        "phases would let a new entrypoint arrive unannounced. The connection "
        "exists to apply the LMC-15 chain and then 072 and to prove what a "
        "double cannot: that the btree_gist EXCLUDE constraint makes two "
        "blocking appointments of the same agent overlapping in time "
        "unrepresentable, buffers included, while adjacent half-open ranges "
        "and non-blocking states are accepted; that blocked_range, version "
        "and updated_at are written by the trigger and ignore the caller; "
        "that the state matrices, the reference tenancy and the active "
        "agent membership are enforced by the database; that stima_id has no "
        "foreign key towards the legacy stime table; that source and "
        "source_record_id make imports idempotent; that appointments refuse "
        "DELETE and appointment_events is append-only; that the 072 down "
        "refuses while an appointment exists; and, deterministically through "
        "pg_stat_activity rather than sleeps, that a second concurrent booking "
        "of the same agent waits on the per-agent advisory lock and then "
        "receives a readable conflict. It monkeypatches get_connection in the "
        "core module onto its own connection. It deliberately does NOT go "
        "through database.get_connection(): that is the application choke "
        "point and must stay uncoupled from a throwaway test database."
    ),
    "tests/test_a30_2_appointments_postgres.py": (
        "TEST-only A30-2 proof; opts in through P29_TEST_DSN and skips "
        "entirely without it. Creates and drops its own throwaway database "
        "and never touches an existing schema. Registered in its own right "
        "rather than reusing the A30-1 entry: an allow-list shared between "
        "phases would let a new entrypoint arrive unannounced. The connection "
        "exists to apply the LMC-15 chain and 072 and to drive the NOT "
        "MOUNTED Agenda router through an isolated FastAPI test app: "
        "idempotent creation by client_request_id, the state machine and its "
        "time guards, optimistic versions, per-role visibility, "
        "availability, and the LMC-15 projection kept OFF (switched on only "
        "inside a test to prove the mapping and its rollback). It "
        "monkeypatches get_connection in the core module onto connections to "
        "its own throwaway database. It must not go through "
        "database.get_connection(): that is the application choke point and "
        "must stay uncoupled from a throwaway test database"
    ),
    "tests/test_a30_11_working_hours_postgres.py": (
        "TEST-only A30-11B proof; opts in through P29_TEST_DSN and skips "
        "entirely without it. Creates and drops its own throwaway database "
        "and never touches an existing schema. Registered in its own right "
        "rather than reusing the A30-1/A30-2 entries: an allow-list shared "
        "between phases would let a new entrypoint arrive unannounced. Its "
        "schema is a deliberately minimal, self-contained subset "
        "(agencies/operator_users/agency_memberships/schema_migrations) "
        "independent of 072-075, against which it applies 076 directly. The "
        "connection exists to prove what a pure-Python unit test cannot: "
        "that the btree_gist EXCLUDE constraints on agent_working_hours, "
        "agent_availability_exceptions and agency_closures make two "
        "overlapping slots for the same agency/user/day (or agency/date) "
        "unrepresentable while adjacent half-open ranges and rows under a "
        "different key are accepted; that the membership-guard triggers "
        "reject a target user without an ACTIVE agency_memberships row; "
        "that the composite tenancy foreign keys reject a cross-agency "
        "target; that the minute-range CHECK constraints reject an invalid "
        "window; and that 076's up/down/up round-trip is clean, with the "
        "down migration refusing while any of the three tables holds rows. "
        "It monkeypatches nothing and opens its own connection directly to "
        "the throwaway database. It must not go through "
        "database.get_connection(): that is the application choke point and "
        "must stay uncoupled from a throwaway test database"
    ),
    "tests/test_a30_12_public_booking_postgres.py": (
        "TEST-only A30-12B proof; opts in through P29_TEST_DSN and skips "
        "entirely without it. Creates and drops its own throwaway database "
        "and never touches an existing schema. Registered in its own right "
        "rather than reusing an earlier phase's entry: an allow-list shared "
        "between phases would let a new entrypoint arrive unannounced. Its "
        "schema applies the LMC-15 chain (009-070) plus 072/073/076/077 "
        "directly, deliberately skipping 074/075 (calendar_sync) because "
        "calendar_sync.integration.on_appointment_mutation fails open when "
        "no deployment namespace is configured. The connection exists to "
        "prove what a pure-Python unit test cannot: migration 077 up/down/up "
        "and its down-rejects-with-rows guard; booking-link CRUD operator "
        "permissions (owner-vs-agent, tenant isolation, rotate invalidation); "
        "the public token-resolution endpoint's identical-generic-404 for "
        "every failure cause; the D10 HARD availability gate against real "
        "weekly hours, exceptions, closures and buffers; the authoritative "
        "submit path's idempotency, contact matching, TOCTOU re-check "
        "against a same-day closure inserted after the GET, and true "
        "concurrency (multiple threads racing the same slot, exactly one "
        "201); rate limiting; and that client_ip_hash is a real HMAC, never "
        "the raw IP. It monkeypatches nothing and opens its own connection "
        "directly to the throwaway database. It must not go through "
        "database.get_connection(): that is the application choke point and "
        "must stay uncoupled from a throwaway test database"
    ),
    # SENTINELLA AGGIORNATA DA CRM-OPS-1A: +1 sito, dichiarato qui e nel
    # documento, come ogni prova PostgreSQL delle fasi precedenti.
    "tests/test_crm_ops_1a_creator_assignment.py": (
        "TEST-only CRM-OPS-1A proof; opts in through P29_TEST_DSN and skips "
        "entirely without it. Creates and drops its own throwaway database "
        "and never touches an existing schema. Registered in its own right "
        "rather than reusing an earlier entry: an allow-list shared between "
        "phases would let a new entrypoint arrive unannounced. The connection "
        "exists to apply the REAL P26 core migrations (027, 001, 028, 029, "
        "030) and to prove what a recording cursor cannot: that a contact or "
        "lead created by an agent is inserted with assigned_agent_id equal to "
        "the creator and satisfies migration 030's composite foreign key "
        "(agency_id, assigned_agent_id) -> agency_memberships, which the "
        "module first shows to be live with a negative control; that the "
        "creator then reads and updates its own row through the production "
        "scope while a colleague agent and another agency get NotFound; that "
        "owner and admin keep full access and still create unassigned rows; "
        "and that an agent cannot open a lead on a contact it does not see. "
        "It monkeypatches get_connection in the core module onto its own "
        "connection. It must not go through database.get_connection(): that "
        "is the application choke point and must stay uncoupled from a "
        "throwaway test database"
    ),
    "tests/test_crm_ops_2_property_form.py": (
        "TEST-only CRM-OPS-2 proof; opts in through P29_TEST_DSN and skips "
        "its database layer entirely without it, and FAILS rather than run if "
        "the DSN is not a Unix socket or localhost. Creates and drops its own "
        "throwaway database and never touches an existing schema. Registered "
        "in its own right rather than reusing an earlier entry: an allow-list "
        "shared between phases would let a new entrypoint arrive unannounced. "
        "The connection exists to apply the REAL migrations (027, 001, 028, "
        "029, 030, 002, 003, 034, 035, 036 and 080) and to prove what a "
        "recording cursor cannot: that migration 080 adds region and "
        "assigned_agent_id with a live composite foreign key to "
        "agency_memberships; that a property created without title or code "
        "gets IMM-<id> and a generated description in the same transaction; "
        "that edits keep the id, the code, the assignment and historical "
        "values; that only active agents of the same agency are assignable; "
        "and that the down migration refuses while data would be lost. It "
        "monkeypatches get_connection in the core module onto its own "
        "connection. It must not go through database.get_connection(): that "
        "is the application choke point and must stay uncoupled from a "
        "throwaway test database"
    ),
    "tests/test_censimento_1_schema_postgres.py": (
        "TEST-only CENSIMENTO-1 proof; opts in through P29_TEST_DSN and skips "
        "entirely without it, and FAILS rather than run if the DSN is not a "
        "Unix socket or localhost. Creates and drops its own throwaway "
        "database (its name carries the 'test' marker the runner demands) and "
        "never touches an existing schema. Registered in its own right rather "
        "than reusing an earlier entry: an allow-list shared between phases "
        "would let a new entrypoint arrive unannounced. The connection exists "
        "to install the REAL ledger (026 through the runner's apply_baseline, "
        "027..082 registered with the checksums of the files on disk) and then "
        "to let scripts/p26_migrate.py apply migration 083 itself, and to "
        "prove what a recording cursor cannot: that every pre-existing "
        "property row keeps the same fingerprint, code, state and links; that "
        "building and pertinenza links stay inside one agency, with no "
        "self-link, no cycle and depth one; that the cadastral identity is "
        "unique only when complete (section NULL = unknown, '' = absent) after "
        "normalisation; that the census guard refuses every forbidden path on "
        "INSERT and UPDATE while historical 'crm' rows keep working; that two "
        "concurrent retries with the same client_request_id leave one row; "
        "and that the down migration refuses while any census value exists "
        "and restores the previous schema when none does. It opens its own "
        "connections (two of them concurrently for the retry race) and does "
        "NOT monkeypatch get_connection: no application code runs here. It "
        "must not go through database.get_connection(): that is the "
        "application choke point and must stay uncoupled from a throwaway "
        "test database"
    ),
    "tests/test_censimento_1_fullschema_postgres.py": (
        "TEST-only CENSIMENTO-1 proof on the COMPLETE previous schema; opts in "
        "through P29_TEST_DSN and skips entirely without it, and FAILS rather "
        "than run if the DSN is not a Unix socket or localhost. It creates a "
        "throwaway database that MUST be named stima360_db_test (migrations "
        "010/011 check that name) on the local cluster only, skips if one "
        "already exists there, and drops it at teardown. It rebuilds the TEST "
        "schema the way TEST was born: the legacy site tables through the real "
        "database.py functions (get_connection monkeypatched onto the "
        "throwaway database), migrations 001..025 as applied on TEST (no _prod "
        "variants), then 026 and 027..082 EXECUTED by scripts/p26_migrate.py "
        "with its migration folder pinned to the version TEST saw at the time, "
        "and finally 083 through the same runner. It compares the pre-baseline "
        "schema with the certified TEST baseline snapshot and proves that the "
        "083 guards coexist with the real 081 foreign key and trigger. It "
        "opens its own connections and must not go through "
        "database.get_connection() for the test traffic: that is the "
        "application choke point and must stay uncoupled from a throwaway "
        "test database"
    ),
    "tests/test_censimento_2_catalog_postgres.py": (
        "TEST-only CENSIMENTO-1 Fase 2 (catalogues) proof on the COMPLETE "
        "schema; opts in through P29_TEST_DSN and skips entirely without it, "
        "and FAILS rather than run if the DSN is not a Unix socket or "
        "localhost. Registered in its own right rather than reusing the Fase 1 "
        "entry: an allow-list shared between phases would let a new entrypoint "
        "arrive unannounced. Its module fixture rebuilds, by itself, the same "
        "throwaway database as the Fase 1 full-schema proof (it MUST be named "
        "stima360_db_test, skips if one already exists on the local cluster, "
        "drops it at teardown): legacy tables through the real database.py "
        "functions (get_connection monkeypatched onto the throwaway database "
        "for that step only), 001..025 as on TEST, then 026, 027..082 and 083 "
        "EXECUTED by scripts/p26_migrate.py. It then monkeypatches "
        "get_connection in the core module onto the throwaway database so the "
        "REAL property service and repository run against it, and proves what "
        "a recording cursor cannot: that the new 'storage' type is created, "
        "read, updated and isolated per agency with historical rows untouched; "
        "that every one of the 52 cadastral catalogue codes passes the 083 "
        "format CHECK and normalisation trigger, while catalogue membership "
        "stays with the service; and that form-options carries the catalogue. "
        "It must not go through database.get_connection() for the test "
        "traffic: that is the application choke point and must stay uncoupled "
        "from a throwaway test database"
    ),
    "tests/test_censimento_3_backend_postgres.py": (
        "TEST-only CENSIMENTO-1 Fase 3 (backend) proof on the COMPLETE schema "
        "through the REAL routes; opts in through P29_TEST_DSN and skips "
        "entirely without it, and FAILS rather than run if the DSN is not a "
        "Unix socket or localhost. Registered in its own right rather than "
        "reusing the Fase 1/2 entries: an allow-list shared between phases "
        "would let a new entrypoint arrive unannounced. Its module fixture "
        "rebuilds, by itself, the throwaway stima360_db_test database as the "
        "Fase 1 full-schema proof does (skips if one already exists on the "
        "local cluster, drops it at teardown), then monkeypatches "
        "get_connection in the core module onto it so the real property and "
        "acquisitions routers run against it behind a per-role operator "
        "context. It proves what a recording cursor cannot: buildings, census "
        "units, pertinenze and accessories created, read, updated and refused "
        "inside one agency and invisible to the other; one transaction per "
        "composed operation with full rollback; idempotency under concurrent "
        "requests with the same client_request_id; the 083 guards translated "
        "into API errors; take-in-charge and undo-create; the acquisitions "
        "and readiness guards; historical rows untouched. It must not go "
        "through database.get_connection() for the test traffic: that is the "
        "application choke point and must stay uncoupled from a throwaway "
        "test database"
    ),
    "tests/test_censimento_3_senza_083_postgres.py": (
        "TEST-only CENSIMENTO-1 Fase 3 proof of the DB-first order: the Fase 3 "
        "code against a database WITHOUT migration 083. Opts in through "
        "P29_TEST_DSN and skips entirely without it, and FAILS rather than run "
        "if the DSN is not a Unix socket or localhost. Registered in its own "
        "right rather than reusing the other CENSIMENTO entries: an allow-list "
        "shared between phases would let a new entrypoint arrive unannounced. "
        "Its module fixture rebuilds, by itself, the throwaway stima360_db_test "
        "database as the full-schema proof does but STOPS at 082 (skips if one "
        "already exists on the local cluster, drops it at teardown), then "
        "monkeypatches get_connection in the core module onto it so the real "
        "property and acquisitions routers run against it. It proves what a "
        "recording cursor cannot: every census route answers 503 "
        "CENSUS_NOT_INSTALLED from the explicit catalogue check without "
        "writing; generic POST/PATCH with an 083 field sent explicitly answer "
        "a controlled 503 while ordinary creation and update keep working; "
        "acquisitions and readiness are unchanged. It must not go through "
        "database.get_connection() for the test traffic: that is the "
        "application choke point and must stay uncoupled from a throwaway "
        "test database"
    ),
    # SENTINELLA AGGIORNATA DA CATALOGO-CANONICO-1: un sito di connessione di
    # TEST nuovo, registrato con la sua giustificazione (nessun accesso
    # applicativo nuovo: la sincronizzazione dal sito usa core_cursor). Il
    # completamento: le colonne della dettagliata le crea la 088, non piu'
    # l'helper di database.py.
    "tests/test_catalogo_canonico_1_postgres.py": (
        "TEST-only CATALOGO-CANONICO-1 proof of the site -> property sync on the "
        "COMPLETE schema (migrations 087 and 088 included), reusing the CENSIMENTO-1 Fase "
        "3 module fixture (throwaway stima360_db_test, opt-in through "
        "P29_TEST_DSN, local DSN only, dropped at teardown). Registered in its "
        "own right: it points the REAL public endpoints main.salva_stima, "
        "main.salva_stima_dettagliata and main.prefill (main.get_connection) "
        "at the throwaway database, so the real CORE bridge and property/site_sync.py "
        "run end to end. It must not go through database.get_connection() for "
        "the test traffic: that is the application choke point and must stay "
        "uncoupled from a throwaway test database"
    ),
    # SENTINELLA AGGIORNATA DA SITE-IMPORT-1: due siti nuovi, nominati con la
    # loro giustificazione. Il CRM continua a scrivere SOLO da
    # database.get_connection(); l'unica connessione nuova e' verso un ALTRO
    # database (quello del sito) ed e' in sola lettura imposta dal server.
    "site_import/source.py": (
        "READ-ONLY connection to a DIFFERENT database: the public site's "
        "database (SITE_DB_URL), never the CRM's. It cannot go through "
        "database.get_connection(), which by design points at the CRM. The "
        "session is opened with default_transaction_read_only=on and a "
        "statement_timeout, and the module verifies SHOW "
        "default_transaction_read_only = 'on' before any query; it issues "
        "SELECT/SHOW only and rolls back after each read. The importer refuses "
        "to run if the source is the CRM database itself or carries the CRM "
        "import ledger. It is not an application/RLS entrypoint of the CRM"
    ),
    "tests/test_site_import_1_postgres.py": (
        "TEST-only SITE-IMPORT-1 proof on TWO throwaway databases: the CRM "
        "complete schema (reusing the CENSIMENTO-1 Fase 3 module fixture, "
        "opt-in through P29_TEST_DSN, local DSN only, dropped at teardown) and "
        "a throwaway site_import_sito_test built from the PROD site schema and "
        "dropped at teardown. It proves against a real PostgreSQL that the "
        "site connection is really read-only, that the UNIQUE ledger and the "
        "advisory lock prevent duplicates under concurrent runs, and that an "
        "interrupted run resumes without losing records. It must not go "
        "through database.get_connection() for the test traffic: that is the "
        "application choke point and must stay uncoupled from a throwaway test "
        "database"
    ),
    # SENTINELLA AGGIORNATA DA CRM-PROD-BOOTSTRAP: il canale di migrazione del
    # CRM PROD, separato dal runner TEST (che resta intatto) e dal suo test.
    "scripts/crm_prod_migrate.py": (
        "privileged migration channel for the CRM PRODUCTION database only, the "
        "counterpart of scripts/p26_migrate.py, which stays TEST only and is "
        "imported unchanged for discovery, validation, planning and the ledger "
        "row. It connects through CRM_PROD_DATABASE_URL (never the DB_* "
        "variables), requires the database name typed on the command line and "
        "refuses the site's production names, any name carrying the TEST "
        "marker, the SITE_DB_URL database, and any non-empty database that does "
        "not carry its own crm_instance marker - so the site's database and the "
        "CRM TEST database can never be bootstrapped over. The migrator "
        "legitimately sits outside RLS, like the TEST runner"
    ),
    "tests/test_crm_prod_bootstrap_postgres.py": (
        "TEST-only proof of scripts/crm_prod_migrate.py on TWO throwaway "
        "databases of the local cluster (opt-in through P29_TEST_DSN, local DSN "
        "only, both dropped at teardown): an EMPTY database named without the "
        "TEST marker, bootstrapped to the last migration exactly as CRM PROD "
        "will be, and a PROD-shaped site database that the channel must refuse "
        "and that the SITE-IMPORT-1 cron then imports from, as a subprocess with "
        "the DB_* variables of the bootstrapped database. It must not go through "
        "database.get_connection() for the test traffic: that is the application "
        "choke point and must stay uncoupled from a throwaway test database"
    ),
}

# Modules that legitimately import the choke point to build their own cursor
# contextmanager. Each one delegates; none opens its own connection.
CURSOR_HELPER_MODULES = (
    "core/database.py",
    "followup/database.py",
    "seller_intent/database.py",
    "seller_intelligence/database.py",
    "property_watch/database.py",
    "next_best_action/database.py",
    "database_revival/database.py",
)

CONNECT_RE = re.compile(r"psycopg2\s*\.\s*connect\b")

SKIP_DIRECTORIES = {".venv", ".git", "__pycache__", ".pytest_cache", "node_modules"}


def _python_files() -> list[Path]:
    files = []
    for path in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRECTORIES for part in path.parts):
            continue
        # This module names every connection site in prose in order to pin
        # them. It opens none itself, so scanning it would only ever rediscover
        # its own whitelist.
        if path.resolve() == Path(__file__).resolve():
            continue
        files.append(path)
    return files


def test_h11_this_module_opens_no_connection():
    # Checked against the module namespace rather than its source, because the
    # source names connection sites in prose by design.
    assert "psycopg2" not in globals()


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


@pytest.fixture(scope="module")
def connection_sites() -> set[str]:
    found = set()
    for path in _python_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        if CONNECT_RE.search(text):
            found.add(_relative(path))
    return found


# ---------------------------------------------------------------------------
# The pinned set
# ---------------------------------------------------------------------------

def test_h11_no_unknown_connection_site(connection_sites):
    unexpected = connection_sites - set(ALLOWED_CONNECTION_SITES)
    assert not unexpected, (
        "new database connection site(s) detected: "
        f"{sorted(unexpected)}. Route them through database.get_connection(), "
        "or add them to ALLOWED_CONNECTION_SITES with a justification and to "
        "docs/P26_DB_ENTRYPOINTS.md. RLS depends on this set staying known."
    )


def test_h11_every_allowed_site_still_exists(connection_sites):
    missing = set(ALLOWED_CONNECTION_SITES) - connection_sites
    assert not missing, (
        f"whitelisted connection site(s) no longer connect: {sorted(missing)}. "
        "Remove the stale entries so the whitelist keeps meaning something."
    )


def test_h11_choke_point_is_present(connection_sites):
    assert RUNTIME_CHOKE_POINT in connection_sites


def test_h11_choke_point_exposes_get_connection():
    source = (ROOT / "database.py").read_text(encoding="utf-8")
    assert re.search(r"^def get_connection\(\):", source, re.MULTILINE)


# ---------------------------------------------------------------------------
# Runtime paths must delegate, never connect
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("module", CURSOR_HELPER_MODULES)
def test_h11_cursor_helpers_delegate_to_the_choke_point(module):
    source = (ROOT / module).read_text(encoding="utf-8")
    assert "from database import get_connection" in source, (
        f"{module} must obtain its connection from the choke point"
    )
    assert not CONNECT_RE.search(source), (
        f"{module} must not build its own connection"
    )


def test_h11_application_packages_do_not_connect_directly():
    packages = (
        "owner", "buy", "match", "flow", "property", "proposal", "sale", "crm",
        "core", "followup", "seller_intent", "seller_intelligence",
        "property_watch", "next_best_action", "database_revival",
    )
    offenders = []
    for package in packages:
        directory = ROOT / package
        if not directory.is_dir():
            continue
        for path in directory.rglob("*.py"):
            if any(part in SKIP_DIRECTORIES for part in path.parts):
                continue
            if CONNECT_RE.search(path.read_text(encoding="utf-8", errors="replace")):
                offenders.append(_relative(path))
    assert not offenders, (
        f"application packages must not open connections directly: {offenders}"
    )


def test_h11_main_uses_the_choke_point():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "from database import get_connection" in source
    assert not CONNECT_RE.search(source)


# ---------------------------------------------------------------------------
# P26-0 must not have altered the risks it recorded
# ---------------------------------------------------------------------------

def test_h11_migrate_add_token_is_unmodified_and_still_unguarded():
    # P26-0 records this file as a risk; it does not touch it. The assertions
    # below fail if someone "fixes" it silently instead of running the
    # Render / cron / runbook verification first.
    source = (ROOT / "migrate_add_token.py").read_text(encoding="utf-8")
    assert CONNECT_RE.search(source)
    assert "DATABASE_URL" in source
    assert "stima" in source


def test_h11_privileged_migration_channel_guards_against_production():
    for script in ("scripts/p26_schema_snapshot.py", "scripts/p26_migrate.py"):
        source = (ROOT / script).read_text(encoding="utf-8")
        assert "PROD_DATABASE_NAMES" in source, (
            f"{script} must refuse production database names"
        )
        assert "assert_test_database_name" in source


# ---------------------------------------------------------------------------
# The inventory document is part of the contract
# ---------------------------------------------------------------------------

def test_h11_inventory_document_exists():
    assert ENTRYPOINT_DOC.exists(), (
        "docs/P26_DB_ENTRYPOINTS.md is the versioned inventory required by the spec"
    )


@pytest.mark.parametrize("site", sorted(ALLOWED_CONNECTION_SITES))
def test_h11_every_allowed_site_is_documented(site):
    document = ENTRYPOINT_DOC.read_text(encoding="utf-8")
    assert site in document, (
        f"{site} bypasses or is the choke point but is not listed in "
        "docs/P26_DB_ENTRYPOINTS.md"
    )


def test_h11_inventory_records_the_future_role_separation():
    document = ENTRYPOINT_DOC.read_text(encoding="utf-8").lower()
    for requirement in ("migrator", "bypassrls", "row level security"):
        assert requirement in document, (
            f"the inventory must state the {requirement!r} precondition for RLS"
        )
