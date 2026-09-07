"""P26-1 Task 14 - the core_agency_integrity() decision table.

Offline. No plpgsql interpreter is available in this environment, so the task
is split the way the approved plan specifies:

* the **logic** is asserted against a Python reference implementation of the
  same decision table, written here from migration 030's source; and
* the **SQL** is asserted to implement that table branch for branch.

Neither half alone would be worth much. A reference implementation on its own
proves only that the author can restate their own intent; SQL text assertions
on their own degrade into grepping for keywords. Together they pin the shape of
the trigger and the meaning of each branch, and a future edit that changes one
without the other fails.

Why this matters more than a normal test: `activities` and `tasks` are written
by five P17-P25 modules that P26-1 leaves untouched (design spec R-4). For
those writers the trigger is the *only* thing placing the row in an agency, and
it is invisible from Python. Risk R-4 accepts that cost; these tests are the
control that makes it acceptable.

The trigger both **derives** an absent agency and **rejects** an incoherent
one. The rejection half closes threat T-22: a row whose references span two
agencies is corruption if it succeeds.

Coverage map (design spec section 15, items 58-69):

    F1  the decision table, case by case, against the reference
    F2  the SQL implements each branch of that table
    F3  the fallback is bounded by all three of its conditions
    F4  rejection paths exist and are RAISE, not a silent default
    F5  the trigger belongs to 030 and appears nowhere in 029
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"

UP_030 = MIGRATIONS / "030_p26_core_agency_enforce.sql"
UP_029 = MIGRATIONS / "029_p26_core_agency_backfill.sql"

DEFAULT_AGENCY_SLUG = "stima360"
DEFAULT_AGENCY_ID = 1

AGENCY_A = 10
AGENCY_B = 20


def _sql() -> str:
    return " ".join(UP_030.read_text(encoding="utf-8").split())


def _function_body() -> str:
    """Just the core_agency_integrity() body, squashed."""
    text = _sql()
    start = text.index("CREATE OR REPLACE FUNCTION core_agency_integrity")
    end = text.index("$fn$ LANGUAGE plpgsql", start)
    return text[start:end]


# ---------------------------------------------------------------------------
# The reference implementation.
#
# A faithful restatement of migration 030's branches, in the same order. It is
# the executable form of the decision table; the SQL assertions below check the
# migration still matches it.
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
    default_agency=DEFAULT_AGENCY_ID,
):
    """Return the agency_id the trigger would write, or raise.

    Mirrors 030 branch for branch: B pairwise agreement, C explicit validation,
    D derivation, E the bounded fallback, F refusal.
    """
    # B. every resolved pair must agree.
    for first, second in (
        (lead_agency, contact_agency),
        (lead_agency, stima_agency),
        (contact_agency, stima_agency),
    ):
        if first is not None and second is not None and first != second:
            raise IntegrityRejection("references span two agencies")

    resolved = next(
        (value for value in (lead_agency, contact_agency, stima_agency) if value is not None),
        None,
    )

    # C. an explicit agency_id is validated, never trusted - and never
    # overwritten once it agrees.
    if explicit_agency is not None:
        if resolved is not None and explicit_agency != resolved:
            raise IntegrityRejection("explicit agency_id contradicts the references")
        return explicit_agency

    # D. derive.
    if resolved is not None:
        return resolved

    # E. bounded fallback: all three conditions, or nothing.
    if stima_id is not None and lead_id is None and contact_id is None:
        if default_agency is None:
            raise IntegrityRejection("the Default Agency is missing or inactive")
        return default_agency

    # F. unresolvable, and must not be guessed.
    raise IntegrityRejection("agency_id could not be resolved")


# ---------------------------------------------------------------------------
# F1 - the decision table, case by case
# ---------------------------------------------------------------------------

def test_f1_lead_only_takes_the_leads_agency():
    assert resolve_agency(lead_agency=AGENCY_A, lead_id=5) == AGENCY_A


def test_f1_contact_only_takes_the_contacts_agency():
    assert resolve_agency(contact_agency=AGENCY_A, contact_id=5) == AGENCY_A


def test_f1_stima_with_a_link_takes_the_agency_behind_the_link():
    assert resolve_agency(stima_agency=AGENCY_B, stima_id=5) == AGENCY_B


def test_f1_coherent_multiple_references_are_accepted():
    assert resolve_agency(
        lead_agency=AGENCY_A, contact_agency=AGENCY_A, stima_agency=AGENCY_A,
        lead_id=1, contact_id=2, stima_id=3,
    ) == AGENCY_A


def test_f1_a_lead_and_contact_in_different_agencies_are_rejected():
    """Spec item 60 - the T-22 case."""
    with pytest.raises(IntegrityRejection):
        resolve_agency(lead_agency=AGENCY_A, contact_agency=AGENCY_B, lead_id=1, contact_id=2)


def test_f1_a_contact_and_stima_in_different_agencies_are_rejected():
    with pytest.raises(IntegrityRejection):
        resolve_agency(contact_agency=AGENCY_A, stima_agency=AGENCY_B, contact_id=1, stima_id=2)


def test_f1_a_lead_and_stima_in_different_agencies_are_rejected():
    with pytest.raises(IntegrityRejection):
        resolve_agency(lead_agency=AGENCY_A, stima_agency=AGENCY_B, lead_id=1, stima_id=2)


def test_f1_an_explicit_agency_contradicting_the_references_is_rejected():
    """Spec item 61: an explicit agency_id is validated, never trusted."""
    with pytest.raises(IntegrityRejection):
        resolve_agency(explicit_agency=AGENCY_A, lead_agency=AGENCY_B, lead_id=1)


def test_f1_a_coherent_explicit_agency_is_preserved():
    """Spec item 62: it agrees, so it survives untouched."""
    assert resolve_agency(explicit_agency=AGENCY_A, lead_agency=AGENCY_A, lead_id=1) == AGENCY_A


def test_f1_an_explicit_agency_with_no_references_is_preserved():
    """The operator write path: agency_id stamped from the scope, nothing to
    contradict it."""
    assert resolve_agency(explicit_agency=AGENCY_A) == AGENCY_A


def test_f1_a_stima_only_row_falls_back_to_the_default_agency():
    """The one real flow that reaches E: a follow-up task carrying a stima and
    nothing else, after the public bridge returned skipped or error."""
    assert resolve_agency(stima_id=99) == DEFAULT_AGENCY_ID


def test_f1_a_stima_only_row_is_rejected_when_the_default_agency_is_absent():
    with pytest.raises(IntegrityRejection):
        resolve_agency(stima_id=99, default_agency=None)


def test_f1_an_unresolvable_row_is_rejected_not_defaulted():
    """Spec item 69: a generic activity or task must never be parked."""
    with pytest.raises(IntegrityRejection):
        resolve_agency()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"contact_id": 7},                    # a reference that resolved to nothing
        {"lead_id": 7},
        {"lead_id": 7, "contact_id": 8},
        {"stima_id": 1, "contact_id": 2},     # stima present, but not alone
        {"stima_id": 1, "lead_id": 2},
    ],
)
def test_f1_the_fallback_does_not_catch_other_unresolvable_shapes(kwargs):
    """E is not a general safety net; only the exact three-condition shape."""
    with pytest.raises(IntegrityRejection):
        resolve_agency(**kwargs)


# ---------------------------------------------------------------------------
# F2 - the SQL implements that table
# ---------------------------------------------------------------------------

def test_f2_the_function_resolves_all_three_references():
    body = _function_body()
    assert "SELECT agency_id INTO a_lead FROM leads WHERE id = NEW.lead_id" in body
    assert "SELECT agency_id INTO a_contact FROM contacts WHERE id = NEW.contact_id" in body
    assert "FROM lead_stime ls JOIN leads l ON l.id = ls.lead_id" in body


def test_f2_every_pair_is_compared():
    body = _function_body()
    for left, right in (("a_lead", "a_contact"), ("a_lead", "a_stima"), ("a_contact", "a_stima")):
        pattern = rf"IF {left} IS NOT NULL AND {right} IS NOT NULL AND {left} <> {right} THEN"
        assert re.search(pattern, body), f"no comparison of {left} and {right}"


def test_f2_the_resolved_value_is_the_first_non_null_reference():
    assert "resolved := COALESCE(a_lead, a_contact, a_stima)" in _function_body()


def test_f2_an_explicit_agency_is_validated_then_returned_unchanged():
    body = _function_body()
    explicit = body[body.index("IF NEW.agency_id IS NOT NULL THEN"):]
    assert "resolved IS NOT NULL AND NEW.agency_id <> resolved" in explicit
    assert "RAISE" in explicit.split("RETURN NEW")[0]
    # It is returned, not overwritten.
    assert "NEW.agency_id := resolved" not in explicit.split("RETURN NEW")[0]


def test_f2_derivation_assigns_the_resolved_value():
    assert "NEW.agency_id := resolved" in _function_body()


def test_f2_the_trigger_fires_before_insert_and_on_the_reference_columns():
    sql = _sql()
    for table in ("activities", "tasks"):
        block = sql[sql.index(f"CREATE TRIGGER trg_{table}_agency_integrity"):]
        header = block[: block.index("EXECUTE FUNCTION")]
        assert "BEFORE INSERT OR UPDATE OF" in header, header
        # An UPDATE that re-points a reference must be re-validated.
        for column in ("agency_id", "contact_id", "lead_id", "stima_id"):
            assert column in header, (table, column, header)
        assert "FOR EACH ROW" in header


def test_f2_repointing_a_reference_is_re_validated():
    """Spec item 68: UPDATE ... SET lead_id = <B lead> on an A task."""
    sql = _sql()
    for table in ("activities", "tasks"):
        block = sql[sql.index(f"CREATE TRIGGER trg_{table}_agency_integrity"):]
        assert "UPDATE OF" in block[: block.index("EXECUTE FUNCTION")]
    # And the reference implementation rejects exactly that transition.
    with pytest.raises(IntegrityRejection):
        resolve_agency(explicit_agency=AGENCY_A, lead_agency=AGENCY_B, lead_id=1)


# ---------------------------------------------------------------------------
# F3 - the fallback is bounded
# ---------------------------------------------------------------------------

def test_f3_the_fallback_tests_all_three_conditions():
    """A two-condition version silently widens it into a dumping ground."""
    body = _function_body()
    # rindex, not index: branch A also opens with `IF NEW.stima_id IS NOT
    # NULL`, and slicing from the first occurrence would check the reference
    # resolution instead of the fallback guard.
    fallback = body[body.rindex("IF NEW.stima_id IS NOT NULL"):]
    guard = fallback[: fallback.index("THEN")]
    assert "NEW.stima_id IS NOT NULL" in guard, guard
    assert "NEW.lead_id IS NULL" in guard, guard
    assert "NEW.contact_id IS NULL" in guard, guard
    assert guard.count("AND") == 2, guard


def test_f3_the_fallback_resolves_the_default_agency_by_slug_and_status():
    fallback = _function_body()
    assert f"WHERE slug = '{DEFAULT_AGENCY_SLUG}' AND status = 'active'" in fallback


def test_f3_the_fallback_raises_when_the_default_agency_is_absent():
    body = _function_body()
    fallback = body[body.rindex("IF NEW.stima_id IS NOT NULL"):]
    assert "IF NEW.agency_id IS NULL THEN RAISE EXCEPTION" in fallback, fallback


def test_f3_no_numeric_agency_id_literal_appears_in_the_function():
    """The Default Agency is resolved, never hard-coded."""
    body = _function_body()
    assignments = re.findall(r"NEW\.agency_id\s*:=\s*(\w+)", body)
    assert assignments, "nothing assigns agency_id"
    for value in assignments:
        assert not value.isdigit(), f"hard-coded agency id {value}"


# ---------------------------------------------------------------------------
# F4 - rejection is loud, never a silent default
# ---------------------------------------------------------------------------

def test_f4_every_rejection_branch_raises():
    body = _function_body()
    # Three pairwise, one explicit-contradiction, one missing default, one
    # unresolvable: six RAISE EXCEPTIONs, and no branch that quietly returns.
    assert body.count("RAISE EXCEPTION") == 6, body.count("RAISE EXCEPTION")


def test_f4_the_function_never_returns_null():
    """Returning NULL from a BEFORE trigger silently drops the row."""
    body = _function_body()
    assert "RETURN NULL" not in body, body


def test_f4_the_final_branch_is_a_rejection_not_a_default():
    body = _function_body()
    tail = body[body.rindex("RETURN NEW"):]
    assert "RAISE EXCEPTION" in tail, tail
    assert "agency_id could not be resolved" in tail


def test_f4_a_rejection_message_names_the_table_not_a_person():
    body = _function_body()
    messages = re.findall(r"RAISE EXCEPTION\s+'([^']+)'", body)
    assert len(messages) == 6, messages
    for message in messages:
        assert "P26-1 agency integrity on %" in message, message
        for leak in ("email", "password", "token"):
            assert leak not in message.lower(), message


# ---------------------------------------------------------------------------
# F5 - staging: the trigger belongs to 030
# ---------------------------------------------------------------------------

def test_f5_the_trigger_is_defined_only_in_030():
    """P26-0 rule 9: 029 backfills, 030 enforces. A trigger in 029 would
    validate rows while they were still being corrected."""
    backfill = UP_029.read_text(encoding="utf-8")
    assert "core_agency_integrity" not in backfill
    assert "CREATE TRIGGER" not in backfill.upper()
    assert "core_agency_integrity" in _sql()


def test_f5_the_enforcement_migration_is_still_unapplied():
    """Task 14 asserts SQL text only; nothing here executes a migration."""
    ledger = ROOT / "migrations"
    assert (ledger / "030_p26_core_agency_enforce.sql").exists()
    assert (ledger / "030_p26_core_agency_enforce_down.sql").exists()
