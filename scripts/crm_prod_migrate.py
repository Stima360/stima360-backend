#!/usr/bin/env python3
"""CRM PROD migration channel: bootstrap and forward migrations for the CRM
production database, and for NOTHING else.

This is a SEPARATE path from ``scripts/p26_migrate.py``. That runner is, by
design, TEST only: it refuses the site's production names and demands the
``test`` marker in the database name. Nothing here loosens it - it is imported
unchanged for discovery, static validation, planning and the ledger row - and
nothing here goes through it with a renamed database. The channel exists so
that no one is ever tempted to call a production database "test".

Target identity, all enforced before a single statement runs:

* ``CRM_PROD_DATABASE_URL`` names the database. It is a dedicated variable:
  the ``DB_*`` variables of the web service are deliberately NOT read, so the
  channel cannot be pointed at a database by accident.
* ``--database`` must be typed by the operator and must equal both the name in
  the URL and ``current_database()`` on the live session.
* The name must NOT be one of the site's production names and must NOT carry
  the ``test`` marker (both lists are imported from the TEST runner, so the
  two channels stay disjoint by construction).
* If ``SITE_DB_URL`` is set, the target must not be that server+database.
* ``bootstrap`` requires an EMPTY database (no table in any user schema). A
  database that holds the site's tables, or any other schema, is refused by
  name: the site's database and the CRM TEST database can never be
  bootstrapped over.
* ``upgrade`` and ``status`` require the marker table ``crm_instance`` with
  ``instance_kind = 'crm_prod'`` and ``database_name = current_database()``.
  The marker is written in the first transaction of a bootstrap, only on an
  empty database, and can never be deleted (trigger).

What ``bootstrap`` does, in order, exactly as the TEST database was built
(see tests/test_censimento_1_fullschema_postgres.py):

  1. marker ``crm_instance`` (state ``bootstrapping``);
  2. the legacy site tables with the real functions of ``database.py``
     (``init_db.py``: stime, stime_dettagliate, zone_valori, allinea_stime);
  3. migrations 001..025 in sequence, each with its own BEGIN/COMMIT, without
     the ``*_prod`` variants (014/015 are the same bodies as 010/011 gated on
     the SITE's database name, which this channel refuses). 010 and 011 are
     gated on ``current_database() = 'stima360_db_test'``: for the CRM PROD
     database that literal is replaced, in memory only, by the target name
     typed by the operator - the gate keeps working against the real name,
     nothing on disk changes, and the manifest records both checksums;
  4. the pre-baseline schema is compared with the certified TEST baseline
     (same tables, same columns; the marker table is the one declared
     exception), its fingerprint is computed with the real snapshot code and
     written under reports/ as the CRM PROD certificate;
  5. 026 (baseline) with that certificate, then 027..latest through the
     ledger, every step by the TEST runner's own functions;
  6. ``stime``/``stime_dettagliate`` sequences moved far ahead, so a record
     created by the CRM can never take an id the site will use (SITE-IMPORT-1
     keeps the site's ids);
  7. marker state ``ready``.

A bootstrap that fails half-way leaves a non-empty database without a ready
marker: the channel refuses to touch it again. Drop that database and create
an empty one; nothing of value is in it yet.

Typical use (Render shell of the CRM PROD service, URL passed inline)::

    CRM_PROD_DATABASE_URL=... python3 scripts/crm_prod_migrate.py status    --database stima360_crm
    CRM_PROD_DATABASE_URL=... python3 scripts/crm_prod_migrate.py bootstrap --database stima360_crm --operator "nome.cognome"
    CRM_PROD_DATABASE_URL=... python3 scripts/crm_prod_migrate.py upgrade   --database stima360_crm --operator "nome.cognome"

``status`` is read-only. Nothing is printed that contains a credential.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPOSITORY_ROOT / "scripts"
MIGRATIONS_DIR = REPOSITORY_ROOT / "migrations"
REPORTS_DIR = REPOSITORY_ROOT / "reports"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import p26_migrate as runner  # noqa: E402 - the TEST runner, imported unchanged
import p26_schema_snapshot as snapshot  # noqa: E402 - the real fingerprint code

INSTANCE_KIND = "crm_prod"
MARKER_TABLE = "crm_instance"
DATABASE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,62}$")
PRE_BASELINE_MAX = runner.MIN_VERSION - 1          # 025
TEST_GATE_LITERAL = "stima360_db_test"
#: The two pre-baseline files gated on the TEST database name, and how many
#: times each names it. A different count means the file changed: refuse.
GATED_PRE_BASELINE = {"010_owner_02_p1.sql": 2, "011_owner_02_p5.sql": 2}
TEST_CERTIFICATE = REPORTS_DIR / "p26_baseline_TEST_20260905T170601Z.json"
BASELINE_VERSION_LABEL = "P26-BASELINE-001-CRM-PROD"
DEFAULT_SITE_ID_OFFSET = 1_000_000_000
SITE_ID_SEQUENCES = ("stime_id_seq", "stime_dettagliate_id_seq")


class GuardFailure(runner.GuardFailure):
    """A precondition of the CRM PROD channel is not satisfied."""


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

def assert_crm_prod_database_name(database_name: str | None) -> str:
    """The CRM PROD database name: not the site's, not a TEST name."""
    name = (database_name or "").strip()
    if not name:
        raise GuardFailure("BLOCKED: --database is required; the channel never guesses a target.")
    lowered = name.lower()
    if lowered in runner.PROD_DATABASE_NAMES:
        raise GuardFailure(
            f"BLOCKED: {name!r} is the public site's production database name. "
            "The CRM PROD database is a different database with a different name.")
    if runner.REQUIRED_NAME_MARKER in lowered:
        raise GuardFailure(
            f"BLOCKED: {name!r} carries the {runner.REQUIRED_NAME_MARKER!r} marker: that is a "
            "TEST database, served by scripts/p26_migrate.py, never by this channel.")
    if not DATABASE_NAME_RE.match(lowered) or lowered != name:
        raise GuardFailure(f"BLOCKED: {name!r} is not a plain lowercase database name.")
    return name


def _url_parts(url: str) -> tuple[str, str, str]:
    parsed = urlparse(url)
    if parsed.scheme not in ("postgresql", "postgres"):
        raise GuardFailure("BLOCKED: the database URL must be a postgresql:// URL.")
    query = parse_qs(parsed.query)
    host = (parsed.hostname or (query.get("host") or [""])[0] or "").lower()
    port = str(parsed.port or (query.get("port") or ["5432"])[0])
    dbname = parsed.path.lstrip("/")
    if not dbname or not host:
        raise GuardFailure("BLOCKED: the database URL must name both a host and a database.")
    return host, port, dbname


def resolve_target(database_name: str | None, environ=os.environ) -> tuple[str, str]:
    """Return ``(url, name)`` once the URL, the typed name and the site agree."""
    name = assert_crm_prod_database_name(database_name)
    url = (environ.get("CRM_PROD_DATABASE_URL") or "").strip()
    if not url:
        raise GuardFailure(
            "BLOCKED: CRM_PROD_DATABASE_URL is not set. The DB_* variables are "
            "deliberately ignored by this channel.")
    host, port, dbname = _url_parts(url)
    if dbname != name:
        raise GuardFailure(
            f"BLOCKED: the URL names database {dbname!r} but --database says {name!r}.")
    site_url = (environ.get("SITE_DB_URL") or "").strip()
    if site_url:
        site_host, site_port, site_db = _url_parts(site_url)
        if (site_host, site_port, site_db) == (host, port, dbname):
            raise GuardFailure(
                "BLOCKED: CRM_PROD_DATABASE_URL is the public site's database (SITE_DB_URL). "
                "The site's database is read-only for the CRM and is never migrated by it.")
    return url, name


def connect(url: str):
    import psycopg2

    connection = psycopg2.connect(url)
    connection.autocommit = False
    return connection


def classify(cursor) -> dict:
    """What is on the live database: ``empty``, ``crm_prod`` or something else."""
    cursor.execute("SELECT current_database()")
    live = cursor.fetchone()[0]
    cursor.execute("""
        SELECT table_schema, table_name FROM information_schema.tables
        WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
          AND table_type = 'BASE TABLE'
    """)
    tables = {(r[0], r[1]) for r in cursor.fetchall()}
    names = {t for _, t in tables}
    marker = None
    if ("public", MARKER_TABLE) in tables:
        cursor.execute(f"SELECT instance_kind, database_name, bootstrap_state, created_by_operator, "
                       f"created_at FROM {MARKER_TABLE} WHERE id = 1")
        row = cursor.fetchone()
        if row is not None:
            marker = {"instance_kind": row[0], "database_name": row[1],
                      "bootstrap_state": row[2], "created_by_operator": row[3],
                      "created_at": row[4]}
    if not tables:
        kind = "empty"
    elif marker is not None and marker["instance_kind"] == INSTANCE_KIND \
            and marker["database_name"] == live:
        kind = "crm_prod"
    elif marker is not None:
        kind = "foreign_marker"
    elif {"stime", "stime_dettagliate"} <= names and "agencies" not in names:
        kind = "site_like"
    elif "schema_migrations" in names or "agencies" in names:
        kind = "crm_without_marker"
    else:
        kind = "foreign"
    return {"live": live, "kind": kind, "marker": marker, "table_count": len(tables)}


def assert_live_is(cursor, name: str, expected: tuple[str, ...]) -> dict:
    state = classify(cursor)
    if state["live"] != name:
        raise GuardFailure(
            f"BLOCKED: the session is on database {state['live']!r}, not {name!r}.")
    kind = state["kind"]
    if kind in expected:
        return state
    reasons = {
        "site_like": "this database holds the public site's tables (stime, stime_dettagliate) "
                     "and no CRM schema: it is the SITE's database, which the CRM only reads",
        "crm_without_marker": "this database holds a CRM schema but no crm_prod marker: it is "
                              "another CRM database (for example the TEST one), not CRM PROD",
        "foreign_marker": "this database carries a crm_instance marker of another kind or of "
                          "another database name: a copy, not CRM PROD",
        "foreign": "this database is not empty and is not a CRM PROD database",
        "empty": "this database is empty: it has not been bootstrapped",
        "crm_prod": "this database is already a bootstrapped CRM PROD database",
    }
    raise GuardFailure(f"BLOCKED: {reasons[kind]} ({state['table_count']} table(s)).")


# ---------------------------------------------------------------------------
# Bootstrap steps
# ---------------------------------------------------------------------------

MARKER_DDL = f"""
CREATE TABLE {MARKER_TABLE} (
    id                    SMALLINT    PRIMARY KEY DEFAULT 1,
    instance_kind         TEXT        NOT NULL,
    database_name         TEXT        NOT NULL DEFAULT current_database(),
    bootstrap_state       TEXT        NOT NULL DEFAULT 'bootstrapping',
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by_operator   TEXT        NOT NULL,
    repository_commit     TEXT,
    pre_baseline_manifest JSONB       NOT NULL DEFAULT '[]'::jsonb,
    site_id_offset        BIGINT,
    ready_at              TIMESTAMPTZ,
    notes                 TEXT        NOT NULL,
    CONSTRAINT crm_instance_singleton CHECK (id = 1),
    CONSTRAINT crm_instance_kind CHECK (instance_kind = '{INSTANCE_KIND}'),
    CONSTRAINT crm_instance_state CHECK (bootstrap_state IN ('bootstrapping', 'ready')),
    CONSTRAINT crm_instance_operator_present CHECK (length(btrim(created_by_operator)) > 0),
    CONSTRAINT crm_instance_ready_consistency CHECK (
        (bootstrap_state = 'ready') = (ready_at IS NOT NULL))
);

CREATE OR REPLACE FUNCTION crm_instance_guard() RETURNS trigger AS $fn$
BEGIN
    IF TG_OP = 'DELETE' OR TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'crm_instance guard: the CRM PROD marker is never removed';
    END IF;
    IF NEW.database_name <> current_database() THEN
        RAISE EXCEPTION 'crm_instance guard: marker declares database % but the session is on %',
            NEW.database_name, current_database();
    END IF;
    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

CREATE TRIGGER trg_crm_instance_guard
    BEFORE INSERT OR UPDATE OR DELETE ON {MARKER_TABLE}
    FOR EACH ROW EXECUTE FUNCTION crm_instance_guard();
CREATE TRIGGER trg_crm_instance_guard_truncate
    BEFORE TRUNCATE ON {MARKER_TABLE}
    FOR EACH STATEMENT EXECUTE FUNCTION crm_instance_guard();
"""


def _repository_commit() -> str | None:
    for variable in ("RENDER_GIT_COMMIT", "GIT_COMMIT"):
        value = (os.getenv(variable) or "").strip()
        if value:
            return value
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPOSITORY_ROOT,
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def write_marker(cursor, operator: str) -> None:
    cursor.execute(MARKER_DDL)
    cursor.execute(
        f"INSERT INTO {MARKER_TABLE} (id, instance_kind, created_by_operator, repository_commit, notes) "
        "VALUES (1, %s, %s, %s, %s)",
        (INSTANCE_KIND, operator, _repository_commit(),
         "CRM PROD instance, bootstrapped on an empty database by scripts/crm_prod_migrate.py. "
         "Separate from the public site's database (read-only source of SITE-IMPORT-1) and from "
         "the CRM TEST database (scripts/p26_migrate.py)."))


def create_legacy_tables(url: str) -> list[str]:
    """The legacy site tables, with the real functions of database.py."""
    import psycopg2

    import database as legacy

    original = legacy.get_connection
    legacy.get_connection = lambda: psycopg2.connect(url)
    try:
        legacy.crea_tabella_stime()
        legacy.crea_tabella_stime_dettagliate()
        legacy.crea_tabella_zone_valori()
        legacy.migrazione_allinea_stime()
    finally:
        legacy.get_connection = original
    return ["crea_tabella_stime", "crea_tabella_stime_dettagliate",
            "crea_tabella_zone_valori", "migrazione_allinea_stime"]


def pre_baseline_files() -> list[Path]:
    """001..025 in the TEST order: no down files, no *_prod variants."""
    out = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        match = re.match(r"^(\d{3})_", path.name)
        if not match or int(match.group(1)) > PRE_BASELINE_MAX:
            continue
        if path.name.endswith("_down.sql") or "_prod" in path.name:
            continue
        out.append(path)
    return out


def pre_baseline_text(path: Path, database_name: str) -> tuple[str, bool]:
    """The executable text of one pre-baseline file for the CRM PROD database."""
    text = path.read_text(encoding="utf-8")
    expected = GATED_PRE_BASELINE.get(path.name)
    occurrences = text.count(TEST_GATE_LITERAL)
    if expected is None:
        if occurrences:
            raise GuardFailure(
                f"BLOCKED: {path.name} names the TEST database but is not a declared gated file.")
        return text, False
    if occurrences != expected:
        raise GuardFailure(
            f"BLOCKED: {path.name} names the TEST database {occurrences} time(s), "
            f"expected {expected}: the file changed, the channel will not guess.")
    return text.replace(TEST_GATE_LITERAL, database_name), True


def apply_pre_baseline(connection, database_name: str) -> list[dict]:
    manifest = []
    cursor = connection.cursor()
    connection.autocommit = True        # each file brackets its own transaction
    try:
        for path in pre_baseline_files():
            text, rewritten = pre_baseline_text(path, database_name)
            cursor.execute(text)
            manifest.append({
                "filename": path.name,
                "sha256_on_disk": runner.sha256_of_file(path),
                "sha256_executed": runner.sha256_of_text(text),
                "gate_rewritten_to": database_name if rewritten else None,
            })
            print(f"  pre-baseline {path.name}" + ("  [gate -> " + database_name + "]" if rewritten else ""))
    finally:
        connection.autocommit = False
    return manifest


def compare_with_test_certificate(cursor) -> tuple[int, int]:
    """Same tables and columns as the certified TEST pre-baseline schema."""
    certificate = json.loads(TEST_CERTIFICATE.read_text(encoding="utf-8"))["schema"]
    expected_columns = {(r["table_name"], r["column_name"]) for r in certificate["columns"]}
    expected_tables = {t for t, _ in expected_columns}
    cursor.execute("""
        SELECT table_name, column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name <> %s
    """, (MARKER_TABLE,))
    columns = {(r[0], r[1]) for r in cursor.fetchall()}
    tables = {t for t, _ in columns}
    if tables != expected_tables or columns != expected_columns:
        raise GuardFailure(
            "BLOCKED: the pre-baseline schema differs from the certified TEST baseline: "
            f"tables +{sorted(tables - expected_tables)} -{sorted(expected_tables - tables)}, "
            f"columns +{sorted(columns - expected_columns)[:5]} -{sorted(expected_columns - columns)[:5]}")
    return len(tables), len(columns)


def certify_baseline(connection, database_name: str) -> tuple[str, str]:
    """Fingerprint the fresh schema with the real snapshot code; write the
    CRM PROD certificate under reports/. Returns ``(fingerprint, artifact)``."""
    cursor = snapshot.open_readonly_cursor(connection)
    payload = snapshot.collect_schema(cursor)
    connection.rollback()
    digest = snapshot.fingerprint(snapshot.canonicalise_payload(payload))
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    document = snapshot.build_document(payload, digest, database_name, timestamp)
    document["metadata"]["phase"] = "CRM-PROD-BOOTSTRAP"
    document["metadata"]["certified_against"] = TEST_CERTIFICATE.name
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORTS_DIR / f"p26_baseline_CRM_PROD_{timestamp}.json"
    json_path.write_text(json.dumps(document, sort_keys=True, indent=2, default=str) + "\n",
                         encoding="utf-8")
    (REPORTS_DIR / f"p26_baseline_CRM_PROD_{timestamp}.sha256").write_text(
        f"{digest}  {json_path.name}\n", encoding="utf-8")
    return digest, json_path.relative_to(REPOSITORY_ROOT).as_posix()


def validated_migrations() -> list:
    migrations = runner.discover_migrations()
    runner.verify_contiguous(migrations)
    violations: list[str] = []
    for migration in migrations:
        violations.extend(runner.validate_migration(migration))
    if violations:
        raise GuardFailure("BLOCKED: static validation failed:\n  " + "\n  ".join(violations))
    return migrations


def apply_forward(connection, migrations: list, operator: str, baseline_args=None) -> int:
    """026 (only with ``baseline_args``) and 027..latest through the ledger,
    with the TEST runner's own planning and ledger row."""
    cursor = connection.cursor()
    _, baseline_present, rows = runner.read_state(cursor)
    connection.rollback()
    applied = 0
    if not baseline_present and baseline_args is not None:
        # As the TEST database received it: 026 alone first (the planner
        # refuses every later version until the baseline is certified), then
        # the chain. Nothing is registered in place of being executed.
        baseline_only = [m for m in migrations if m.is_baseline]
        plan = runner.build_plan(baseline_only, rows, baseline_present)
        if plan.problems or len(plan.to_apply) != 1:
            raise GuardFailure("BLOCKED: the baseline cannot be planned:\n  " + "\n  ".join(plan.problems))
        runner.apply_baseline(cursor, plan.to_apply[0], operator, baseline_args)
        connection.commit()
        applied += 1
        print(f"  applied {plan.to_apply[0].version}")
        _, baseline_present, rows = runner.read_state(cursor)
        connection.rollback()
    plan = runner.build_plan(migrations, rows, baseline_present)
    if plan.problems:
        raise GuardFailure("BLOCKED: the ledger and the files on disk disagree:\n  "
                           + "\n  ".join(plan.problems))
    for migration in plan.to_apply:
        started = time.monotonic()
        if migration.non_transactional:
            connection.autocommit = True
            cursor.execute(migration.path.read_text(encoding="utf-8"))
            connection.autocommit = False
            runner.register(cursor, migration, operator, int((time.monotonic() - started) * 1000))
            connection.commit()
        elif migration.is_baseline:
            if baseline_args is None:
                raise GuardFailure("BLOCKED: the baseline is missing; this database was not bootstrapped here.")
            runner.apply_baseline(cursor, migration, operator, baseline_args)
            connection.commit()
        else:
            cursor.execute(migration.path.read_text(encoding="utf-8"))
            runner.register(cursor, migration, operator, int((time.monotonic() - started) * 1000))
            connection.commit()
        applied += 1
        print(f"  applied {migration.version}")
    return applied


def offset_site_sequences(cursor, offset: int) -> None:
    for sequence in SITE_ID_SEQUENCES:
        cursor.execute("SELECT setval(%s, %s, false)", (sequence, offset))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def command_status(args) -> int:
    url, name = resolve_target(args.database)
    migrations = validated_migrations()
    connection = connect(url)
    try:
        cursor = connection.cursor()
        cursor.execute("SET TRANSACTION READ ONLY")
        state = classify(cursor)
        _, baseline_present, rows = runner.read_state(cursor)
        connection.rollback()
    finally:
        connection.close()
    print(f"database        : {state['live']}")
    print(f"classification  : {state['kind']}")
    if state["marker"]:
        print(f"marker          : {state['marker']['instance_kind']} state={state['marker']['bootstrap_state']} "
              f"by={state['marker']['created_by_operator']} at={state['marker']['created_at']}")
    print(f"baseline present: {baseline_present}")
    plan = runner.build_plan(migrations, rows, baseline_present)
    runner._report_plan(plan)
    if state["kind"] != "crm_prod":
        print("not a CRM PROD database: bootstrap is possible only when empty")
        return 1
    return 1 if plan.problems else 0


def command_bootstrap(args) -> int:
    operator = runner.assert_operator_identity(args.operator)
    url, name = resolve_target(args.database)
    if not TEST_CERTIFICATE.exists():
        raise GuardFailure(f"BLOCKED: the TEST certificate {TEST_CERTIFICATE.name} is missing.")
    offset = int(args.site_id_offset)
    if offset < 1_000_000:
        raise GuardFailure("BLOCKED: --site-id-offset must leave the site's ids far below it.")
    migrations = validated_migrations()

    connection = connect(url)
    try:
        cursor = connection.cursor()
        assert_live_is(cursor, name, ("empty",))
        connection.rollback()

        # 1. the marker, first, on the empty database
        write_marker(cursor, operator)
        connection.commit()
        print(f"  marker       {MARKER_TABLE} ({INSTANCE_KIND}, bootstrapping)")

        # 2. legacy tables, 3. pre-baseline
        for step in create_legacy_tables(url):
            print(f"  legacy       {step}")
        manifest = apply_pre_baseline(connection, name)

        # 4. certification against the TEST baseline
        tables, columns = compare_with_test_certificate(cursor)
        connection.rollback()
        digest, artifact = certify_baseline(connection, name)
        print(f"  certified    {tables} tables, {columns} columns = TEST baseline; fingerprint {digest[:12]}...")
        print(f"  certificate  {artifact}")

        # 5. 026 and the forward chain
        baseline_args = argparse.Namespace(baseline_version=BASELINE_VERSION_LABEL,
                                           baseline_fingerprint=digest, baseline_artifact=artifact)
        applied = apply_forward(connection, migrations, operator, baseline_args)

        # 6. sequences, 7. ready
        offset_site_sequences(cursor, offset)
        cursor.execute(
            f"UPDATE {MARKER_TABLE} SET bootstrap_state = 'ready', ready_at = NOW(), "
            "pre_baseline_manifest = %s::jsonb, site_id_offset = %s WHERE id = 1",
            (json.dumps(manifest), offset))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    print(f"database        : {name}")
    print(f"operator        : {operator}")
    print(f"migrations      : {applied} applied, last {migrations[-1].version}")
    print(f"site id offset  : {offset} ({', '.join(SITE_ID_SEQUENCES)})")
    print(f"bootstrap       : ready. Commit {artifact} to the repository for audit.")
    return 0


def command_upgrade(args) -> int:
    operator = runner.assert_operator_identity(args.operator)
    url, name = resolve_target(args.database)
    migrations = validated_migrations()
    connection = connect(url)
    try:
        cursor = connection.cursor()
        state = assert_live_is(cursor, name, ("crm_prod",))
        if state["marker"]["bootstrap_state"] != "ready":
            raise GuardFailure("BLOCKED: the bootstrap of this database never completed. "
                               "Drop it and bootstrap an empty database again.")
        connection.rollback()
        applied = apply_forward(connection, migrations, operator)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    print(f"database        : {name}")
    print(f"operator        : {operator}")
    print("nothing to apply" if not applied else f"migrations      : {applied} applied")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CRM PROD migration channel (bootstrap on an empty database, then forward only)")
    sub = parser.add_subparsers(dest="command", required=True)
    for command, handler in (("status", command_status), ("bootstrap", command_bootstrap),
                             ("upgrade", command_upgrade)):
        p = sub.add_parser(command)
        p.add_argument("--database", required=True,
                       help="the CRM PROD database name, typed by the operator")
        p.add_argument("--operator", default=None,
                       help="explicit operator identity recorded in the ledger")
        if command == "bootstrap":
            p.add_argument("--site-id-offset", default=DEFAULT_SITE_ID_OFFSET, type=int,
                           help="first id the CRM may assign to stime/stime_dettagliate")
        p.set_defaults(handler=handler)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (runner.GuardFailure, runner.MigrationError) as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
