"""P26-2B2D - static checks for migration 033_p26_stima_agency_enforce.

Offline, stdlib only. Nothing here opens a database or applies a migration.

033 closes STIMA ownership structurally:

    031  nullable column        (applied)
    032  controlled backfill    (applied)
    033  NOT NULL + integrity   <- this file

Three things happen, and they belong together because each is only safe once
the others hold:

    1. stime.agency_id becomes NOT NULL
    2. core_agency_integrity() resolves a stima *directly*, from
       stime.agency_id, instead of guessing through its links
    3. lead_stime gains a trigger that refuses to link a lead and a stima
       belonging to different agencies

WHAT 033 DELETES, AND WHY THAT IS THE POINT

Migration 030 could not read stime.agency_id, because the column did not exist.
So it resolved a stima's agency the only way available:

    SELECT l.agency_id FROM lead_stime ls JOIN leads l ON l.id = ls.lead_id
     WHERE ls.stima_id = NEW.stima_id ORDER BY ls.id LIMIT 1

`ORDER BY ls.id LIMIT 1` is a *choice among candidates*. lead_stime's unique key
is (lead_id, stima_id), so a stima can carry several links; if two pointed at
different agencies, that query returned the oldest one and the trigger accepted
it. It could not do better at the time - but it means the row's tenant was
decided by insertion order.

030 also carried a bounded Default Agency fallback for a stima-only shape, for
the same reason: nothing else could place such a row.

Both disappear here. A stima now *is* its agency, so:

    SELECT agency_id INTO a_stima FROM stime WHERE id = NEW.stima_id;

is total, exact and unordered. There is nothing left to choose between and
nothing left to fall back to, and this file asserts that neither construct
survives - the absences are the deliverable, not a side effect.

030 IS NOT MODIFIED. Forward-only: 033 replaces the function body, and its down
file restores 030's. tests/test_p26_1_agency_integrity.py still certifies 030's
own text and stays green untouched; the post-033 decision table is owned here.

Coverage map:

    H1  shared P26 rules, transaction ownership, runner
    H2  stime.agency_id SET NOT NULL
    H3  the new resolution - direct, and the old one gone
    H4  the decision table after 033 (executable reference)
    H5  the lead_stime coherence trigger
    H6  lead_stime stays CHILD-DERIVED - no physical column
    H7  no arbitrary selection, no default fallback, anywhere
    H8  scope - no runtime, auth or admin change; no 034
    H9  the down migration is genuinely reversible
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

VERSION = "033_p26_stima_agency_enforce"
UP_PATH = MIGRATIONS / f"{VERSION}.sql"
DOWN_PATH = MIGRATIONS / f"{VERSION}_down.sql"
UP_030 = MIGRATIONS / "030_p26_core_agency_enforce.sql"

INTEGRITY_FN = "core_agency_integrity"
LINK_FN = "lead_stime_agency_coherence"
LINK_TRIGGER = "trg_lead_stime_agency_coherence"

AGENCY_A = 10
AGENCY_B = 20


@pytest.fixture(scope="module")
def runner_module():
    return _load(RUNNER, "p26_migrate_033")


def _up() -> str:
    return _strip_sql_comments(UP_PATH.read_text(encoding="utf-8"))


def _down() -> str:
    return _strip_sql_comments(DOWN_PATH.read_text(encoding="utf-8"))


def _squash(sql: str) -> str:
    return " ".join(sql.split())


def _strip_sql_strings(sql: str) -> str:
    """Drop quoted literals so prose in RAISE messages cannot trip code rules.

    This file bans the word `stima360` and the construct `LIMIT 1`, and its own
    error messages explain those bans. Without this, the explanation would fail
    the rule it explains - a trap this project has hit repeatedly.
    """
    return re.sub(r"'(?:[^']|'')*'", " ", sql)


def _function_body(sql: str, name: str) -> str:
    """The body of one CREATE [OR REPLACE] FUNCTION, dollar-quote to dollar-quote."""
    start = sql.index(f"FUNCTION {name}")
    marker = re.search(r"\$[A-Za-z_]+\$", sql[start:])
    assert marker, f"{name} has no dollar-quoted body"
    open_at = start + marker.start()
    tag = marker.group(0)
    close_at = sql.index(tag, open_at + len(tag))
    return sql[open_at:close_at]


def _new_integrity_body() -> str:
    return _function_body(_squash(_up()), INTEGRITY_FN)


def _link_body() -> str:
    return _function_body(_squash(_up()), LINK_FN)


# ---------------------------------------------------------------------------
# H1 - rules, transaction ownership, runner
# ---------------------------------------------------------------------------

def test_h1_033_obeys_the_shared_rules_and_is_reversible():
    assert_p26_migration_rules(VERSION, expect_reversible=True)


def test_h1_up_does_not_bracket_its_own_transaction():
    up = UP_PATH.read_text(encoding="utf-8")
    assert not re.search(r"^\s*BEGIN\s*;", up, re.IGNORECASE | re.MULTILINE)
    assert not re.search(r"^\s*COMMIT\s*;", up, re.IGNORECASE | re.MULTILINE)


def test_h1_down_brackets_its_own_transaction():
    down = DOWN_PATH.read_text(encoding="utf-8")
    assert re.search(r"^\s*BEGIN\s*;", down, re.IGNORECASE | re.MULTILINE)
    assert re.search(r"^\s*COMMIT\s*;", down, re.IGNORECASE | re.MULTILINE)


def _discovered(runner_module, number: int):
    return next(
        m for m in runner_module.discover_migrations(MIGRATIONS) if m.number == number
    )


def test_h1_the_runner_discovers_a_contiguous_026_to_033(runner_module):
    present = sorted(m.number for m in runner_module.discover_migrations(MIGRATIONS))
    assert present[:8] == [26, 27, 28, 29, 30, 31, 32, 33], present
    for later in present[8:]:
        assert later > 33, present


def test_h1_the_runner_validates_033(runner_module):
    assert runner_module.validate_migration(_discovered(runner_module, 33)) == []


def test_h1_the_runner_still_validates_027_to_032(runner_module):
    for number in (27, 28, 29, 30, 31, 32):
        violations = runner_module.validate_migration(_discovered(runner_module, number))
        assert violations == [], (number, violations)


def test_h1_033_is_transactional(runner_module):
    assert not _discovered(runner_module, 33).non_transactional


# ---------------------------------------------------------------------------
# H2 - NOT NULL
# ---------------------------------------------------------------------------

def test_h2_stime_agency_id_becomes_not_null():
    up = _squash(_strip_sql_strings(_up()))
    assert re.search(
        r"ALTER TABLE stime\s+ALTER COLUMN agency_id\s+SET NOT NULL",
        up, re.IGNORECASE,
    ), up


def test_h2_no_other_column_is_made_not_null():
    up = _squash(_strip_sql_strings(_up()))
    statements = re.findall(r"ALTER COLUMN\s+(\w+)\s+SET NOT NULL", up, re.IGNORECASE)
    assert statements == ["agency_id"], statements


def test_h2_no_default_is_introduced():
    """A DEFAULT would let a future unowned insert look correct."""
    up = _squash(_strip_sql_strings(_up())).lower()
    assert "set default" not in up, up


# ---------------------------------------------------------------------------
# H3 - the new resolution, and the removal of the old one
# ---------------------------------------------------------------------------

def test_h3_the_stima_agency_is_read_directly_from_stime():
    body = _strip_sql_strings(_new_integrity_body())
    assert re.search(
        r"SELECT agency_id INTO a_stima\s+FROM stime\s+WHERE id = NEW\.stima_id",
        body, re.IGNORECASE,
    ), body


def test_h3_the_link_walk_is_gone():
    """The old resolution went through lead_stime. It must not survive."""
    body = _strip_sql_strings(_new_integrity_body()).lower()
    assert "lead_stime" not in body, body


def test_h3_no_ordering_or_row_limit_chooses_the_agency():
    body = _strip_sql_strings(_new_integrity_body()).lower()
    for construct in ("order by", "limit 1", "limit ", "fetch first"):
        assert construct not in body, f"{construct!r} still chooses among candidates"


def test_h3_the_other_two_references_are_still_resolved_the_same_way():
    body = _strip_sql_strings(_new_integrity_body())
    assert "SELECT agency_id INTO a_lead FROM leads WHERE id = NEW.lead_id" in body
    assert "SELECT agency_id INTO a_contact FROM contacts WHERE id = NEW.contact_id" in body


def test_h3_an_unresolvable_stima_is_a_hard_failure():
    """FK plus NOT NULL make this unreachable - which is why it must raise.

    A branch that cannot fire costs nothing; a missing branch that silently
    yields NULL would place the row by accident.
    """
    body = _new_integrity_body()
    stima_part = body[body.index("a_stima"):]
    assert re.search(r"a_stima IS NULL", stima_part), body
    assert "RAISE EXCEPTION" in stima_part


# ---------------------------------------------------------------------------
# H4 - the decision table after 033
#
# An executable restatement, in the same order as the SQL, mirroring the one
# tests/test_p26_1_agency_integrity.py keeps for 030. The SQL assertions above
# are what tie it to the migration; on its own it would only test itself.
# ---------------------------------------------------------------------------

class IntegrityRejection(Exception):
    """Stands in for the trigger's RAISE EXCEPTION."""


def resolve_agency(
    *,
    lead_agency=None,
    contact_agency=None,
    stima_agency=None,
    explicit_agency=None,
    lead_id=None,
    contact_id=None,
    stima_id=None,
):
    """Return the agency_id the post-033 trigger would write, or raise.

    Note what is absent compared with the 030 version: `default_agency`. There
    is no parameter for it because there is no branch that could use one.
    """
    # A. a referenced stima must resolve. After 033 it always can.
    if stima_id is not None and stima_agency is None:
        raise IntegrityRejection("the stima carries no agency")

    # B. every resolved pair must agree.
    for first, second in (
        (lead_agency, contact_agency),
        (lead_agency, stima_agency),
        (contact_agency, stima_agency),
    ):
        if first is not None and second is not None and first != second:
            raise IntegrityRejection("references span two agencies")

    resolved = next(
        (v for v in (lead_agency, contact_agency, stima_agency) if v is not None),
        None,
    )

    # C. an explicit agency_id is validated, never trusted.
    if explicit_agency is not None:
        if resolved is not None and explicit_agency != resolved:
            raise IntegrityRejection("explicit agency_id contradicts the references")
        return explicit_agency

    # D. derive.
    if resolved is not None:
        return resolved

    # E. unresolvable, and must not be guessed. (030's Default Agency fallback
    #    stood here and is deliberately gone.)
    raise IntegrityRejection("agency_id could not be resolved")


def test_h4_a_stima_only_activity_derives_from_the_stima():
    assert resolve_agency(stima_id=7, stima_agency=AGENCY_A) == AGENCY_A


def test_h4_a_stima_only_task_derives_from_the_stima():
    assert resolve_agency(stima_id=7, stima_agency=AGENCY_B) == AGENCY_B


def test_h4_a_stima_only_row_no_longer_reaches_a_default_agency():
    """The 030 behaviour this replaces: stima-only used to be parked."""
    with pytest.raises(IntegrityRejection):
        resolve_agency(stima_id=7, stima_agency=None)


def test_h4_lead_only_and_contact_only_are_unchanged():
    assert resolve_agency(lead_id=1, lead_agency=AGENCY_A) == AGENCY_A
    assert resolve_agency(contact_id=1, contact_agency=AGENCY_B) == AGENCY_B


def test_h4_coherent_references_are_accepted():
    assert resolve_agency(
        lead_id=1, contact_id=2, stima_id=3,
        lead_agency=AGENCY_A, contact_agency=AGENCY_A, stima_agency=AGENCY_A,
    ) == AGENCY_A


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(lead_id=1, contact_id=2, lead_agency=AGENCY_A, contact_agency=AGENCY_B),
        dict(lead_id=1, stima_id=3, lead_agency=AGENCY_A, stima_agency=AGENCY_B),
        dict(contact_id=2, stima_id=3, contact_agency=AGENCY_A, stima_agency=AGENCY_B),
    ],
)
def test_h4_any_pair_spanning_two_agencies_is_rejected(kwargs):
    with pytest.raises(IntegrityRejection):
        resolve_agency(**kwargs)


def test_h4_an_explicit_agency_that_contradicts_is_rejected():
    with pytest.raises(IntegrityRejection):
        resolve_agency(stima_id=3, stima_agency=AGENCY_A, explicit_agency=AGENCY_B)


def test_h4_a_coherent_explicit_agency_is_preserved():
    assert resolve_agency(
        stima_id=3, stima_agency=AGENCY_A, explicit_agency=AGENCY_A
    ) == AGENCY_A


def test_h4_an_unresolvable_row_is_rejected_not_defaulted():
    with pytest.raises(IntegrityRejection):
        resolve_agency()


def test_h4_the_reference_table_matches_the_sql_branch_order():
    body = _strip_sql_strings(_new_integrity_body())
    pair_check = body.index("a_lead IS NOT NULL AND a_contact IS NOT NULL")
    explicit = body.index("IF NEW.agency_id IS NOT NULL THEN")
    derive = body.index("NEW.agency_id := resolved")
    assert pair_check < explicit < derive, "the SQL branches are out of order"


def test_h4_the_resolved_value_is_the_first_non_null_reference():
    assert "resolved := COALESCE(a_lead, a_contact, a_stima)" in _new_integrity_body()


# ---------------------------------------------------------------------------
# H5 - the lead_stime coherence trigger
# ---------------------------------------------------------------------------

def link_is_accepted(*, lead_agency, stima_agency):
    """Executable restatement of the lead_stime trigger."""
    if lead_agency is None:
        raise IntegrityRejection("the lead carries no agency")
    if stima_agency is None:
        raise IntegrityRejection("the stima carries no agency")
    if lead_agency != stima_agency:
        raise IntegrityRejection("a link may not span two agencies")
    return True


def test_h5_a_link_within_one_agency_is_accepted():
    assert link_is_accepted(lead_agency=AGENCY_A, stima_agency=AGENCY_A)


def test_h5_a_lead_in_a_and_a_stima_in_b_is_rejected():
    with pytest.raises(IntegrityRejection):
        link_is_accepted(lead_agency=AGENCY_A, stima_agency=AGENCY_B)


@pytest.mark.parametrize(
    "lead_agency,stima_agency",
    [(None, AGENCY_A), (AGENCY_A, None), (None, None)],
)
def test_h5_an_unresolvable_side_is_rejected(lead_agency, stima_agency):
    with pytest.raises(IntegrityRejection):
        link_is_accepted(lead_agency=lead_agency, stima_agency=stima_agency)


def test_h5_the_function_reads_both_sides():
    body = _strip_sql_strings(_link_body())
    assert re.search(
        r"SELECT agency_id INTO a_lead\s+FROM leads\s+WHERE id = NEW\.lead_id",
        body, re.IGNORECASE,
    ), body
    assert re.search(
        r"SELECT agency_id INTO a_stima\s+FROM stime\s+WHERE id = NEW\.stima_id",
        body, re.IGNORECASE,
    ), body


def test_h5_the_function_raises_on_every_rejection():
    body = _link_body()
    assert body.count("RAISE EXCEPTION") >= 3, (
        "the link trigger must refuse an unresolvable lead, an unresolvable "
        "stima and a cross-agency pair"
    )
    assert re.search(r"a_lead\s+IS NULL", body), body
    assert re.search(r"a_stima\s+IS NULL", body), body
    assert re.search(r"a_lead\s*<>\s*a_stima", body), body


def test_h5_the_trigger_fires_on_insert_and_on_repointing():
    """An UPDATE that repoints a link must be revalidated, or the guard is
    an insert-time formality."""
    up = _squash(_up())
    block = up[up.index(f"CREATE TRIGGER {LINK_TRIGGER}"):]
    header = block[: block.index("EXECUTE FUNCTION")]
    assert "BEFORE INSERT OR UPDATE OF" in header, header
    for column in ("lead_id", "stima_id"):
        assert column in header, (column, header)
    assert re.search(r"ON\s+lead_stime", header, re.IGNORECASE), header
    assert "FOR EACH ROW" in header, header


def test_h5_the_trigger_creation_is_idempotent():
    """PostgreSQL has no CREATE TRIGGER IF NOT EXISTS; 026's pattern is used."""
    up = _squash(_up())
    assert "pg_trigger" in up, "the trigger is created without consulting the catalogue"
    assert "tgisinternal" in up, (
        "an internal constraint trigger could produce a false positive"
    )


# ---------------------------------------------------------------------------
# H6 - lead_stime stays CHILD-DERIVED
# ---------------------------------------------------------------------------

def test_h6_lead_stime_gains_no_physical_agency_column():
    up = _squash(_strip_sql_strings(_up()))
    assert not re.search(
        r"ALTER TABLE\s+lead_stime\s+ADD COLUMN", up, re.IGNORECASE
    ), up
    assert not re.search(
        r"ALTER TABLE\s+lead_stime\s+ADD\s+", up, re.IGNORECASE
    ), up


@pytest.mark.parametrize("table", ("lead_stime", "stime_dettagliate"))
def test_h6_child_tables_keep_no_agency_of_their_own(table):
    up = _squash(_strip_sql_strings(_up()))
    assert not re.search(rf"{table}\s+ADD COLUMN\s+agency_id", up, re.IGNORECASE), up


def test_h6_033_writes_no_business_data():
    """033 is structure and enforcement. 032 owned the data."""
    up = _strip_sql_strings(_up())
    for statement in re.split(r";\s*(?![^$]*\$[a-z_]*\$)", up):
        head = statement.strip().lower()
        assert not head.startswith(("insert into", "delete from")), statement
        if head.startswith("update "):
            raise AssertionError(f"033 updates data: {_squash(statement)[:160]}")


# ---------------------------------------------------------------------------
# H7 - no arbitrary selection, no fallback
# ---------------------------------------------------------------------------

def test_h7_the_default_agency_slug_appears_nowhere():
    up = _squash(_strip_sql_strings(_up())).lower()
    assert "stima360" not in up, up
    assert "slug" not in up, up


def test_h7_the_agencies_table_is_never_consulted():
    up = _squash(_strip_sql_strings(_up())).lower()
    assert not re.search(r"\b(from|join)\s+agencies\b", up), up


@pytest.mark.parametrize(
    "construct", ("order by", "limit 1", "coalesce(a_lead, a_contact, a_stima, ")
)
def test_h7_no_arbitrary_selection_survives(construct):
    up = _squash(_strip_sql_strings(_up())).lower()
    assert construct not in up, f"033 still selects arbitrarily: {construct!r}"


def test_h7_no_numeric_agency_literal():
    up = _strip_sql_strings(_up())
    assert not re.search(r"agency_id\s*(:?=)\s*\d", up, re.IGNORECASE), up


# ---------------------------------------------------------------------------
# H8 - scope
# ---------------------------------------------------------------------------

def test_h8_030_is_not_modified():
    """Forward-only. 033 replaces the function; it does not rewrite history."""
    body = _function_body(" ".join(UP_030.read_text(encoding="utf-8").split()), INTEGRITY_FN)
    assert "lead_stime" in body, (
        "030 no longer contains the link walk - it must be evolved forward, "
        "not edited"
    )
    assert "ORDER BY ls.id" in body
    assert "LIMIT 1" in body


def test_h8_033_is_the_last_of_the_stima_slice():
    """Retargeted when 034 arrived.

    This asserted that no 034 existed at all - a scope check for the 2B2D
    block, which correctly fired the moment the PROPERTY slice began. What it
    protects is that 033 finishes STIMA ownership and does not reach into the
    next subject; whether a later migration exists is not its business.
    """
    up = _squash(_strip_sql_strings(_up())).lower()
    for later_subject in ("properties", "property_contacts", "property_visits"):
        assert later_subject not in up, f"033 reaches into {later_subject}"

    stima_slice = sorted(
        path.stem for path in MIGRATIONS.glob("03[123]_p26_stima*.sql")
        if not path.stem.endswith("_down")
    )
    assert stima_slice == [
        "031_p26_stima_agency_columns",
        "032_p26_stima_agency_backfill",
        "033_p26_stima_agency_enforce",
    ], stima_slice


def test_h8_no_runtime_auth_or_admin_file_is_referenced():
    up = _squash(_up()).lower()
    for forbidden in ("operator_sessions", "operator_users", "agency_memberships"):
        assert forbidden not in up, f"033 touches {forbidden}"


def test_h8_033_creates_no_index_and_no_foreign_key():
    up = _squash(_strip_sql_strings(_up())).lower()
    assert "create index" not in up, up
    assert "add constraint" not in up, up


# ---------------------------------------------------------------------------
# H9 - the down migration is genuinely reversible
# ---------------------------------------------------------------------------

def test_h9_down_drops_the_link_trigger_then_its_function():
    down = _squash(_strip_sql_strings(_down()))
    trigger_at = down.upper().index(f"DROP TRIGGER IF EXISTS {LINK_TRIGGER.upper()}")
    function_at = down.upper().index(f"DROP FUNCTION IF EXISTS {LINK_FN.upper()}")
    assert trigger_at < function_at, "the function is dropped before its trigger"


def test_h9_down_restores_the_030_function():
    """Not merely 'a' function - 030's, including the constructs 033 removed."""
    down_body = _function_body(_squash(_down()), INTEGRITY_FN)
    assert "lead_stime" in down_body, "the restored function is not 030's"
    assert "ORDER BY ls.id" in down_body
    assert "LIMIT 1" in down_body


def test_h9_the_restored_function_matches_030_branch_for_branch():
    """Compared against 030's actual text, so the two cannot drift apart.

    Both sides go through the same normalisation - comments and string
    literals removed, whitespace collapsed, case folded - so the comparison is
    about executable SQL and not about commentary or message wording.
    """
    original = _function_body(
        _squash(_strip_sql_comments(UP_030.read_text(encoding="utf-8"))), INTEGRITY_FN
    )
    restored = _function_body(_squash(_down()), INTEGRITY_FN)

    def normalise(body: str) -> str:
        return " ".join(_strip_sql_strings(body).split()).lower()

    assert normalise(restored) == normalise(original), (
        "the down file's core_agency_integrity is not 030's"
    )


def test_h9_the_restored_function_is_not_the_033_one():
    """The negative half: a rollback that kept 033's stricter body would be a
    different migration wearing this one's name."""
    restored = _squash(_strip_sql_strings(_function_body(_squash(_down()), INTEGRITY_FN))).lower()
    current = _squash(_strip_sql_strings(_new_integrity_body())).lower()
    assert restored != current, "the down restores 033's function, not 030's"
    assert "order by ls.id" in restored
    assert "order by" not in current


def test_h9_down_drops_not_null_on_stime_agency_id():
    down = _squash(_strip_sql_strings(_down()))
    assert re.search(
        r"ALTER TABLE stime\s+ALTER COLUMN agency_id\s+DROP NOT NULL",
        down, re.IGNORECASE,
    ), down


def test_h9_down_preserves_every_agency_value():
    down = _squash(_strip_sql_strings(_down())).lower()
    assert not re.search(r"set\s+agency_id\s*=\s*null", down), down
    assert "delete from" not in down, down
    assert "drop column" not in down, down
    assert "truncate" not in down, down


def test_h9_down_does_not_refuse():
    """Unlike 029 and 032, this one really can be undone: 033 wrote no data."""
    down = _down()
    fn_bodies = "".join(
        _function_body(_squash(_down()), name)
        for name in (INTEGRITY_FN,)
    )
    outside = _squash(down).replace(_squash(fn_bodies), "")
    assert "Rollback 033 refused" not in outside, outside


# ---------------------------------------------------------------------------
# Mutation table - Layer B
#
# Every rule above is re-run against deliberately broken variants. A rule that
# stays green on a mutant is not a rule.
# ---------------------------------------------------------------------------

def _all_probe_failures(raw_sql: str) -> list[str]:
    up = _strip_sql_comments(raw_sql)
    squashed = _squash(up)
    flat = _squash(_strip_sql_strings(up)).lower()
    failures: list[str] = []

    if not re.search(
        r"ALTER TABLE stime\s+ALTER COLUMN agency_id\s+SET NOT NULL",
        _squash(_strip_sql_strings(up)), re.IGNORECASE,
    ):
        failures.append("no SET NOT NULL on stime.agency_id")

    if "stima360" in flat or "slug" in flat:
        failures.append("default agency fallback")
    if re.search(r"\b(from|join)\s+agencies\b", flat):
        failures.append("agencies consulted")

    try:
        integrity = _strip_sql_strings(_function_body(squashed, INTEGRITY_FN)).lower()
    except (ValueError, AssertionError):
        failures.append("core_agency_integrity is not defined")
        integrity = ""
    if integrity:
        if not re.search(
            r"select agency_id into a_stima\s+from stime\s+where id = new\.stima_id",
            integrity,
        ):
            failures.append("the stima agency is not read from stime")
        if "lead_stime" in integrity:
            failures.append("the link walk survives in the integrity function")
        for construct in ("order by", "limit 1"):
            if construct in integrity:
                failures.append(f"arbitrary selection in the integrity function: {construct}")

    try:
        link = _strip_sql_strings(_function_body(squashed, LINK_FN)).lower()
    except (ValueError, AssertionError):
        failures.append("the lead_stime coherence function is not defined")
        link = ""
    if link:
        if not re.search(r"a_lead\s*<>\s*a_stima", link):
            failures.append("the link trigger does not compare the two agencies")
        if not re.search(r"a_lead\s+is null", link):
            failures.append("the link trigger accepts an unresolvable lead")
        if not re.search(r"a_stima\s+is null", link):
            failures.append("the link trigger accepts an unresolvable stima")

    if f"CREATE TRIGGER {LINK_TRIGGER}" not in squashed:
        failures.append("the lead_stime trigger is never created")
    else:
        header = squashed[squashed.index(f"CREATE TRIGGER {LINK_TRIGGER}"):]
        header = header[: header.index("EXECUTE FUNCTION")]
        if "BEFORE INSERT OR UPDATE OF" not in header:
            failures.append("the link trigger does not fire on repointing")

    if re.search(r"ALTER TABLE\s+lead_stime\s+ADD", _squash(_strip_sql_strings(up)), re.IGNORECASE):
        failures.append("lead_stime gained a physical column")

    return failures


MUTANTS = {
    "NOT NULL removed":
        lambda s: re.sub(
            r"ALTER TABLE stime\s+ALTER COLUMN agency_id\s+SET NOT NULL\s*;",
            "", s, flags=re.IGNORECASE,
        ),
    "old link walk restored":
        lambda s: s.replace(
            "SELECT agency_id INTO a_stima\n          FROM stime\n         WHERE id = NEW.stima_id;",
            "SELECT l.agency_id INTO a_stima FROM lead_stime ls "
            "JOIN leads l ON l.id = ls.lead_id WHERE ls.stima_id = NEW.stima_id "
            "ORDER BY ls.id LIMIT 1;",
        ),
    "default agency fallback reintroduced":
        lambda s: s.replace(
            "    -- E. Anything else is unresolvable and must not be guessed.",
            "    IF NEW.stima_id IS NOT NULL THEN\n"
            "        SELECT id INTO NEW.agency_id FROM agencies "
            "WHERE slug = 'stima360';\n        RETURN NEW;\n    END IF;\n",
        ),
    "link trigger comparison removed":
        lambda s: s.replace("IF a_lead <> a_stima THEN", "IF false THEN"),
    "link trigger dropped":
        lambda s: re.sub(
            r"CREATE TRIGGER trg_lead_stime_agency_coherence.*?;", "", s,
            flags=re.DOTALL,
        ),
    "link trigger becomes insert-only":
        lambda s: s.replace(
            "BEFORE INSERT OR UPDATE OF lead_id, stima_id", "BEFORE INSERT"
        ),
    "lead_stime gains a column":
        lambda s: s + "\nALTER TABLE lead_stime ADD COLUMN agency_id BIGINT;\n",
}


def test_the_real_migration_passes_every_probe():
    assert _all_probe_failures(UP_PATH.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_every_mutant_is_caught(name):
    original = UP_PATH.read_text(encoding="utf-8")
    mutant = MUTANTS[name](original)
    assert mutant != original, f"the mutation {name!r} did not change the file"
    assert _all_probe_failures(mutant), f"mutation {name!r} passed every rule"
