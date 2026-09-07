"""P26-1 Task 1 - reusable static guard infrastructure for migrations 027-030.

These are offline tests. Nothing here opens a database connection: every
assertion is made against SQL text or against pure functions operating on
in-memory or tmp_path fixtures. Same discipline as
tests/test_p26_baseline_isolation.py.

This module deliberately asserts NOTHING about the real migrations 027, 028,
029 or 030 beyond the shared rules. Tasks 2, 7, 8 and 9 own those files and
each owns its own RED -> GREEN cycle.

A guard that has never been shown to reject is not a guard, so every rule the
helper enforces has its own negative case below.

-----------------------------------------------------------------------------
TRANSACTION OWNERSHIP CHANGES AT VERSION 027
-----------------------------------------------------------------------------

Versions <= 026 were authored under P26-0 section 6 rule 3: the migration file
brackets its own transaction with BEGIN; ... COMMIT;.

From 027 the runner owns the transaction. The file carries no BEGIN and no
COMMIT, so scripts/p26_migrate.py can execute the migration body and write its
schema_migrations row inside ONE transaction and commit them together. A file
that committed itself would make the ledger insert a second, separate
transaction - the atomicity defect recorded in
docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.

DOWN files are unaffected in both eras: they are executed manually (the runner
has no down command), so they must always bracket themselves.

Rules enforced, from the approved P26-0 spec section 6, its atomicity
addendum, and the approved P26-1 design spec:

    G1  the migration and its _down sibling both exist        (P26-0 rule 2)
    G2  transaction ownership, by version:
          <= 026  the UP file opens BEGIN; and closes COMMIT;  (P26-0 rule 3)
          >= 027  the UP file contains neither                 (addendum)
    G3  no CONCURRENTLY in a transactional migration          (P26-0 rule 3/10)
    G4  the ledger is append-only: no DELETE FROM schema_migrations
                                                             (P26-0 rule 12)
    G5  no environment guard comparing current_database() to a hard-coded
        database name                                        (P26-0 rule 6)
    G6  no secret-shaped key written into agencies.settings   (P26-1 spec D-8)
    G7  a migration declared irreversible ships a down file that RAISEs
                                                       (P26-0 rule 2 / 7.3)
    G8  the DOWN file always brackets its own transaction     (addendum)
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
RUNNER = ROOT / "scripts" / "p26_migrate.py"

# The one real migration this module asserts against directly: 026 exists, is
# applied on stima360_db_test, and is certified by P26-0.
BASELINE_VERSION = "026_p26_baseline"

# The first version whose UP transaction is owned by the runner rather than by
# the file. Mirrors p26_migrate.RUNNER_OWNED_TRANSACTION_FROM; the two are
# asserted equal below, so the duplication cannot silently drift.
RUNNER_OWNED_TRANSACTION_FROM = 27


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # Registered before execution so dataclasses can resolve __module__ under
    # postponed annotation evaluation.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def runner_module():
    return _load(RUNNER, "p26_migrate_task1")


# ---------------------------------------------------------------------------
# The shared helper. Tasks 2, 7, 8 and 9 import this.
#
# These patterns mirror scripts/p26_migrate.py deliberately rather than
# importing it. The runner enforces them at apply time; this enforces them at
# review time. Two independent checks of the same rule is the point - a single
# shared implementation could be wrong in both places at once.
# ---------------------------------------------------------------------------

_BEGIN_RE = re.compile(r"^\s*BEGIN\s*;", re.IGNORECASE | re.MULTILINE)
_COMMIT_RE = re.compile(r"^\s*COMMIT\s*;", re.IGNORECASE | re.MULTILINE)
_CONCURRENTLY_RE = re.compile(r"\bCONCURRENTLY\b", re.IGNORECASE)
_LEDGER_DELETE_RE = re.compile(r"DELETE\s+FROM\s+schema_migrations", re.IGNORECASE)

# G5 bans only the 010/014 shape: current_database() compared against a
# hard-coded database name. 026 legitimately uses current_database() as a
# column DEFAULT and compares it against a column, so a blanket ban would
# reject the certified baseline.
_DB_NAME_GUARD_RE = re.compile(
    r"current_database\(\)\s*(?:<>|!=|=)\s*'"
    r"|'[^']*'\s*(?:<>|!=|=)\s*current_database\(\)",
    re.IGNORECASE,
)

_JSON_OBJECT_RE = re.compile(r"'(\{.*?\})'", re.DOTALL)
_JSON_KEY_RE = re.compile(r'"([^"]+)"\s*:')
_SECRET_KEY_RE = re.compile(r"(?i)(pass|secret|token|key|smtp|api)")

_VERSION_NUMBER_RE = re.compile(r"^(\d{3})_")


def _strip_sql_comments(sql: str) -> str:
    """Return only the executable part of a SQL file.

    Assertions about what a migration *does* must not be satisfied or defeated
    by prose in its comments. Same discipline as
    tests/test_p26_baseline_isolation.py.
    """
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def _version_number(version: str) -> int:
    match = _VERSION_NUMBER_RE.match(version)
    assert match, f"{version!r} does not start with a three-digit version"
    return int(match.group(1))


def assert_p26_migration_rules(
    version: str,
    *,
    expect_reversible: bool,
    directory: Path | None = None,
) -> None:
    """Assert that one P26 migration obeys the rules in this module's docstring.

    ``version`` is the filename stem, e.g. ``"026_p26_baseline"``.
    ``expect_reversible`` is ``False`` for a migration whose down file must
    refuse (a data backfill); ``True`` otherwise.
    ``directory`` overrides the migrations directory so the negative cases can
    be exercised against synthetic files in ``tmp_path``. It defaults to the
    repository's real ``migrations/``.
    """
    base = directory if directory is not None else MIGRATIONS
    up_path = base / f"{version}.sql"
    down_path = base / f"{version}_down.sql"
    number = _version_number(version)

    # G1 - both halves exist.
    assert up_path.exists(), f"{version}: missing migration file {up_path.name}"
    assert down_path.exists(), (
        f"{version}: missing down migration {down_path.name}. Every migration "
        "ships a down, even one that refuses loudly (P26-0 rule 2)."
    )

    up = _strip_sql_comments(up_path.read_text(encoding="utf-8"))
    down = _strip_sql_comments(down_path.read_text(encoding="utf-8"))

    # G2 - transaction ownership, by version.
    if number < RUNNER_OWNED_TRANSACTION_FROM:
        assert _BEGIN_RE.search(up), (
            f"{version}: a migration below "
            f"{RUNNER_OWNED_TRANSACTION_FROM:03d} brackets its own "
            "transaction and must open BEGIN; (P26-0 rule 3)"
        )
        assert _COMMIT_RE.search(up), (
            f"{version}: a migration below "
            f"{RUNNER_OWNED_TRANSACTION_FROM:03d} brackets its own "
            "transaction and must close COMMIT; (P26-0 rule 3)"
        )
    else:
        assert not _BEGIN_RE.search(up), (
            f"{version}: an UP migration from "
            f"{RUNNER_OWNED_TRANSACTION_FROM:03d} must not contain BEGIN;. "
            "The runner brackets the migration body and its "
            "schema_migrations registration in one transaction, so a file "
            "that opens its own would let the two diverge "
            "(P26-0 atomicity addendum)."
        )
        assert not _COMMIT_RE.search(up), (
            f"{version}: an UP migration from "
            f"{RUNNER_OWNED_TRANSACTION_FROM:03d} must not contain COMMIT;. "
            "A self-commit would make the ledger insert a second, separate "
            "transaction - exactly the atomicity defect the addendum records."
        )

    # G3 - CONCURRENTLY cannot run inside a transaction.
    assert not _CONCURRENTLY_RE.search(up), (
        f"{version}: CONCURRENTLY cannot run inside a transaction; it belongs "
        "in a dedicated migration marked '-- NON-TRANSACTIONAL' "
        "(P26-0 rules 3 and 10)"
    )

    # G4 - the ledger is append-only.
    assert not _LEDGER_DELETE_RE.search(up), (
        f"{version}: the ledger is append-only; record a rollback with "
        "rolled_back_at instead of removing the row (P26-0 rule 12)"
    )

    # G5 - no environment guard on a hard-coded database name.
    for label, text in (("up", up), ("down", down)):
        assert not _DB_NAME_GUARD_RE.search(text), (
            f"{version} ({label}): current_database() must not be compared "
            "against a hard-coded database name. That is the 010/014 pattern "
            "P26-0 rule 6 abandoned; the schema must be identical in TEST and "
            "PROD."
        )

    # G6 - no secret-shaped key in agencies.settings.
    if "settings" in up.lower():
        for literal in _JSON_OBJECT_RE.findall(up):
            for json_key in _JSON_KEY_RE.findall(literal):
                assert not _SECRET_KEY_RE.search(json_key), (
                    f"{version}: settings must hold only non-sensitive "
                    f"configuration; found the key {json_key!r}. No secrets in "
                    "the schema (P26-1 design spec D-8, P26-0 section 8)."
                )

    # G7 - an irreversible migration must refuse, not silently no-op.
    if not expect_reversible:
        assert "RAISE EXCEPTION" in down.upper(), (
            f"{version}: this migration is declared irreversible, so its down "
            "file must contain a RAISE EXCEPTION explaining why and naming the "
            "recovery path (P26-0 rule 2 and section 7.3)."
        )

    # G8 - the down file always brackets its own transaction, in every era.
    # The runner has no down command: down files are executed manually, where
    # each statement would otherwise autocommit on its own.
    assert _BEGIN_RE.search(down), (
        f"{version}: the down migration must open BEGIN;. It is executed "
        "manually, so it must bracket itself or its guards and its rollback "
        "stamp would commit independently."
    )
    assert _COMMIT_RE.search(down), (
        f"{version}: the down migration must close COMMIT;."
    )


# ---------------------------------------------------------------------------
# Synthetic migration fixtures.
#
# Names are deliberately neutral ("synthetic_probe") and never reuse a real
# P26-1 migration stem, so that nothing in this module can be misread as an
# assertion about a migration Task 2, 7, 8 or 9 owns. The final test in this
# file enforces that rule against this file's own source - which is how this
# comment came to be worded generically.
#
# Two UP shapes, one per transaction-ownership era.
# ---------------------------------------------------------------------------

BODY = """
CREATE TABLE IF NOT EXISTS synthetic_probe (
    id BIGSERIAL PRIMARY KEY
);
"""

# <= 026: the file brackets its own transaction.
LEGACY_UP = f"BEGIN;\n{BODY}\nCOMMIT;\n"

# >= 027: the runner brackets it.
RUNNER_OWNED_UP = BODY

# Down files bracket themselves in both eras.
COMPLIANT_DOWN = """BEGIN;
DROP TABLE IF EXISTS synthetic_probe;
COMMIT;
"""

REFUSING_DOWN = """BEGIN;
DO $do$ BEGIN
    RAISE EXCEPTION 'synthetic probe: this migration cannot be reversed.';
END $do$;
COMMIT;
"""


def _write(directory: Path, version: str, up: str, down: str | None) -> None:
    (directory / f"{version}.sql").write_text(up, encoding="utf-8")
    if down is not None:
        (directory / f"{version}_down.sql").write_text(down, encoding="utf-8")


# ---------------------------------------------------------------------------
# The version gate is a single source of truth.
# ---------------------------------------------------------------------------

def test_helper_and_runner_agree_on_the_version_gate(runner_module):
    assert (
        runner_module.RUNNER_OWNED_TRANSACTION_FROM == RUNNER_OWNED_TRANSACTION_FROM
    ), (
        "the review-time guard and the apply-time runner must switch "
        "transaction ownership at the same version"
    )


def test_the_gate_is_immediately_above_the_certified_baseline(runner_module):
    assert runner_module.RUNNER_OWNED_TRANSACTION_FROM == runner_module.BASELINE_VERSION + 1, (
        "026 is applied and certified; the new ownership rule starts at the "
        "first version that has never been applied"
    )


# ---------------------------------------------------------------------------
# Positive cases.
# ---------------------------------------------------------------------------

def test_helper_accepts_the_certified_baseline_migration():
    """026 is real, applied and certified. The helper must not reject it.

    This is the guard against a helper so strict it would be unusable - in
    particular against banning every mention of current_database(), which 026
    legitimately uses as a column DEFAULT and inside its cross-environment
    trigger, and against applying the runner-owned rule retroactively.
    """
    assert_p26_migration_rules(BASELINE_VERSION, expect_reversible=True)


def test_helper_accepts_a_legacy_shaped_migration_below_the_gate(tmp_path):
    _write(tmp_path, "026_synthetic_probe", LEGACY_UP, COMPLIANT_DOWN)
    assert_p26_migration_rules(
        "026_synthetic_probe", expect_reversible=True, directory=tmp_path
    )


def test_helper_accepts_a_runner_owned_migration_at_the_gate(tmp_path):
    _write(tmp_path, "027_synthetic_probe", RUNNER_OWNED_UP, COMPLIANT_DOWN)
    assert_p26_migration_rules(
        "027_synthetic_probe", expect_reversible=True, directory=tmp_path
    )


def test_helper_accepts_a_runner_owned_migration_above_the_gate(tmp_path):
    _write(tmp_path, "030_synthetic_probe", RUNNER_OWNED_UP, COMPLIANT_DOWN)
    assert_p26_migration_rules(
        "030_synthetic_probe", expect_reversible=True, directory=tmp_path
    )


# ---------------------------------------------------------------------------
# G1
# ---------------------------------------------------------------------------

def test_g1_rejects_a_migration_with_no_down_sibling(tmp_path):
    _write(tmp_path, "027_synthetic_probe", RUNNER_OWNED_UP, None)
    with pytest.raises(AssertionError, match="down"):
        assert_p26_migration_rules(
            "027_synthetic_probe", expect_reversible=True, directory=tmp_path
        )


# ---------------------------------------------------------------------------
# G2 - both wrong shapes, in both eras.
# ---------------------------------------------------------------------------

def test_g2_below_the_gate_rejects_a_migration_that_never_commits(tmp_path):
    _write(tmp_path, "026_synthetic_probe", LEGACY_UP.replace("COMMIT;", ""), COMPLIANT_DOWN)
    with pytest.raises(AssertionError, match="COMMIT"):
        assert_p26_migration_rules(
            "026_synthetic_probe", expect_reversible=True, directory=tmp_path
        )


def test_g2_below_the_gate_rejects_a_migration_that_never_opens(tmp_path):
    _write(tmp_path, "026_synthetic_probe", LEGACY_UP.replace("BEGIN;", ""), COMPLIANT_DOWN)
    with pytest.raises(AssertionError, match="BEGIN"):
        assert_p26_migration_rules(
            "026_synthetic_probe", expect_reversible=True, directory=tmp_path
        )


def test_g2_at_the_gate_rejects_a_self_bracketed_migration(tmp_path):
    """The negative control for the atomicity fix.

    A 027+ file that brackets itself would commit its DDL before the runner
    writes the ledger row, leaving schema and ledger able to diverge.
    """
    _write(tmp_path, "027_synthetic_probe", LEGACY_UP, COMPLIANT_DOWN)
    with pytest.raises(AssertionError, match="must not contain BEGIN"):
        assert_p26_migration_rules(
            "027_synthetic_probe", expect_reversible=True, directory=tmp_path
        )


def test_g2_at_the_gate_rejects_a_stray_commit(tmp_path):
    _write(tmp_path, "027_synthetic_probe", f"{BODY}\nCOMMIT;\n", COMPLIANT_DOWN)
    with pytest.raises(AssertionError, match="must not contain COMMIT"):
        assert_p26_migration_rules(
            "027_synthetic_probe", expect_reversible=True, directory=tmp_path
        )


def test_g2_at_the_gate_rejects_a_stray_begin(tmp_path):
    _write(tmp_path, "027_synthetic_probe", f"BEGIN;\n{BODY}", COMPLIANT_DOWN)
    with pytest.raises(AssertionError, match="must not contain BEGIN"):
        assert_p26_migration_rules(
            "027_synthetic_probe", expect_reversible=True, directory=tmp_path
        )


# ---------------------------------------------------------------------------
# G3 - G7
# ---------------------------------------------------------------------------

def test_g3_rejects_concurrently_inside_a_transactional_migration(tmp_path):
    polluted = RUNNER_OWNED_UP + (
        "CREATE INDEX CONCURRENTLY idx_synthetic ON synthetic_probe (id);\n"
    )
    _write(tmp_path, "027_synthetic_probe", polluted, COMPLIANT_DOWN)
    with pytest.raises(AssertionError, match="CONCURRENTLY"):
        assert_p26_migration_rules(
            "027_synthetic_probe", expect_reversible=True, directory=tmp_path
        )


def test_g4_rejects_a_migration_that_deletes_from_the_ledger(tmp_path):
    polluted = RUNNER_OWNED_UP + (
        "DELETE FROM schema_migrations WHERE version = '027_synthetic_probe';\n"
    )
    _write(tmp_path, "027_synthetic_probe", polluted, COMPLIANT_DOWN)
    with pytest.raises(AssertionError, match="append-only"):
        assert_p26_migration_rules(
            "027_synthetic_probe", expect_reversible=True, directory=tmp_path
        )


def test_g5_rejects_an_environment_guard_on_a_hard_coded_database_name(tmp_path):
    """The 010/014 pattern that P26-0 section 6 rule 6 abandoned.

    The banned shape is a DDL-time gate comparing current_database() against a
    literal database name - the direct cause of TEST and PROD schemas
    diverging. Mentioning current_database() is not itself the offence.
    """
    polluted = (
        "DO $$\nBEGIN\n    IF current_database() <> 'stima360_db_test' THEN\n"
        "        RAISE EXCEPTION 'wrong database';\n    END IF;\nEND $$;\n"
        + RUNNER_OWNED_UP
    )
    _write(tmp_path, "027_synthetic_probe", polluted, COMPLIANT_DOWN)
    with pytest.raises(AssertionError, match="current_database"):
        assert_p26_migration_rules(
            "027_synthetic_probe", expect_reversible=True, directory=tmp_path
        )


def test_g5_permits_current_database_that_is_not_compared_to_a_literal(tmp_path):
    """026's shape: a DEFAULT and a comparison against a column, not a literal."""
    permitted = RUNNER_OWNED_UP.replace(
        "    id BIGSERIAL PRIMARY KEY",
        "    id BIGSERIAL PRIMARY KEY,\n"
        "    database_name TEXT NOT NULL DEFAULT current_database()",
    )
    _write(tmp_path, "027_synthetic_probe", permitted, COMPLIANT_DOWN)
    assert_p26_migration_rules(
        "027_synthetic_probe", expect_reversible=True, directory=tmp_path
    )


def test_g6_rejects_a_secret_shaped_key_written_into_agency_settings(tmp_path):
    polluted = RUNNER_OWNED_UP + (
        "INSERT INTO agencies (name, slug, settings)\n"
        "SELECT 'Probe', 'probe', '{\"smtp_password\": \"hunter2\"}'::jsonb;\n"
    )
    _write(tmp_path, "027_synthetic_probe", polluted, COMPLIANT_DOWN)
    with pytest.raises(AssertionError, match="settings"):
        assert_p26_migration_rules(
            "027_synthetic_probe", expect_reversible=True, directory=tmp_path
        )


def test_g6_permits_a_non_sensitive_settings_payload(tmp_path):
    permitted = RUNNER_OWNED_UP + (
        "INSERT INTO agencies (name, slug, settings)\n"
        "SELECT 'Probe', 'probe', '{\"display_locale\": \"it\"}'::jsonb;\n"
    )
    _write(tmp_path, "027_synthetic_probe", permitted, COMPLIANT_DOWN)
    assert_p26_migration_rules(
        "027_synthetic_probe", expect_reversible=True, directory=tmp_path
    )


def test_g7_rejects_an_irreversible_migration_whose_down_does_not_raise(tmp_path):
    _write(tmp_path, "029_synthetic_probe", RUNNER_OWNED_UP, COMPLIANT_DOWN)
    with pytest.raises(AssertionError, match="RAISE EXCEPTION"):
        assert_p26_migration_rules(
            "029_synthetic_probe", expect_reversible=False, directory=tmp_path
        )


def test_g7_accepts_an_irreversible_migration_whose_down_refuses(tmp_path):
    _write(tmp_path, "029_synthetic_probe", RUNNER_OWNED_UP, REFUSING_DOWN)
    assert_p26_migration_rules(
        "029_synthetic_probe", expect_reversible=False, directory=tmp_path
    )


# ---------------------------------------------------------------------------
# G8 - the down file brackets itself, in both eras.
# ---------------------------------------------------------------------------

def test_g8_rejects_a_down_file_with_no_transaction_block(tmp_path):
    _write(
        tmp_path,
        "027_synthetic_probe",
        RUNNER_OWNED_UP,
        "DROP TABLE IF EXISTS synthetic_probe;\n",
    )
    with pytest.raises(AssertionError, match="down migration must open BEGIN"):
        assert_p26_migration_rules(
            "027_synthetic_probe", expect_reversible=True, directory=tmp_path
        )


def test_g8_applies_below_the_gate_too(tmp_path):
    _write(
        tmp_path,
        "026_synthetic_probe",
        LEGACY_UP,
        "DROP TABLE IF EXISTS synthetic_probe;\n",
    )
    with pytest.raises(AssertionError, match="down migration must open BEGIN"):
        assert_p26_migration_rules(
            "026_synthetic_probe", expect_reversible=True, directory=tmp_path
        )


def test_rules_are_read_from_executable_sql_not_from_comments(tmp_path):
    """Prose in a comment must neither satisfy nor defeat a rule.

    In particular a comment mentioning COMMIT must not trip the G2 rule for a
    runner-owned migration.
    """
    commented = (
        "-- This migration does not DELETE FROM schema_migrations, uses no\n"
        "-- CREATE INDEX CONCURRENTLY, and relies on the runner to BEGIN; and\n"
        "-- COMMIT; around it.\n" + RUNNER_OWNED_UP
    )
    _write(tmp_path, "027_synthetic_probe", commented, COMPLIANT_DOWN)
    assert_p26_migration_rules(
        "027_synthetic_probe", expect_reversible=True, directory=tmp_path
    )


# ---------------------------------------------------------------------------
# The runner's own validate_migration, which is the apply-time gate.
# ---------------------------------------------------------------------------

def test_runner_accepts_a_runner_owned_migration_without_a_transaction_block(
    runner_module, tmp_path
):
    _write(tmp_path, "027_synthetic_probe", RUNNER_OWNED_UP, COMPLIANT_DOWN)
    migration = runner_module.discover_migrations(tmp_path)[0]
    assert runner_module.validate_migration(migration) == []


def test_runner_rejects_a_runner_owned_migration_that_brackets_itself(
    runner_module, tmp_path
):
    _write(tmp_path, "027_synthetic_probe", LEGACY_UP, COMPLIANT_DOWN)
    migration = runner_module.discover_migrations(tmp_path)[0]
    violations = runner_module.validate_migration(migration)
    assert any("BEGIN" in violation for violation in violations), violations


def test_runner_still_requires_a_transaction_block_below_the_gate(
    runner_module, tmp_path
):
    """026's certified rule is preserved exactly."""
    _write(tmp_path, "026_synthetic_probe", BODY, COMPLIANT_DOWN)
    migration = runner_module.discover_migrations(tmp_path)[0]
    violations = runner_module.validate_migration(migration)
    assert any("BEGIN" in violation for violation in violations), violations


def test_runner_accepts_the_legacy_shape_below_the_gate(runner_module, tmp_path):
    _write(tmp_path, "026_synthetic_probe", LEGACY_UP, COMPLIANT_DOWN)
    migration = runner_module.discover_migrations(tmp_path)[0]
    assert runner_module.validate_migration(migration) == []


def test_runner_non_transactional_rules_are_unchanged_at_the_gate(
    runner_module, tmp_path
):
    """A CONCURRENTLY build stays the one admitted exception, in both eras."""
    _write(
        tmp_path,
        "027_synthetic_probe",
        "-- NON-TRANSACTIONAL\nCREATE INDEX CONCURRENTLY idx_x ON t (c);\n",
        COMPLIANT_DOWN,
    )
    migration = runner_module.discover_migrations(tmp_path)[0]
    assert migration.non_transactional is True
    assert runner_module.validate_migration(migration) == []


def test_runner_non_transactional_marker_still_requires_concurrently(
    runner_module, tmp_path
):
    _write(
        tmp_path,
        "027_synthetic_probe",
        "-- NON-TRANSACTIONAL\nALTER TABLE t ADD COLUMN c INTEGER;\n",
        COMPLIANT_DOWN,
    )
    migration = runner_module.discover_migrations(tmp_path)[0]
    violations = runner_module.validate_migration(migration)
    assert any("concurrent index build" in violation for violation in violations)


def test_runner_still_rejects_concurrently_in_a_transactional_migration(
    runner_module, tmp_path
):
    _write(
        tmp_path,
        "027_synthetic_probe",
        "CREATE INDEX CONCURRENTLY idx_x ON t (c);\n",
        COMPLIANT_DOWN,
    )
    migration = runner_module.discover_migrations(tmp_path)[0]
    violations = runner_module.validate_migration(migration)
    assert any("CONCURRENTLY" in violation for violation in violations)


def test_runner_validates_every_migration_on_disk(runner_module):
    """The real repository set must pass the apply-time gate."""
    for migration in runner_module.discover_migrations():
        assert runner_module.validate_migration(migration) == [], migration.version


# ---------------------------------------------------------------------------
# verify_contiguous: the forward-only sequence gate, exercised as a pure
# function against synthetic directories.
# ---------------------------------------------------------------------------

def test_verify_contiguous_rejects_a_gap_in_the_sequence(tmp_path, runner_module):
    _write(tmp_path, "026_synthetic_baseline", LEGACY_UP, COMPLIANT_DOWN)
    _write(tmp_path, "028_synthetic_probe", RUNNER_OWNED_UP, COMPLIANT_DOWN)
    discovered = runner_module.discover_migrations(tmp_path)
    with pytest.raises(runner_module.MigrationError, match="027"):
        runner_module.verify_contiguous(discovered)


def test_verify_contiguous_accepts_an_unbroken_sequence(tmp_path, runner_module):
    _write(tmp_path, "026_synthetic_baseline", LEGACY_UP, COMPLIANT_DOWN)
    _write(tmp_path, "027_synthetic_probe", RUNNER_OWNED_UP, COMPLIANT_DOWN)
    discovered = runner_module.discover_migrations(tmp_path)
    runner_module.verify_contiguous(discovered)


def test_discover_migrations_still_ignores_pre_baseline_versions(tmp_path, runner_module):
    """The forward-only floor is the runner's, not this helper's - assert it holds."""
    _write(tmp_path, "025_synthetic_legacy", LEGACY_UP, COMPLIANT_DOWN)
    _write(tmp_path, "026_synthetic_baseline", LEGACY_UP, COMPLIANT_DOWN)
    discovered = runner_module.discover_migrations(tmp_path)
    assert [item.version for item in discovered] == ["026_synthetic_baseline"]


# ---------------------------------------------------------------------------
# This module must not creep into Task 2 and beyond.
# ---------------------------------------------------------------------------

def test_task_1_asserts_nothing_about_migrations_027_to_030():
    """Task 1 must not reach into Tasks 2, 7, 8 or 9.

    The forbidden stems are composed from parts rather than written out, so
    that this guard does not trip on its own source. Writing them as literals
    here would make the assertion permanently false - which is exactly what a
    first run of this test demonstrated.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    owned_elsewhere = (
        (27, "agency", "identity"),
        (28, "core", "agency", "columns"),
        (29, "core", "agency", "backfill"),
        (30, "core", "agency", "enforce"),
    )
    for number, *words in owned_elsewhere:
        version = "_".join([f"{number:03d}", "p26", *words])
        assert version not in source, (
            f"Task 1 must not reference the real migration {version!r}; "
            "Tasks 2, 7, 8 and 9 own those files and their own RED -> GREEN cycles"
        )


# ---------------------------------------------------------------------------
# Comment awareness in the runner's validator.
#
# The runner is the real apply-time gate, so its rules must describe what a
# migration *does*, not what its prose mentions. Before this fix
# validate_migration scanned raw text, so a comment reading "no CONCURRENTLY
# here" was enough to have the migration refused - which is how 028 was
# rejected while containing no such statement.
#
# The NON-TRANSACTIONAL marker is the deliberate exception: it *is* a comment,
# and is read from the raw header by discover_migrations, so stripping must not
# reach it.
# ---------------------------------------------------------------------------

def test_runner_accepts_a_comment_mentioning_concurrently(runner_module, tmp_path):
    up = (
        "-- This migration uses no CONCURRENTLY: it is an ordinary\n"
        "-- transactional index build owned by the runner.\n" + RUNNER_OWNED_UP
    )
    _write(tmp_path, "027_synthetic_probe", up, COMPLIANT_DOWN)
    migration = runner_module.discover_migrations(tmp_path)[0]
    assert runner_module.validate_migration(migration) == []


def test_runner_accepts_a_comment_mentioning_the_ledger_delete(runner_module, tmp_path):
    up = (
        "-- The ledger is append-only; never DELETE FROM schema_migrations,\n"
        "-- stamp rolled_back_at instead.\n" + RUNNER_OWNED_UP
    )
    _write(tmp_path, "027_synthetic_probe", up, COMPLIANT_DOWN)
    migration = runner_module.discover_migrations(tmp_path)[0]
    assert runner_module.validate_migration(migration) == []


def test_runner_accepts_a_trailing_comment_after_executable_sql(runner_module, tmp_path):
    up = RUNNER_OWNED_UP + "CREATE INDEX IF NOT EXISTS idx_x ON synthetic_probe (id); -- not CONCURRENTLY\n"
    _write(tmp_path, "027_synthetic_probe", up, COMPLIANT_DOWN)
    migration = runner_module.discover_migrations(tmp_path)[0]
    assert runner_module.validate_migration(migration) == []


def test_runner_still_rejects_executable_concurrently(runner_module, tmp_path):
    """Negative control: the enforcement itself must not be weakened."""
    up = RUNNER_OWNED_UP + "CREATE INDEX CONCURRENTLY idx_x ON synthetic_probe (id);\n"
    _write(tmp_path, "027_synthetic_probe", up, COMPLIANT_DOWN)
    migration = runner_module.discover_migrations(tmp_path)[0]
    violations = runner_module.validate_migration(migration)
    assert any("CONCURRENTLY" in violation for violation in violations), violations


def test_runner_still_rejects_an_executable_ledger_delete(runner_module, tmp_path):
    up = RUNNER_OWNED_UP + "DELETE FROM schema_migrations WHERE version = 'x';\n"
    _write(tmp_path, "027_synthetic_probe", up, COMPLIANT_DOWN)
    migration = runner_module.discover_migrations(tmp_path)[0]
    violations = runner_module.validate_migration(migration)
    assert any("append-only" in violation for violation in violations), violations


def test_runner_does_not_let_a_string_literal_hide_a_concurrent_build(
    runner_module, tmp_path
):
    """A naive line-based strip would cut at the '--' inside the literal and
    hide the CONCURRENTLY that follows it - turning a false positive into a far
    worse false negative in a safety check."""
    up = (
        RUNNER_OWNED_UP
        + "INSERT INTO synthetic_probe (note) SELECT 'a -- b';\n"
        + "CREATE INDEX CONCURRENTLY idx_x ON synthetic_probe (id);\n"
    )
    _write(tmp_path, "027_synthetic_probe", up, COMPLIANT_DOWN)
    migration = runner_module.discover_migrations(tmp_path)[0]
    violations = runner_module.validate_migration(migration)
    assert any("CONCURRENTLY" in violation for violation in violations), violations


def test_runner_does_not_let_a_dollar_quoted_body_hide_a_ledger_delete(
    runner_module, tmp_path
):
    """026 and 027 both use dollar-quoted blocks, so they must be handled."""
    up = (
        RUNNER_OWNED_UP
        + "DO $do$ BEGIN RAISE NOTICE 'x -- y'; END $do$;\n"
        + "DELETE FROM schema_migrations WHERE version = 'x';\n"
    )
    _write(tmp_path, "027_synthetic_probe", up, COMPLIANT_DOWN)
    migration = runner_module.discover_migrations(tmp_path)[0]
    violations = runner_module.validate_migration(migration)
    assert any("append-only" in violation for violation in violations), violations


def test_runner_still_reads_the_non_transactional_marker_from_the_raw_header(
    runner_module, tmp_path
):
    """The marker IS a comment. Stripping must not reach the header scan, or
    every concurrent index build would be judged transactional and refused."""
    up = "-- NON-TRANSACTIONAL\nCREATE INDEX CONCURRENTLY idx_x ON t (c);\n"
    _write(tmp_path, "027_synthetic_probe", up, COMPLIANT_DOWN)
    migration = runner_module.discover_migrations(tmp_path)[0]
    assert migration.non_transactional is True
    assert runner_module.validate_migration(migration) == []


def test_runner_comment_stripper_preserves_executable_statements(runner_module):
    """Direct unit check on the stripper itself."""
    strip = runner_module.strip_sql_comments
    assert "CREATE INDEX CONCURRENTLY" in strip("-- note\nCREATE INDEX CONCURRENTLY x;")
    assert "CONCURRENTLY" not in strip("-- CONCURRENTLY is forbidden\nSELECT 1;")
    assert "'a -- b'" in strip("SELECT 'a -- b';")
    assert "keep" in strip("SELECT 1; -- drop\nkeep\n")
    assert strip("$do$ -- inside $do$") == "$do$ -- inside $do$"


def test_026_validation_is_unchanged_by_the_comment_fix(runner_module):
    """The certified baseline must validate exactly as before."""
    baseline = [
        item for item in runner_module.discover_migrations()
        if item.number == runner_module.BASELINE_VERSION
    ][0]
    assert runner_module.validate_migration(baseline) == []
    assert baseline.non_transactional is False
    assert baseline.down_available is True
