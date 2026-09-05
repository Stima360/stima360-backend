#!/usr/bin/env python3
"""P26 forward-only migration runner.

The runner executes migrations from version 026 onward and records each one in
``schema_migrations``. Versions below 026 are structurally invisible to it:
migrations 001-025 stay where they are in ``migrations/`` and are never
candidates for execution, regardless of file layout.

Design boundaries, all enforced below.

* **Forward-only.** Only versions ``>= 026`` are discovered. The ledger table
  carries the same rule as a CHECK constraint, so the gate holds even if this
  script is bypassed.
* **Not a schema-diff engine.** The runner verifies baseline presence, database
  identity, applied versions, registered checksums and version ordering. It
  does not recompute a schema fingerprint on every run; the fingerprint is
  audit evidence held in ``schema_baseline``.
* **Explicit operator identity.** ``--operator`` is mandatory and is never
  derived from environment credentials.
* **Append-only ledger.** Nothing here removes a ledger row. A rolled-back
  version is consumed: it is never re-executed and never overwritten.
* **Transactions.** An ordinary migration runs inside one transaction together
  with its ledger row. The single exception is a migration explicitly marked
  non-transactional for a concurrent index build.
* **TEST only.** P26-0 refuses to run against production.

Typical use::

    python scripts/p26_migrate.py status  --operator "giorgio.larasa"
    python scripts/p26_migrate.py plan    --operator "giorgio.larasa"
    python scripts/p26_migrate.py apply   --operator "giorgio.larasa" \\
        --baseline-fingerprint <sha256> \\
        --baseline-artifact reports/p26_baseline_TEST_<ts>.json

Nothing is executed against the database unless the ``apply`` subcommand is
used. ``status`` and ``plan`` are read-only.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = REPOSITORY_ROOT / "migrations"

# The first version the ledger accepts. Everything below is pre-baseline.
MIN_VERSION = 26
# 026 installs the ledger itself, so it cannot register itself inside it.
BASELINE_VERSION = 26
BASELINE_VERSION_LABEL = "P26-BASELINE-001"

PROD_DATABASE_NAMES = frozenset({"stima360_db", "stima360"})
REQUIRED_NAME_MARKER = "test"

MIGRATION_NAME_RE = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")
NON_TRANSACTIONAL_MARKER = "-- NON-TRANSACTIONAL"
CONCURRENTLY_RE = re.compile(r"\bCONCURRENTLY\b", re.IGNORECASE)
BEGIN_RE = re.compile(r"^\s*BEGIN\s*;", re.IGNORECASE | re.MULTILINE)
COMMIT_RE = re.compile(r"^\s*COMMIT\s*;", re.IGNORECASE | re.MULTILINE)


class GuardFailure(RuntimeError):
    """Raised when a safety precondition is not satisfied."""


class MigrationError(RuntimeError):
    """Raised when the migration set on disk is not consistent."""


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

def assert_test_database_name(database_name: str | None) -> str:
    """Return ``database_name`` when it identifies a permitted TEST target."""
    name = (database_name or "").strip()
    if not name:
        raise GuardFailure(
            "BLOCKED: DB_NAME is not set. The runner refuses to guess a target."
        )
    lowered = name.lower()
    if lowered in PROD_DATABASE_NAMES:
        raise GuardFailure(
            f"BLOCKED: {name!r} is a production database. P26-0 is TEST only."
        )
    if REQUIRED_NAME_MARKER not in lowered:
        raise GuardFailure(
            f"BLOCKED: {name!r} is not identifiable as a TEST database "
            f"(expected the marker {REQUIRED_NAME_MARKER!r})."
        )
    return name


def assert_operator_identity(operator: str | None) -> str:
    """Validate the operator identity supplied on the command line.

    The identity must be given explicitly by the person running the migration.
    It is never read from the environment: the administrative credentials are
    a shared emergency login, not an auditable identity, and the ledger must
    not inherit that ambiguity.
    """
    identity = (operator or "").strip()
    if not identity:
        raise GuardFailure(
            "BLOCKED: --operator is required. The ledger records a real, "
            "auditable identity for every applied migration."
        )
    if len(identity) < 3:
        raise GuardFailure(
            "BLOCKED: --operator is too short to identify a person."
        )
    shared_admin_login = os.getenv("ADMIN_USER")
    if shared_admin_login and identity == shared_admin_login:
        raise GuardFailure(
            "BLOCKED: the shared administrative login is not an operator "
            "identity. Supply the real person running this migration."
        )
    return identity


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Migration:
    number: int
    version: str
    path: Path
    down_path: Path | None
    checksum_up: str
    checksum_down: str | None
    non_transactional: bool

    @property
    def is_baseline(self) -> bool:
        return self.number == BASELINE_VERSION

    @property
    def down_available(self) -> bool:
        return self.down_path is not None


def sha256_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_of_file(path: Path) -> str:
    return sha256_of_text(path.read_text(encoding="utf-8"))


def parse_version(filename: str) -> tuple[int, str] | None:
    """Return ``(number, version)`` for a migration file, else ``None``.

    Down files and any name not matching the convention return ``None``.
    """
    match = MIGRATION_NAME_RE.match(filename)
    if not match:
        return None
    stem = filename[:-4]
    if stem.endswith("_down"):
        return None
    return int(match.group(1)), stem


def is_non_transactional(sql_text: str) -> bool:
    """True when the file declares itself non-transactional in its header.

    Only the first lines are inspected, so the marker cannot be smuggled in
    lower down the file.
    """
    header = "\n".join(sql_text.splitlines()[:15])
    return NON_TRANSACTIONAL_MARKER in header


def discover_migrations(directory: Path | None = None) -> list[Migration]:
    """Return every eligible migration, ordered by version.

    Versions below :data:`MIN_VERSION` are filtered out here. This is the
    runner-side half of the forward-only gate; the other half is the CHECK
    constraint on ``schema_migrations``.
    """
    base = directory or MIGRATIONS_DIR
    found: list[Migration] = []
    for path in sorted(base.glob("*.sql")):
        parsed = parse_version(path.name)
        if parsed is None:
            continue
        number, version = parsed
        if number < MIN_VERSION:
            # Pre-baseline. Structurally ignored, never a candidate.
            continue
        text = path.read_text(encoding="utf-8")
        down_path = base / f"{version}_down.sql"
        found.append(
            Migration(
                number=number,
                version=version,
                path=path,
                down_path=down_path if down_path.exists() else None,
                checksum_up=sha256_of_text(text),
                checksum_down=(
                    sha256_of_file(down_path) if down_path.exists() else None
                ),
                non_transactional=is_non_transactional(text),
            )
        )
    found.sort(key=lambda item: item.number)
    return found


# ---------------------------------------------------------------------------
# Static validation
# ---------------------------------------------------------------------------

def validate_migration(migration: Migration, sql_text: str | None = None) -> list[str]:
    """Return a list of rule violations for one migration file."""
    text = sql_text if sql_text is not None else migration.path.read_text(encoding="utf-8")
    violations: list[str] = []

    has_begin = bool(BEGIN_RE.search(text))
    has_commit = bool(COMMIT_RE.search(text))
    has_concurrently = bool(CONCURRENTLY_RE.search(text))

    if migration.non_transactional:
        # The concurrent index build is the only admitted exception.
        if has_begin or has_commit:
            violations.append(
                f"{migration.version}: declared non-transactional but opens an "
                "explicit transaction block"
            )
        if not has_concurrently:
            violations.append(
                f"{migration.version}: declared non-transactional but performs "
                "no concurrent index build; the marker is only for CONCURRENTLY"
            )
    else:
        if not has_begin or not has_commit:
            violations.append(
                f"{migration.version}: an ordinary migration must open BEGIN "
                "and close COMMIT"
            )
        if has_concurrently:
            violations.append(
                f"{migration.version}: CONCURRENTLY cannot run inside a "
                "transaction; move it to a dedicated migration marked "
                f"'{NON_TRANSACTIONAL_MARKER}'"
            )

    if not migration.down_available:
        violations.append(
            f"{migration.version}: no down file. Every migration ships a down, "
            "even one that refuses loudly."
        )

    if re.search(r"DELETE\s+FROM\s+schema_migrations", text, re.IGNORECASE):
        violations.append(
            f"{migration.version}: the ledger is append-only; record a "
            "rollback with rolled_back_at instead of removing the row"
        )

    return violations


def verify_contiguous(migrations: list[Migration]) -> None:
    """Versions must run from 026 upward with no gap and no duplicate."""
    if not migrations:
        return
    numbers = [item.number for item in migrations]
    if len(set(numbers)) != len(numbers):
        raise MigrationError(f"duplicate migration versions: {numbers}")
    if numbers[0] != MIN_VERSION:
        raise MigrationError(
            f"the first migration must be {MIN_VERSION:03d}, found {numbers[0]:03d}"
        )
    expected = list(range(numbers[0], numbers[0] + len(numbers)))
    if numbers != expected:
        missing = sorted(set(expected) - set(numbers))
        raise MigrationError(
            f"gap in migration versions; missing {[f'{n:03d}' for n in missing]}"
        )


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

@dataclass
class Plan:
    to_apply: list[Migration] = field(default_factory=list)
    already_applied: list[Migration] = field(default_factory=list)
    rolled_back: list[Migration] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def build_plan(
    migrations: list[Migration],
    ledger_rows: list[dict],
    baseline_present: bool,
) -> Plan:
    """Decide what to run, given the files on disk and the ledger contents.

    ``ledger_rows`` holds one dictionary per ``schema_migrations`` row, with at
    least ``version``, ``checksum_up`` and ``rolled_back_at``.
    """
    plan = Plan()
    by_version = {item.version: item for item in migrations}
    ledger_by_version = {row["version"]: row for row in ledger_rows}

    # Registry gate: the ledger must not reference a file that is not present.
    for version in sorted(ledger_by_version):
        if version not in by_version:
            plan.problems.append(
                f"{version}: registered in the ledger but absent from disk"
            )

    for migration in migrations:
        if migration.is_baseline:
            # 026 installs the ledger, so its record lives in schema_baseline.
            if baseline_present:
                plan.already_applied.append(migration)
            else:
                plan.to_apply.append(migration)
            continue

        if not baseline_present:
            plan.problems.append(
                f"{migration.version}: refused because no certified baseline "
                "is present; apply 026 first"
            )
            continue

        row = ledger_by_version.get(migration.version)
        if row is None:
            plan.to_apply.append(migration)
            continue

        if row.get("rolled_back_at") is not None:
            # Consumed. Never re-executed under the same version; a correction
            # ships as a new version.
            plan.rolled_back.append(migration)
            continue

        recorded = (row.get("checksum_up") or "").strip()
        if recorded != migration.checksum_up:
            plan.problems.append(
                f"{migration.version}: file content changed after it was "
                f"applied (ledger {recorded[:12]}..., disk "
                f"{migration.checksum_up[:12]}...). A registered migration is "
                "never edited; ship a correction as a new version."
            )
            continue

        plan.already_applied.append(migration)

    return plan


# ---------------------------------------------------------------------------
# Database access
# ---------------------------------------------------------------------------

def connect(database_name: str):
    import psycopg2

    connection = psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=database_name,
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )
    connection.autocommit = False
    return connection


def read_state(cursor) -> tuple[bool, bool, list[dict]]:
    """Return ``(ledger_exists, baseline_present, ledger_rows)``."""
    cursor.execute(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name IN ('schema_migrations', 'schema_baseline')
        """
    )
    present = {row[0] for row in cursor.fetchall()}
    ledger_exists = "schema_migrations" in present
    baseline_present = False
    rows: list[dict] = []

    if "schema_baseline" in present:
        cursor.execute("SELECT count(*) FROM schema_baseline")
        baseline_present = cursor.fetchone()[0] > 0

    if ledger_exists:
        cursor.execute(
            """
            SELECT version, checksum_up, rolled_back_at
            FROM schema_migrations ORDER BY version
            """
        )
        rows = [
            {"version": r[0], "checksum_up": r[1], "rolled_back_at": r[2]}
            for r in cursor.fetchall()
        ]
    return ledger_exists, baseline_present, rows


def verify_live_database(cursor) -> str:
    cursor.execute("SELECT current_database()")
    return assert_test_database_name(cursor.fetchone()[0])


def apply_baseline(cursor, migration: Migration, operator: str, args) -> None:
    """Apply 026, supplying the certified baseline values as local settings."""
    if not args.baseline_fingerprint or not re.fullmatch(
        r"[0-9a-f]{64}", args.baseline_fingerprint
    ):
        raise GuardFailure(
            "BLOCKED: --baseline-fingerprint must be the 64 hex character "
            "SHA256 produced by scripts/p26_schema_snapshot.py."
        )
    if not args.baseline_artifact:
        raise GuardFailure(
            "BLOCKED: --baseline-artifact must reference the certified "
            "snapshot artefact under reports/."
        )
    artifact = REPOSITORY_ROOT / args.baseline_artifact
    if not artifact.exists():
        raise GuardFailure(
            f"BLOCKED: snapshot artefact {args.baseline_artifact} not found."
        )

    for key, value in (
        ("p26.baseline_version", args.baseline_version),
        ("p26.schema_fingerprint", args.baseline_fingerprint),
        ("p26.snapshot_artifact", args.baseline_artifact),
        ("p26.certified_by_operator", operator),
    ):
        cursor.execute("SELECT set_config(%s, %s, true)", (key, value))

    cursor.execute(migration.path.read_text(encoding="utf-8"))


def register(cursor, migration: Migration, operator: str, execution_ms: int) -> None:
    """Record an applied migration. Never removes or replaces a row."""
    cursor.execute(
        """
        INSERT INTO schema_migrations (
            version, filename, checksum_up, checksum_down,
            down_available, transactional, applied_by_operator,
            execution_ms
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            migration.version,
            migration.path.name,
            migration.checksum_up,
            migration.checksum_down,
            migration.down_available,
            not migration.non_transactional,
            operator,
            execution_ms,
        ),
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _report_plan(plan: Plan) -> None:
    for migration in plan.already_applied:
        print(f"  applied      {migration.version}")
    for migration in plan.rolled_back:
        print(f"  rolled back  {migration.version} (consumed, not re-applied)")
    for migration in plan.to_apply:
        mode = "non-transactional" if migration.non_transactional else "transactional"
        print(f"  pending      {migration.version} [{mode}]")
    for problem in plan.problems:
        print(f"  PROBLEM      {problem}")


def command_status(args) -> int:
    database_name = assert_test_database_name(os.getenv("DB_NAME"))
    migrations = discover_migrations()
    verify_contiguous(migrations)
    connection = connect(database_name)
    try:
        cursor = connection.cursor()
        cursor.execute("SET TRANSACTION READ ONLY")
        live = verify_live_database(cursor)
        ledger_exists, baseline_present, rows = read_state(cursor)
        connection.rollback()
    finally:
        connection.close()

    print(f"database        : {live}")
    print(f"ledger present  : {ledger_exists}")
    print(f"baseline present: {baseline_present}")
    print(f"discovered      : {len(migrations)} migration(s) >= {MIN_VERSION:03d}")
    plan = build_plan(migrations, rows, baseline_present)
    _report_plan(plan)
    return 1 if plan.problems else 0


def command_plan(args) -> int:
    operator = assert_operator_identity(args.operator)
    migrations = discover_migrations()
    verify_contiguous(migrations)

    violations: list[str] = []
    for migration in migrations:
        violations.extend(validate_migration(migration))
    if violations:
        print("static validation failed:")
        for violation in violations:
            print(f"  {violation}")
        return 1

    print(f"operator        : {operator}")
    print(f"discovered      : {len(migrations)} migration(s) >= {MIN_VERSION:03d}")
    for migration in migrations:
        mode = "non-transactional" if migration.non_transactional else "transactional"
        print(f"  {migration.version} [{mode}] {migration.checksum_up[:12]}...")
    print("static validation: OK")
    print("no database was contacted; use 'status' to compare against a ledger")
    return 0


def command_apply(args) -> int:
    operator = assert_operator_identity(args.operator)
    database_name = assert_test_database_name(os.getenv("DB_NAME"))
    migrations = discover_migrations()
    verify_contiguous(migrations)

    violations: list[str] = []
    for migration in migrations:
        violations.extend(validate_migration(migration))
    if violations:
        print("BLOCKED: static validation failed:")
        for violation in violations:
            print(f"  {violation}")
        return 1

    connection = connect(database_name)
    try:
        cursor = connection.cursor()
        live = verify_live_database(cursor)
        _, baseline_present, rows = read_state(cursor)
        connection.rollback()

        plan = build_plan(migrations, rows, baseline_present)
        if plan.problems:
            print("BLOCKED: the ledger and the files on disk disagree:")
            for problem in plan.problems:
                print(f"  {problem}")
            return 1

        if not plan.to_apply:
            print(f"database        : {live}")
            print("nothing to apply")
            return 0

        import time

        for migration in plan.to_apply:
            started = time.monotonic()
            if migration.non_transactional:
                # A concurrent index build cannot sit in a transaction. The
                # statement runs on its own, then the ledger row is written in
                # a separate transaction immediately afterwards.
                connection.autocommit = True
                cursor.execute(migration.path.read_text(encoding="utf-8"))
                connection.autocommit = False
                elapsed = int((time.monotonic() - started) * 1000)
                register(cursor, migration, operator, elapsed)
                connection.commit()
            else:
                if migration.is_baseline:
                    apply_baseline(cursor, migration, operator, args)
                else:
                    cursor.execute(migration.path.read_text(encoding="utf-8"))
                    elapsed = int((time.monotonic() - started) * 1000)
                    register(cursor, migration, operator, elapsed)
                connection.commit()
            print(f"  applied {migration.version}")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    print(f"database        : {live}")
    print(f"operator        : {operator}")
    return 0


SHARED_DEFAULTS = {
    "operator": None,
    "baseline_version": BASELINE_VERSION_LABEL,
    "baseline_fingerprint": None,
    "baseline_artifact": None,
}


def _shared_arguments() -> argparse.ArgumentParser:
    """Options accepted either before or after the subcommand.

    Defaults are suppressed so the subparser copy of each option does not
    overwrite a value already given before the subcommand. The real defaults
    are applied once, after parsing, by :func:`_apply_shared_defaults`.
    """
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument(
        "--operator",
        default=argparse.SUPPRESS,
        help="explicit operator identity recorded in the ledger",
    )
    shared.add_argument(
        "--baseline-version",
        default=argparse.SUPPRESS,
        help=f"baseline label recorded in schema_baseline (default: {BASELINE_VERSION_LABEL})",
    )
    shared.add_argument(
        "--baseline-fingerprint",
        default=argparse.SUPPRESS,
        help="SHA256 from scripts/p26_schema_snapshot.py, required for 026",
    )
    shared.add_argument(
        "--baseline-artifact",
        default=argparse.SUPPRESS,
        help="path under reports/ of the certified snapshot, required for 026",
    )
    return shared


def _apply_shared_defaults(args: argparse.Namespace) -> argparse.Namespace:
    for name, default in SHARED_DEFAULTS.items():
        if not hasattr(args, name):
            setattr(args, name, default)
    return args


def build_parser() -> argparse.ArgumentParser:
    shared = _shared_arguments()
    parser = argparse.ArgumentParser(
        description="P26 forward-only migration runner (version >= 026, TEST only)",
        parents=[shared],
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "status",
        parents=[shared],
        help="read-only comparison of files and ledger",
    )
    sub.add_parser(
        "plan",
        parents=[shared],
        help="static validation only, no database access",
    )
    sub.add_parser(
        "apply",
        parents=[shared],
        help="apply pending migrations",
    )
    return parser


COMMANDS = {
    "status": command_status,
    "plan": command_plan,
    "apply": command_apply,
}


def main(argv: list[str] | None = None) -> int:
    args = _apply_shared_defaults(build_parser().parse_args(argv))
    try:
        return COMMANDS[args.command](args)
    except (GuardFailure, MigrationError) as failure:
        print(str(failure), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
