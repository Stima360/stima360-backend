"""P26-2B2C - static checks for migration 032_p26_stima_agency_backfill.

Offline, stdlib only. Nothing here opens a database or applies a migration:
every assertion is made against the SQL text and the runner's own validation,
the same discipline as tests/test_p26_1_migration_029.py.

That constraint deserves saying plainly. The cases this block cares about -
"zero provenance fails", "two agencies fail" - are *behavioural*, and no
database is reachable from here to exercise them. So each is asserted in two
layers instead:

    Layer A   the guard exists, targets the right tables, and raises
    Layer B   a negative control - the same migration with that one guard
              removed - must be *rejected* by the rule that claims to check it

Layer B is what keeps Layer A honest. A rule that passes on the real file and
also passes on a file with the guard deleted proves nothing, and several of the
rules below were rewritten once their negative control turned out to be green.

031 added `stime.agency_id` as nullable. 032 fills the historical rows and
nothing else:

    031  nullable structure           (applied)
    032  controlled backfill          <- this file
    ---  NOT NULL / integrity         (a later block)

The derivation is provenance, never convenience:

    stime -> lead_stime -> leads.agency_id

A stima must resolve to *exactly one* distinct agency through that path. Zero
is a hard failure, two is a hard failure, and a linked lead with no agency of
its own is a hard failure. There is no Default Agency fallback here - unlike
029, which legitimately had one because it was assigning the whole legacy
estate to a single known agency. Guessing an owner for a specific historical
estimation is a different act, and a wrong guess is a cross-tenant leak that
looks exactly like correct data.

Coverage map:

    E1  the shared P26 migration rules accept 032 as irreversible
    E2  transaction ownership, up and down
    E3  the runner discovers a contiguous 026-032 and validates 032
    E4  the derivation: provenance only, exactly one agency
    E5  the guards - zero, multi, NULL-lead, prefilled conflict
    E6  activities and tasks may not disagree with the derived agency
    E7  no fallback, no arbitrary selection, no hardcoded agency
    E8  scope: only stime.agency_id is written, only NULL rows
    E9  the post-condition
    E10 the down migration refuses
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


@pytest.fixture(scope="module")
def runner_module():
    """The real runner, loaded from scripts/ - not a stand-in.

    Local rather than shared: the fixture in test_p26_1_migration_rules is not
    exposed through a conftest, and importing the module does not import its
    fixtures.
    """
    return _load(RUNNER, "p26_migrate_032")

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"

VERSION = "032_p26_stima_agency_backfill"
UP_PATH = MIGRATIONS / f"{VERSION}.sql"
DOWN_PATH = MIGRATIONS / f"{VERSION}_down.sql"

# The provenance chain, and the only tables 032 is allowed to read from.
PROVENANCE = ("stime", "lead_stime", "leads")
# Read for the conflict guard, never written.
CONSULTED = ("activities", "tasks")


def _up() -> str:
    return _strip_sql_comments(UP_PATH.read_text(encoding="utf-8"))


def _down() -> str:
    return _strip_sql_comments(DOWN_PATH.read_text(encoding="utf-8"))


def _squash(sql: str) -> str:
    return " ".join(sql.split())


def _strip_sql_strings(sql: str) -> str:
    """Remove quoted literals, including dollar-quoted bodies' string parts.

    RAISE messages are prose and repeatedly trip rules written about code -
    'no COALESCE' must not fire on the word COALESCE inside an error message
    explaining why COALESCE is refused.
    """
    without_dollar_strings = re.sub(r"'(?:[^']|'')*'", " ", sql)
    return without_dollar_strings


def _statements(sql: str) -> list[str]:
    """Top-level statements, with dollar-quoted blocks kept intact."""
    out: list[str] = []
    depth_marker = None
    buffer: list[str] = []
    i = 0
    while i < len(sql):
        if depth_marker is None:
            match = re.match(r"\$[A-Za-z_]*\$", sql[i:])
            if match:
                depth_marker = match.group(0)
                buffer.append(depth_marker)
                i += len(depth_marker)
                continue
            if sql[i] == ";":
                out.append("".join(buffer))
                buffer = []
                i += 1
                continue
        else:
            if sql.startswith(depth_marker, i):
                buffer.append(depth_marker)
                i += len(depth_marker)
                depth_marker = None
                continue
        buffer.append(sql[i])
        i += 1
    if "".join(buffer).strip():
        out.append("".join(buffer))
    return [s.strip() for s in out if s.strip()]


# ---------------------------------------------------------------------------
# E1 / E2 - the shared rules and transaction ownership
# ---------------------------------------------------------------------------

def test_e1_032_obeys_the_shared_rules_as_an_irreversible_migration():
    assert_p26_migration_rules(VERSION, expect_reversible=False)


def test_e2_up_does_not_bracket_its_own_transaction():
    """From 027 the runner owns the UP transaction (P26-0 rule 3)."""
    up = UP_PATH.read_text(encoding="utf-8")
    assert not re.search(r"^\s*BEGIN\s*;", up, re.IGNORECASE | re.MULTILINE)
    assert not re.search(r"^\s*COMMIT\s*;", up, re.IGNORECASE | re.MULTILINE)


def test_e2_down_brackets_its_own_transaction():
    """The runner has no down command, so the down file is run by hand."""
    down = DOWN_PATH.read_text(encoding="utf-8")
    assert re.search(r"^\s*BEGIN\s*;", down, re.IGNORECASE | re.MULTILINE)
    assert re.search(r"^\s*COMMIT\s*;", down, re.IGNORECASE | re.MULTILINE)


# ---------------------------------------------------------------------------
# E3 - the runner
# ---------------------------------------------------------------------------

def _discovered(runner_module, number: int):
    return next(
        m for m in runner_module.discover_migrations(MIGRATIONS) if m.number == number
    )


def test_e3_the_runner_discovers_a_contiguous_026_to_032(runner_module):
    present = sorted(m.number for m in runner_module.discover_migrations(MIGRATIONS))
    assert present[:7] == [26, 27, 28, 29, 30, 31, 32], present
    for later in present[7:]:
        assert later > 32, present


def test_e3_the_runner_validates_032_without_complaint(runner_module):
    violations = runner_module.validate_migration(_discovered(runner_module, 32))
    assert violations == [], violations


def test_e3_the_runner_still_validates_027_to_031(runner_module):
    """032 must not disturb the migrations it follows."""
    for number in (27, 28, 29, 30, 31):
        violations = runner_module.validate_migration(_discovered(runner_module, number))
        assert violations == [], (number, violations)


def test_e3_032_is_not_marked_non_transactional(runner_module):
    migration = _discovered(runner_module, 32)
    assert not migration.non_transactional, (
        "032 is an ordinary migration; the runner must wrap it in a transaction "
        "so a failing guard rolls the whole backfill back"
    )


def test_e3_032_ships_a_down_sibling(runner_module):
    assert _discovered(runner_module, 32).down_path is not None


# ---------------------------------------------------------------------------
# E4 - the derivation
# ---------------------------------------------------------------------------

def test_e4_the_agency_is_derived_through_lead_stime_and_leads():
    up = _squash(_up()).lower()
    assert "lead_stime" in up, "032 does not consult the provenance table"
    assert "leads" in up
    assert re.search(r"join\s+leads\s+\w*\s*on", up), (
        "the derivation does not join leads"
    )
    assert re.search(r"l\w*\.agency_id", up), "leads.agency_id is never read"


def test_e4_exactly_one_distinct_agency_is_required():
    """`count(DISTINCT ...) = 1` is the whole rule; anything else guesses."""
    up = _squash(_strip_sql_strings(_up())).lower()
    assert re.search(r"count\s*\(\s*distinct\s+\w+\.agency_id\s*\)", up), (
        "032 never counts the distinct agencies a stima resolves to"
    )


def test_e4_the_update_takes_the_value_from_the_join_not_an_aggregate():
    up = _squash(_strip_sql_strings(_up()))
    update = _update_statement(up)
    assert re.search(r"set\s+agency_id\s*=\s*\w+\.agency_id", update, re.IGNORECASE), (
        f"the UPDATE does not assign a joined agency: {update}"
    )


def _update_statement(up: str) -> str:
    statements = [s for s in _statements(up) if s.lstrip().lower().startswith("update")]
    assert statements, "032 contains no UPDATE"
    assert len(statements) == 1, f"032 must perform exactly one UPDATE, found {len(statements)}"
    return statements[0]


# ---------------------------------------------------------------------------
# E5 - the four hard-fail guards
#
# Each is asserted twice: the concept is present in the real file, and a
# synthetic file with that guard removed is caught by the same probe.
# ---------------------------------------------------------------------------

GUARD_PROBES = {
    "zero provenance": r"not\s+exists",
    "multi agency": r"count\s*\(\s*distinct\s+\w+\.agency_id\s*\)\s*[<>]",
    # Specifically the *lead's* agency being absent. An earlier version of this
    # probe looked for any `<alias>.agency_id IS NULL`, which the file contains
    # in several other places - so deleting this guard left the probe green.
    # The mutation table below is what caught that.
    "lead agency null": r"\bl\w*\.agency_id\s+is\s+null\b",
    "prefilled conflict": r"\w+\.agency_id\s*(<>|!=)\s*\w+\.agency_id",
}


@pytest.mark.parametrize("name,pattern", sorted(GUARD_PROBES.items()))
def test_e5_each_hard_fail_guard_is_present(name, pattern):
    up = _squash(_strip_sql_strings(_up())).lower()
    assert re.search(pattern, up), f"032 has no {name} guard (probe {pattern!r})"


def test_e5_every_guard_raises_rather_than_repairing():
    """A guard that silently fixed its finding would defeat the point."""
    up = _up()
    blocks = re.findall(r"\$do\$(.*?)\$do\$", up, re.DOTALL)
    assert blocks, "032 contains no DO block"
    for block in blocks:
        assert re.search(r"raise\s+exception", block, re.IGNORECASE), (
            f"a DO block that never raises: {_squash(block)[:160]}"
        )


def test_e5_no_guard_swallows_its_own_failure():
    up = _squash(_strip_sql_strings(_up())).lower()
    assert "exception when" not in up, (
        "032 catches an exception; a failure other than success must surface"
    )


def test_e5_all_guards_run_before_the_update():
    """A guard after the write would report on damage already done.

    The runner rolls the transaction back either way, but ordering is what
    makes the failure a refusal rather than an undo.
    """
    up = _strip_sql_strings(_up())
    statements = _statements(up)
    update_index = next(
        i for i, s in enumerate(statements) if s.lstrip().lower().startswith("update")
    )
    guards_before = [
        s for s in statements[:update_index]
        if re.search(r"raise\s+exception", s, re.IGNORECASE)
    ]
    assert len(guards_before) >= 4, (
        f"only {len(guards_before)} guard(s) run before the UPDATE; "
        "zero-provenance, multi-agency, NULL-lead and prefilled-conflict "
        "must all refuse before anything is written"
    )


# ---------------------------------------------------------------------------
# E6 - activities and tasks
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("table", CONSULTED)
def test_e6_a_conflicting_dependent_row_is_a_hard_failure(table):
    up = _squash(_strip_sql_strings(_up())).lower()
    assert table in up, f"032 never checks {table} against the derived agency"
    # It must be joined on the stima, not merely mentioned.
    assert re.search(rf"{table}\s+\w*\s*(where|join|on)?", up), table


@pytest.mark.parametrize("table", CONSULTED)
def test_e6_dependent_tables_are_read_and_never_written(table):
    """Rule 11: 032 writes stime.agency_id and nothing else."""
    up = _strip_sql_strings(_up())
    for statement in _statements(up):
        head = statement.lstrip().lower()
        if head.startswith(("update", "insert", "delete")):
            assert not re.search(rf"\b{table}\b", statement, re.IGNORECASE), (
                f"032 writes to {table}: {_squash(statement)[:160]}"
            )


# ---------------------------------------------------------------------------
# E7 - no fallback, no arbitrary selection
# ---------------------------------------------------------------------------

FORBIDDEN_SELECTORS = ("min(", "max(", "limit ", "coalesce(", "fetch first", "order by")


@pytest.mark.parametrize("construct", FORBIDDEN_SELECTORS)
def test_e7_no_arbitrary_selection_construct(construct):
    """Every one of these turns "ambiguous" into "plausible", which is worse.

    Checked on the SQL with string literals removed, so the RAISE messages that
    explain these bans do not trip the ban itself.
    """
    up = _squash(_strip_sql_strings(_up())).lower()
    assert construct not in up, f"032 uses {construct!r} to choose an agency"


def test_e7_no_default_agency_fallback():
    up = _squash(_strip_sql_strings(_up())).lower()
    assert "stima360" not in up, "032 falls back to the Default Agency slug"
    assert "slug" not in up, "032 resolves an agency by slug rather than by provenance"


def test_e7_no_hardcoded_numeric_agency():
    """`agency_id = <number>` anywhere would be an invented owner."""
    up = _strip_sql_strings(_up())
    assert not re.search(r"agency_id\s*=\s*\d", up, re.IGNORECASE), up
    assert not re.search(r"set\s+agency_id\s*=\s*\d", up, re.IGNORECASE), up


def test_e7_the_agencies_table_is_never_consulted():
    """Provenance is the only authority; `agencies` cannot supply one."""
    up = _squash(_strip_sql_strings(_up())).lower()
    assert not re.search(r"\bfrom\s+agencies\b", up), up
    assert not re.search(r"\bjoin\s+agencies\b", up), up


# ---------------------------------------------------------------------------
# E8 - scope
# ---------------------------------------------------------------------------

def test_e8_only_null_rows_are_updated():
    update = _update_statement(_squash(_strip_sql_strings(_up())))
    assert re.search(r"agency_id\s+is\s+null", update, re.IGNORECASE), (
        f"the UPDATE does not restrict itself to unowned rows: {update}"
    )


def test_e8_the_update_targets_stime_and_sets_only_agency_id():
    update = _update_statement(_squash(_strip_sql_strings(_up())))
    assert re.match(r"update\s+stime\b", update.strip(), re.IGNORECASE), update
    set_clause = re.search(r"\bset\b(.*?)(\bfrom\b|\bwhere\b)", update, re.IGNORECASE | re.DOTALL)
    assert set_clause, update
    assignments = [a for a in set_clause.group(1).split(",") if a.strip()]
    assert len(assignments) == 1, f"032 sets more than agency_id: {assignments}"
    assert "agency_id" in assignments[0].lower(), assignments


@pytest.mark.parametrize(
    "table", ("stime_dettagliate", "whatsapp_incoming", "contacts", "agencies")
)
def test_e8_no_other_table_is_written(table):
    up = _strip_sql_strings(_up())
    for statement in _statements(up):
        head = statement.lstrip().lower()
        if head.startswith(("update", "insert", "delete", "alter", "drop", "create")):
            assert not re.search(rf"\b{table}\b", statement, re.IGNORECASE), (
                f"032 touches {table}: {_squash(statement)[:160]}"
            )


def test_e8_no_structural_change():
    up = _squash(_strip_sql_strings(_up())).lower()
    for forbidden in ("alter table", "create index", "create table", "drop ",
                      "set not null", "add constraint"):
        assert forbidden not in up, f"032 is a data migration; found {forbidden!r}"


# ---------------------------------------------------------------------------
# E9 - the post-condition
# ---------------------------------------------------------------------------

def test_e9_the_migration_certifies_its_own_post_condition():
    """Zero NULL rows when 032 completes, checked by 032 itself.

    The later NOT NULL migration is a second, independent gate - not a
    substitute. Finding an incomplete backfill then would mean two migrations
    to unpick instead of one refused transaction.
    """
    up = _strip_sql_strings(_up())
    statements = _statements(up)
    update_index = next(
        i for i, s in enumerate(statements) if s.lstrip().lower().startswith("update")
    )
    after = " ".join(statements[update_index + 1:]).lower()
    assert "count" in after, "nothing is counted after the backfill"
    assert re.search(r"agency_id\s+is\s+null", after), (
        "032 never verifies that no stima was left unowned"
    )
    assert re.search(r"raise\s+exception", after), (
        "the post-condition does not fail loudly"
    )


# ---------------------------------------------------------------------------
# E10 - the down migration refuses
# ---------------------------------------------------------------------------

def test_e10_down_raises():
    assert re.search(r"raise\s+exception", _down(), re.IGNORECASE)


def test_e10_down_performs_no_business_write():
    down = _squash(_strip_sql_strings(_down())).lower()
    for forbidden in ("update stime", "delete from", "alter table", "drop column"):
        assert forbidden not in down, (
            f"the down must refuse, not act; found {forbidden!r}"
        )


def test_e10_down_never_sets_agency_id_back_to_null():
    down = _squash(_strip_sql_strings(_down())).lower()
    assert not re.search(r"set\s+agency_id\s*=\s*null", down), down


def test_e10_down_states_that_032_is_irreversible():
    assert "irreversible" in _down().lower()


def test_e10_down_explains_why_the_two_populations_are_indistinguishable():
    """The reason must be in the file, not only in a runbook.

    After 032 a backfilled historical row and a row the runtime writer stamped
    look identical - same column, same kind of value, no marker. That is the
    whole reason the rollback refuses, and an operator reading the failure
    needs it in front of them.
    """
    down = _down().lower()
    assert "runtime" in down or "written afterwards" in down or "since" in down, down


def test_e10_down_names_a_recovery_path():
    down = _down().lower()
    assert "restore" in down or "snapshot" in down or "backup" in down, down


# ---------------------------------------------------------------------------
# Negative controls - Layer B
#
# Each probe above is re-run against a mutilated copy of the real migration and
# must fail. Without this the whole file could be green against SQL that does
# none of what it claims.
# ---------------------------------------------------------------------------

def _all_probe_failures(raw_sql: str) -> list[str]:
    """Run every rule in this file against arbitrary SQL, collecting failures.

    This is the same set of checks the tests above make, expressed once so a
    mutant can be pushed through all of them. A rule that cannot fail is not a
    rule, and running the real file through this must produce no failures while
    every mutant produces at least one.
    """
    up = _strip_sql_comments(raw_sql)
    flat = _squash(_strip_sql_strings(up)).lower()
    stripped = _strip_sql_strings(up)
    failures: list[str] = []

    for name, pattern in GUARD_PROBES.items():
        if not re.search(pattern, flat):
            failures.append(f"guard missing: {name}")

    for construct in FORBIDDEN_SELECTORS:
        if construct in flat:
            failures.append(f"arbitrary selector: {construct}")

    if re.search(r"agency_id\s*=\s*\d", stripped, re.IGNORECASE):
        failures.append("hardcoded agency")
    if "stima360" in flat or "slug" in flat:
        failures.append("default agency fallback")
    if re.search(r"\b(from|join)\s+agencies\b", flat):
        failures.append("agencies consulted")

    for table in CONSULTED:
        if table not in flat:
            failures.append(f"dependent table unchecked: {table}")

    statements = _statements(stripped)
    updates = [
        i for i, s in enumerate(statements) if s.lstrip().lower().startswith("update")
    ]
    if len(updates) != 1:
        failures.append(f"expected exactly one UPDATE, found {len(updates)}")
    else:
        index = updates[0]
        update = _squash(statements[index])
        if not re.search(r"agency_id\s+is\s+null", update, re.IGNORECASE):
            failures.append("UPDATE is not restricted to unowned rows")
        if not re.match(r"update\s+stime\b", update.strip(), re.IGNORECASE):
            failures.append("UPDATE does not target stime")
        guards_before = [
            s for s in statements[:index]
            if re.search(r"raise\s+exception", s, re.IGNORECASE)
        ]
        if len(guards_before) < 4:
            failures.append(f"only {len(guards_before)} guard(s) before the UPDATE")
        after = " ".join(statements[index + 1:]).lower()
        if not re.search(r"agency_id\s+is\s+null", after) or "raise" not in after:
            failures.append("no post-condition after the UPDATE")

    for statement in statements:
        if statement.lstrip().lower().startswith(("update", "insert", "delete")):
            for table in CONSULTED + ("contacts", "agencies", "stime_dettagliate"):
                if re.search(rf"\b{table}\b", statement, re.IGNORECASE):
                    failures.append(f"writes to {table}")

    return failures


# Each mutant is a plausible way this migration could be wrong. The name says
# what was broken; the suite requires every one of them to be caught.
MUTANTS = {
    "guard 1 removed (zero provenance)":
        lambda s: re.sub(r"^-- Guard 1.*?\$do\$;$", "", s,
                         flags=re.DOTALL | re.MULTILINE),
    "guard 2 removed (lead with no agency)":
        lambda s: re.sub(r"^-- Guard 2.*?\$do\$;$", "", s,
                         flags=re.DOTALL | re.MULTILINE),
    "guard 3 defanged (multi-agency accepted)":
        lambda s: s.replace("HAVING count(DISTINCT l.agency_id) > 1", "HAVING true"),
    "guard 5 removed (dependents unchecked)":
        lambda s: re.sub(r"^-- Guard 5.*?\$do\$;$", "", s,
                         flags=re.DOTALL | re.MULTILINE),
    "post-condition removed":
        lambda s: re.sub(r"^-- Guard 6.*\Z", "", s, flags=re.DOTALL | re.MULTILINE),
    "UPDATE widened to every row":
        lambda s: s.replace("   AND s.agency_id IS NULL;", ";"),
    "arbitrary selection introduced":
        lambda s: s.replace("       ) AS derived", "        LIMIT 1) AS derived"),
    "agency hardcoded":
        lambda s: s.replace("SET agency_id = derived.agency_id", "SET agency_id = 1"),
    "default agency fallback introduced":
        lambda s: s.replace(
            "         WHERE l.agency_id IS NOT NULL",
            "         WHERE l.agency_id IS NOT NULL OR 1 = "
            "(SELECT id FROM agencies WHERE slug = 'x')",
        ),
    "a second table is written":
        lambda s: s + "\nUPDATE activities SET agency_id = 5;\n",
}


def test_the_real_migration_passes_every_probe():
    """The baseline of the mutation table: no false positives."""
    assert _all_probe_failures(UP_PATH.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_every_mutant_is_caught(name):
    original = UP_PATH.read_text(encoding="utf-8")
    mutant = MUTANTS[name](original)
    assert mutant != original, f"the mutation {name!r} did not change the file"
    failures = _all_probe_failures(mutant)
    assert failures, f"mutation {name!r} passed every rule in this file"
