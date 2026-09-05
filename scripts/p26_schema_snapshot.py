#!/usr/bin/env python3
"""P26-0 read-only schema snapshot of the TEST database.

The snapshot is the evidence behind the P26 baseline certificate. It reads
catalogue metadata inside a read-only transaction, canonicalises the result so
the same schema always yields the same bytes, and derives a SHA256 fingerprint
from those bytes.

Safety properties, all enforced below:

* the session is opened against the TEST database only, verified twice: once
  from the environment before connecting, once from ``current_database()``
  after connecting;
* the transaction is marked read only, so the server itself rejects any
  attempt to modify data;
* the module issues SELECT statements exclusively;
* artefacts are written under ``reports/`` and nowhere else;
* no credential, connection string, or environment value is ever printed.

Restore stability
-----------------

``pg_get_constraintdef`` and ``pg_get_indexdef`` return deparsed text whose
shape depends on how the expression tree was built, not only on what it means.
A dump and restore cycle rebuilds that tree and can deparse the same predicate
differently. Hashing the raw text therefore made the fingerprint change across
a restore even when the schema was equivalent.

The artefact keeps the raw text for audit. The fingerprint is taken over a
canonical projection produced by ``scripts/p26_sql_canonical.py``, in which
equivalent deparse forms collapse to one representation and real differences
do not. See ``SNAPSHOT_FORMAT_VERSION``.

Usage::

    python scripts/p26_schema_snapshot.py

Environment: the standard ``DB_HOST`` / ``DB_PORT`` / ``DB_NAME`` / ``DB_USER``
/ ``DB_PASSWORD`` variables already used by ``database.get_connection``.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = REPOSITORY_ROOT / "reports"

# scripts/ is a plain directory rather than a package, and this module is also
# loaded by file location from the test suite, so the sibling canonicaliser is
# made importable explicitly instead of relying on the caller's path.
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path[:0] = [_SCRIPTS_DIR]

from p26_sql_canonical import canonicalise_expression  # noqa: E402

# The production database is named explicitly so that a mistyped or absent
# DB_NAME cannot be mistaken for a permitted target.
PROD_DATABASE_NAMES = frozenset({"stima360_db", "stima360"})
REQUIRED_NAME_MARKER = "test"

# Raised from p26-snapshot-1 when the fingerprint moved from raw deparsed text
# to the restore-stable canonical projection. The version is part of the
# fingerprint input, so a digest produced by the old algorithm can never be
# mistaken for one produced by this algorithm.
SNAPSHOT_FORMAT_VERSION = "p26-snapshot-2"

# Sections whose rows carry a deparsed SQL expression, and the fields holding
# it. Each gains a ``<field>_canonical`` sibling in the artefact; only the
# canonical sibling reaches the fingerprint.
EXPRESSION_FIELDS: dict[str, tuple[str, ...]] = {
    "constraints": ("definition",),
    "indexes": ("indexdef",),
    "columns": ("column_default",),
    "policies": ("qual", "with_check"),
}


class GuardFailure(RuntimeError):
    """Raised when the target database is not a permitted TEST target."""


# ---------------------------------------------------------------------------
# Environment guard
# ---------------------------------------------------------------------------

def assert_test_database_name(database_name: str | None) -> str:
    """Return ``database_name`` when it identifies a TEST database.

    The check is deliberately strict and allow-list shaped: a name must contain
    the marker ``test`` and must not be one of the known production names.
    """
    name = (database_name or "").strip()
    if not name:
        raise GuardFailure(
            "BLOCKED: DB_NAME is not set. The snapshot refuses to guess a target."
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


# ---------------------------------------------------------------------------
# Catalogue queries - SELECT only
# ---------------------------------------------------------------------------
# Every entry maps a section name to a SELECT statement. Ordering is applied in
# SQL and again in Python, so the artefact does not depend on server collation
# or on plan choice.

QUERIES: dict[str, str] = {
    "tables": """
        SELECT table_name, table_type
        FROM information_schema.tables
        WHERE table_schema = 'public'
        ORDER BY table_name
    """,
    "columns": """
        SELECT table_name, column_name, ordinal_position, data_type,
               is_nullable, column_default, character_maximum_length,
               numeric_precision, numeric_scale
        FROM information_schema.columns
        WHERE table_schema = 'public'
        ORDER BY table_name, ordinal_position
    """,
    "constraints": """
        SELECT c.relname AS table_name, con.conname AS constraint_name,
               con.contype AS constraint_type,
               pg_get_constraintdef(con.oid) AS definition
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
        ORDER BY c.relname, con.conname
    """,
    "key_columns": """
        SELECT table_name, constraint_name, column_name, ordinal_position
        FROM information_schema.key_column_usage
        WHERE table_schema = 'public'
        ORDER BY table_name, constraint_name, ordinal_position
    """,
    "constraint_columns": """
        SELECT table_name, constraint_name, column_name
        FROM information_schema.constraint_column_usage
        WHERE table_schema = 'public'
        ORDER BY table_name, constraint_name, column_name
    """,
    "indexes": """
        SELECT tablename, indexname, indexdef
        FROM pg_indexes
        WHERE schemaname = 'public'
        ORDER BY tablename, indexname
    """,
    "sequences": """
        SELECT sequence_name, data_type, start_value, minimum_value,
               maximum_value, increment
        FROM information_schema.sequences
        WHERE sequence_schema = 'public'
        ORDER BY sequence_name
    """,
    "row_level_security": """
        SELECT c.relname AS table_name, c.relrowsecurity AS rls_enabled,
               c.relforcerowsecurity AS rls_forced
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r'
        ORDER BY c.relname
    """,
    "policies": """
        SELECT tablename, policyname, permissive, roles, cmd, qual, with_check
        FROM pg_policies
        WHERE schemaname = 'public'
        ORDER BY tablename, policyname
    """,
    "extensions": """
        SELECT extname, extversion
        FROM pg_extension
        ORDER BY extname
    """,
}


# ---------------------------------------------------------------------------
# Canonicalisation and fingerprint
# ---------------------------------------------------------------------------

def _stringify(value: object) -> object:
    """Reduce a driver value to a JSON-stable representation."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_stringify(item) for item in value]
    return str(value)


def normalise_rows(rows: list[tuple], headers: list[str]) -> list[dict]:
    """Turn driver rows into dictionaries with stable key order."""
    normalised = [
        {header: _stringify(cell) for header, cell in zip(headers, row)}
        for row in rows
    ]
    # Sorting on the serialised form guarantees a total order even when a
    # section has no natural unique key.
    normalised.sort(key=lambda item: json.dumps(item, sort_keys=True, default=str))
    return normalised


def _sort_rows(rows: list[dict]) -> list[dict]:
    """Order rows by their serialised form, so no section depends on arrival order."""
    return sorted(rows, key=lambda item: json.dumps(item, sort_keys=True, default=str))


def annotate_payload(payload: dict) -> dict:
    """Return a copy of ``payload`` with canonical siblings beside the raw text.

    This is the artefact form: the original deparsed text is preserved so a
    reviewer can read exactly what the server reported, and the canonical form
    sits next to it so the fingerprint input is auditable too.
    """
    annotated = copy.deepcopy(payload)
    for section, fields in EXPRESSION_FIELDS.items():
        rows = annotated.get(section)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            for field_name in fields:
                if field_name in row:
                    row[f"{field_name}_canonical"] = canonicalise_expression(
                        row[field_name]
                    )
    return annotated


def canonicalise_payload(payload: dict) -> dict:
    """Return the fingerprint projection of ``payload``.

    Raw deparsed text is removed and only the canonical form is retained, then
    every affected section is ordered again. Re-ordering is not cosmetic: rows
    are first ordered by their raw serialisation, so two equivalent schemas
    whose raw text differs could otherwise yield the same rows in a different
    order, and a different digest.
    """
    projection = annotate_payload(payload)
    for section, fields in EXPRESSION_FIELDS.items():
        rows = projection.get(section)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            for field_name in fields:
                row.pop(field_name, None)
        projection[section] = _sort_rows(
            [row for row in rows if isinstance(row, dict)]
        )
    return projection


def canonical_json(payload: dict) -> str:
    """Serialise ``payload`` so identical schemas produce identical bytes."""
    return json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
        default=str,
    )


def fingerprint(payload: dict) -> str:
    """SHA256 of the canonical serialisation of ``payload``."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------

def collect_schema(cursor) -> dict:
    """Run every catalogue query and return the canonical payload.

    The returned payload holds schema facts only. Volatile metadata such as the
    run timestamp is kept outside it, so the fingerprint stays reproducible
    across runs of an unchanged schema.
    """
    payload: dict[str, object] = {"snapshot_format": SNAPSHOT_FORMAT_VERSION}
    for section, statement in sorted(QUERIES.items()):
        cursor.execute(statement)
        headers = [description[0] for description in cursor.description]
        payload[section] = normalise_rows(cursor.fetchall(), headers)
    return payload


def open_readonly_cursor(connection):
    """Return a cursor bound to a read-only transaction."""
    cursor = connection.cursor()
    cursor.execute("SET TRANSACTION READ ONLY")
    return cursor


def verify_live_database(cursor) -> str:
    """Second guard: confirm the live session really is the TEST database."""
    cursor.execute("SELECT current_database()")
    live_name = cursor.fetchone()[0]
    return assert_test_database_name(live_name)


# ---------------------------------------------------------------------------
# Artefact writing - confined to reports/
# ---------------------------------------------------------------------------

def _reports_path(filename: str) -> Path:
    """Resolve ``filename`` inside reports/, refusing to escape it."""
    candidate = (REPORTS_DIR / filename).resolve()
    if candidate.parent != REPORTS_DIR.resolve():
        raise GuardFailure(
            f"BLOCKED: refusing to write outside reports/ ({candidate})"
        )
    return candidate


def write_artifacts(document: dict, digest: str, timestamp: str) -> tuple[Path, Path]:
    """Persist the snapshot and its checksum under reports/."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = _reports_path(f"p26_baseline_TEST_{timestamp}.json")
    sha_path = _reports_path(f"p26_baseline_TEST_{timestamp}.sha256")
    json_path.write_text(
        json.dumps(document, sort_keys=True, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    sha_path.write_text(f"{digest}  {json_path.name}\n", encoding="utf-8")
    return json_path, sha_path


def build_document(payload: dict, digest: str, database_name: str, timestamp: str) -> dict:
    """Wrap the fingerprinted payload with non-fingerprinted run metadata."""
    return {
        "metadata": {
            "snapshot_format": SNAPSHOT_FORMAT_VERSION,
            "database_name": database_name,
            "generated_at_utc": timestamp,
            "phase": "P26-0",
            "fingerprint_covers": (
                "the canonical projection of the 'schema' object, never this "
                "metadata and never the raw deparsed text kept beside it"
            ),
            "fingerprint_basis": (
                "raw expression fields are retained for audit; the digest is "
                "taken over their '<field>_canonical' siblings so that a "
                "dump and restore cycle cannot change it"
            ),
        },
        "schema_fingerprint": digest,
        "schema": payload,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    try:
        declared_name = assert_test_database_name(os.getenv("DB_NAME"))
    except GuardFailure as failure:
        print(str(failure), file=sys.stderr)
        return 2

    try:
        import psycopg2
    except ImportError:
        print("BLOCKED: psycopg2 is not installed in this environment.", file=sys.stderr)
        return 3

    connection = psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=declared_name,
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )
    connection.autocommit = False
    try:
        cursor = open_readonly_cursor(connection)
        live_name = verify_live_database(cursor)
        payload = collect_schema(cursor)
        connection.rollback()
    finally:
        connection.close()

    # The artefact carries raw plus canonical; the digest covers canonical only.
    annotated = annotate_payload(payload)
    digest = fingerprint(canonicalise_payload(payload))
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    document = build_document(annotated, digest, live_name, timestamp)
    json_path, sha_path = write_artifacts(document, digest, timestamp)

    # Only non-sensitive facts are reported.
    print(f"database      : {live_name}")
    print(f"format        : {SNAPSHOT_FORMAT_VERSION}")
    print(f"fingerprint   : {digest}")
    print(f"snapshot      : {json_path.relative_to(REPOSITORY_ROOT)}")
    print(f"checksum      : {sha_path.relative_to(REPOSITORY_ROOT)}")
    for section in sorted(QUERIES):
        print(f"  {section:<20} {len(payload[section])} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
