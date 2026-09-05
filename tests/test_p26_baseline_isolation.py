"""P26-0 static checks for the baseline migration, snapshot script and runner.

These are offline tests. Nothing here opens a database connection: every
assertion is made against the SQL text, the Python source, or pure functions
that operate on in-memory fixtures. That is deliberate, because the properties
being protected are properties of the artefacts themselves.

Coverage map from the approved spec (section 10.1):

    H1  baseline migration is additive
    H2  baseline migration is transactional
    H3  baseline migration is idempotent in shape
    H4  no retroactive registration
    H5  ledger rejects pre-baseline versions
    H6  no secrets in schema
    H7  baseline down is complete and safe
    H8  snapshot script is read-only
    H9  fingerprint is deterministic
    H10 runner ignores pre-baseline files
    H12 operator identity is not derived from the shared admin login
    H13 ledger rollback never deletes
    H14 a rolled-back version is not re-applied
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
SCRIPTS = ROOT / "scripts"

UP = MIGRATIONS / "026_p26_baseline.sql"
DOWN = MIGRATIONS / "026_p26_baseline_down.sql"
SNAPSHOT = SCRIPTS / "p26_schema_snapshot.py"
RUNNER = SCRIPTS / "p26_migrate.py"
# The canonicaliser is reached from the snapshot script, so it inherits the
# same read-only obligation and is held to the same scan.
CANONICAL = SCRIPTS / "p26_sql_canonical.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # Registered before execution so dataclasses can resolve __module__ under
    # postponed annotation evaluation.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _strip_sql_comments(sql: str) -> str:
    """Return only the executable part of a SQL file.

    Assertions about what a migration *does* must not be satisfied or defeated
    by prose in its comments.
    """
    return "\n".join(
        line.split("--", 1)[0] for line in sql.splitlines()
    )


@pytest.fixture(scope="module")
def up_sql() -> str:
    return UP.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def down_sql() -> str:
    return DOWN.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def snapshot_module():
    return _load(SNAPSHOT, "p26_schema_snapshot")


@pytest.fixture(scope="module")
def runner_module():
    return _load(RUNNER, "p26_migrate")


def test_p26_files_exist():
    for path in (UP, DOWN, SNAPSHOT, RUNNER):
        assert path.exists(), f"missing P26-0 artefact: {path.name}"


# ---------------------------------------------------------------------------
# H1 - the baseline migration is additive
# ---------------------------------------------------------------------------

def test_h1_baseline_migration_is_additive(up_sql):
    forbidden = ("DROP ", "TRUNCATE", "DELETE FROM", "ALTER TABLE")
    for token in forbidden:
        assert token not in up_sql.upper(), (
            f"026 must be additive; found {token!r}"
        )


def test_h1_baseline_migration_touches_no_application_table(up_sql):
    application_tables = (
        "contacts", "leads", "properties", "buy_requests", "matches",
        "stime", "stime_dettagliate", "zone_valori", "tasks", "activities",
        "owner_accounts", "flow_rules", "property_watches",
    )
    upper = up_sql.upper()
    for table in application_tables:
        assert f" {table.upper()} " not in upper, (
            f"026 must not reference the application table {table!r}"
        )


# ---------------------------------------------------------------------------
# H2 - transactional
# ---------------------------------------------------------------------------

def test_h2_baseline_migration_is_transactional(up_sql):
    stripped = up_sql.strip()
    statements = [
        line.strip().upper()
        for line in stripped.splitlines()
        if line.strip() and not line.strip().startswith("--")
    ]
    assert statements[0].startswith("BEGIN"), "026 must open with BEGIN;"
    assert statements[-1].startswith("COMMIT"), "026 must close with COMMIT;"


def test_h2_down_migration_is_transactional(down_sql):
    statements = [
        line.strip().upper()
        for line in down_sql.strip().splitlines()
        if line.strip() and not line.strip().startswith("--")
    ]
    assert statements[0].startswith("BEGIN")
    assert statements[-1].startswith("COMMIT")


# ---------------------------------------------------------------------------
# H3 - idempotent in shape
# ---------------------------------------------------------------------------

def test_h3_tables_are_created_idempotently(up_sql):
    for table in ("schema_migrations", "schema_baseline"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in up_sql


def test_h3_index_is_created_idempotently(up_sql):
    assert "CREATE INDEX IF NOT EXISTS" in up_sql


def test_h3_guard_function_uses_create_or_replace(up_sql):
    assert "CREATE OR REPLACE FUNCTION schema_ledger_guard()" in up_sql


def test_h3_triggers_use_catalogue_check_not_if_not_exists(up_sql):
    # CREATE TRIGGER IF NOT EXISTS does not exist in PostgreSQL. Comments are
    # stripped so the prose explaining that fact cannot trip the assertion.
    executable = _strip_sql_comments(up_sql).upper()
    assert "CREATE TRIGGER IF NOT EXISTS" not in executable
    assert "FROM pg_trigger" in up_sql, (
        "trigger creation must consult pg_trigger to stay idempotent"
    )
    assert up_sql.count("NOT t.tgisinternal") == 2, (
        "both catalogue checks must exclude internal constraint triggers"
    )
    for trigger in ("trg_schema_migrations_guard", "trg_schema_baseline_guard"):
        assert f"CREATE TRIGGER {trigger}" in up_sql


def test_h3_baseline_row_insert_is_idempotent(up_sql):
    assert "WHERE NOT EXISTS (SELECT 1 FROM schema_baseline)" in up_sql


# ---------------------------------------------------------------------------
# H4 - no retroactive registration
# ---------------------------------------------------------------------------

def test_h4_no_insert_into_schema_migrations(up_sql):
    assert not re.search(
        r"INSERT\s+INTO\s+schema_migrations", up_sql, re.IGNORECASE
    ), "026 must never register a migration, least of all a historical one"


def test_h4_only_insert_is_the_baseline_row(up_sql):
    inserts = re.findall(r"INSERT\s+INTO\s+(\w+)", up_sql, re.IGNORECASE)
    assert inserts == ["schema_baseline"], (
        f"026 must contain exactly one INSERT, into schema_baseline; found {inserts}"
    )


def test_h4_no_historical_version_appears_in_the_migration(up_sql):
    for number in range(1, 26):
        assert f"'{number:03d}_" not in up_sql, (
            f"026 must not reference historical migration {number:03d}"
        )


def test_h4_baseline_cannot_claim_pre_baseline_tracking(up_sql):
    assert "pre_baseline_tracked = FALSE" in up_sql
    assert "schema_baseline_not_retroactive" in up_sql


# ---------------------------------------------------------------------------
# H5 - the ledger rejects pre-baseline versions
# ---------------------------------------------------------------------------

def _extract_version_regex(up_sql: str) -> str:
    match = re.search(
        r"CONSTRAINT schema_migrations_no_pre_baseline CHECK \(\s*"
        r"version ~ '([^']+)'",
        up_sql,
    )
    assert match, "the no_pre_baseline constraint must be present and readable"
    return match.group(1)


def test_h5_constraint_is_present(up_sql):
    assert "schema_migrations_no_pre_baseline" in up_sql


@pytest.mark.parametrize("number", list(range(1, 26)))
def test_h5_regex_rejects_every_pre_baseline_version(up_sql, number):
    pattern = re.compile(_extract_version_regex(up_sql))
    assert not pattern.match(f"{number:03d}_some_migration"), (
        f"version {number:03d} must be rejected by the ledger constraint"
    )


@pytest.mark.parametrize("number", [26, 27, 30, 42, 99, 100, 250, 999])
def test_h5_regex_accepts_baseline_and_later_versions(up_sql, number):
    pattern = re.compile(_extract_version_regex(up_sql))
    assert pattern.match(f"{number:03d}_some_migration"), (
        f"version {number:03d} must be accepted by the ledger constraint"
    )


def test_h5_constraint_avoids_a_cast(up_sql):
    # A substring cast would raise on a malformed version instead of rejecting
    # it, and CHECK evaluation order is not guaranteed.
    section = up_sql.split("schema_migrations_no_pre_baseline")[1][:400]
    assert "::int" not in section and "::integer" not in section


def test_h5_operator_identity_is_mandatory(up_sql):
    assert "applied_by_operator     TEXT        NOT NULL" in up_sql
    assert "schema_migrations_operator_present" in up_sql


# ---------------------------------------------------------------------------
# H6 - no secrets
# ---------------------------------------------------------------------------

FORBIDDEN_SECRET_TOKENS = (
    "password", "smtp_pass", "api_token", "secret", "credential",
    "access_key", "private_key", "bearer",
)


@pytest.mark.parametrize("token", FORBIDDEN_SECRET_TOKENS)
def test_h6_no_secret_identifiers_in_p26_ddl(up_sql, down_sql, token):
    for name, sql in (("026", up_sql), ("026_down", down_sql)):
        assert not re.search(rf"\b{token}\b", sql, re.IGNORECASE), (
            f"{name} must not contain the identifier {token!r}"
        )


def test_h6_no_connection_string_in_p26_artefacts(up_sql, down_sql):
    for sql in (up_sql, down_sql):
        assert "postgres://" not in sql
        assert "postgresql://" not in sql


# ---------------------------------------------------------------------------
# H7 - the down is complete and safe
# ---------------------------------------------------------------------------

def test_h7_down_removes_exactly_the_five_created_objects(down_sql):
    expected = (
        "DROP TRIGGER IF EXISTS trg_schema_migrations_guard ON schema_migrations;",
        "DROP TRIGGER IF EXISTS trg_schema_baseline_guard ON schema_baseline;",
        "DROP TABLE IF EXISTS schema_migrations;",
        "DROP TABLE IF EXISTS schema_baseline;",
        "DROP FUNCTION IF EXISTS schema_ledger_guard();",
    )
    for statement in expected:
        assert statement in down_sql, f"down must contain: {statement}"


def test_h7_down_drops_nothing_else(down_sql):
    dropped = re.findall(
        r"DROP\s+(?:TRIGGER|TABLE|FUNCTION|INDEX|VIEW|SCHEMA|COLUMN)"
        r"(?:\s+IF\s+EXISTS)?\s+([a-z_][a-z0-9_]*)",
        down_sql,
        re.IGNORECASE,
    )
    assert sorted(set(dropped)) == sorted({
        "trg_schema_migrations_guard",
        "trg_schema_baseline_guard",
        "schema_migrations",
        "schema_baseline",
        "schema_ledger_guard",
    })


def test_h7_down_touches_no_application_data(down_sql):
    assert not re.search(r"\bDELETE\s+FROM\b", down_sql, re.IGNORECASE)
    assert not re.search(r"\bTRUNCATE\b", down_sql, re.IGNORECASE)
    assert not re.search(r"\bUPDATE\s+\w+\s+SET\b", down_sql, re.IGNORECASE)


def test_h7_down_refuses_when_later_migrations_are_registered(down_sql):
    assert "RAISE EXCEPTION" in down_sql
    assert "Rollback 026 refused" in down_sql


# ---------------------------------------------------------------------------
# H8 - the snapshot script is read-only
# ---------------------------------------------------------------------------

WRITE_KEYWORDS = (
    "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP",
    "TRUNCATE", "GRANT", "REVOKE",
)


READ_ONLY_SCRIPTS = (SNAPSHOT, CANONICAL)


@pytest.mark.parametrize("keyword", WRITE_KEYWORDS)
@pytest.mark.parametrize("path", READ_ONLY_SCRIPTS, ids=lambda p: p.name)
def test_h8_snapshot_script_contains_no_write_keyword(path, keyword):
    source = path.read_text(encoding="utf-8")
    assert not re.search(rf"\b{keyword}\b", source, re.IGNORECASE), (
        f"{path.name} must not contain {keyword!r} in any form"
    )


def test_h8_snapshot_script_sets_a_read_only_transaction():
    source = SNAPSHOT.read_text(encoding="utf-8")
    assert "SET TRANSACTION READ ONLY" in source


def test_h8_snapshot_script_writes_only_under_reports(snapshot_module):
    assert snapshot_module.REPORTS_DIR == ROOT / "reports"
    with pytest.raises(snapshot_module.GuardFailure):
        snapshot_module._reports_path("../escaped.json")


def test_h8_snapshot_script_guards_against_production(snapshot_module):
    guard = snapshot_module.assert_test_database_name
    for rejected in ("stima360_db", "stima360", "", None, "production_db"):
        with pytest.raises(snapshot_module.GuardFailure):
            guard(rejected)
    assert guard("stima360_db_test") == "stima360_db_test"


def test_h8_snapshot_script_never_prints_credentials():
    source = SNAPSHOT.read_text(encoding="utf-8")
    for sensitive in ("DB_PASSWORD", "DB_USER"):
        for line in source.splitlines():
            if "print(" in line:
                assert sensitive not in line, (
                    f"the snapshot script must never print {sensitive}"
                )


# ---------------------------------------------------------------------------
# H9 - the fingerprint is deterministic
# ---------------------------------------------------------------------------

def test_h9_fingerprint_is_stable_across_runs(snapshot_module):
    payload = {
        "snapshot_format": "p26-snapshot-1",
        "tables": [{"table_name": "contacts", "table_type": "BASE TABLE"}],
        "columns": [{"table_name": "contacts", "column_name": "id"}],
    }
    assert snapshot_module.fingerprint(payload) == snapshot_module.fingerprint(payload)


def test_h9_fingerprint_ignores_key_insertion_order(snapshot_module):
    first = {"a": [1, 2], "b": {"x": 1, "y": 2}}
    second = {"b": {"y": 2, "x": 1}, "a": [1, 2]}
    assert snapshot_module.fingerprint(first) == snapshot_module.fingerprint(second)


def test_h9_fingerprint_changes_when_the_schema_changes(snapshot_module):
    base = {"tables": [{"table_name": "contacts"}]}
    changed = {"tables": [{"table_name": "contacts"}, {"table_name": "agencies"}]}
    assert snapshot_module.fingerprint(base) != snapshot_module.fingerprint(changed)


def test_h9_row_order_from_the_server_does_not_change_the_fingerprint(snapshot_module):
    headers = ["table_name", "column_name"]
    forward = snapshot_module.normalise_rows([("a", "x"), ("b", "y")], headers)
    reverse = snapshot_module.normalise_rows([("b", "y"), ("a", "x")], headers)
    assert forward == reverse


def test_h9_fingerprint_excludes_volatile_metadata(snapshot_module):
    payload = {"tables": []}
    digest = snapshot_module.fingerprint(payload)
    first = snapshot_module.build_document(payload, digest, "db_test", "20260905T000000Z")
    second = snapshot_module.build_document(payload, digest, "db_test", "20991231T235959Z")
    assert first["schema_fingerprint"] == second["schema_fingerprint"]
    assert first["metadata"] != second["metadata"]


# ---------------------------------------------------------------------------
# H10 - the runner ignores pre-baseline files
# ---------------------------------------------------------------------------

def _write_migration(directory: Path, number: int, transactional: bool = True) -> None:
    body = "BEGIN;\nSELECT 1;\nCOMMIT;\n" if transactional else "SELECT 1;\n"
    (directory / f"{number:03d}_fixture_{number}.sql").write_text(body, encoding="utf-8")
    (directory / f"{number:03d}_fixture_{number}_down.sql").write_text(
        "BEGIN;\nSELECT 1;\nCOMMIT;\n", encoding="utf-8"
    )


def test_h10_runner_selects_only_versions_from_026(runner_module, tmp_path):
    for number in range(1, 31):
        _write_migration(tmp_path, number)
    discovered = runner_module.discover_migrations(tmp_path)
    assert [item.number for item in discovered] == list(range(26, 31))


def test_h10_runner_min_version_is_026(runner_module):
    assert runner_module.MIN_VERSION == 26


def test_h10_runner_ignores_down_files(runner_module, tmp_path):
    _write_migration(tmp_path, 26)
    discovered = runner_module.discover_migrations(tmp_path)
    assert [item.version for item in discovered] == ["026_fixture_26"]


def test_h10_real_repository_migrations_are_filtered(runner_module):
    discovered = runner_module.discover_migrations()
    numbers = [item.number for item in discovered]
    assert numbers, "026 must be discoverable"
    assert min(numbers) >= 26
    assert all(number >= 26 for number in numbers)


def test_h10_historical_migrations_are_still_on_disk():
    # The forward-only gate must not have been implemented by moving files.
    for number in range(1, 26):
        matches = list(MIGRATIONS.glob(f"{number:03d}_*.sql"))
        assert matches, f"historical migration {number:03d} must remain in place"


def test_h10_version_parsing_rejects_pre_baseline_and_down(runner_module):
    assert runner_module.parse_version("026_p26_baseline.sql") == (26, "026_p26_baseline")
    assert runner_module.parse_version("026_p26_baseline_down.sql") is None
    assert runner_module.parse_version("not_a_migration.sql") is None


def test_h10_gap_in_versions_is_rejected(runner_module, tmp_path):
    _write_migration(tmp_path, 26)
    _write_migration(tmp_path, 28)
    discovered = runner_module.discover_migrations(tmp_path)
    with pytest.raises(runner_module.MigrationError):
        runner_module.verify_contiguous(discovered)


def test_h10_repository_migration_set_is_contiguous(runner_module):
    runner_module.verify_contiguous(runner_module.discover_migrations())


# ---------------------------------------------------------------------------
# Transaction rules (spec section 6, rules 3 and 10)
# ---------------------------------------------------------------------------

def test_ordinary_migration_must_be_transactional(runner_module, tmp_path):
    _write_migration(tmp_path, 26, transactional=False)
    migration = runner_module.discover_migrations(tmp_path)[0]
    violations = runner_module.validate_migration(migration)
    assert any("BEGIN" in violation for violation in violations)


def test_concurrently_is_refused_in_an_ordinary_migration(runner_module, tmp_path):
    (tmp_path / "026_fixture.sql").write_text(
        "BEGIN;\nCREATE INDEX CONCURRENTLY idx_x ON t (c);\nCOMMIT;\n",
        encoding="utf-8",
    )
    (tmp_path / "026_fixture_down.sql").write_text("BEGIN;\nCOMMIT;\n", encoding="utf-8")
    migration = runner_module.discover_migrations(tmp_path)[0]
    violations = runner_module.validate_migration(migration)
    assert any("CONCURRENTLY" in violation for violation in violations)


def test_non_transactional_migration_is_accepted_only_for_concurrently(
    runner_module, tmp_path
):
    (tmp_path / "026_fixture.sql").write_text(
        "-- NON-TRANSACTIONAL\nCREATE INDEX CONCURRENTLY idx_x ON t (c);\n",
        encoding="utf-8",
    )
    (tmp_path / "026_fixture_down.sql").write_text("BEGIN;\nCOMMIT;\n", encoding="utf-8")
    migration = runner_module.discover_migrations(tmp_path)[0]
    assert migration.non_transactional is True
    assert runner_module.validate_migration(migration) == []


def test_non_transactional_marker_without_concurrently_is_refused(
    runner_module, tmp_path
):
    (tmp_path / "026_fixture.sql").write_text(
        "-- NON-TRANSACTIONAL\nALTER TABLE t ADD COLUMN c INTEGER;\n",
        encoding="utf-8",
    )
    (tmp_path / "026_fixture_down.sql").write_text("BEGIN;\nCOMMIT;\n", encoding="utf-8")
    migration = runner_module.discover_migrations(tmp_path)[0]
    violations = runner_module.validate_migration(migration)
    assert any("concurrent index build" in violation for violation in violations)


def test_marker_is_only_honoured_in_the_header(runner_module, tmp_path):
    body = "BEGIN;\n" + "SELECT 1;\n" * 20 + "-- NON-TRANSACTIONAL\nCOMMIT;\n"
    (tmp_path / "026_fixture.sql").write_text(body, encoding="utf-8")
    (tmp_path / "026_fixture_down.sql").write_text("BEGIN;\nCOMMIT;\n", encoding="utf-8")
    migration = runner_module.discover_migrations(tmp_path)[0]
    assert migration.non_transactional is False


def test_missing_down_file_is_a_violation(runner_module, tmp_path):
    (tmp_path / "026_fixture.sql").write_text("BEGIN;\nCOMMIT;\n", encoding="utf-8")
    migration = runner_module.discover_migrations(tmp_path)[0]
    violations = runner_module.validate_migration(migration)
    assert any("no down file" in violation for violation in violations)


def test_repository_migration_026_passes_static_validation(runner_module):
    for migration in runner_module.discover_migrations():
        assert runner_module.validate_migration(migration) == []


# ---------------------------------------------------------------------------
# H12 - the operator identity is explicit, never the shared admin login
# ---------------------------------------------------------------------------

def test_h12_operator_is_required(runner_module):
    for rejected in (None, "", "   ", "ab"):
        with pytest.raises(runner_module.GuardFailure):
            runner_module.assert_operator_identity(rejected)


def test_h12_operator_is_accepted_when_explicit(runner_module):
    assert runner_module.assert_operator_identity(" giorgio.larasa ") == "giorgio.larasa"


def test_h12_shared_admin_login_is_refused_as_an_identity(runner_module, monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "giorgio")
    with pytest.raises(runner_module.GuardFailure):
        runner_module.assert_operator_identity("giorgio")


def test_h12_operator_is_never_read_from_the_environment():
    source = RUNNER.read_text(encoding="utf-8")
    # The only permitted mention of the shared login is the refusal check.
    assert 'getenv("ADMIN_USER")' in source
    assert source.count("ADMIN_USER") == 1
    assert not re.search(
        r"operator\s*=\s*os\.getenv", source
    ), "the operator identity must come from the command line only"
    assert not re.search(r"getenv\(\s*[\"']ADMIN_PASS", source)


def test_h12_ledger_insert_uses_the_supplied_operator():
    source = RUNNER.read_text(encoding="utf-8")
    insert_block = source.split("INSERT INTO schema_migrations")[1][:600]
    assert "operator," in insert_block


# ---------------------------------------------------------------------------
# H13 - the ledger is append-only
# ---------------------------------------------------------------------------

def test_h13_no_delete_against_the_ledger_anywhere(up_sql, down_sql):
    runner_source = RUNNER.read_text(encoding="utf-8")
    for name, text in (
        ("026", up_sql), ("026_down", down_sql), ("runner", runner_source)
    ):
        assert not re.search(
            r"DELETE\s+FROM\s+schema_migrations", text, re.IGNORECASE
        ), f"{name} must never delete a ledger row"


def test_h13_rollback_columns_exist_and_are_consistent(up_sql):
    assert "rolled_back_at          TIMESTAMPTZ" in up_sql
    assert "rolled_back_by_operator TEXT" in up_sql
    assert "schema_migrations_rollback_consistency" in up_sql


def test_h13_rollback_requires_both_columns_together(up_sql):
    section = up_sql.split("schema_migrations_rollback_consistency")[1][:400]
    assert "rolled_back_at IS NULL AND rolled_back_by_operator IS NULL" in section
    assert "rolled_back_at IS NOT NULL" in section


def test_h13_runner_declares_the_ledger_append_only():
    source = RUNNER.read_text(encoding="utf-8")
    assert "Never removes or replaces a row" in source


# ---------------------------------------------------------------------------
# H14 - a rolled-back version is never re-applied
# ---------------------------------------------------------------------------

def test_h14_rolled_back_version_is_not_scheduled(runner_module, tmp_path):
    _write_migration(tmp_path, 26)
    _write_migration(tmp_path, 27)
    migrations = runner_module.discover_migrations(tmp_path)
    target = next(item for item in migrations if item.number == 27)
    plan = runner_module.build_plan(
        migrations,
        [{
            "version": target.version,
            "checksum_up": target.checksum_up,
            "rolled_back_at": "2026-09-05T10:00:00Z",
        }],
        baseline_present=True,
    )
    assert [item.version for item in plan.rolled_back] == [target.version]
    assert target.version not in [item.version for item in plan.to_apply]
    assert plan.problems == []


def test_h14_applied_version_is_not_reapplied(runner_module, tmp_path):
    _write_migration(tmp_path, 26)
    _write_migration(tmp_path, 27)
    migrations = runner_module.discover_migrations(tmp_path)
    target = next(item for item in migrations if item.number == 27)
    plan = runner_module.build_plan(
        migrations,
        [{
            "version": target.version,
            "checksum_up": target.checksum_up,
            "rolled_back_at": None,
        }],
        baseline_present=True,
    )
    assert [item.version for item in plan.already_applied] == [
        "026_fixture_26", target.version
    ]
    assert plan.to_apply == []


def test_h14_edited_registered_migration_is_a_problem(runner_module, tmp_path):
    _write_migration(tmp_path, 26)
    _write_migration(tmp_path, 27)
    migrations = runner_module.discover_migrations(tmp_path)
    target = next(item for item in migrations if item.number == 27)
    plan = runner_module.build_plan(
        migrations,
        [{
            "version": target.version,
            "checksum_up": "0" * 64,
            "rolled_back_at": None,
        }],
        baseline_present=True,
    )
    assert any("changed after it was applied" in problem for problem in plan.problems)


def test_h14_ledger_row_without_a_file_is_a_problem(runner_module, tmp_path):
    _write_migration(tmp_path, 26)
    migrations = runner_module.discover_migrations(tmp_path)
    plan = runner_module.build_plan(
        migrations,
        [{"version": "099_vanished", "checksum_up": "0" * 64, "rolled_back_at": None}],
        baseline_present=True,
    )
    assert any("absent from disk" in problem for problem in plan.problems)


def test_h14_nothing_runs_before_the_baseline(runner_module, tmp_path):
    _write_migration(tmp_path, 26)
    _write_migration(tmp_path, 27)
    migrations = runner_module.discover_migrations(tmp_path)
    plan = runner_module.build_plan(migrations, [], baseline_present=False)
    assert [item.number for item in plan.to_apply] == [26]
    assert any("apply 026 first" in problem for problem in plan.problems)


def test_h14_baseline_is_not_registered_in_the_ledger(runner_module, tmp_path):
    _write_migration(tmp_path, 26)
    migrations = runner_module.discover_migrations(tmp_path)
    plan = runner_module.build_plan(migrations, [], baseline_present=True)
    # 026 installs the ledger, so schema_baseline is its record, not a row in
    # schema_migrations.
    assert [item.version for item in plan.already_applied] == ["026_fixture_26"]
    assert plan.to_apply == []


# ---------------------------------------------------------------------------
# Runner safety guards
# ---------------------------------------------------------------------------

def test_runner_refuses_production_databases(runner_module):
    for rejected in ("stima360_db", "stima360", "", None, "live"):
        with pytest.raises(runner_module.GuardFailure):
            runner_module.assert_test_database_name(rejected)
    assert runner_module.assert_test_database_name("stima360_db_test") == "stima360_db_test"


def test_runner_requires_an_explicit_subcommand(runner_module):
    with pytest.raises(SystemExit):
        runner_module.build_parser().parse_args([])


@pytest.mark.parametrize(
    "argv",
    [
        ["plan", "--operator", "giorgio.larasa"],
        ["--operator", "giorgio.larasa", "plan"],
    ],
)
def test_runner_accepts_the_operator_on_either_side_of_the_subcommand(
    runner_module, argv
):
    parsed = runner_module._apply_shared_defaults(
        runner_module.build_parser().parse_args(argv)
    )
    assert parsed.command == "plan"
    assert parsed.operator == "giorgio.larasa"


def test_runner_applies_shared_defaults_when_options_are_absent(runner_module):
    parsed = runner_module._apply_shared_defaults(
        runner_module.build_parser().parse_args(["plan"])
    )
    assert parsed.operator is None
    assert parsed.baseline_version == runner_module.BASELINE_VERSION_LABEL
    assert parsed.baseline_fingerprint is None
    assert parsed.baseline_artifact is None


def test_runner_plan_command_touches_no_database(runner_module):
    source = RUNNER.read_text(encoding="utf-8")
    body = source.split("def command_plan(")[1].split("def command_apply(")[0]
    assert "connect(" not in body


def test_baseline_application_requires_a_real_fingerprint(up_sql):
    # current_setting without a fallback raises when the value is absent, so
    # 026 cannot be applied with an invented or placeholder fingerprint.
    executable = _strip_sql_comments(up_sql)
    assert "current_setting('p26.schema_fingerprint')" in executable
    assert "missing_ok" not in executable
    assert "schema_baseline_fingerprint_shape" in up_sql
    assert "'^[0-9a-f]{64}$'" in up_sql
