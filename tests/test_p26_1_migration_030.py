"""P26-1 Task 9 - static checks for migration 030_p26_core_agency_enforce.

Offline tests. Nothing here opens a database connection: every assertion is
made against the SQL text.

030 is the enforcement half of CORE agency scoping. It turns "the application
must remember to check" into "the database refuses":

    028  nullable structure
    029  controlled backfill
    030  NOT NULL, composite FKs, agency-aware indexes, integrity trigger

The four SET NOT NULL are the second, independent proof that 029 reached every
row - 029 certified itself, and this refuses to apply if it did not.

The integrity function is the *revised* one. It validates before it derives: a
derive-only version would let a row carrying an explicit agency_id that
contradicts its own references pass straight through, and activities/tasks have
no composite-FK equivalent to catch that, because their three reference columns
are independently nullable.

Coverage map:

    D1   the shared P26 migration rules accept 030
    D2   transaction ownership
    D3   exactly four SET NOT NULL, agency_id only
    D4   one unique key, three composite FKs, guarded idempotently
    D5   exactly nine agency-aware indexes
    D6   statement order: NOT NULL -> constraints -> indexes -> function -> triggers
    D7   core_agency_integrity decision table, branch by branch
    D8   exactly two triggers, on activities and tasks only
    D9   the down migration reverses everything, in reverse
    D10  no scope creep
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

VERSION = "030_p26_core_agency_enforce"
UP_PATH = MIGRATIONS / f"{VERSION}.sql"
DOWN_PATH = MIGRATIONS / f"{VERSION}_down.sql"

NOT_NULL_TABLES = ("contacts", "leads", "activities", "tasks")

COMPOSITE_FKS = {
    "leads_contact_same_agency_fk": ("leads", "agency_id, contact_id", "contacts"),
    "contacts_agent_same_agency_fk": (
        "contacts", "agency_id, assigned_agent_id", "agency_memberships",
    ),
    "leads_agent_same_agency_fk": (
        "leads", "agency_id, assigned_agent_id", "agency_memberships",
    ),
}

EXPECTED_INDEXES = {
    "idx_contacts_agency_created": ("contacts", "agency_id, created_at desc, id desc"),
    "idx_contacts_agency_agent": ("contacts", "agency_id, assigned_agent_id"),
    "idx_contacts_agency_email": ("contacts", "agency_id, email_normalized"),
    "idx_contacts_agency_phone": ("contacts", "agency_id, phone_normalized"),
    "idx_leads_agency_created": ("leads", "agency_id, created_at desc, id desc"),
    "idx_leads_agency_agent": ("leads", "agency_id, assigned_agent_id"),
    "idx_leads_agency_contact": ("leads", "agency_id, contact_id"),
    "idx_activities_agency_occurred": ("activities", "agency_id, occurred_at desc, id desc"),
    "idx_tasks_agency_due": ("tasks", "agency_id, due_at, id desc"),
}

# Created by 028. 030 must not drop them: other modules still query these
# tables without an agency predicate.
INDEXES_028 = (
    "idx_contacts_agency_id", "idx_leads_agency_id",
    "idx_activities_agency_id", "idx_tasks_agency_id",
)


def _up() -> str:
    return _strip_sql_comments(UP_PATH.read_text(encoding="utf-8"))


def _down() -> str:
    return _strip_sql_comments(DOWN_PATH.read_text(encoding="utf-8"))


def _squash(sql: str) -> str:
    return " ".join(sql.split())


def _function_body(sql: str) -> str:
    """Return the body of core_agency_integrity(), lowercased."""
    squashed = _squash(sql)
    match = re.search(
        r"CREATE OR REPLACE FUNCTION core_agency_integrity.*?\$fn\$(.*?)\$fn\$",
        squashed, re.IGNORECASE | re.DOTALL,
    )
    assert match, "030 must define core_agency_integrity() in a $fn$ block"
    return match.group(1).lower()


def _index_statements(sql: str) -> dict[str, str]:
    found = {}
    for match in re.finditer(
        r"CREATE INDEX IF NOT EXISTS\s+(\w+)\s+ON\s+(\w+)\s*\(([^)]*)\)",
        _squash(sql), re.IGNORECASE,
    ):
        found[match.group(1).lower()] = (
            match.group(2).lower(), " ".join(match.group(3).split()).lower()
        )
    return found


# ---------------------------------------------------------------------------
# D1 / D2 - shared rules and transaction ownership
# ---------------------------------------------------------------------------

def test_d1_030_obeys_the_shared_p26_migration_rules():
    assert_p26_migration_rules(VERSION, expect_reversible=True)


def test_d2_up_does_not_bracket_its_own_transaction():
    up = UP_PATH.read_text(encoding="utf-8")
    assert not re.search(r"^\s*BEGIN\s*;", up, re.IGNORECASE | re.MULTILINE)
    assert not re.search(r"^\s*COMMIT\s*;", up, re.IGNORECASE | re.MULTILINE)


def test_d2_down_brackets_its_own_transaction():
    down = DOWN_PATH.read_text(encoding="utf-8")
    assert re.search(r"^\s*BEGIN\s*;", down, re.IGNORECASE | re.MULTILINE)
    assert re.search(r"^\s*COMMIT\s*;", down, re.IGNORECASE | re.MULTILINE)


def test_d2_no_self_written_ledger_row():
    for sql in (_up(), _down()):
        assert "insert into schema_migrations" not in _squash(sql).lower()


def test_d2_no_concurrently():
    assert not re.search(r"\bCONCURRENTLY\b", _up(), re.IGNORECASE)


# ---------------------------------------------------------------------------
# D3 - the four NOT NULL columns
# ---------------------------------------------------------------------------

def test_d3_exactly_four_set_not_null():
    statements = re.findall(
        r"ALTER TABLE\s+(\w+)\s+ALTER COLUMN\s+(\w+)\s+SET NOT NULL",
        _squash(_up()), re.IGNORECASE,
    )
    assert len(statements) == 4, statements


def test_d3_not_null_covers_the_four_core_tables():
    statements = re.findall(
        r"ALTER TABLE\s+(\w+)\s+ALTER COLUMN\s+(\w+)\s+SET NOT NULL",
        _squash(_up()), re.IGNORECASE,
    )
    assert sorted(table.lower() for table, _ in statements) == sorted(NOT_NULL_TABLES)


def test_d3_only_agency_id_becomes_not_null():
    """assigned_agent_id and created_by_user_id stay nullable: 029 wrote neither."""
    statements = re.findall(
        r"ALTER COLUMN\s+(\w+)\s+SET NOT NULL", _squash(_up()), re.IGNORECASE
    )
    assert {column.lower() for column in statements} == {"agency_id"}


def test_d3_no_drop_not_null_in_the_up_migration():
    assert not re.search(r"DROP NOT NULL", _up(), re.IGNORECASE)


# ---------------------------------------------------------------------------
# D4 - constraints
# ---------------------------------------------------------------------------

def test_d4_contacts_referencable_unique_key_exists():
    assert re.search(
        r"ADD CONSTRAINT contacts_agency_scope_unq UNIQUE\s*\(\s*agency_id\s*,\s*id\s*\)",
        _squash(_up()), re.IGNORECASE,
    ), "G-1's FK needs a UNIQUE (agency_id, id) on contacts"


def test_d4_leads_agency_scope_unq_must_not_exist():
    """Nothing references leads(agency_id, id); the revised plan removed it."""
    assert "leads_agency_scope_unq" not in _squash(_up()).lower()
    assert "leads_agency_scope_unq" not in _squash(_down()).lower()


def test_d4_exactly_one_unique_constraint_is_added():
    uniques = re.findall(r"ADD CONSTRAINT\s+(\w+)\s+UNIQUE", _squash(_up()), re.IGNORECASE)
    assert [name.lower() for name in uniques] == ["contacts_agency_scope_unq"], uniques


def test_d4_exactly_three_composite_foreign_keys():
    fks = re.findall(r"ADD CONSTRAINT\s+(\w+)\s+FOREIGN KEY", _squash(_up()), re.IGNORECASE)
    assert sorted(name.lower() for name in fks) == sorted(COMPOSITE_FKS), fks


def test_d4_each_composite_fk_has_the_right_table_columns_and_target():
    squashed = _squash(_up())
    separator = r"\s*,\s*"
    for name, (table, columns, target) in COMPOSITE_FKS.items():
        column_pattern = separator.join(part.strip() for part in columns.split(","))
        pattern = (
            rf"ALTER TABLE {table}\s+ADD CONSTRAINT {name}\s+FOREIGN KEY\s*"
            rf"\(\s*{column_pattern}\s*\)\s*REFERENCES\s+{target}\s*\("
        )
        assert re.search(pattern, squashed, re.IGNORECASE), f"{name} is wrong or missing"


def test_d4_assignment_fks_target_the_membership_key_from_027():
    """027 already provides UNIQUE (agency_id, operator_user_id)."""
    squashed = _squash(_up())
    for name in ("contacts_agent_same_agency_fk", "leads_agent_same_agency_fk"):
        assert re.search(
            rf"{name}.*?REFERENCES\s+agency_memberships\s*\(\s*agency_id\s*,\s*operator_user_id\s*\)",
            squashed, re.IGNORECASE,
        ), name


def test_d4_no_extra_uniqueness_on_agency_memberships():
    squashed = _squash(_up()).lower()
    assert not re.search(r"alter table agency_memberships", squashed)
    assert "agency_memberships_scope_unq" not in squashed


def test_d4_every_constraint_is_guarded_by_a_catalogue_check():
    """PostgreSQL has no ADD CONSTRAINT IF NOT EXISTS."""
    squashed = _squash(_up()).lower()
    assert squashed.count("pg_constraint") >= 4, (
        "each of the four named constraints needs its own pg_constraint guard"
    )
    assert not re.search(
        r";\s*ALTER TABLE \w+ ADD CONSTRAINT", _squash(_up()), re.IGNORECASE
    ), "a bare unguarded ADD CONSTRAINT would break re-execution"


def test_d4_no_exception_swallowing():
    """EXCEPTION WHEN duplicate_object THEN NULL would hide a real failure."""
    squashed = _squash(_up()).lower()
    assert "exception when" not in squashed
    assert "when others" not in squashed


# ---------------------------------------------------------------------------
# D5 - the nine agency-aware indexes
# ---------------------------------------------------------------------------

def test_d5_exactly_nine_indexes_are_created():
    assert len(_index_statements(_up())) == 9, sorted(_index_statements(_up()))


def test_d5_index_names_match_the_approved_set():
    assert sorted(_index_statements(_up())) == sorted(EXPECTED_INDEXES)


def test_d5_each_index_has_the_approved_table_and_column_order():
    found = _index_statements(_up())
    for name, (table, columns) in EXPECTED_INDEXES.items():
        assert found[name] == (table, columns), (name, found[name])


def test_d5_the_028_indexes_are_not_dropped():
    """Other modules still query these tables without an agency predicate."""
    squashed = _squash(_up()).lower()
    for name in INDEXES_028:
        assert f"drop index if exists {name}" not in squashed, name


def test_d5_every_index_is_created_if_not_exists():
    total = len(re.findall(r"CREATE\s+INDEX", _up(), re.IGNORECASE))
    guarded = len(re.findall(r"CREATE INDEX IF NOT EXISTS", _up(), re.IGNORECASE))
    assert total == guarded == 9


# ---------------------------------------------------------------------------
# D6 - statement order
# ---------------------------------------------------------------------------

def test_d6_statement_order_is_not_null_constraints_indexes_function_triggers():
    squashed = _squash(_up()).lower()
    positions = {
        "not_null": squashed.index("set not null"),
        "constraints": squashed.index("add constraint"),
        "indexes": squashed.index("create index"),
        "function": squashed.index("create or replace function"),
        "triggers": squashed.index("create trigger"),
    }
    ordered = sorted(positions, key=positions.get)
    assert ordered == ["not_null", "constraints", "indexes", "function", "triggers"], ordered


def test_d6_not_null_is_the_first_thing_that_can_fail():
    """Cheapest failure first: a partial backfill aborts before any DDL."""
    squashed = _squash(_up()).lower()
    assert squashed.index("set not null") < squashed.index("add constraint")


# ---------------------------------------------------------------------------
# D7 - the integrity function's decision table
# ---------------------------------------------------------------------------

def test_d7_function_is_a_trigger_function():
    squashed = _squash(_up())
    assert re.search(
        r"CREATE OR REPLACE FUNCTION core_agency_integrity\s*\(\s*\)\s*RETURNS trigger",
        squashed, re.IGNORECASE,
    )


def test_d7_resolves_the_lead_agency():
    body = _function_body(_up())
    assert re.search(r"select agency_id .*?from leads where id = new\.lead_id", body)


def test_d7_resolves_the_contact_agency():
    body = _function_body(_up())
    assert re.search(r"select agency_id .*?from contacts where id = new\.contact_id", body)


def test_d7_resolves_the_stima_agency_through_lead_stime_and_leads():
    body = _function_body(_up())
    assert "lead_stime" in body and "new.stima_id" in body
    assert re.search(r"join leads l on l\.id = ls\.lead_id", body)


def test_d7_compares_every_pair_of_resolved_references():
    """lead vs contact, lead vs stima, contact vs stima - all three."""
    body = _function_body(_up())
    pairs = (
        ("a_lead", "a_contact"),
        ("a_lead", "a_stima"),
        ("a_contact", "a_stima"),
    )
    for left, right in pairs:
        assert re.search(
            rf"{left} is not null and {right} is not null and {left} <> {right}", body
        ), f"missing pairwise check {left} vs {right}"


def test_d7_explicit_agency_is_compared_against_the_resolved_reference():
    body = _function_body(_up())
    assert re.search(r"new\.agency_id is not null", body)
    assert re.search(r"new\.agency_id <> resolved", body), (
        "an explicit agency_id must be validated, not trusted"
    )


def test_d7_no_early_return_before_reference_validation():
    """The forbidden derive-only shape:

        IF NEW.agency_id IS NOT NULL THEN RETURN NEW; END IF;

    placed before the reference checks would let a contradictory explicit
    agency pass straight through.
    """
    body = _function_body(_up())
    first_pairwise = body.index("a_lead is not null and a_contact is not null")
    explicit = body.index("new.agency_id is not null")
    assert first_pairwise < explicit, (
        "references must be validated before an explicit agency_id is considered"
    )
    assert not re.search(
        r"if new\.agency_id is not null then\s*return new\s*;\s*end if;", body
    ), "derive-only early return is forbidden"


def test_d7_derives_from_a_coherent_reference():
    body = _function_body(_up())
    assert re.search(r"resolved\s*:=\s*coalesce\(a_lead, a_contact, a_stima\)", body)
    assert re.search(r"new\.agency_id\s*:=\s*resolved", body)


def test_d7_bounded_fallback_checks_all_three_conditions():
    """The Default Agency may only rescue the P18 stima-only shape."""
    body = _function_body(_up())
    fallback = body[body.index("slug = 'stima360'"):] if "slug = 'stima360'" in body else ""
    assert fallback, "the fallback must resolve the default agency by slug"
    guard = body[:body.index("slug = 'stima360'")]
    assert "new.stima_id is not null" in guard
    assert "new.lead_id is null" in guard
    assert "new.contact_id is null" in guard


def test_d7_fallback_requires_an_active_default_agency():
    body = _function_body(_up())
    assert re.search(r"slug = 'stima360' and status = 'active'", body)


def test_d7_fallback_raises_when_the_default_agency_is_missing():
    body = _function_body(_up())
    tail = body[body.index("slug = 'stima360'"):]
    assert "raise exception" in tail


def test_d7_any_other_unresolved_shape_raises():
    """A generic activity or task with no resolvable agency must not be
    silently parked in the Default Agency."""
    body = _function_body(_up())
    assert body.rstrip().rstrip(";").rstrip().endswith("end") or "raise exception" in body
    assert body.count("raise exception") >= 5, (
        "three mismatch branches, the explicit-conflict branch, the missing "
        "default and the terminal unresolved branch each need their own raise"
    )


def test_d7_the_default_agency_is_never_a_generic_fallback():
    body = _function_body(_up())
    occurrences = body.count("stima360")
    assert occurrences == 1, (
        f"the default agency should be resolvable in exactly one bounded "
        f"branch; found {occurrences}"
    )


def test_d7_function_reports_the_table_it_rejected():
    body = _function_body(_up())
    assert "tg_table_name" in body, "the exception must name the offending table"


# ---------------------------------------------------------------------------
# D8 - triggers
# ---------------------------------------------------------------------------

def test_d8_exactly_two_triggers():
    triggers = re.findall(r"CREATE TRIGGER\s+(\w+)", _squash(_up()), re.IGNORECASE)
    assert len(triggers) == 2, triggers


def test_d8_triggers_are_on_activities_and_tasks_only():
    tables = re.findall(
        r"CREATE TRIGGER\s+\w+.*?\bON\s+(\w+)", _squash(_up()), re.IGNORECASE
    )
    assert sorted(table.lower() for table in tables) == ["activities", "tasks"], tables


def test_d8_no_trigger_on_contacts_or_leads():
    """Those two are protected by the composite FKs instead."""
    squashed = _squash(_up()).lower()
    for table in ("contacts", "leads"):
        assert not re.search(rf"create trigger \w+ before[^;]*? on {table}\b", squashed)


def test_d8_triggers_fire_before_insert_or_update_of_the_four_reference_columns():
    squashed = _squash(_up())
    # Non-greedy (.+?), not a negated character class: under IGNORECASE a
    # class like [^O] also excludes lowercase 'o', which every one of these
    # column names contains.
    matches = re.findall(
        r"BEFORE INSERT OR UPDATE OF\s+(.+?)\s+ON\s+(\w+)\s+FOR EACH ROW",
        squashed, re.IGNORECASE,
    )
    assert len(matches) == 2, matches
    for columns, _table in matches:
        listed = {name.strip().lower() for name in columns.split(",")}
        assert listed == {"agency_id", "contact_id", "lead_id", "stima_id"}, listed


def test_d8_triggers_are_row_level_and_call_the_function():
    squashed = _squash(_up()).lower()
    assert squashed.count("for each row execute function core_agency_integrity()") == 2


def test_d8_no_after_trigger():
    assert not re.search(r"\bAFTER\s+(INSERT|UPDATE|DELETE)", _up(), re.IGNORECASE)


def test_d8_trigger_creation_is_idempotent_via_the_catalogue():
    """PostgreSQL has no CREATE TRIGGER IF NOT EXISTS; 026 set the pattern."""
    squashed = _squash(_up()).lower()
    assert squashed.count("pg_trigger") >= 2
    assert "tgisinternal" in squashed, (
        "constraint-backed internal triggers must not produce a false positive"
    )


# ---------------------------------------------------------------------------
# D9 - the down migration
# ---------------------------------------------------------------------------

def test_d9_down_drops_the_two_triggers_and_the_function():
    squashed = _squash(_down()).lower()
    assert squashed.count("drop trigger if exists") == 2
    assert "drop function if exists core_agency_integrity" in squashed


def test_d9_down_drops_the_three_composite_fks_and_the_single_unique_key():
    squashed = _squash(_down()).lower()
    for name in COMPOSITE_FKS:
        assert f"drop constraint if exists {name}" in squashed, name
    assert "drop constraint if exists contacts_agency_scope_unq" in squashed
    assert squashed.count("drop constraint if exists") == 4


def test_d9_down_drops_exactly_the_nine_agency_aware_indexes():
    squashed = _squash(_down()).lower()
    for name in EXPECTED_INDEXES:
        assert f"drop index if exists {name}" in squashed, name
    assert len(re.findall(r"DROP\s+INDEX", _down(), re.IGNORECASE)) == 9


def test_d9_down_does_not_drop_the_028_indexes():
    squashed = _squash(_down()).lower()
    for name in INDEXES_028:
        assert f"drop index if exists {name}" not in squashed, name


def test_d9_down_restores_nullability_on_the_four_columns():
    statements = re.findall(
        r"ALTER TABLE\s+(\w+)\s+ALTER COLUMN\s+agency_id\s+DROP NOT NULL",
        _squash(_down()), re.IGNORECASE,
    )
    assert sorted(table.lower() for table in statements) == sorted(NOT_NULL_TABLES)


def test_d9_down_order_is_the_reverse_of_the_up():
    squashed = _squash(_down()).lower()
    positions = [
        squashed.index("drop trigger"),
        squashed.index("drop function"),
        squashed.index("drop constraint"),
        squashed.index("drop index"),
        squashed.index("drop not null"),
    ]
    assert positions == sorted(positions), positions


def test_d9_down_drops_no_column_and_no_table():
    squashed = _squash(_down()).lower()
    assert "drop column" not in squashed
    assert "drop table" not in squashed


def test_d9_down_touches_no_identity_table_or_business_data():
    squashed = _squash(_down()).lower()
    for table in ("agencies", "operator_users", "agency_memberships", "operator_sessions"):
        assert not re.search(rf"(alter|drop)\s+table\s+(if exists\s+)?{table}\b", squashed), table
    for forbidden in ("update ", "insert into", "delete from", "truncate"):
        assert forbidden not in squashed, forbidden


def test_d9_down_uses_no_cascade():
    assert "cascade" not in _squash(_down()).lower()


# ---------------------------------------------------------------------------
# D10 - no scope creep
# ---------------------------------------------------------------------------

def test_d10_no_column_is_added_or_dropped():
    up = _up().upper()
    assert "ADD COLUMN" not in up
    assert "DROP COLUMN" not in up


def test_d10_no_data_is_written():
    """030 is DDL only.

    A bare "update " check would be wrong here: the trigger definitions
    legitimately contain UPDATE OF, which is a DDL clause naming the columns
    that fire the trigger, not a data write. The real signature of a write is
    UPDATE <table> SET.
    """
    squashed = _squash(_up()).lower()
    assert not re.search(r"\bupdate\s+\w+\s+set\b", squashed), "030 must write no data"
    for forbidden in ("insert into", "delete from", "truncate"):
        assert forbidden not in squashed, f"030 is DDL only; found {forbidden!r}"


def test_d10_touches_no_table_outside_the_core_four():
    squashed = _squash(_up()).lower()
    for table in ("stime", "properties", "buy_requests", "contact_roles", "owner_accounts"):
        assert not re.search(rf"alter table\s+{table}\b", squashed), table


# ---------------------------------------------------------------------------
# The migration set is now complete.
# ---------------------------------------------------------------------------

def test_the_p26_1_migration_set_is_026_to_030():
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
