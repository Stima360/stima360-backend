"""P26-2C2A - static checks for migration 034_p26_property_agency_columns.

Offline, stdlib only. Nothing here opens a database or applies a migration.

034 opens the PROPERTY slice the way 031 opened the STIMA one, and the two are
deliberately the same shape: **one nullable column on one table, plus its
foreign key and its index.** Nothing else. It must be applicable to a running
system without changing any behaviour - no writer sets the column, nothing
reads it, no constraint requires it.

    034  nullable structure       <- this file
    ---  controlled backfill      (a later block)
    ---  NOT NULL / integrity     (a later block)

P26-0 rule 9 stages it that way, and fusing the steps removes the gate between
them: a later SET NOT NULL is what *proves* the backfill reached every row, and
a DEFAULT here would make every row look correct and destroy that proof.

Ownership matrix (P26-2C, approved):

    properties          ROOT-OWNED     -> physical agency_id   (this file)
    property_contacts   CHILD-DERIVED  -> must NOT get one
    property_leads      CHILD-DERIVED  -> must NOT get one
    property_documents  CHILD-DERIVED  -> must NOT get one
    property_photos     CHILD-DERIVED  -> must NOT get one
    property_visits     CHILD-DERIVED  -> must NOT get one
    property_price_history   CHILD-DERIVED
    property_status_history  CHILD-DERIVED
    property_watches         CHILD-DERIVED
    property_watch_observations  CHILD-DERIVED

The nine children are asserted as *absences* below. The cheapest way for a
migration like this to go wrong is to quietly do more than it was scoped to do,
and an agency column on a child table would be a second copy of the same fact -
one more thing to keep consistent, and one more place for the two to disagree.

The backfill this file deliberately does not perform is not trivial, which is
another reason it is not here: of 22 properties, 13 resolve to exactly one
agency and 9 have no provenance at all. Those 9 all predate 027, and agency B
was created after it, so they are legacy rather than ambiguous - but deciding
what to do with them is a data question for the next block, not a structural
one for this one.

Coverage map:

    K1  the shared P26 migration rules accept 034
    K2  transaction ownership, up and down
    K3  the runner discovers a contiguous 026-034 and validates 034
    K4  exactly one column, on exactly one table, BIGINT and nullable
    K5  the foreign key: named, to agencies(id), ON DELETE RESTRICT
    K6  the index
    K7  what 034 must NOT contain
    K8  scope: no child table, no other table, no data
    K9  the down migration reverses exactly what the up added
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from test_p26_1_migration_rules import (
    RUNNER,
    _load,
    _strip_sql_comments,
    assert_p26_migration_rules,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"

VERSION = "034_p26_property_agency_columns"
UP_PATH = MIGRATIONS / f"{VERSION}.sql"
DOWN_PATH = MIGRATIONS / f"{VERSION}_down.sql"

TABLE = "properties"
COLUMN = "agency_id"
FK_NAME = "properties_agency_id_fk"
INDEX_NAME = "idx_properties_agency_id"

# Every PROPERTY child in the approved matrix. None may gain an agency column.
CHILD_TABLES = (
    "property_contacts",
    "property_leads",
    "property_documents",
    "property_photos",
    "property_visits",
    "property_price_history",
    "property_status_history",
    "property_watches",
    "property_watch_observations",
)


@pytest.fixture(scope="module")
def runner_module():
    return _load(RUNNER, "p26_migrate_034")


def _up() -> str:
    return _strip_sql_comments(UP_PATH.read_text(encoding="utf-8"))


def _down() -> str:
    return _strip_sql_comments(DOWN_PATH.read_text(encoding="utf-8"))


def _squash(sql: str) -> str:
    return " ".join(sql.split())


def _strip_sql_strings(sql: str) -> str:
    """Drop quoted literals so prose in RAISE messages cannot trip code rules.

    This file bans NOT NULL and DEFAULT, and the migration's own error messages
    explain those bans. Without this the explanation would fail the rule it
    explains - a trap this project has hit repeatedly.
    """
    return re.sub(r"'(?:[^']|'')*'", " ", sql)


def _flat() -> str:
    return _squash(_strip_sql_strings(_up())).lower()


# ---------------------------------------------------------------------------
# K1 / K2 - shared rules and transaction ownership
# ---------------------------------------------------------------------------

def test_k1_034_obeys_the_shared_rules_and_is_reversible():
    assert_p26_migration_rules(VERSION, expect_reversible=True)


def test_k2_up_does_not_bracket_its_own_transaction():
    """From 027 the runner owns the UP transaction (P26-0 rule 3)."""
    up = UP_PATH.read_text(encoding="utf-8")
    assert not re.search(r"^\s*BEGIN\s*;", up, re.IGNORECASE | re.MULTILINE)
    assert not re.search(r"^\s*COMMIT\s*;", up, re.IGNORECASE | re.MULTILINE)


def test_k2_down_brackets_its_own_transaction():
    """The runner has no down command, so the down file is run by hand."""
    down = DOWN_PATH.read_text(encoding="utf-8")
    assert re.search(r"^\s*BEGIN\s*;", down, re.IGNORECASE | re.MULTILINE)
    assert re.search(r"^\s*COMMIT\s*;", down, re.IGNORECASE | re.MULTILINE)


# ---------------------------------------------------------------------------
# K3 - the runner
# ---------------------------------------------------------------------------

def _discovered(runner_module, number: int):
    return next(
        m for m in runner_module.discover_migrations(MIGRATIONS) if m.number == number
    )


def test_k3_the_runner_discovers_a_contiguous_026_to_034(runner_module):
    present = sorted(m.number for m in runner_module.discover_migrations(MIGRATIONS))
    assert present[:9] == [26, 27, 28, 29, 30, 31, 32, 33, 34], present
    for later in present[9:]:
        assert later > 34, present


def test_k3_the_runner_validates_034(runner_module):
    assert runner_module.validate_migration(_discovered(runner_module, 34)) == []


def test_k3_the_runner_still_validates_027_to_033(runner_module):
    """034 must not disturb the migrations it follows."""
    for number in (27, 28, 29, 30, 31, 32, 33):
        violations = runner_module.validate_migration(_discovered(runner_module, number))
        assert violations == [], (number, violations)


def test_k3_034_is_transactional(runner_module):
    assert not _discovered(runner_module, 34).non_transactional


def test_k3_034_ships_a_down_sibling(runner_module):
    assert _discovered(runner_module, 34).down_path is not None


# ---------------------------------------------------------------------------
# K4 - the column
# ---------------------------------------------------------------------------

def test_k4_the_column_is_added_to_properties():
    up = _squash(_strip_sql_strings(_up()))
    assert re.search(
        rf"ALTER TABLE {TABLE}\s+ADD COLUMN IF NOT EXISTS {COLUMN}\s+BIGINT",
        up, re.IGNORECASE,
    ), up


def test_k4_bigint_because_agencies_id_is_bigserial():
    """properties.id is BIGSERIAL too, but that is beside the point: this
    column points at agencies, not at properties."""
    up = _flat()
    assert "agency_id bigint" in up, up
    assert not re.search(r"agency_id\s+integer\b", up), up


def test_k4_exactly_one_column_is_added():
    up = _squash(_strip_sql_strings(_up()))
    added = re.findall(r"ADD COLUMN(?: IF NOT EXISTS)?\s+(\w+)", up, re.IGNORECASE)
    assert added == [COLUMN], added


def test_k4_the_column_shape_is_verified_after_the_add():
    """`ADD COLUMN IF NOT EXISTS` is silent when a column of that name already
    exists, whatever its type, nullability or default. So the catalogue is
    consulted afterwards rather than the statement being trusted."""
    up = _flat()
    assert "information_schema.columns" in up, up
    for probe in ("data_type", "is_nullable", "column_default"):
        assert probe in up, f"the column shape check ignores {probe}"


def test_k4_a_wrong_column_shape_raises():
    up = _up()
    block = up[up.index("information_schema.columns"):]
    assert "RAISE EXCEPTION" in block, block[:400]


# ---------------------------------------------------------------------------
# K5 - the foreign key
# ---------------------------------------------------------------------------

def test_k5_the_foreign_key_is_named():
    """Named rather than left to PostgreSQL's inline REFERENCES form, so the
    down file has a deterministic name to drop."""
    assert FK_NAME in _flat()


def test_k5_the_foreign_key_points_at_agencies_id_with_restrict():
    up = _squash(_strip_sql_strings(_up()))
    assert re.search(
        rf"ADD CONSTRAINT {FK_NAME}\s+FOREIGN KEY \({COLUMN}\)\s+"
        r"REFERENCES agencies \(id\)\s+ON DELETE RESTRICT",
        up, re.IGNORECASE,
    ), up


def test_k5_restrict_not_cascade_or_set_null():
    """A SET NULL would silently orphan a property from its agency; a CASCADE
    would delete real estate because an agency row went away."""
    up = _flat()
    assert "on delete cascade" not in up, up
    assert "on delete set null" not in up, up


def test_k5_the_existing_constraint_lookup_is_scoped_to_this_table():
    """Constraint names are unique per table, not per database. A lookup by
    conname alone could inspect a constraint belonging to somewhere else."""
    up = _flat()
    assert "conrelid" in up, "the FK guard is not scoped by table"
    assert re.search(r"conname\s*=", up), up


def test_k5_an_incompatible_existing_constraint_raises():
    up = _up()
    block = up[up.index("pg_constraint"):]
    assert "RAISE EXCEPTION" in block, block[:400]
    for predicate in ("contype", "confrelid", "confdeltype", "conkey", "confkey"):
        assert predicate in block, f"the FK compatibility check ignores {predicate}"


# ---------------------------------------------------------------------------
# K6 - the index
# ---------------------------------------------------------------------------

def test_k6_the_index_is_created_on_the_new_column():
    up = _squash(_strip_sql_strings(_up()))
    assert re.search(
        rf"CREATE INDEX {INDEX_NAME} ON {TABLE} \({COLUMN}\)", up, re.IGNORECASE
    ), up


def test_k6_create_index_if_not_exists_is_not_used():
    """It is silent when a relation of that name exists - even a table, or an
    index on another column."""
    up = _flat()
    assert "create index if not exists" not in up, up


def test_k6_an_existing_relation_is_checked_for_equivalence():
    up = _flat()
    assert "pg_index" in up, "an existing index is accepted without inspection"
    for predicate in (
        "indrelid", "indnatts", "indnkeyatts", "indkey", "indisunique",
        "indisvalid", "indisready", "indislive", "indpred", "indexprs",
    ):
        assert predicate in up, f"the index equivalence check ignores {predicate}"


def test_k6_not_concurrently():
    """A concurrent build cannot run inside the runner's transaction."""
    assert "concurrently" not in _flat()


# ---------------------------------------------------------------------------
# K7 - what 034 must not contain
# ---------------------------------------------------------------------------

def _add_column_statement() -> str:
    """The ALTER TABLE ... ADD COLUMN statement, on its own.

    The nullability and default rules are asserted here rather than over the
    whole file. A blanket ban on the strings "not null" and "default" reads as
    stricter but is not: the verification block legitimately contains
    `IS NOT NULL` and `column_default`, so a blanket rule would either fail on
    correct SQL or have to be dropped. Scoped to the statement that actually
    declares the column, the rule is both true and unavoidable.
    """
    up = _squash(_strip_sql_strings(_up()))
    match = re.search(
        rf"(ALTER TABLE {TABLE}\s+ADD COLUMN[^;]*);", up, re.IGNORECASE
    )
    assert match, f"034 contains no ADD COLUMN statement: {up[:200]}"
    return match.group(1)


def test_k7_the_column_stays_nullable():
    """034 is the additive half; NOT NULL belongs to the enforcement block."""
    assert "not null" not in _add_column_statement().lower().replace(
        "if not exists", ""
    ), _add_column_statement()
    assert "set not null" not in _flat(), "034 enforces nullability"


def test_k7_no_default():
    """A DEFAULT would make every row look correct and destroy the proof the
    later SET NOT NULL exists to provide."""
    assert "default" not in _add_column_statement().lower(), _add_column_statement()
    assert "set default" not in _flat(), _flat()


def test_k7_the_migration_verifies_both_of_those_on_the_live_column():
    """Not only absent from the statement - checked against the catalogue.

    `ADD COLUMN IF NOT EXISTS` is a no-op against a pre-existing column that is
    NOT NULL or carries a default, so the text of the statement alone proves
    nothing about the column that ends up there.
    """
    up = _up()
    blocks = [b for b in re.findall(r"\$do\$(.*?)\$do\$", up, re.DOTALL)
              if "information_schema.columns" in b]
    assert blocks, "034 never inspects the column it added"
    block = blocks[0]
    assert "is_nullable" in block and "column_default" in block, block[:400]
    assert block.count("RAISE EXCEPTION") >= 3, block[:600]


def test_k7_no_backfill_and_no_data_statement():
    up = _strip_sql_strings(_up())
    for statement in re.split(r";\s*(?![^$]*\$[a-z_]*\$)", up):
        head = statement.strip().lower()
        assert not head.startswith(("update ", "insert into", "delete from")), statement


def test_k7_no_trigger_and_no_function():
    up = _flat()
    assert "create trigger" not in up, up
    assert "create or replace function" not in up, up
    assert "create function" not in up, up


def test_k7_no_default_agency_lookup():
    """This block resolves no owner at all - that is the next block's job."""
    up = _flat()
    assert "stima360" not in up, up
    assert "slug" not in up, up
    assert not re.search(r"\b(from|join)\s+agencies\b", up), up


def test_k7_no_hardcoded_numeric_agency():
    assert not re.search(r"agency_id\s*(:?=)\s*\d", _strip_sql_strings(_up()), re.IGNORECASE)


def test_k7_no_exception_handler_swallows_a_failure():
    assert "exception when" not in _flat()


# ---------------------------------------------------------------------------
# K8 - scope
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("child", CHILD_TABLES)
def test_k8_no_child_table_is_mentioned_at_all(child):
    """CHILD-DERIVED means no physical column, and the simplest proof that none
    was added is that the table never appears."""
    assert child not in _flat(), f"034 touches {child}"


@pytest.mark.parametrize("child", CHILD_TABLES)
def test_k8_no_child_table_gains_an_agency_column(child):
    up = _squash(_strip_sql_strings(_up()))
    assert not re.search(
        rf"ALTER TABLE\s+{child}\b", up, re.IGNORECASE
    ), f"034 alters {child}"


@pytest.mark.parametrize(
    "table", ("stime", "contacts", "leads", "activities", "tasks", "lead_stime")
)
def test_k8_no_core_or_stima_table_is_touched(table):
    up = _squash(_strip_sql_strings(_up()))
    assert not re.search(rf"ALTER TABLE\s+{table}\b", up, re.IGNORECASE), table


def test_k8_only_properties_is_altered():
    up = _squash(_strip_sql_strings(_up()))
    altered = {t.lower() for t in re.findall(r"ALTER TABLE\s+(\w+)", up, re.IGNORECASE)}
    assert altered == {TABLE}, altered


def test_k8_no_035_exists():
    assert not list(MIGRATIONS.glob("035*.sql")), "this block ships no 035"


def test_k8_no_runtime_property_file_changed():
    """034 is schema only. The PROPERTY runtime belongs to a later block.

    Asserted on the migration, which is the only artefact this block ships:
    it names no application concern and creates nothing the runtime could use.
    """
    up = _flat()
    for forbidden in ("operator_sessions", "operator_users", "agency_memberships"):
        assert forbidden not in up, f"034 references {forbidden}"


# ---------------------------------------------------------------------------
# K9 - the down migration
# ---------------------------------------------------------------------------

def test_k9_down_drops_the_three_objects_in_reverse_order():
    down = _squash(_strip_sql_strings(_down())).lower()
    index_at = down.index(INDEX_NAME)
    fk_at = down.index(FK_NAME)
    column_at = down.index("drop column")
    assert index_at < fk_at < column_at, (
        "the down must drop the index, then the constraint, then the column"
    )


def test_k9_down_drops_exactly_what_the_up_added():
    down = _squash(_strip_sql_strings(_down()))
    assert re.search(rf"DROP INDEX IF EXISTS {INDEX_NAME}", down, re.IGNORECASE), down
    assert re.search(
        rf"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {FK_NAME}", down, re.IGNORECASE
    ), down
    assert re.search(
        rf"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS {COLUMN}", down, re.IGNORECASE
    ), down


def test_k9_down_uses_no_cascade():
    """If something has come to depend on these objects, that must fail loudly
    rather than be swept away silently."""
    assert "cascade" not in _squash(_strip_sql_strings(_down())).lower()


def test_k9_down_touches_no_other_table():
    down = _squash(_strip_sql_strings(_down()))
    altered = {t.lower() for t in re.findall(r"ALTER TABLE\s+(\w+)", down, re.IGNORECASE)}
    assert altered == {TABLE}, altered


def test_k9_down_changes_no_data():
    down = _squash(_strip_sql_strings(_down())).lower()
    for forbidden in ("update ", "insert into", "delete from", "truncate"):
        assert forbidden not in down, f"the down changes data: {forbidden!r}"


def test_k9_down_does_not_refuse():
    """034 writes no data, so unlike 029 and 032 it really can be undone."""
    assert "raise exception" not in _squash(_down()).lower()


# ---------------------------------------------------------------------------
# Mutation table - Layer B
#
# Every rule above is re-run against deliberately broken variants. A rule that
# stays green on a mutant is not a rule.
# ---------------------------------------------------------------------------

def _all_probe_failures(raw_sql: str) -> list[str]:
    up = _strip_sql_comments(raw_sql)
    squashed = _squash(_strip_sql_strings(up))
    flat = squashed.lower()
    failures: list[str] = []

    if not re.search(
        rf"ALTER TABLE {TABLE}\s+ADD COLUMN IF NOT EXISTS {COLUMN}\s+BIGINT",
        squashed, re.IGNORECASE,
    ):
        failures.append("the column is not added as a nullable BIGINT")

    # Scoped to the ADD COLUMN statement: the verification block legitimately
    # contains `IS NOT NULL` and `column_default`.
    add_column = re.search(
        rf"(ALTER TABLE {TABLE}\s+ADD COLUMN[^;]*);", squashed, re.IGNORECASE
    )
    if add_column is None:
        failures.append("no ADD COLUMN statement")
    else:
        declaration = add_column.group(1).lower().replace("if not exists", "")
        if "not null" in declaration:
            failures.append("NOT NULL introduced")
        if "default" in declaration:
            failures.append("DEFAULT introduced")
    if "set not null" in flat or "set default" in flat:
        failures.append("enforcement introduced")
    if "concurrently" in flat:
        failures.append("CONCURRENTLY inside a transaction")
    if "create index if not exists" in flat:
        failures.append("silent CREATE INDEX IF NOT EXISTS")

    if not re.search(
        rf"ADD CONSTRAINT {FK_NAME}\s+FOREIGN KEY \({COLUMN}\)\s+"
        r"REFERENCES agencies \(id\)\s+ON DELETE RESTRICT",
        squashed, re.IGNORECASE,
    ):
        failures.append("the foreign key is missing or not ON DELETE RESTRICT")
    if "conrelid" not in flat:
        failures.append("the FK lookup is not scoped by table")

    if not re.search(
        rf"CREATE INDEX {INDEX_NAME} ON {TABLE} \({COLUMN}\)", squashed, re.IGNORECASE
    ):
        failures.append("the index is missing")
    for predicate in ("indisunique", "indisvalid", "indpred", "indexprs"):
        if predicate not in flat:
            failures.append(f"the index equivalence check ignores {predicate}")

    altered = {t.lower() for t in re.findall(r"ALTER TABLE\s+(\w+)", squashed, re.IGNORECASE)}
    if altered != {TABLE}:
        failures.append(f"tables altered: {sorted(altered)}")
    for child in CHILD_TABLES:
        if child in flat:
            failures.append(f"child table referenced: {child}")

    for statement in re.split(r";\s*(?![^$]*\$[a-z_]*\$)", _strip_sql_strings(up)):
        if statement.strip().lower().startswith(("update ", "insert into", "delete from")):
            failures.append("a data statement appeared")

    if "create trigger" in flat or "function" in flat:
        failures.append("a trigger or function appeared")
    if "stima360" in flat or "slug" in flat:
        failures.append("a Default Agency lookup appeared")

    return failures


MUTANTS = {
    "column made NOT NULL":
        lambda s: s.replace(
            "ADD COLUMN IF NOT EXISTS agency_id BIGINT;",
            "ADD COLUMN IF NOT EXISTS agency_id BIGINT NOT NULL;",
        ),
    "column given a DEFAULT":
        lambda s: s.replace(
            "ADD COLUMN IF NOT EXISTS agency_id BIGINT;",
            "ADD COLUMN IF NOT EXISTS agency_id BIGINT DEFAULT 1;",
        ),
    "foreign key weakened to SET NULL":
        lambda s: s.replace("ON DELETE RESTRICT", "ON DELETE SET NULL"),
    "foreign key lookup unscoped":
        lambda s: s.replace("AND conrelid = 'public.properties'::regclass", ""),
    "index equivalence check gutted":
        lambda s: s.replace("AND i.indisunique = false", "")
                   .replace("AND i.indpred   IS NULL", "")
                   .replace("AND i.indexprs  IS NULL", ""),
    "silent CREATE INDEX IF NOT EXISTS":
        lambda s: s.replace(
            "CREATE INDEX idx_properties_agency_id",
            "CREATE INDEX IF NOT EXISTS idx_properties_agency_id",
        ),
    "a child table gains a column":
        lambda s: s + "\nALTER TABLE property_visits ADD COLUMN agency_id BIGINT;\n",
    "a backfill sneaks in":
        lambda s: s + "\nUPDATE properties SET agency_id = 1 WHERE agency_id IS NULL;\n",
}


def test_the_real_migration_passes_every_probe():
    assert _all_probe_failures(UP_PATH.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_every_mutant_is_caught(name):
    original = UP_PATH.read_text(encoding="utf-8")
    mutant = MUTANTS[name](original)
    assert mutant != original, f"the mutation {name!r} did not change the file"
    assert _all_probe_failures(mutant), f"mutation {name!r} passed every rule"
