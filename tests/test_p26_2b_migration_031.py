"""P26-2B2A - static checks for migration 031_p26_stima_agency_columns.

Offline. Nothing here opens a database connection or applies a migration:
every assertion is made against the SQL text and the runner's own
validation. Stdlib only - no SQL parser is imported, because none is a
versioned dependency of this project and a migration test must not be the
thing that introduces one. Same discipline as tests/test_p26_1_migration_028.py.

`031` is the first micro-block of P26-2B and is deliberately the smallest
possible step: **one nullable column on one table, plus its foreign key and
its index.** Nothing else. It must be applicable to a running system without
changing any behaviour - which is exactly what "nullable, no default, no
backfill" buys. The runtime does not yet write the column, so every existing
writer keeps working unchanged.

Why the column stays nullable here, when 028 did the same for CORE: P26-0
rule 9 stages a column addition as

    additive (nullable)  ->  controlled backfill  ->  constraints

Fusing them removes the gate between them. `SET NOT NULL` is what *proves* the
backfill reached every row; a DEFAULT would make every row look correct and
destroy that proof. Those two steps are later blocks, not this one.

Ownership matrix (P26-2A, approved):

    stime              ROOT-OWNED     -> gets a physical agency_id  (this file)
    lead_stime         CHILD-DERIVED  -> must NOT get one
    stime_dettagliate  CHILD-DERIVED  -> must NOT get one

The last two are asserted as absences below, because the cheapest way for this
migration to go wrong is to quietly do more than it was scoped to do.

Coverage map:

    A1  the shared P26 migration rules accept 031
    A2  transaction ownership, up and down
    A3  the runner discovers a contiguous 026-031 and validates 031 cleanly
    A4  exactly one column, on exactly one table, BIGINT and nullable
    A5  the foreign key: named, to agencies(id), ON DELETE RESTRICT
    A6  the index
    A7  what 031 must NOT contain
    A8  scope: no other table is touched
    A9  the down migration reverses exactly what the up added
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from test_p26_1_migration_rules import (
    _strip_sql_comments,
    assert_p26_migration_rules,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"

VERSION = "031_p26_stima_agency_columns"
UP_PATH = MIGRATIONS / f"{VERSION}.sql"
DOWN_PATH = MIGRATIONS / f"{VERSION}_down.sql"

TABLE = "stime"
COLUMN = "agency_id"
FK_NAME = "stime_agency_id_fk"
INDEX_NAME = "idx_stime_agency_id"

# The two child tables the ownership matrix says must stay physically
# agency-less, plus the ingress table that is out of this block entirely
# (it does not even exist in the TEST database).
MUST_NOT_BE_TOUCHED = ("lead_stime", "stime_dettagliate", "whatsapp_incoming")


def _up() -> str:
    return _strip_sql_comments(UP_PATH.read_text(encoding="utf-8"))


def _down() -> str:
    return _strip_sql_comments(DOWN_PATH.read_text(encoding="utf-8"))


def _squash(sql: str) -> str:
    return " ".join(sql.split())


def _strip_sql_strings(sql: str) -> str:
    """Remove single-quoted literals, keeping the DDL around them.

    A rule about what the *DDL* does must not be satisfied or tripped by the
    text of an error message. 031's RAISE messages legitimately contain the
    words "NOT NULL" and "default" - they are what the migration says when it
    refuses - and a rule reading those as a NOT NULL clause or a DEFAULT clause
    is reading prose as code. `''` is Postgres's escaped quote and is consumed
    with the literal it sits in.
    """
    return re.sub(r"'(?:[^']|'')*'", "''", sql)


# ---------------------------------------------------------------------------
# A1 / A2 - the shared rules and transaction ownership
# ---------------------------------------------------------------------------

def test_a1_031_obeys_the_shared_p26_migration_rules():
    assert_p26_migration_rules(VERSION, expect_reversible=True)


def test_a2_up_does_not_bracket_its_own_transaction():
    """From 027 the runner brackets the body and its ledger row in one commit."""
    up = UP_PATH.read_text(encoding="utf-8")
    assert not re.search(r"^\s*BEGIN\s*;", up, re.IGNORECASE | re.MULTILINE)
    assert not re.search(r"^\s*COMMIT\s*;", up, re.IGNORECASE | re.MULTILINE)


def test_a2_down_brackets_its_own_transaction():
    """The down file is executed by hand, so it must bracket itself."""
    down = DOWN_PATH.read_text(encoding="utf-8")
    assert re.search(r"^\s*BEGIN\s*;", down, re.IGNORECASE | re.MULTILINE)
    assert re.search(r"^\s*COMMIT\s*;", down, re.IGNORECASE | re.MULTILINE)


def test_a2_no_self_written_ledger_row():
    """register() supplies the checksum, which a file cannot know about itself."""
    for sql in (_up(), _down()):
        assert "insert into schema_migrations" not in _squash(sql).lower()


def test_a2_down_never_deletes_ledger_history():
    assert "delete from schema_migrations" not in _squash(_down()).lower()


# ---------------------------------------------------------------------------
# A3 - the runner's own view: discovery, contiguity, static validation
#
# This is the migration infrastructure certifying the file, rather than a
# second opinion written here. If the runner would refuse to apply it, that is
# the fact that matters.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def runner():
    import importlib

    return importlib.import_module("scripts.p26_migrate")


def test_a3_the_runner_discovers_a_contiguous_026_to_031(runner):
    """031 must be present and the set contiguous from the baseline.

    Retargeted when 032 arrived: this asserted the set *ended* at 031, which
    made it a tripwire on every later migration rather than a check on this
    one. What 031 needs to certify is that it sits in an unbroken run from 026;
    whether anything follows is not its business.
    """
    discovered = runner.discover_migrations()
    numbers = [item.number for item in discovered]
    assert numbers[:6] == list(range(26, 32)), numbers
    for later in numbers[6:]:
        assert later > 31, numbers
    # verify_contiguous raises rather than returning; calling it is the test.
    runner.verify_contiguous(discovered)


def test_a3_the_runner_static_validation_passes(runner):
    migration = next(
        item for item in runner.discover_migrations() if item.version == VERSION
    )
    violations = runner.validate_migration(migration)
    assert violations == [], violations


def test_a3_the_runner_sees_a_reversible_migration(runner):
    migration = next(
        item for item in runner.discover_migrations() if item.version == VERSION
    )
    assert migration.down_path is not None
    assert migration.down_path.name == f"{VERSION}_down.sql"
    assert migration.number == 31


# ---------------------------------------------------------------------------
# A4 / A5 / A6 - the DDL, parsed rather than grepped
# ---------------------------------------------------------------------------

def _dollar_blocks(sql: str) -> list[str]:
    """The bodies of every $tag$...$tag$ block, in order.

    Stdlib only. sqlglot would parse this file more thoroughly, but it is not a
    versioned dependency of this project, and a migration test must not be the
    thing that introduces one.
    """
    return re.findall(r"\$(\w*)\$(.*?)\$\1\$", sql, re.DOTALL)


def test_a4_the_dollar_quoted_blocks_are_balanced():
    """A structural sanity check that needs no SQL parser.

    An unbalanced $do$ would make the rest of the file part of a string
    literal, so every later assertion here would pass while the migration did
    nothing. Counting the delimiters catches that.
    """
    raw = UP_PATH.read_text(encoding="utf-8")
    blocks = _dollar_blocks(raw)
    assert blocks, "the migration has no guarded block"
    for tag, _ in blocks:
        assert raw.count(f"${tag}$") % 2 == 0, f"unbalanced ${tag}$"


def test_a4_every_statement_is_terminated():
    up = _up().strip()
    assert up.endswith(";"), "the last statement is not terminated"


def test_a4_exactly_one_column_is_added():
    added = re.findall(r"ADD COLUMN IF NOT EXISTS\s+(\w+)", _up(), re.IGNORECASE)
    assert added == [COLUMN], added


def test_a4_only_stime_is_altered():
    altered = {
        match.lower()
        for match in re.findall(r"ALTER TABLE\s+(?:ONLY\s+)?(\w+)", _up(), re.IGNORECASE)
    }
    assert altered == {TABLE}, altered


def test_a4_the_column_is_bigint():
    """agencies.id is BIGSERIAL, so a reference to it is BIGINT.

    stime.id is INTEGER, but that is irrelevant here - this column points at
    agencies, not at stime.
    """
    match = re.search(
        rf"ADD COLUMN IF NOT EXISTS\s+{COLUMN}\s+(\w+)", _up(), re.IGNORECASE
    )
    assert match, "the column declaration could not be read"
    assert match.group(1).upper() == "BIGINT", match.group(1)


def test_a4_the_column_is_nullable():
    """P26-0 rule 9: nullable first. A later block owns SET NOT NULL.

    `IS NOT NULL` is a predicate, not a column constraint - 031's verification
    block legitimately tests `column_default IS NOT NULL` to decide whether to
    abort. The predicates are removed before the constraint check so the rule
    reads DDL and not SQL expressions.
    """
    ddl = _strip_sql_strings(_up())
    ddl = re.sub(r"\bIS\s+(?:NOT\s+)?NULL\b", " ", ddl, flags=re.IGNORECASE)
    assert not re.search(r"NOT\s+NULL", ddl, re.IGNORECASE), (
        "031 must leave agency_id nullable; SET NOT NULL is a later migration"
    )


def test_a5_the_foreign_key_is_named_deterministically():
    up = _squash(_up())
    assert f"ADD CONSTRAINT {FK_NAME}" in up, up
    assert re.search(
        rf"{FK_NAME}\s+FOREIGN KEY\s*\(\s*{COLUMN}\s*\)\s*REFERENCES\s+agencies\s*\(\s*id\s*\)",
        up, re.IGNORECASE,
    ), up


def test_a5_the_foreign_key_restricts_deletion():
    """An agency holding estimations must not be deletable by accident.

    RESTRICT matches what 028 chose for the four CORE tables; a SET NULL here
    would silently orphan a stima from its agency.
    """
    up = _squash(_up())
    window = up[up.index(FK_NAME):]
    assert "ON DELETE RESTRICT" in window[:200], window[:200]


def test_a5_the_constraint_is_guarded_for_idempotence():
    """PostgreSQL has no ADD CONSTRAINT IF NOT EXISTS (the 030 pattern)."""
    up = _squash(_up())
    assert "pg_constraint" in up, "the constraint is not guarded"
    assert f"conname = '{FK_NAME}'" in up, up


def test_a5_the_migration_raises_but_never_swallows():
    """Fail-loud, not fail-quiet.

    `RAISE EXCEPTION` is required - it is how an incompatible pre-existing
    object aborts the migration. An `EXCEPTION WHEN ... THEN` *handler* is
    forbidden, because that is the construct that would turn a real failure
    into a silent no-op.
    """
    up = _squash(_up()).upper()
    assert "RAISE EXCEPTION" in up, "the migration cannot fail loudly"
    assert "EXCEPTION WHEN" not in up, (
        "an exception handler would swallow a failure that must surface"
    )


# -- the three shapes the review requires: A no-op, B create, C fail loud ---

def test_a5_an_incompatible_existing_column_fails_loudly():
    """Case C for the column: right name, wrong shape.

    ADD COLUMN IF NOT EXISTS is silent when a column of that name already
    exists, whatever its type - so on its own it would accept an INTEGER, a
    NOT NULL or a DEFAULT left by some earlier hand-edit. The verification
    block is what turns that silence into an abort.
    """
    up = _squash(_up())
    assert "information_schema.columns" in up, (
        "the column's real shape is never verified after ADD COLUMN"
    )
    for expectation in ("'bigint'", "is_nullable", "column_default"):
        assert expectation in up, f"the column check does not test {expectation}"
    window = up[up.index("information_schema.columns"):]
    assert "RAISE EXCEPTION" in window, "an incompatible column does not abort"


def test_a5_the_foreign_key_check_is_not_just_the_name():
    """Case C for the FK: a homonymous constraint must not be accepted.

    conname alone proves nothing - any constraint on any table could carry
    that name. The guard must verify the relation, the referenced relation,
    the columns on both sides and the delete action.
    """
    up = _squash(_up())
    for attribute in ("contype", "conrelid", "confrelid", "confdeltype",
                      "conkey", "confkey"):
        assert attribute in up, f"the FK check ignores {attribute}"
    assert "'public.stime'::regclass" in up or "'stime'::regclass" in up, up
    assert "agencies'::regclass" in up, up
    # 'r' is RESTRICT in pg_constraint.confdeltype.
    assert "confdeltype <> 'r'" in up or "confdeltype != 'r'" in up, up


def test_a5_the_foreign_key_is_created_only_when_absent():
    """Case B, and case A as its complement: present-and-correct is a no-op."""
    up = _squash(_up())
    assert "IF NOT FOUND THEN" in up, up
    creation = up.index(f"ADD CONSTRAINT {FK_NAME}")
    guard = up.index("IF NOT FOUND THEN")
    assert guard < creation, "the constraint is created before the guard runs"


def test_a5_the_foreign_key_lookup_is_scoped_to_stime():
    """R2 fix 1: pg_constraint.conname is not globally unique.

    Constraint names are unique per *table*, not per database, so a constraint
    called stime_agency_id_fk on some other table is entirely legitimate. A
    lookup on conname alone would find it, decide "already present", and then
    either skip the creation stime actually needs or abort on a shape mismatch
    that was never ours to judge. The lookup must carry conrelid.
    """
    up = _squash(_up())
    lookup = up[up.index("FROM pg_constraint"):]
    lookup = lookup[: lookup.index("IF NOT FOUND")]
    assert f"conname = '{FK_NAME}'" in lookup, lookup
    assert "conrelid = 'public.stime'::regclass" in lookup, (
        "the constraint lookup is not scoped to stime: " + lookup
    )


def test_a5_the_foreign_key_lookup_takes_no_arbitrary_row():
    """No LIMIT 1, no ORDER BY, no first-row-wins.

    Once the lookup is scoped by (conname, conrelid) it can match at most one
    row, so narrowing an ambiguous result set is never the right fix - it would
    hide the ambiguity instead of making it impossible.
    """
    up = _squash(_up())
    lookup = up[up.index("FROM pg_constraint"):]
    lookup = lookup[: lookup.index("IF NOT FOUND")]
    for forbidden in ("LIMIT 1", "ORDER BY", "FETCH FIRST"):
        assert forbidden not in lookup.upper(), f"{forbidden} in the FK lookup"


@pytest.mark.parametrize(
    "attribute,reason",
    [
        ("indisunique", "a UNIQUE index is not equivalent to a plain one"),
        ("indpred", "a partial index does not cover every row"),
        ("indexprs", "an expression index is not an index on the column"),
        ("indisvalid", "an invalid index is not usable by the planner"),
        ("indisready", "an index still building is not ready for writes"),
        ("indislive", "a dead index is being dropped"),
        ("indnkeyatts", "a covering index has non-key columns"),
    ],
)
def test_a6_the_index_equivalence_check_is_exact(attribute, reason):
    """R2 fix 2: accept a pre-existing index only if it is truly equivalent.

    `relname` plus `indrelid` plus `indkey[0]` still admits a UNIQUE index, a
    partial index, an expression index, or one left invalid by a failed
    CREATE INDEX CONCURRENTLY. None of those is what
    `CREATE INDEX idx_stime_agency_id ON stime (agency_id)` produces, and a
    later migration relying on this index would be relying on something else.
    """
    up = _squash(_up())
    assert attribute in up, f"the index check ignores {attribute} - {reason}"


def test_a6_the_index_check_is_not_just_the_name():
    """Case C for the index: a homonymous object of any kind must abort."""
    up = _squash(_up())
    assert "pg_index" in up, "the index is never verified structurally"
    assert "indrelid" in up and "indkey" in up, up
    window = up[up.index("pg_index"):]
    assert "RAISE EXCEPTION" in window, "an incompatible index does not abort"


def test_a6_the_agency_index_exists_and_is_plain():
    up = _squash(_up())
    assert re.search(
        rf"CREATE INDEX\s+(?:IF NOT EXISTS\s+)?{INDEX_NAME}\s+ON\s+{TABLE}\s*\(\s*{COLUMN}\s*\)",
        up, re.IGNORECASE,
    ), up
    assert "CONCURRENTLY" not in up.upper(), (
        "a concurrent build cannot run inside the runner's transaction"
    )


def test_a6_exactly_one_index_is_created():
    created = re.findall(
        r"CREATE INDEX\s+(?:IF NOT EXISTS\s+)?(\w+)", _up(), re.IGNORECASE
    )
    assert created == [INDEX_NAME], created


# ---------------------------------------------------------------------------
# A7 - what 031 must NOT contain
# ---------------------------------------------------------------------------

def test_a7_no_default_agency_is_attached_to_the_column():
    """A DEFAULT would make every future row look correct and would remove a
    later migration's ability to prove the backfill covered every existing one."""
    assert not re.search(r"\bDEFAULT\b", _strip_sql_strings(_up()), re.IGNORECASE)


def test_a7_no_backfill_is_performed():
    up = _squash(_strip_sql_strings(_up())).upper()
    for forbidden in ("UPDATE ", "INSERT INTO", "DELETE FROM", "MERGE "):
        assert forbidden not in up, f"031 is additive only; found {forbidden!r}"


def test_a7_no_hardcoded_agency_id():
    """The Default Agency is resolved from its slug, in a later block."""
    up = _squash(_up())
    assert "stima360" not in up, "031 must not name the Default Agency at all"
    assert not re.search(rf"{COLUMN}\s*=\s*\d+", up), up


def test_a7_no_trigger_function_or_composite_key():
    up = _squash(_strip_sql_strings(_up())).upper()
    for forbidden in ("CREATE TRIGGER", "CREATE OR REPLACE FUNCTION",
                      "CREATE FUNCTION", "UNIQUE (", "UNIQUE("):
        assert forbidden not in up, f"031 must not contain {forbidden!r}"


def test_a7_no_set_not_null_anywhere():
    assert "SET NOT NULL" not in _squash(_strip_sql_strings(_up())).upper()


# ---------------------------------------------------------------------------
# A8 - scope: nothing else is touched
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("table", MUST_NOT_BE_TOUCHED)
def test_a8_no_other_stima_table_is_modified(table):
    """lead_stime and stime_dettagliate are CHILD-DERIVED by the approved
    ownership matrix: they derive their agency from a parent and must never
    carry one physically. whatsapp_incoming is out of this block entirely - it
    does not exist in the TEST database."""
    for sql in (_up(), _down()):
        assert not re.search(rf"\b{table}\b", sql, re.IGNORECASE), (
            f"031 references {table}, which is out of scope for this block"
        )


@pytest.mark.parametrize("table", ("contacts", "leads", "activities", "tasks"))
def test_a8_the_core_tables_are_not_touched(table):
    """P26-1 owns those four. 031 is the STIMA root only."""
    for sql in (_up(), _down()):
        assert not re.search(rf"\bALTER TABLE\s+{table}\b", sql, re.IGNORECASE)


def test_a8_only_agencies_is_referenced():
    referenced = {
        match.lower()
        for match in re.findall(r"REFERENCES\s+(\w+)", _up(), re.IGNORECASE)
    }
    assert referenced == {"agencies"}, referenced


# ---------------------------------------------------------------------------
# A9 - the down migration
# ---------------------------------------------------------------------------

def test_a9_down_removes_exactly_what_up_added():
    down = _squash(_down())
    assert f"DROP INDEX IF EXISTS {INDEX_NAME}" in down, down
    assert f"DROP CONSTRAINT IF EXISTS {FK_NAME}" in down, down
    assert f"DROP COLUMN IF EXISTS {COLUMN}" in down, down


def test_a9_down_drops_in_reverse_order():
    """Index, then constraint, then column - each depends on the next."""
    down = _squash(_down())
    assert (
        down.index(INDEX_NAME) < down.index(FK_NAME) < down.index(f"DROP COLUMN IF EXISTS {COLUMN}")
    ), down


def test_a9_down_touches_no_data():
    down = _squash(_strip_sql_strings(_down())).upper()
    for forbidden in ("UPDATE ", "INSERT INTO", "DELETE FROM", "TRUNCATE", "DROP TABLE"):
        assert forbidden not in down, f"the down migration would touch data: {forbidden!r}"


def test_a9_down_alters_only_stime():
    altered = {
        match.lower()
        for match in re.findall(r"ALTER TABLE\s+(?:ONLY\s+)?(\w+)", _down(), re.IGNORECASE)
    }
    assert altered == {TABLE}, altered


def test_a9_down_uses_no_cascade():
    """If something has come to depend on these objects, that must fail loudly."""
    assert "CASCADE" not in _squash(_down()).upper()
