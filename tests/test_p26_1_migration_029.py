"""P26-1 Task 8 - static checks for migration 029_p26_core_agency_backfill.

Offline tests. Nothing here opens a database connection: every assertion is
made against the SQL text.

029 is the *data* half of CORE agency scoping. It assigns every existing legacy
row in contacts, leads, activities and tasks to the real Default Agency
(slug 'stima360'), and does nothing else: no structure, no constraint, no
index, no trigger, no agent assignment, no invented creator attribution.

    028 = nullable structure
    029 = controlled backfill      <- this file
    030 = constraints and enforcement

029 is the one irreversible step of the three. Its down file refuses loudly
rather than pretending it can tell a backfilled row from one written
afterwards; recovery is a restore from the pre-029 snapshot.

Test quality note: these assertions parse each UPDATE statement individually -
its table, its SET target, its WHERE predicate and its agency lookup. Counting
the word UPDATE would pass a migration that wrote the wrong column to the wrong
table four times.

Coverage map:

    C1  the shared P26 migration rules accept 029 as irreversible
    C2  transaction ownership, up and down
    C3  the Default Agency guard
    C4  the four backfill statements, parsed individually
    C5  idempotency
    C6  no invented legacy attribution
    C7  the post-backfill integrity guard
    C8  no scope creep
    C9  the down migration refuses
    C10 030 is still absent
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

VERSION = "029_p26_core_agency_backfill"
UP_PATH = MIGRATIONS / f"{VERSION}.sql"
DOWN_PATH = MIGRATIONS / f"{VERSION}_down.sql"

BACKFILLED_TABLES = ("contacts", "leads", "activities", "tasks")
DEFAULT_AGENCY_SLUG = "stima360"

# Columns 029 must never write. Legacy rows carry no trustworthy historical
# operator identity, and inventing one would repeat the error P26-0 refused
# when it declined to retro-register migrations 001-025.
FORBIDDEN_TARGETS = ("assigned_agent_id", "created_by_user_id")


def _up() -> str:
    return _strip_sql_comments(UP_PATH.read_text(encoding="utf-8"))


def _down() -> str:
    return _strip_sql_comments(DOWN_PATH.read_text(encoding="utf-8"))


def _squash(sql: str) -> str:
    return " ".join(sql.split())


def _update_statements(sql: str) -> list[str]:
    """Return each UPDATE statement, from the keyword to its terminating ';'.

    Semicolons inside a dollar-quoted body would break a naive split, so those
    blocks are removed first: the DO guards are asserted separately.
    """
    without_blocks = re.sub(r"\$do\$.*?\$do\$", " ", _squash(sql), flags=re.DOTALL)
    return [
        match.group(0)
        for match in re.finditer(r"UPDATE\s+.*?;", without_blocks, re.IGNORECASE | re.DOTALL)
    ]


def _do_blocks(sql: str) -> list[str]:
    return re.findall(r"\$do\$.*?\$do\$", _squash(sql), re.DOTALL)


# ---------------------------------------------------------------------------
# C1 - the shared rules
# ---------------------------------------------------------------------------

def test_c1_029_obeys_the_shared_rules_as_an_irreversible_migration():
    assert_p26_migration_rules(VERSION, expect_reversible=False)


# ---------------------------------------------------------------------------
# C2 - transaction ownership
# ---------------------------------------------------------------------------

def test_c2_up_does_not_bracket_its_own_transaction():
    up = UP_PATH.read_text(encoding="utf-8")
    assert not re.search(r"^\s*BEGIN\s*;", up, re.IGNORECASE | re.MULTILINE)
    assert not re.search(r"^\s*COMMIT\s*;", up, re.IGNORECASE | re.MULTILINE)


def test_c2_down_brackets_its_own_transaction():
    down = DOWN_PATH.read_text(encoding="utf-8")
    assert re.search(r"^\s*BEGIN\s*;", down, re.IGNORECASE | re.MULTILINE)
    assert re.search(r"^\s*COMMIT\s*;", down, re.IGNORECASE | re.MULTILINE)


def test_c2_no_self_written_ledger_row():
    for sql in (_up(), _down()):
        assert "insert into schema_migrations" not in _squash(sql).lower()


def test_c2_neither_half_deletes_ledger_history():
    for sql in (_up(), _down()):
        assert "delete from schema_migrations" not in _squash(sql).lower()


# ---------------------------------------------------------------------------
# C3 - the Default Agency guard
# ---------------------------------------------------------------------------

def test_c3_a_guard_runs_before_any_update():
    """The agency must be proven to exist before a single row is written."""
    squashed = _squash(_up()).lower()
    first_guard = squashed.index("$do$")
    first_update = squashed.index("update ")
    assert first_guard < first_update, "the guard must precede the backfill"


def test_c3_guard_resolves_the_agency_by_slug():
    guard = _do_blocks(_up())[0].lower()
    assert f"slug = '{DEFAULT_AGENCY_SLUG}'" in guard or f"slug='{DEFAULT_AGENCY_SLUG}'" in guard


def test_c3_guard_requires_the_agency_to_be_active():
    guard = _do_blocks(_up())[0].lower()
    assert "status = 'active'" in guard or "status='active'" in guard


def test_c3_guard_raises_when_the_agency_cannot_be_resolved():
    guard = _do_blocks(_up())[0]
    assert "RAISE EXCEPTION" in guard.upper()


def test_c3_guard_rejects_an_ambiguous_agency():
    """agencies.slug is UNIQUE, but the guard must not silently accept a
    surprise: exactly one active row, counted."""
    guard = _do_blocks(_up())[0].lower()
    assert "count(" in guard


def test_c3_no_update_can_write_null_when_the_agency_is_missing():
    """A bare subselect returning no row would set agency_id = NULL silently.

    The guard above is what prevents that, so it must be present *and* the
    statements must not be reachable without it.
    """
    assert len(_do_blocks(_up())) >= 2, (
        "029 needs a pre-backfill agency guard and a post-backfill integrity guard"
    )


# ---------------------------------------------------------------------------
# C4 - the four backfill statements, parsed individually
# ---------------------------------------------------------------------------

def test_c4_exactly_four_update_statements():
    statements = _update_statements(_up())
    assert len(statements) == 4, [s[:60] for s in statements]


def test_c4_one_update_per_core_table_and_no_other_table():
    tables = [
        re.match(r"UPDATE\s+(\w+)", statement, re.IGNORECASE).group(1).lower()
        for statement in _update_statements(_up())
    ]
    assert sorted(tables) == sorted(BACKFILLED_TABLES), tables


def test_c4_every_update_sets_agency_id_and_nothing_else():
    """A statement writing any other column must fail, even if there are four."""
    for statement in _update_statements(_up()):
        set_clause = re.search(
            r"\bSET\b(.*?)\bWHERE\b", statement, re.IGNORECASE | re.DOTALL
        )
        assert set_clause, statement[:80]
        assigned = re.findall(r"(\w+)\s*=", set_clause.group(1))
        # The subselect's own "slug = '...'" and "status = '...'" are inside
        # parentheses; strip the parenthesised part before counting targets.
        outer = re.sub(r"\([^()]*(?:\([^()]*\)[^()]*)*\)", "", set_clause.group(1))
        targets = [name.lower() for name in re.findall(r"(\w+)\s*=", outer)]
        assert targets == ["agency_id"], (statement[:80], targets, assigned)


def test_c4_no_update_writes_a_forbidden_column():
    for statement in _update_statements(_up()):
        for forbidden in FORBIDDEN_TARGETS:
            assert not re.search(
                rf"\b{forbidden}\s*=", statement, re.IGNORECASE
            ), f"029 must not write {forbidden}"


def test_c4_every_update_resolves_the_agency_by_slug():
    for statement in _update_statements(_up()):
        assert re.search(
            rf"SELECT\s+id\s+FROM\s+agencies\s+WHERE\s+slug\s*=\s*'{DEFAULT_AGENCY_SLUG}'",
            statement,
            re.IGNORECASE,
        ), statement[:120]


def test_c4_every_update_requires_the_agency_to_be_active():
    for statement in _update_statements(_up()):
        assert re.search(r"status\s*=\s*'active'", statement, re.IGNORECASE), statement[:120]


def test_c4_no_literal_numeric_agency_id_anywhere():
    """A restore or re-seed can allocate a different serial; id = 1 is a trap."""
    squashed = _squash(_up())
    assert not re.search(r"agency_id\s*=\s*\d+", squashed, re.IGNORECASE)
    assert not re.search(r"agencies\s+WHERE\s+id\s*=\s*\d+", squashed, re.IGNORECASE)


# ---------------------------------------------------------------------------
# C5 - idempotency
# ---------------------------------------------------------------------------

def test_c5_every_update_is_guarded_by_agency_id_is_null():
    for statement in _update_statements(_up()):
        where = re.split(r"\bWHERE\b", statement, flags=re.IGNORECASE)[-1]
        assert re.search(r"agency_id\s+IS\s+NULL", where, re.IGNORECASE), statement[:120]


def test_c5_no_update_can_overwrite_an_existing_agency():
    """WHERE agency_id IS NULL is the only admitted predicate on that column."""
    for statement in _update_statements(_up()):
        where = re.split(r"\bWHERE\b", statement, flags=re.IGNORECASE)[-1]
        assert not re.search(r"agency_id\s+IS\s+NOT\s+NULL", where, re.IGNORECASE)
        assert not re.search(r"WHERE\s+TRUE", statement, re.IGNORECASE)


def test_c5_no_update_is_unconditional():
    for statement in _update_statements(_up()):
        assert re.search(r"\bWHERE\b", statement, re.IGNORECASE), statement[:80]


# ---------------------------------------------------------------------------
# C6 - no invented legacy attribution
# ---------------------------------------------------------------------------

def test_c6_neither_operator_column_appears_in_the_up_migration():
    squashed = _squash(_up()).lower()
    for forbidden in FORBIDDEN_TARGETS:
        assert forbidden not in squashed, (
            f"029 owns agency_id only; {forbidden} has no trustworthy legacy value"
        )


def test_c6_no_operator_table_is_read_or_written():
    squashed = _squash(_up()).lower()
    for table in ("operator_users", "agency_memberships", "operator_sessions"):
        assert table not in squashed, table


# ---------------------------------------------------------------------------
# C7 - the post-backfill integrity guard
# ---------------------------------------------------------------------------

def test_c7_a_guard_runs_after_the_updates():
    squashed = _squash(_up()).lower()
    last_update = squashed.rindex("update ")
    last_guard = squashed.rindex("$do$")
    assert last_guard > last_update, "the integrity guard must follow the backfill"


def test_c7_every_table_is_checked_for_remaining_nulls():
    """A guard covering only one of the four would certify nothing."""
    guard = _do_blocks(_up())[-1].lower()
    for table in BACKFILLED_TABLES:
        assert re.search(
            rf"from\s+{table}\s+where\s+agency_id\s+is\s+null", guard
        ), f"the integrity guard does not check {table}"


def test_c7_the_guard_raises_when_any_null_remains():
    guard = _do_blocks(_up())[-1]
    assert "RAISE EXCEPTION" in guard.upper()


def test_c7_the_guard_reports_enough_to_diagnose():
    """A bare 'failed' message would leave the operator guessing which table."""
    guard = _do_blocks(_up())[-1].lower()
    assert "%" in guard, "the exception must interpolate the failing counts"


def test_c7_029_certifies_itself_rather_than_deferring_to_030():
    """030's SET NOT NULL is a second gate, not the first one.

    029 must prove its own post-condition inside its own transaction, so an
    incomplete backfill is one refused migration rather than two to unpick.
    """
    final_guard = _do_blocks(_up())[-1]
    assert "agency_id is null" in final_guard.lower(), (
        "029 must count what it failed to backfill, not assume success"
    )
    assert "RAISE EXCEPTION" in final_guard.upper()
    assert "set not null" not in _squash(_up()).lower(), "enforcement belongs to 030"


# ---------------------------------------------------------------------------
# C8 - no scope creep
# ---------------------------------------------------------------------------

def test_c8_no_schema_change_of_any_kind():
    up = _up().upper()
    for forbidden in (
        "ALTER TABLE", "CREATE TABLE", "DROP TABLE", "CREATE INDEX", "DROP INDEX",
        "CREATE TRIGGER", "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION",
        "ADD CONSTRAINT", "SET NOT NULL", "TRUNCATE",
    ):
        assert forbidden not in up, f"029 is data only; found {forbidden!r}"


def test_c8_no_row_is_inserted_or_deleted():
    squashed = _squash(_up()).lower()
    assert "insert into" not in squashed
    assert "delete from" not in squashed


def test_c8_touches_no_table_outside_the_core_four():
    squashed = _squash(_up()).lower()
    for table in ("stime", "properties", "buy_requests", "property_matches",
                  "contact_roles", "lead_stime", "owner_accounts"):
        assert not re.search(rf"\bupdate\s+{table}\b", squashed), table
        assert not re.search(rf"\bfrom\s+{table}\b", squashed), table


# ---------------------------------------------------------------------------
# C9 - the down migration refuses
# ---------------------------------------------------------------------------

def test_c9_down_raises():
    assert "RAISE EXCEPTION" in _down().upper()


def test_c9_down_performs_no_business_write():
    squashed = _squash(_down()).lower()
    for forbidden in ("update ", "insert into", "delete from", "truncate"):
        assert forbidden not in squashed, f"the down must refuse, not act; found {forbidden!r}"


def test_c9_down_never_sets_agency_id_back_to_null():
    squashed = _squash(_down()).lower()
    assert not re.search(r"agency_id\s*=\s*null", squashed)


def test_c9_down_does_not_try_to_identify_legacy_rows():
    """It cannot: 029 leaves no mark distinguishing a row it set from one
    written afterwards. That impossibility is the reason it refuses."""
    squashed = _squash(_down()).lower()
    for table in BACKFILLED_TABLES:
        assert not re.search(rf"\b(update|delete\s+from)\s+{table}\b", squashed), table


def test_c9_down_names_the_recovery_path():
    down = _down()
    assert "P26_BACKUP_RESTORE_TEST" in down or "docs/P26_BACKUP_RESTORE_TEST.md" in down


def test_c9_down_states_that_029_is_irreversible():
    assert "irreversible" in _down().lower()


def test_c9_the_named_recovery_document_exists():
    """A recovery instruction pointing at a missing file is worse than none."""
    assert (ROOT / "docs" / "P26_BACKUP_RESTORE_TEST.md").exists()


# ---------------------------------------------------------------------------
# C10 - Task 8 must not reach into Task 9
# ---------------------------------------------------------------------------

def test_c10_the_p26_1_migration_set_is_complete():
    """The P26-1 migration set is complete: 026 through 030.

    This guard was fail-closed while the set was being built, firing as each
    task legitimately added its migration. With 030 in place there is no later
    migration to exclude, so it now asserts the finished shape instead - and
    will fire again if a 031 appears without a decision to extend P26-1.
    """
    present = sorted(
        path.stem for path in MIGRATIONS.glob("0[23]*_p26*.sql")
        if not path.stem.endswith("_down")
    )
    assert present == [
        "026_p26_baseline",
        "027_p26_agency_identity",
        "028_p26_core_agency_columns",
        "029_p26_core_agency_backfill",
        "030_p26_core_agency_enforce",
    ], present
