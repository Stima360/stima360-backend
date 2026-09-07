"""P26-1 Task 7 - static checks for migration 028_p26_core_agency_columns.

Offline tests. Nothing here opens a database connection: every assertion is
made against the SQL text. Same discipline as tests/test_p26_baseline_isolation.py.

028 is the *structural* half of CORE agency scoping: ten nullable columns and
four plain indexes. It writes no data, constrains nothing, and defaults nothing.
The staging is deliberate and comes from P26-0 section 6 rule 9:

    028 = nullable structure
    029 = controlled backfill
    030 = constraints and enforcement

Fusing them would remove the gate between them. `030`'s SET NOT NULL is what
*proves* `029` reached every row; a DEFAULT in `028` would make every row look
correct and destroy that proof.

Transaction ownership: from `027` the runner owns the UP transaction, so `028`
carries no BEGIN/COMMIT and writes no schema_migrations row - `register()` owns
the ledger. The paired `_down.sql` is executed manually and therefore brackets
itself. See docs/P26_0_MIGRATION_ATOMICITY_ADDENDUM.md.

Coverage map from the approved design spec section 5.2:

    B1  the shared P26 migration rules accept 028
    B2  transaction ownership, up and down
    B3  exactly ten nullable columns, on the right four tables
    B4  foreign key targets and delete actions
    B5  what 028 must NOT contain
    B6  the four plain agency indexes
    B7  the down migration reverses exactly what the up added
    B8  029 and 030 have not been started
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

VERSION = "028_p26_core_agency_columns"
UP_PATH = MIGRATIONS / f"{VERSION}.sql"
DOWN_PATH = MIGRATIONS / f"{VERSION}_down.sql"

# The approved column plan. contacts and leads carry an assignment; activities
# and tasks do not - they are visible through their agency, and they already
# have a free-text assigned_to written by flow/, followup/ and buy/.
EXPECTED_COLUMNS = {
    "contacts": ["agency_id", "assigned_agent_id", "created_by_user_id"],
    "leads": ["agency_id", "assigned_agent_id", "created_by_user_id"],
    "activities": ["agency_id", "created_by_user_id"],
    "tasks": ["agency_id", "created_by_user_id"],
}
TOTAL_COLUMNS = 10

EXPECTED_INDEXES = {
    "idx_contacts_agency_id": ("contacts", "agency_id"),
    "idx_leads_agency_id": ("leads", "agency_id"),
    "idx_activities_agency_id": ("activities", "agency_id"),
    "idx_tasks_agency_id": ("tasks", "agency_id"),
}


def _up() -> str:
    return _strip_sql_comments(UP_PATH.read_text(encoding="utf-8"))


def _down() -> str:
    return _strip_sql_comments(DOWN_PATH.read_text(encoding="utf-8"))


def _squash(sql: str) -> str:
    return " ".join(sql.split())


def _alter_block(sql: str, table: str) -> str:
    """Return the ALTER TABLE statement for one table, up to its semicolon."""
    squashed = _squash(sql)
    match = re.search(rf"ALTER TABLE {table}\b", squashed, re.IGNORECASE)
    assert match, f"028 must alter {table!r}"
    end = squashed.index(";", match.start())
    return squashed[match.start():end]


def _added_columns(sql: str, table: str) -> list[str]:
    block = _alter_block(sql, table)
    return re.findall(r"ADD COLUMN IF NOT EXISTS\s+(\w+)", block, re.IGNORECASE)


# ---------------------------------------------------------------------------
# B1 - the shared rules
# ---------------------------------------------------------------------------

def test_b1_028_obeys_the_shared_p26_migration_rules():
    assert_p26_migration_rules(VERSION, expect_reversible=True)


# ---------------------------------------------------------------------------
# B2 - transaction ownership
# ---------------------------------------------------------------------------

def test_b2_up_does_not_bracket_its_own_transaction():
    """The runner brackets the body and its ledger row in one commit."""
    up = UP_PATH.read_text(encoding="utf-8")
    assert not re.search(r"^\s*BEGIN\s*;", up, re.IGNORECASE | re.MULTILINE)
    assert not re.search(r"^\s*COMMIT\s*;", up, re.IGNORECASE | re.MULTILINE)


def test_b2_down_brackets_its_own_transaction():
    """The down file is executed manually, so it must bracket itself."""
    down = DOWN_PATH.read_text(encoding="utf-8")
    assert re.search(r"^\s*BEGIN\s*;", down, re.IGNORECASE | re.MULTILINE)
    assert re.search(r"^\s*COMMIT\s*;", down, re.IGNORECASE | re.MULTILINE)


def test_b2_no_self_written_ledger_row():
    """register() supplies the checksum, which a file cannot know about itself."""
    for sql in (_up(), _down()):
        assert "insert into schema_migrations" not in _squash(sql).lower()


def test_b2_down_never_deletes_ledger_history():
    assert "delete from schema_migrations" not in _squash(_down()).lower()


# ---------------------------------------------------------------------------
# B3 - the ten columns
# ---------------------------------------------------------------------------

def test_b3_exactly_ten_columns_are_added():
    total = len(re.findall(r"ADD COLUMN IF NOT EXISTS", _up(), re.IGNORECASE))
    assert total == TOTAL_COLUMNS, total


def test_b3_only_the_four_core_tables_are_altered():
    altered = {
        match.lower()
        for match in re.findall(r"ALTER TABLE\s+(\w+)", _up(), re.IGNORECASE)
    }
    assert altered == set(EXPECTED_COLUMNS), altered


def test_b3_contacts_gets_three_columns():
    assert _added_columns(_up(), "contacts") == EXPECTED_COLUMNS["contacts"]


def test_b3_leads_gets_three_columns():
    assert _added_columns(_up(), "leads") == EXPECTED_COLUMNS["leads"]


def test_b3_activities_gets_two_columns():
    assert _added_columns(_up(), "activities") == EXPECTED_COLUMNS["activities"]


def test_b3_tasks_gets_two_columns():
    assert _added_columns(_up(), "tasks") == EXPECTED_COLUMNS["tasks"]


def test_b3_assigned_agent_id_is_absent_from_activities_and_tasks():
    """Spec 3.3: those two are visible through their agency, not per record."""
    for table in ("activities", "tasks"):
        assert "assigned_agent_id" not in _alter_block(_up(), table).lower(), table


def test_b3_every_column_is_bigint():
    """agencies.id and operator_users.id are BIGSERIAL, so references are BIGINT."""
    declarations = re.findall(
        r"ADD COLUMN IF NOT EXISTS\s+\w+\s+(\w+)", _up(), re.IGNORECASE
    )
    assert len(declarations) == TOTAL_COLUMNS
    assert {declaration.upper() for declaration in declarations} == {"BIGINT"}


def test_b3_no_column_is_declared_not_null():
    """P26-0 rule 9: nullable first. 030's SET NOT NULL is the backfill's proof."""
    assert not re.search(r"NOT\s+NULL", _up(), re.IGNORECASE)


def test_b3_no_column_carries_a_default():
    """A DEFAULT would make every future insert look correct and would remove
    030's ability to prove the 029 backfill covered every existing row."""
    assert not re.search(r"\bDEFAULT\b", _up(), re.IGNORECASE)


# ---------------------------------------------------------------------------
# B4 - foreign keys
# ---------------------------------------------------------------------------

def test_b4_agency_id_references_agencies_on_delete_restrict():
    """An agency holding CORE records must not be deletable by accident."""
    for table in EXPECTED_COLUMNS:
        block = _alter_block(_up(), table)
        match = re.search(
            r"ADD COLUMN IF NOT EXISTS\s+agency_id\s+BIGINT\s+"
            r"REFERENCES\s+agencies\s*\(\s*id\s*\)\s+ON DELETE RESTRICT",
            block,
            re.IGNORECASE,
        )
        assert match, f"{table}.agency_id FK is wrong or missing"


def test_b4_operator_references_are_on_delete_set_null():
    """Removing a person must never cascade-delete customer records."""
    for table, columns in EXPECTED_COLUMNS.items():
        block = _alter_block(_up(), table)
        for column in columns:
            if column == "agency_id":
                continue
            match = re.search(
                rf"ADD COLUMN IF NOT EXISTS\s+{column}\s+BIGINT\s+"
                r"REFERENCES\s+operator_users\s*\(\s*id\s*\)\s+ON DELETE SET NULL",
                block,
                re.IGNORECASE,
            )
            assert match, f"{table}.{column} FK is wrong or missing"


def test_b4_no_operator_reference_cascades():
    assert not re.search(
        r"REFERENCES\s+operator_users\s*\(\s*id\s*\)\s+ON DELETE CASCADE",
        _up(),
        re.IGNORECASE,
    )


# ---------------------------------------------------------------------------
# B5 - what 028 must not contain
# ---------------------------------------------------------------------------

def test_b5_no_composite_same_agency_foreign_key():
    """The (agency_id, contact_id) and assignment FKs belong to 030."""
    assert not re.search(r"FOREIGN KEY\s*\(", _up(), re.IGNORECASE)
    assert not re.search(r"ADD CONSTRAINT", _up(), re.IGNORECASE)


def test_b5_no_data_is_written():
    """029 owns the backfill. 028 must not touch a single row."""
    squashed = _squash(_up()).lower()
    for forbidden in ("update ", "insert into", "delete from", "merge "):
        assert forbidden not in squashed, f"028 must write no data; found {forbidden!r}"


def test_b5_no_trigger_or_function():
    """core_agency_integrity() is created by 030, after the backfill."""
    squashed = _squash(_up()).lower()
    for forbidden in ("create trigger", "create or replace function", "create function"):
        assert forbidden not in squashed, forbidden


def test_b5_no_hard_coded_agency_identifier():
    """No literal agency id, and no slug lookup: 028 resolves no agency at all."""
    squashed = _squash(_up()).lower()
    assert "stima360" not in squashed
    assert not re.search(r"agency_id\s*=\s*\d+", squashed)
    assert "from agencies" not in squashed


def test_b5_no_unique_constraint_or_index():
    assert not re.search(r"\bUNIQUE\b", _up(), re.IGNORECASE)


def test_b5_no_concurrently():
    assert not re.search(r"\bCONCURRENTLY\b", _up(), re.IGNORECASE)


def test_b5_touches_no_identity_table():
    """agencies and the operator tables are 027's; 028 only references them."""
    squashed = _squash(_up()).lower()
    for table in ("agencies", "operator_users", "agency_memberships", "operator_sessions"):
        assert not re.search(rf"alter table\s+{table}\b", squashed), table


def test_b5_touches_no_table_outside_the_core_four():
    squashed = _squash(_up()).lower()
    for table in ("stime", "contact_roles", "lead_stime", "properties", "buy_requests"):
        assert not re.search(rf"alter table\s+{table}\b", squashed), table


# ---------------------------------------------------------------------------
# B6 - indexes
# ---------------------------------------------------------------------------

def test_b6_the_four_plain_agency_indexes_exist():
    squashed = _squash(_up()).lower()
    for name, (table, column) in EXPECTED_INDEXES.items():
        assert (
            f"create index if not exists {name} on {table} ({column})" in squashed
        ), f"missing index {name}"


def test_b6_exactly_four_indexes_are_created():
    total = len(re.findall(r"CREATE\s+INDEX", _up(), re.IGNORECASE))
    assert total == len(EXPECTED_INDEXES), total


def test_b6_no_agency_aware_composite_index_yet():
    """Those mirror the real ORDER BY clauses and belong to 030."""
    squashed = _squash(_up()).lower()
    assert not re.search(r"create index[^;]*\(\s*agency_id\s*,", squashed)


# ---------------------------------------------------------------------------
# B7 - the down migration
# ---------------------------------------------------------------------------

def test_b7_down_drops_exactly_ten_columns():
    total = len(re.findall(r"DROP COLUMN IF EXISTS", _down(), re.IGNORECASE))
    assert total == TOTAL_COLUMNS, total


def test_b7_down_drops_every_column_the_up_added():
    dropped: dict[str, list[str]] = {}
    for table in EXPECTED_COLUMNS:
        block = _alter_block(_down(), table)
        dropped[table] = re.findall(
            r"DROP COLUMN IF EXISTS\s+(\w+)", block, re.IGNORECASE
        )
    for table, columns in EXPECTED_COLUMNS.items():
        assert sorted(dropped[table]) == sorted(columns), table


def test_b7_down_drops_exactly_the_four_indexes():
    squashed = _squash(_down()).lower()
    for name in EXPECTED_INDEXES:
        assert f"drop index if exists {name}" in squashed, name
    assert len(re.findall(r"DROP\s+INDEX", _down(), re.IGNORECASE)) == len(EXPECTED_INDEXES)


def test_b7_down_uses_no_cascade():
    """A CASCADE would silently remove whatever came to depend on these columns."""
    assert "cascade" not in _squash(_down()).lower()


def test_b7_down_touches_no_business_data():
    squashed = _squash(_down()).lower()
    for forbidden in ("update ", "insert into", "delete from", "truncate"):
        assert forbidden not in squashed, forbidden


def test_b7_down_touches_no_identity_table():
    """Rolling back 028 must not disturb 027's agencies or operators."""
    squashed = _squash(_down()).lower()
    for table in ("agencies", "operator_users", "agency_memberships", "operator_sessions"):
        assert not re.search(rf"(alter|drop)\s+table\s+(if exists\s+)?{table}\b", squashed), table


def test_b7_down_drops_indexes_before_columns():
    """Safe reverse order: an index depending on a column goes first."""
    squashed = _squash(_down()).lower()
    first_index = squashed.index("drop index")
    first_column = squashed.index("drop column")
    assert first_index < first_column


# ---------------------------------------------------------------------------
# B8 - Task 7 must not reach into Tasks 8 or 9
# ---------------------------------------------------------------------------

def test_b8_the_p26_1_migration_set_is_complete():
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
