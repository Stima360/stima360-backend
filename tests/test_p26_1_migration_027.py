"""P26-1 Task 2 - static checks for migration 027_p26_agency_identity.

Offline tests. Nothing here opens a database connection: every assertion is
made against the SQL text. Same discipline as
tests/test_p26_baseline_isolation.py and tests/test_p26_1_migration_rules.py.

027 is the identity foundation: agencies, operator_users, agency_memberships,
operator_sessions, plus the Default Agency row. It touches no existing table
and seeds no operator.

The generic P26 migration rules (transactional, no CONCURRENTLY, append-only
ledger, no hard-coded database-name guard, no secrets in agencies.settings,
down file present) are asserted by the Task 1 helper, which this module calls
rather than reimplementing.

Coverage map from the approved design spec sections 3.1, 3.2 and 4:

    A1  agencies shape, constraints and slug uniqueness
    A2  operator_users identity, with email uniqueness GLOBAL (spec D-4)
    A3  agency_memberships roles, single UNIQUE, two partial unique indexes
    A4  operator_sessions carries NO agency_id (spec D-3)
    A5  required indexes
    A6  the Default Agency row, idempotent and resolved by slug
    A7  027 seeds no operator and no credential
    A8  027 is additive: it alters no existing table
    A9  027 does not write its own ledger row - the runner owns that
    A10 the down migration is a real, FK-safe reversal
"""
from __future__ import annotations

import re
from pathlib import Path

from test_p26_1_migration_rules import (
    _strip_sql_comments,
    assert_p26_migration_rules,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"

VERSION = "027_p26_agency_identity"
UP_PATH = MIGRATIONS / f"{VERSION}.sql"
DOWN_PATH = MIGRATIONS / f"{VERSION}_down.sql"

AGENCY_ROLES = ("agency_owner", "agency_admin", "agent")


def _up() -> str:
    return _strip_sql_comments(UP_PATH.read_text(encoding="utf-8"))


def _down() -> str:
    return _strip_sql_comments(DOWN_PATH.read_text(encoding="utf-8"))


def _squash(sql: str) -> str:
    """Collapse whitespace so multi-line DDL can be matched as one string."""
    return " ".join(sql.split())


def _table_body(sql: str, table: str) -> str:
    """Return the parenthesised body of CREATE TABLE <table>.

    Brace-counting rather than a regex, because the bodies contain nested
    parentheses (CHECK, VARCHAR(n), REFERENCES).
    """
    squashed = _squash(sql)
    match = re.search(
        rf"CREATE TABLE IF NOT EXISTS {table}\s*\(", squashed, re.IGNORECASE
    )
    assert match, f"027 must create table {table!r}"
    depth = 0
    start = match.end() - 1
    for index in range(start, len(squashed)):
        if squashed[index] == "(":
            depth += 1
        elif squashed[index] == ")":
            depth -= 1
            if depth == 0:
                return squashed[start + 1 : index]
    raise AssertionError(f"unbalanced parentheses in CREATE TABLE {table}")


# ---------------------------------------------------------------------------
# The generic P26 rules, via the Task 1 helper.
# ---------------------------------------------------------------------------

def test_027_obeys_the_shared_p26_migration_rules():
    assert_p26_migration_rules(VERSION, expect_reversible=True)


def test_027_up_does_not_bracket_its_own_transaction():
    """027 is the first runner-owned migration.

    scripts/p26_migrate.py executes the migration body, writes the
    schema_migrations row through register(), and commits both together. A
    BEGIN or COMMIT in the file would commit the DDL first and leave the ledger
    insert in a second transaction, so a failure between them would change the
    schema without recording it. See docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.
    """
    up = UP_PATH.read_text(encoding="utf-8")
    assert not re.search(r"^\s*BEGIN\s*;", up, re.IGNORECASE | re.MULTILINE), (
        "027 must not open its own transaction; the runner owns it"
    )
    assert not re.search(r"^\s*COMMIT\s*;", up, re.IGNORECASE | re.MULTILINE), (
        "027 must not commit itself; the runner commits the body and the "
        "ledger row together"
    )


def test_027_down_still_brackets_its_own_transaction():
    """The down file is executed manually, so the asymmetry is deliberate.

    The runner has no down command. Under psql each statement would autocommit
    on its own, so a down file that did not bracket itself could run its guards
    and its rollback stamp as independently durable statements.
    """
    down = DOWN_PATH.read_text(encoding="utf-8")
    assert re.search(r"^\s*BEGIN\s*;", down, re.IGNORECASE | re.MULTILINE)
    assert re.search(r"^\s*COMMIT\s*;", down, re.IGNORECASE | re.MULTILINE)


# ---------------------------------------------------------------------------
# A1 - agencies
# ---------------------------------------------------------------------------

def test_a1_agencies_shape_and_constraints():
    body = _table_body(_up(), "agencies")
    assert re.search(r"\bid\s+BIGSERIAL\s+PRIMARY KEY", body, re.IGNORECASE)
    assert re.search(r"\bname\s+VARCHAR\(\d+\)\s+NOT NULL", body, re.IGNORECASE)
    assert re.search(r"\bslug\s+VARCHAR\(\d+\)\s+NOT NULL", body, re.IGNORECASE)
    assert re.search(r"\bstatus\s+VARCHAR\(\d+\)\s+NOT NULL", body, re.IGNORECASE)
    assert re.search(
        r"settings\s+JSONB\s+NOT NULL\s+DEFAULT\s+'\{\}'::jsonb", body, re.IGNORECASE
    ), "agencies.settings must default to an empty object"
    assert re.search(r"created_at\s+TIMESTAMPTZ\s+NOT NULL", body, re.IGNORECASE)
    assert re.search(r"updated_at\s+TIMESTAMPTZ\s+NOT NULL", body, re.IGNORECASE)


def test_a1_agencies_rejects_an_empty_name():
    body = _table_body(_up(), "agencies")
    assert re.search(r"CHECK\s*\(\s*BTRIM\(name\)\s*<>\s*''\s*\)", body, re.IGNORECASE), (
        "agencies must reject a blank name"
    )


def test_a1_agencies_validates_the_slug_shape():
    body = _table_body(_up(), "agencies")
    assert "slug ~" in body.lower(), "agencies.slug must be shape-validated"


def test_a1_agencies_status_is_the_three_approved_values():
    body = _table_body(_up(), "agencies")
    match = re.search(
        r"CHECK\s*\(\s*status\s+IN\s*\(([^)]*)\)", body, re.IGNORECASE
    )
    assert match, "agencies.status must carry a CHECK"
    values = set(re.findall(r"'([a-z_]+)'", match.group(1)))
    assert values == {"active", "suspended", "archived"}, values


def test_a1_agencies_slug_is_unique():
    body = _table_body(_up(), "agencies")
    assert re.search(r"UNIQUE\s*\(\s*slug\s*\)", body, re.IGNORECASE), (
        "agencies.slug must be UNIQUE - it is the stable key the default "
        "agency and the public bridge resolve by"
    )


# ---------------------------------------------------------------------------
# A2 - operator_users
# ---------------------------------------------------------------------------

def test_a2_operator_users_shape():
    body = _table_body(_up(), "operator_users")
    for column in (
        r"\bid\s+BIGSERIAL\s+PRIMARY KEY",
        r"\bemail\s+VARCHAR\(\d+\)\s+NOT NULL",
        r"\bemail_normalized\s+VARCHAR\(\d+\)\s+NOT NULL",
        r"\bpassword_hash\s+TEXT\s+NOT NULL",
        r"\bfirst_name\s+VARCHAR\(\d+\)",
        r"\blast_name\s+VARCHAR\(\d+\)",
        r"\bstatus\s+VARCHAR\(\d+\)\s+NOT NULL",
        r"\bis_platform_admin\s+BOOLEAN\s+NOT NULL\s+DEFAULT\s+FALSE",
        r"\blast_login_at\s+TIMESTAMPTZ",
        r"\bcreated_at\s+TIMESTAMPTZ\s+NOT NULL",
        r"\bupdated_at\s+TIMESTAMPTZ\s+NOT NULL",
    ):
        assert re.search(column, body, re.IGNORECASE), f"operator_users missing {column}"


def test_a2_email_uniqueness_is_global_not_agency_scoped():
    """Spec D-4. Login happens before any agency context exists.

    A per-agency unique key would make the login lookup ambiguous and would
    force an agency selector into an unauthenticated form.
    """
    body = _table_body(_up(), "operator_users")
    assert re.search(
        r"UNIQUE\s*\(\s*email_normalized\s*\)", body, re.IGNORECASE
    ), "operator_users.email_normalized must be globally UNIQUE"
    assert not re.search(
        r"UNIQUE\s*\(\s*agency_id\s*,\s*email", body, re.IGNORECASE
    ), "email uniqueness must NOT be agency-scoped (spec D-4)"
    assert "agency_id" not in body.lower(), (
        "operator_users must not carry an agency_id; membership lives in "
        "agency_memberships so multi-agency needs no schema demolition"
    )


def test_a2_password_hash_format_is_constrained():
    body = _table_body(_up(), "operator_users")
    assert re.search(
        r"password_hash\s+LIKE\s+'pbkdf2_sha256\$%'", body, re.IGNORECASE
    ), "a plaintext password must be rejected by the schema itself"


def test_a2_operator_users_status_values():
    body = _table_body(_up(), "operator_users")
    match = re.search(r"CHECK\s*\(\s*status\s+IN\s*\(([^)]*)\)", body, re.IGNORECASE)
    assert match, "operator_users.status must carry a CHECK"
    values = set(re.findall(r"'([a-z_]+)'", match.group(1)))
    assert values == {"invited", "active", "disabled"}, values


# ---------------------------------------------------------------------------
# A3 - agency_memberships
# ---------------------------------------------------------------------------

def test_a3_membership_foreign_keys_and_delete_actions():
    body = _table_body(_up(), "agency_memberships")
    assert re.search(
        r"agency_id\s+BIGINT\s+NOT NULL\s+REFERENCES\s+agencies\s*\(\s*id\s*\)"
        r"\s+ON DELETE RESTRICT",
        body,
        re.IGNORECASE,
    ), "an agency holding memberships must not be deletable by accident"
    assert re.search(
        r"operator_user_id\s+BIGINT\s+NOT NULL\s+REFERENCES\s+operator_users"
        r"\s*\(\s*id\s*\)\s+ON DELETE CASCADE",
        body,
        re.IGNORECASE,
    ), "removing an operator removes their memberships"


def test_a3_exactly_three_agency_roles():
    body = _table_body(_up(), "agency_memberships")
    match = re.search(r"CHECK\s*\(\s*role\s+IN\s*\(([^)]*)\)", body, re.IGNORECASE)
    assert match, "agency_memberships.role must carry a CHECK"
    values = set(re.findall(r"'([a-z_]+)'", match.group(1)))
    assert values == set(AGENCY_ROLES), values


def test_a3_platform_admin_is_not_an_agency_role():
    """Spec section 3.2: a platform admin has no membership row at all."""
    body = _table_body(_up(), "agency_memberships")
    assert "platform_admin" not in body.lower(), (
        "platform_admin must live on operator_users.is_platform_admin, never "
        "as a fourth agency role"
    )
    assert "is_platform_admin" not in body.lower()


def test_a3_membership_has_exactly_one_unique_constraint():
    """The redundant three-column superset is retracted (spec section 3.2).

    PostgreSQL satisfies FOREIGN KEY (agency_id, assigned_agent_id) REFERENCES
    agency_memberships (agency_id, operator_user_id) from the two-column key
    alone; a superset including id would not satisfy the FK and would only add
    an index nothing reads.
    """
    body = _table_body(_up(), "agency_memberships")
    uniques = re.findall(r"UNIQUE\s*\(([^)]*)\)", body, re.IGNORECASE)
    normalised = [
        tuple(part.strip().lower() for part in item.split(",")) for item in uniques
    ]
    assert normalised == [("agency_id", "operator_user_id")], normalised


def test_a3_one_active_membership_per_operator():
    squashed = _squash(_up()).lower()
    assert re.search(
        r"create unique index if not exists uq_agency_memberships_single_active "
        r"on agency_memberships \(operator_user_id\) where status = 'active'",
        squashed,
    ), "P26-1 permits at most one ACTIVE membership per operator"


def test_a3_exactly_one_active_owner_per_agency():
    squashed = _squash(_up()).lower()
    assert re.search(
        r"create unique index if not exists uq_agency_memberships_single_owner "
        r"on agency_memberships \(agency_id\) where role = 'agency_owner' "
        r"and status = 'active'",
        squashed,
    ), "an agency has exactly one active owner"


# ---------------------------------------------------------------------------
# A4 - operator_sessions
# ---------------------------------------------------------------------------

def test_a4_session_has_no_agency_id():
    """Spec D-3: agency scope is resolved per request, never pinned.

    A pinned agency_id would keep a suspended membership usable until the
    session expired.
    """
    body = _table_body(_up(), "operator_sessions")
    assert "agency_id" not in body.lower(), (
        "operator_sessions must carry NO agency_id"
    )


def test_a4_session_shape_and_token_hash():
    body = _table_body(_up(), "operator_sessions")
    assert re.search(r"\bid\s+BIGSERIAL\s+PRIMARY KEY", body, re.IGNORECASE)
    assert re.search(
        r"operator_user_id\s+BIGINT\s+NOT NULL\s+REFERENCES\s+operator_users"
        r"\s*\(\s*id\s*\)\s+ON DELETE CASCADE",
        body,
        re.IGNORECASE,
    )
    assert re.search(r"token_hash\s+CHAR\(64\)\s+NOT NULL", body, re.IGNORECASE)
    for column in ("created_at", "last_seen_at", "expires_at"):
        assert re.search(rf"\b{column}\s+TIMESTAMPTZ\s+NOT NULL", body, re.IGNORECASE)
    assert re.search(r"\brevoked_at\s+TIMESTAMPTZ", body, re.IGNORECASE)


def test_a4_session_token_hash_is_lowercase_sha256_and_unique():
    body = _table_body(_up(), "operator_sessions")
    assert re.search(
        r"token_hash\s*~\s*'\^\[0-9a-f\]\{64\}\$'", body, re.IGNORECASE
    ), "only a lowercase hex SHA-256 may be stored"
    assert re.search(r"UNIQUE\s*\(\s*token_hash\s*\)", body, re.IGNORECASE)


def test_a4_session_expiry_is_after_creation():
    body = _table_body(_up(), "operator_sessions")
    assert re.search(
        r"CHECK\s*\(\s*expires_at\s*>\s*created_at\s*\)", body, re.IGNORECASE
    )


def test_a4_raw_session_token_is_never_stored():
    up = _up().lower()
    assert "token TEXT".lower() not in up
    assert not re.search(r"\btoken\s+VARCHAR", up), (
        "only the hash of a session token may be persisted"
    )


# ---------------------------------------------------------------------------
# A5 - indexes
# ---------------------------------------------------------------------------

def test_a5_required_indexes_exist():
    squashed = _squash(_up()).lower()
    for statement in (
        "create index if not exists idx_operator_sessions_user on operator_sessions (operator_user_id)",
        "create index if not exists idx_operator_sessions_expires_at on operator_sessions (expires_at)",
        "create index if not exists idx_agency_memberships_operator on agency_memberships (operator_user_id)",
        "create index if not exists idx_agency_memberships_agency on agency_memberships (agency_id, role, status)",
    ):
        assert statement in squashed, f"missing index: {statement}"


# ---------------------------------------------------------------------------
# A6 - the Default Agency
# ---------------------------------------------------------------------------

def test_a6_default_agency_row_is_created():
    squashed = _squash(_up()).lower()
    assert "insert into agencies" in squashed
    assert "'stima360'" in squashed, "slug must be stima360"
    assert "'STIMA360'" in _squash(_up()), "name must be STIMA360"
    assert "'active'" in squashed


def test_a6_default_agency_insert_is_idempotent_by_slug_not_id():
    squashed = _squash(_up()).lower()
    assert re.search(
        r"where not exists\s*\(\s*select 1 from agencies where slug\s*=\s*'stima360'",
        squashed,
    ), "re-running 027 must be a no-op, guarded by the slug"
    assert not re.search(r"insert into agencies\s*\([^)]*\bid\b", squashed), (
        "the default agency must never be pinned to id = 1"
    )


def test_a6_default_agency_settings_are_empty():
    squashed = _squash(_up()).lower()
    assert "'{}'::jsonb" in squashed, (
        "the default agency ships with empty, non-sensitive settings"
    )


# ---------------------------------------------------------------------------
# A7 - no credentials
# ---------------------------------------------------------------------------

def test_a7_027_seeds_no_operator_user():
    squashed = _squash(_up()).lower()
    assert "insert into operator_users" not in squashed, (
        "027 creates the identity schema only. Operators are seeded by "
        "scripts/p26_seed_agencies_test.py (Task 17), which is TEST-guarded "
        "and reads credentials from the environment."
    )
    assert "insert into agency_memberships" not in squashed
    assert "insert into operator_sessions" not in squashed


def test_a7_027_contains_no_credential_material():
    up = _up()
    assert "pbkdf2_sha256$" not in up.replace("pbkdf2_sha256$%", ""), (
        "the only permitted occurrence is the CHECK pattern 'pbkdf2_sha256$%'"
    )
    for forbidden in ("password =", "passwd", "secret", "api_key", "token_urlsafe"):
        assert forbidden not in up.lower(), f"027 must not contain {forbidden!r}"


# ---------------------------------------------------------------------------
# A8 - additive
# ---------------------------------------------------------------------------

def test_a8_027_alters_no_existing_table():
    up = _up().upper()
    for forbidden in ("ALTER TABLE", "DROP TABLE", "TRUNCATE", "DELETE FROM"):
        assert forbidden not in up, (
            f"027 is additive infrastructure; found {forbidden!r}"
        )


def test_a8_027_touches_no_core_table():
    squashed = _squash(_up()).lower()
    for table in ("contacts", "leads", "activities", "tasks", "stime"):
        assert not re.search(rf"\b(from|join|into|update|table)\s+{table}\b", squashed), (
            f"027 must not reference the CORE table {table!r}; CORE scope is 028-030"
        )


# ---------------------------------------------------------------------------
# A9 - the ledger row belongs to the runner
# ---------------------------------------------------------------------------

def test_a9_027_does_not_write_its_own_ledger_row():
    """scripts/p26_migrate.py register() inserts the schema_migrations row.

    It supplies checksum_up, checksum_down, down_available, transactional and
    execution_ms - values the file cannot know about itself. A self-insert here
    would collide with register() on the version primary key at apply time.
    Migration 026 sets the same precedent: zero self-ledger inserts.
    """
    squashed = _squash(_up()).lower()
    assert "insert into schema_migrations" not in squashed
    assert "delete from schema_migrations" not in squashed


# ---------------------------------------------------------------------------
# A10 - the down migration
# ---------------------------------------------------------------------------

def test_a10_down_drops_all_four_tables_in_fk_safe_order():
    squashed = _squash(_down()).lower()
    order = [
        squashed.index(f"drop table if exists {table}")
        for table in (
            "operator_sessions",
            "agency_memberships",
            "operator_users",
            "agencies",
        )
    ]
    assert order == sorted(order), (
        "drop order must be FK-reverse: operator_sessions, agency_memberships, "
        "operator_users, agencies"
    )


def test_a10_down_uses_no_cascade_shortcut():
    squashed = _squash(_down()).lower()
    assert "cascade" not in squashed, (
        "a CASCADE drop would hide a dependency that should fail loudly - in "
        "particular contacts.agency_id once 028 has run"
    )


def test_a10_down_records_the_rollback_without_deleting_history():
    squashed = _squash(_down()).lower()
    assert "rolled_back_at" in squashed, (
        "the ledger is append-only: a rollback is stamped, never removed"
    )
    assert "delete from schema_migrations" not in squashed


def test_a10_down_is_transactional():
    down = _down()
    assert re.search(r"^\s*BEGIN\s*;", down, re.IGNORECASE | re.MULTILINE)
    assert re.search(r"^\s*COMMIT\s*;", down, re.IGNORECASE | re.MULTILINE)


# ---------------------------------------------------------------------------
# Scope: Task 2 must not reach into Tasks 3, 7, 8 or 9.
# ---------------------------------------------------------------------------

def test_the_p26_1_migration_set_is_complete():
    """The P26-1 migration set is intact: 026 through 030.

    This guard was fail-closed while the set was being built, firing as each
    task legitimately added its migration. It fired again when P26-2B's 031
    appeared - which is what it was for: a later migration must arrive with a
    decision, not by accident.

    P26-2B is that decision (STIMA root ownership, ownership matrix approved in
    P26-2A). So the rule is narrowed from "these are the only P26 migrations"
    to "these five are P26-1's, unchanged, and every later one belongs to a
    later phase". P26-1's own shape is still pinned exactly; what is no longer
    asserted is that no other phase may exist.
    """
    p26_1 = [
        "026_p26_baseline",
        "027_p26_agency_identity",
        "028_p26_core_agency_columns",
        "029_p26_core_agency_backfill",
        "030_p26_core_agency_enforce",
    ]
    present = sorted(
        path.stem for path in MIGRATIONS.glob("0[23]*_p26*.sql")
        if not path.stem.endswith("_down")
    )
    assert present[: len(p26_1)] == p26_1, present
    for later in present[len(p26_1):]:
        assert int(later[:3]) > 30, (
            f"{later} sits inside P26-1's range without being one of its five"
        )
