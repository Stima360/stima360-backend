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
