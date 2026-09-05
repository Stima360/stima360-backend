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
