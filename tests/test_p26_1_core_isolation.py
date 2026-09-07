"""P26-1 Task 11 - the CORE agency-isolation security property.

Offline. No database: `core_cursor` is replaced by a recording fake, and every
assertion is made against the `(sql, params)` pairs the repository actually
issued. That is the point of this file - it does not check that the repository
*looks* scoped, it checks that the statement which would have reached
PostgreSQL carries a bound agency predicate.

Layer B of the two-layer strategy in the approved plan section 11.1. Layer A
(the AST admission rules) lives in tests/test_p26_1_scope_enforcement.py.

Why a recording fake rather than a mock: the assertions are about exact SQL and
exact bound parameters. A mock accepts any call shape, including ones a real
cursor would reject, so it can only prove that a function was called - never
that the statement it built was safe.

Coverage map:

    D1  the harness itself - fail-closed registry and negative control
    D2  reads carry a bound agency predicate
    D3  creates derive agency from ctx, never from the payload
    D4  updates and deletes combine id + scope
    D5  child tables are reached only through a scoped parent
    D6  the agent narrowing, per table
    D7  platform-admin read/write asymmetry
    D8  SystemAgencyContext never widens
    D9  R-4: the cursor helpers stay ctx-less for flow/, followup/, owner/
    D10 the C2 legacy-Basic compatibility context
"""
from __future__ import annotations

import inspect
import re
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from core import repository
from core.exceptions import ConflictError, NotFoundError, ValidationError
from core.scope import ProgrammingError, scoped_predicate
from operator_auth.context import OperatorContext, SystemAgencyContext
from operator_auth.exceptions import PlatformAdminAgencyRequired

AGENCY = 10
OTHER_AGENCY = 20
AGENT_USER = 7
TARGET_AGENT = 55  # the operator an assignment targets (Task 12)

SCOPED_TABLES = ("contacts", "leads", "activities", "tasks")

# A statement "touches" a scoped table when it names one in a position that
# reads or writes it. Table names are used to *discover* which statements to
# police - never to forbid them. Plain, greppable SQL is the goal.
# USING is included deliberately: the child-table deletes join their scoped
# parent through it, and an auditor blind to USING would score those statements
# as "touching nothing" and pass them without looking.
TOUCHES_SCOPED = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|USING)\s+(?:ONLY\s+)?(contacts|leads|activities|tasks)\b",
    re.IGNORECASE,
)
INSERT_INTO_SCOPED = re.compile(
    r"\bINSERT\s+INTO\s+(contacts|leads|activities|tasks)\b", re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Contexts
# ---------------------------------------------------------------------------

def _ctx(**overrides) -> OperatorContext:
    values = {
        "user_id": 1,
        "agency_id": AGENCY,
        "role": "agency_owner",
        "is_platform_admin": False,
        "session_id": 100,
        "auth_channel": "operator_session",
    }
    values.update(overrides)
    return OperatorContext(**values)


def owner_ctx() -> OperatorContext:
    return _ctx()


def admin_ctx() -> OperatorContext:
    return _ctx(role="agency_admin")


def agent_ctx() -> OperatorContext:
    return _ctx(role="agent", user_id=AGENT_USER)


def bound_platform_admin_ctx() -> OperatorContext:
    return _ctx(role=None, is_platform_admin=True)


def unbound_platform_admin_ctx() -> OperatorContext:
    return _ctx(role=None, agency_id=None, is_platform_admin=True)


def legacy_basic_ctx() -> OperatorContext:
    """The C2 compatibility context: agency-bound, no operator, no session."""
    return _ctx(user_id=None, session_id=None, auth_channel="legacy_basic")


def system_ctx() -> SystemAgencyContext:
    return SystemAgencyContext(agency_id=AGENCY, origin="public_stima")


ALL_BOUND_CONTEXTS = {
    "agency_owner": owner_ctx,
    "agency_admin": admin_ctx,
    "agent": agent_ctx,
    "bound_platform_admin": bound_platform_admin_ctx,
    "legacy_basic": legacy_basic_ctx,
}


# ---------------------------------------------------------------------------
# The recording cursor
# ---------------------------------------------------------------------------

class Statement:
    __slots__ = ("sql", "params")

    def __init__(self, sql, params):
        self.sql = " ".join(str(sql).split())
        self.params = params

    @property
    def bound(self) -> list:
        if self.params is None:
            return []
        if isinstance(self.params, dict):
            return list(self.params.values())
        return list(self.params)

    def touches_scoped_table(self) -> bool:
        return bool(TOUCHES_SCOPED.search(self.sql))

    @property
    def scoped_tables(self) -> set[str]:
        return {m.lower() for m in TOUCHES_SCOPED.findall(self.sql)}

    def __repr__(self) -> str:  # pragma: no cover - failure output only
        return f"<{self.sql!r} params={self.params!r}>"


class RecordingCursor:
    """Records every statement and replays queued rows.

    `fetchone` yields a permissive default row when nothing is queued, so a
    function under test proceeds far enough to issue all of its statements.
    Tests that care about absence queue an explicit None.
    """

    DEFAULT_ROW = {
        "id": 1,
        "contact_id": 1,
        "lead_id": 1,
        "stima_id": 1,
        "agency_id": AGENCY,
        "status": "active",
        "archived_at": None,
        "display_name": "Fixture",
        "email_normalized": None,
        "phone_normalized": None,
    }

    def __init__(self, rows=None, rowsets=None, rowcount=1):
        self.calls: list[Statement] = []
        self._rows = list(rows) if rows is not None else None
        self._rowsets = list(rowsets) if rowsets is not None else []
        self.rowcount = rowcount

    def execute(self, sql, params=None):
        self.calls.append(Statement(sql, params))

    def fetchone(self):
        if self._rows is None:
            return dict(self.DEFAULT_ROW)
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        return self._rowsets.pop(0) if self._rowsets else []

    def close(self):
        pass

    # -- reading the recording -------------------------------------------
    @property
    def scoped_statements(self) -> list[Statement]:
        return [call for call in self.calls if call.touches_scoped_table()]

    def sql_text(self) -> str:
        return " | ".join(call.sql for call in self.calls)


@contextmanager
def _fake_core_cursor(cursor, *, commit: bool = False):
    yield (None, cursor)


@pytest.fixture
def cur(monkeypatch):
    """Install a recording cursor in place of core_cursor."""
    recorder = RecordingCursor()

    def _factory(*args, **kwargs):
        return _fake_core_cursor(recorder)

    monkeypatch.setattr(repository, "core_cursor", _factory)
    return recorder


def install(monkeypatch, recorder) -> RecordingCursor:
    """Install a specific recorder (for tests that must queue rows)."""
    monkeypatch.setattr(repository, "core_cursor", lambda *a, **k: _fake_core_cursor(recorder))
    return recorder


# ---------------------------------------------------------------------------
# The security property, asserted on a recording
# ---------------------------------------------------------------------------

def assert_scoped(recorder: RecordingCursor, ctx) -> None:
    """Every recorded statement touching a scoped table carries its scope.

    This is the whole point of the file. It is written as a reusable auditor
    so the negative control below can prove it actually rejects.
    """
    statements = recorder.scoped_statements
    assert statements, "no statement touched a scoped table - nothing was proved"

    unbound_admin = ctx.is_platform_admin is True and ctx.agency_id is None

    for statement in statements:
        insert = INSERT_INTO_SCOPED.search(statement.sql)
        if insert:
            assert "agency_id" in statement.sql, (
                f"INSERT into {insert.group(1)} does not name agency_id: {statement}"
            )
            assert ctx.agency_id in statement.bound, (
                f"INSERT does not bind ctx.agency_id: {statement}"
            )
            continue

        if unbound_admin:
            # A cross-agency read is legitimate for this one context shape.
            assert "TRUE" in statement.sql or "agency_id" in statement.sql, statement
            continue

        assert "agency_id" in statement.sql, (
            f"statement has no agency predicate: {statement}"
        )
        assert ctx.agency_id in statement.bound, (
            f"agency predicate is not bound to ctx.agency_id: {statement}"
        )
        assert "WHERE TRUE" not in statement.sql, (
            f"a bound context reached the widening branch: {statement}"
        )


# ---------------------------------------------------------------------------
# D1 - the harness itself
# ---------------------------------------------------------------------------

# Every public repository function, with a call that exercises it. A new
# function that is not listed here fails test_d1_registry_is_complete: the
# guard fails closed on addition.
COVERAGE = {
    "create_contact": lambda ctx: repository.create_contact(ctx, {"display_name": "x"}),
    "list_contacts": lambda ctx: repository.list_contacts(ctx, 50, 0, None, None),
    "get_contact": lambda ctx: repository.get_contact(ctx, 1),
    "update_contact": lambda ctx: repository.update_contact(ctx, 1, {"status": "active"}),
    "add_contact_role": lambda ctx: repository.add_contact_role(ctx, 1, {"role": "owner", "is_primary": True, "valid_from": None, "valid_to": None, "metadata": None}),
    "delete_contact_role": lambda ctx: repository.delete_contact_role(ctx, 1, "owner"),
    # Task 12. The record's own agency governs the membership check, so these
    # are exercised here for the scope property and in detail under D12.
    "set_contact_assignment": lambda ctx: repository.set_contact_assignment(ctx, 7, TARGET_AGENT),
    "set_lead_assignment": lambda ctx: repository.set_lead_assignment(ctx, 7, TARGET_AGENT),
    "create_lead": lambda ctx: repository.create_lead(ctx, {"contact_id": 1}),
    "list_leads": lambda ctx: repository.list_leads(ctx, 50, 0, None, None, None, None),
    "get_lead": lambda ctx: repository.get_lead(ctx, 1),
    "update_lead": lambda ctx: repository.update_lead(ctx, 1, {"status": "open"}),
    "link_stima": lambda ctx: repository.link_stima(ctx, 1, 2, "related"),
    "unlink_stima": lambda ctx: repository.unlink_stima(ctx, 1, 2),
    "create_activity": lambda ctx: repository.create_activity(ctx, {"activity_type": "note"}),
    "list_activities": lambda ctx: repository.list_activities(ctx, 50, 0, None, None, None),
    "create_task": lambda ctx: repository.create_task(ctx, {"title": "t"}),
    "list_tasks": lambda ctx: repository.list_tasks(ctx, 50, 0, None, None, None, None),
    "update_task": lambda ctx: repository.update_task(ctx, 1, {"status": "open"}),
    "delete_activity": lambda ctx: repository.delete_activity(ctx, 1),
    "delete_task": lambda ctx: repository.delete_task(ctx, 1),
}

# Public functions that legitimately do not take ctx, each with its reason.
CTX_FREE_PUBLIC_FUNCTIONS = {
    "bridge_public_stima": "SYSTEM_CONTEXT_FUNCTIONS member: builds its own scope from the cursor",
    "create_activity_with_cursor": "R-4: called by flow/, followup/, owner/; agency derived by the 030 trigger",
    "create_task_with_cursor": "R-4: called by flow/, followup/, owner/; agency derived by the 030 trigger",
}


def _public_repository_functions() -> set[str]:
    return {
        name
        for name, value in inspect.getmembers(repository, inspect.isfunction)
        if not name.startswith("_") and value.__module__ == repository.__name__
    }


def _exercise(function_name: str, ctx) -> None:
    """Run one repository function purely to capture the SQL it issues.

    A domain outcome is not the subject here: against a permissive fake cursor
    `link_stima` legitimately finds an existing link and raises ConflictError,
    and a queued-empty read legitimately raises NotFoundError. Both happen
    *after* the statements under audit were recorded. PlatformAdminAgencyRequired
    and ProgrammingError are deliberately NOT caught - those are scope
    decisions, and swallowing them would hide exactly what this file exists to
    prove.
    """
    try:
        COVERAGE[function_name](ctx)
    except (NotFoundError, ConflictError):
        pass


def test_d1_registry_is_complete():
    """A new repository function must be classified, or this suite fails."""
    known = set(COVERAGE) | set(CTX_FREE_PUBLIC_FUNCTIONS)
    actual = _public_repository_functions()
    missing = actual - known
    assert not missing, (
        f"unclassified public repository function(s): {sorted(missing)}. "
        "Add them to COVERAGE (scoped) or CTX_FREE_PUBLIC_FUNCTIONS (with a reason)."
    )
    stale = known - actual
    assert not stale, f"COVERAGE names functions that no longer exist: {sorted(stale)}"


def test_d1_every_covered_function_takes_ctx_first():
    for name in COVERAGE:
        parameters = list(inspect.signature(getattr(repository, name)).parameters)
        assert parameters and parameters[0] == "ctx", (
            f"{name} must take ctx as its first positional parameter, got {parameters}"
        )


def test_d1_negative_control_the_auditor_rejects_an_unscoped_statement():
    """B2: a harness never shown to reject has not been shown to detect.

    Defined here, in the test module. No production file is touched to
    manufacture this failure.
    """
    rogue = RecordingCursor()
    rogue.execute("SELECT * FROM contacts WHERE id = %s", (1,))

    with pytest.raises(AssertionError, match="no agency predicate"):
        assert_scoped(rogue, owner_ctx())


def test_d1_negative_control_the_auditor_rejects_an_unbound_predicate():
    """Naming agency_id in the text is not enough; it must be bound to ctx."""
    rogue = RecordingCursor()
    rogue.execute("SELECT * FROM leads l WHERE l.agency_id = %s", (OTHER_AGENCY,))

    with pytest.raises(AssertionError, match="not bound to ctx.agency_id"):
        assert_scoped(rogue, owner_ctx())


def test_d1_negative_control_the_auditor_rejects_an_unscoped_insert():
    rogue = RecordingCursor()
    rogue.execute("INSERT INTO contacts (display_name) VALUES (%s)", ("x",))

    with pytest.raises(AssertionError, match="does not name agency_id"):
        assert_scoped(rogue, owner_ctx())


def test_d1_negative_control_the_auditor_rejects_a_smuggled_where_true():
    rogue = RecordingCursor()
    rogue.execute("SELECT * FROM tasks t WHERE TRUE AND t.agency_id = %s", (AGENCY,))

    with pytest.raises(AssertionError, match="widening branch"):
        assert_scoped(rogue, owner_ctx())


# ---------------------------------------------------------------------------
# D2 - every scoped operation, every bound context
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("function_name", sorted(COVERAGE))
@pytest.mark.parametrize("context_name", sorted(ALL_BOUND_CONTEXTS))
def test_d2_every_scoped_operation_carries_a_bound_predicate(
    function_name, context_name, cur
):
    """The table-driven core of this suite: 19 functions x 5 contexts."""
    ctx = ALL_BOUND_CONTEXTS[context_name]()
    _exercise(function_name, ctx)
    assert_scoped(cur, ctx)


@pytest.mark.parametrize("function_name", sorted(COVERAGE))
def test_d2_system_context_never_widens(function_name, cur):
    ctx = system_ctx()
    _exercise(function_name, ctx)
    assert_scoped(cur, ctx)
    assert "WHERE TRUE" not in cur.sql_text()


# ---------------------------------------------------------------------------
# D3 - creates
# ---------------------------------------------------------------------------

def test_d3_create_contact_stamps_agency_and_creator_from_ctx(cur):
    repository.create_contact(owner_ctx(), {"display_name": "Mario"})
    statement = cur.calls[-1]
    assert "INSERT INTO contacts" in statement.sql
    assert "agency_id" in statement.sql
    assert "created_by_user_id" in statement.sql
    assert statement.params["agency_id"] == AGENCY
    assert statement.params["created_by_user_id"] == 1


def test_d3_create_lead_stamps_agency_and_creator_from_ctx(cur):
    repository.create_lead(owner_ctx(), {"contact_id": 1})
    insert = [c for c in cur.calls if "INSERT INTO leads" in c.sql][0]
    assert insert.params["agency_id"] == AGENCY
    assert insert.params["created_by_user_id"] == 1


@pytest.mark.parametrize(
    "call",
    [
        lambda ctx: repository.create_contact(ctx, {"display_name": "x", "agency_id": OTHER_AGENCY}),
        lambda ctx: repository.create_lead(ctx, {"contact_id": 1, "agency_id": OTHER_AGENCY}),
        lambda ctx: repository.create_activity(ctx, {"activity_type": "note", "agency_id": OTHER_AGENCY}),
        lambda ctx: repository.create_task(ctx, {"title": "t", "agency_id": OTHER_AGENCY}),
        lambda ctx: repository.update_contact(ctx, 1, {"agency_id": OTHER_AGENCY}),
        lambda ctx: repository.update_lead(ctx, 1, {"agency_id": OTHER_AGENCY}),
        lambda ctx: repository.update_task(ctx, 1, {"agency_id": OTHER_AGENCY}),
    ],
)
def test_d3_a_caller_supplied_agency_id_is_refused(call, cur):
    """The payload is never a source of agency authority."""
    with pytest.raises(ProgrammingError):
        call(owner_ctx())


def test_d3_a_caller_supplied_creator_is_refused(cur):
    with pytest.raises(ProgrammingError):
        repository.create_contact(owner_ctx(), {"display_name": "x", "created_by_user_id": 99})


@pytest.mark.parametrize(
    "create",
    [
        lambda ctx: repository.create_contact(ctx, {"display_name": "x"}),
        lambda ctx: repository.create_lead(ctx, {"contact_id": 1}),
        lambda ctx: repository.create_activity(ctx, {"activity_type": "note"}),
        lambda ctx: repository.create_task(ctx, {"title": "t"}),
    ],
)
def test_d3_unbound_platform_admin_cannot_create_and_reaches_no_cursor(create, cur):
    """Plan section 11.2: refused before any statement is issued."""
    with pytest.raises(PlatformAdminAgencyRequired):
        create(unbound_platform_admin_ctx())
    assert cur.calls == [], f"a statement escaped: {cur.calls}"


def test_d3_the_legacy_basic_context_may_create(cur):
    """C2: agency-bound, so it is an ordinary create - no special case."""
    repository.create_contact(legacy_basic_ctx(), {"display_name": "x"})
    insert = cur.calls[-1]
    assert insert.params["agency_id"] == AGENCY
    assert insert.params["created_by_user_id"] is None


# ---------------------------------------------------------------------------
# D4 - updates and deletes: id + scope
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "call,verb,table",
    [
        (lambda ctx: repository.update_contact(ctx, 42, {"status": "active"}), "UPDATE", "contacts"),
        (lambda ctx: repository.update_lead(ctx, 42, {"status": "open"}), "UPDATE", "leads"),
        (lambda ctx: repository.update_task(ctx, 42, {"status": "open"}), "UPDATE", "tasks"),
        (lambda ctx: repository.delete_activity(ctx, 42), "DELETE", "activities"),
        (lambda ctx: repository.delete_task(ctx, 42), "DELETE", "tasks"),
    ],
)
def test_d4_mutations_combine_id_and_scope(call, verb, table, cur):
    ctx = owner_ctx()
    call(ctx)
    statement = [c for c in cur.calls if verb in c.sql][-1]
    assert "id = %s" in statement.sql, statement
    assert "agency_id = %s" in statement.sql, statement
    assert 42 in statement.bound and AGENCY in statement.bound, statement


@pytest.mark.parametrize(
    "call",
    [
        lambda ctx: repository.update_contact(ctx, 42, {"status": "active"}),
        lambda ctx: repository.update_lead(ctx, 42, {"status": "open"}),
        lambda ctx: repository.update_task(ctx, 42, {"status": "open"}),
    ],
)
def test_d4_an_update_that_matches_nothing_is_not_found(call, monkeypatch):
    """Cross-agency and genuinely-absent are the same answer (D-6)."""
    install(monkeypatch, RecordingCursor(rows=[None], rowcount=0))
    with pytest.raises(NotFoundError):
        call(owner_ctx())


@pytest.mark.parametrize(
    "call",
    [
        lambda ctx: repository.delete_activity(ctx, 42),
        lambda ctx: repository.delete_task(ctx, 42),
    ],
)
def test_d4_a_delete_that_matches_nothing_is_not_found(call, monkeypatch):
    install(monkeypatch, RecordingCursor(rowcount=0))
    with pytest.raises(NotFoundError):
        call(owner_ctx())


@pytest.mark.parametrize(
    "call",
    [
        lambda ctx: repository.get_contact(ctx, 42),
        lambda ctx: repository.get_lead(ctx, 42),
    ],
)
def test_d4_reading_a_foreign_id_is_not_found(call, monkeypatch):
    install(monkeypatch, RecordingCursor(rows=[None]))
    with pytest.raises(NotFoundError):
        call(owner_ctx())


def test_d4_a_read_by_id_still_carries_the_predicate(cur):
    """The id alone must never be sufficient to select a record."""
    ctx = owner_ctx()
    repository.get_contact(ctx, 42)
    first = cur.calls[0]
    assert "agency_id = %s" in first.sql, first
    assert AGENCY in first.bound


# ---------------------------------------------------------------------------
# D5 - child tables reached only through a scoped parent
# ---------------------------------------------------------------------------

def test_d5_contact_roles_are_read_only_after_the_parent_resolves(monkeypatch):
    """get_contact must not query contact_roles for a foreign contact."""
    recorder = install(monkeypatch, RecordingCursor(rows=[None]))
    with pytest.raises(NotFoundError):
        repository.get_contact(owner_ctx(), 42)
    assert not any("contact_roles" in call.sql for call in recorder.calls)


def test_d5_lead_stime_is_read_only_after_the_parent_resolves(monkeypatch):
    recorder = install(monkeypatch, RecordingCursor(rows=[None]))
    with pytest.raises(NotFoundError):
        repository.get_lead(owner_ctx(), 42)
    assert not any("lead_stime" in call.sql for call in recorder.calls)


def test_d5_adding_a_role_validates_the_scoped_parent(cur):
    repository.add_contact_role(owner_ctx(), 42, {"role": "owner", "is_primary": True, "valid_from": None, "valid_to": None, "metadata": None})
    guard = cur.calls[0]
    assert "contacts" in guard.sql
    assert "agency_id = %s" in guard.sql, guard
    assert AGENCY in guard.bound


def test_d5_adding_a_role_to_a_foreign_contact_is_not_found(monkeypatch):
    install(monkeypatch, RecordingCursor(rows=[None]))
    with pytest.raises(NotFoundError):
        repository.add_contact_role(owner_ctx(), 42, {"role": "owner", "is_primary": True, "valid_from": None, "valid_to": None, "metadata": None})


@pytest.mark.parametrize(
    "call,child",
    [
        (lambda ctx: repository.delete_contact_role(ctx, 42, "owner"), "contact_roles"),
        (lambda ctx: repository.unlink_stima(ctx, 42, 7), "lead_stime"),
    ],
)
def test_d5_child_deletes_join_the_scoped_parent(call, child, cur):
    """A child row must not be removable by guessing its parent's id."""
    ctx = owner_ctx()
    call(ctx)
    statement = [c for c in cur.calls if child in c.sql][-1]
    assert "agency_id = %s" in statement.sql, statement
    assert AGENCY in statement.bound, statement


@pytest.mark.parametrize(
    "call",
    [
        lambda ctx: repository.delete_contact_role(ctx, 42, "owner"),
        lambda ctx: repository.unlink_stima(ctx, 42, 7),
    ],
)
def test_d5_a_child_delete_matching_nothing_is_not_found(call, monkeypatch):
    install(monkeypatch, RecordingCursor(rowcount=0))
    with pytest.raises(NotFoundError):
        call(owner_ctx())


def test_d5_link_stima_validates_the_lead_within_scope(monkeypatch):
    # lead present, stima present, then no existing link.
    recorder = install(monkeypatch, RecordingCursor(rows=[{"id": 42}, {"id": 7}, None, {"id": 1}]))
    repository.link_stima(owner_ctx(), 42, 7, "related")
    guard = recorder.calls[0]
    assert "leads" in guard.sql
    assert "agency_id = %s" in guard.sql, guard


def test_d5_link_stima_to_a_foreign_lead_is_not_found(monkeypatch):
    install(monkeypatch, RecordingCursor(rows=[None]))
    with pytest.raises(NotFoundError):
        repository.link_stima(owner_ctx(), 42, 7, "related")


# ---------------------------------------------------------------------------
# D6 - the agent narrowing, per table
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "call,table",
    [
        (lambda ctx: repository.list_contacts(ctx, 50, 0, None, None), "contacts"),
        (lambda ctx: repository.list_leads(ctx, 50, 0, None, None, None, None), "leads"),
    ],
)
def test_d6_agent_reads_of_assignable_tables_add_the_assignment_filter(call, table, cur):
    ctx = agent_ctx()
    call(ctx)
    statement = cur.scoped_statements[0]
    assert "assigned_agent_id = %s" in statement.sql, statement
    assert statement.bound[:2] == [AGENCY, AGENT_USER], statement


@pytest.mark.parametrize(
    "call",
    [
        lambda ctx: repository.list_activities(ctx, 50, 0, None, None, None),
        lambda ctx: repository.list_tasks(ctx, 50, 0, None, None, None, None),
    ],
)
def test_d6_agent_reads_of_activities_and_tasks_are_agency_only(call, cur):
    ctx = agent_ctx()
    call(ctx)
    statement = cur.scoped_statements[0]
    assert "assigned_agent_id" not in statement.sql, statement
    assert "assigned_to" not in statement.sql, statement
    assert statement.bound[0] == AGENCY


def test_d6_an_agent_update_respects_the_same_assignment_filter(cur):
    """An agent must not mutate a colleague's contact by id."""
    repository.update_contact(agent_ctx(), 42, {"status": "active"})
    statement = [c for c in cur.calls if "UPDATE" in c.sql][-1]
    assert "assigned_agent_id = %s" in statement.sql, statement
    assert AGENT_USER in statement.bound


@pytest.mark.parametrize("role", ["agency_owner", "agency_admin"])
def test_d6_owner_and_admin_get_no_assignment_filter(role, cur):
    repository.list_contacts(_ctx(role=role), 50, 0, None, None)
    assert "assigned_agent_id" not in cur.sql_text()


# ---------------------------------------------------------------------------
# D7 - platform admin
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "call",
    [
        lambda ctx: repository.list_contacts(ctx, 50, 0, None, None),
        lambda ctx: repository.list_leads(ctx, 50, 0, None, None, None, None),
        lambda ctx: repository.list_activities(ctx, 50, 0, None, None, None),
        lambda ctx: repository.list_tasks(ctx, 50, 0, None, None, None, None),
    ],
)
def test_d7_unbound_platform_admin_reads_cross_agency(call, cur):
    call(unbound_platform_admin_ctx())
    statement = cur.scoped_statements[0]
    assert "WHERE TRUE" in statement.sql, statement


def test_d7_bound_platform_admin_is_scoped_like_anyone_else(cur):
    ctx = bound_platform_admin_ctx()
    repository.list_contacts(ctx, 50, 0, None, None)
    statement = cur.scoped_statements[0]
    assert "TRUE" not in statement.sql, statement
    assert AGENCY in statement.bound


# ---------------------------------------------------------------------------
# D8 - SystemAgencyContext
# ---------------------------------------------------------------------------

def test_d8_the_bridge_opens_no_agency_lookup_of_its_own(monkeypatch):
    """P26-2B2B-R1: the bridge receives a scope instead of resolving one.

    It used to build its own `SystemAgencyContext` here, which meant a public
    estimation resolved the Default Agency twice - once for `stime.agency_id`
    and again, in a later transaction, for its contact and lead. Two lookups
    are two decisions, and they can disagree. Now the writer decides once and
    the context travels, so this asserts the second lookup is *gone*.
    """
    recorder = install(monkeypatch, RecordingCursor(rows=[None, None, None]))
    repository.bridge_public_stima(
        1, {"display_name": None}, {}, "related", system_ctx=system_ctx()
    )
    assert not [c for c in recorder.calls if "FROM agencies" in c.sql], recorder.calls


def test_d8_the_bridge_scopes_its_identity_lookups(monkeypatch):
    recorder = install(
        monkeypatch,
        RecordingCursor(
            # lead_stime (absent) -> INSERT contact -> INSERT lead -> INSERT link
            rows=[None, {"id": 5}, {"id": 6}, {"id": 7}],
            rowsets=[[], []],
        ),
    )
    repository.bridge_public_stima(
        1,
        {"display_name": "X", "email_normalized": "a@b.c"},
        {},
        "related",
        system_ctx=system_ctx(),
    )
    lookups = [c for c in recorder.calls if "email_normalized" in c.sql and "SELECT" in c.sql]
    assert lookups, "the identity lookup did not run"
    for lookup in lookups:
        assert "agency_id = %s" in lookup.sql, lookup
        assert AGENCY in lookup.bound, lookup


def test_d8_the_bridge_fails_closed_on_a_scope_it_will_not_accept(monkeypatch):
    """Fail-closed moved with the resolution, it did not disappear.

    A missing Default Agency is now caught in the writer, before any row is
    written (tests/test_p26_2b_public_stima_writer.py, group B3). What the
    bridge must still refuse is being handed something that is not the public
    flow's own server-built scope - otherwise threading the context would just
    relocate the authority to whoever calls it.
    """
    recorder = install(monkeypatch, RecordingCursor(rows=[None, None, None]))

    class LookAlike:
        agency_id = 999
        origin = "public_stima"
        role = None
        user_id = None
        is_platform_admin = False

        def require_agency(self):
            return self.agency_id

    for hostile in (LookAlike(), None, owner_ctx()):
        with pytest.raises(ProgrammingError):
            repository.bridge_public_stima(
                1, {"display_name": "X"}, {}, "related", system_ctx=hostile
            )
    assert not recorder.calls, "the bridge issued SQL under a rejected scope"


def test_d8_system_context_gets_no_assignment_filter(cur):
    repository.list_contacts(system_ctx(), 50, 0, None, None)
    assert "assigned_agent_id" not in cur.sql_text()


# ---------------------------------------------------------------------------
# D9 - R-4: the cursor helpers stay ctx-less
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name", ["create_activity_with_cursor", "create_task_with_cursor"]
)
def test_d9_cursor_helpers_do_not_require_ctx(name):
    """Approved exception R-4.

    flow/, followup/ and owner/ call these with (cur, data) and are explicitly
    unmodified in P26-1. Their agency_id is derived and validated by migration
    030's core_agency_integrity() trigger from the row's own references. Making
    ctx mandatory here would break three P17-P25-certified modules.
    """
    signature = inspect.signature(getattr(repository, name))
    parameters = list(signature.parameters)
    assert parameters[0] == "cur", parameters
    assert "ctx" not in parameters[:2], parameters
    if "ctx" in signature.parameters:
        assert signature.parameters["ctx"].default is None, "ctx must stay optional"


@pytest.mark.parametrize(
    "name,table",
    [("create_activity_with_cursor", "activities"), ("create_task_with_cursor", "tasks")],
)
def test_d9_ctx_less_call_emits_no_agency_column(name, table):
    """The statement the certified callers issue is unchanged."""
    recorder = RecordingCursor()
    getattr(repository, name)(recorder, {"activity_type": "note", "title": "t"})
    insert = [c for c in recorder.calls if "INSERT INTO" in c.sql][-1]
    assert table in insert.sql
    assert "agency_id" not in insert.sql, insert


@pytest.mark.parametrize(
    "name", ["create_activity_with_cursor", "create_task_with_cursor"]
)
def test_d9_passing_ctx_stamps_the_agency(name):
    """The operator path opts in; the trigger validates rather than derives."""
    recorder = RecordingCursor()
    getattr(repository, name)(recorder, {"activity_type": "note", "title": "t"}, ctx=owner_ctx())
    insert = [c for c in recorder.calls if "INSERT INTO" in c.sql][-1]
    assert "agency_id" in insert.sql
    assert insert.params["agency_id"] == AGENCY


# ---------------------------------------------------------------------------
# D11 - the CORE router forwards the dependency's scope, unchanged
#
# The structural walk in tests/test_p26_1_scope_enforcement.py proves every
# handler *forwards* a ctx. These tests prove the object it forwards is the one
# require_operator produced, and nothing the caller sent.
# ---------------------------------------------------------------------------

ROUTER_CALLS = {
    "create_contact": ("post", "/api/core/contacts", {"json": {"display_name": "X"}}),
    "list_contacts": ("get", "/api/core/contacts", {}),
    "get_contact": ("get", "/api/core/contacts/7", {}),
    "update_contact": ("patch", "/api/core/contacts/7", {"json": {"notes": "n"}}),
    "add_contact_role": ("post", "/api/core/contacts/7/roles", {"json": {"role": "owner"}}),
    "delete_contact_role": ("delete", "/api/core/contacts/7/roles/owner", {}),
    "create_lead": ("post", "/api/core/leads", {"json": {"contact_id": 1}}),
    "list_leads": ("get", "/api/core/leads", {}),
    "get_lead": ("get", "/api/core/leads/7", {}),
    "update_lead": ("patch", "/api/core/leads/7", {"json": {"notes": "n"}}),
    "link_stima": ("post", "/api/core/leads/7/stime/9", {"json": {"relation_type": "related"}}),
    "unlink_stima": ("delete", "/api/core/leads/7/stime/9", {}),
    # activity/task creates require one of contact_id/lead_id/stima_id (CORE
    # schema root validator); the payloads below satisfy it so the request
    # reaches the handler rather than stopping at a 422.
    "create_activity": ("post", "/api/core/activities", {"json": {"activity_type": "note", "contact_id": 1}}),
    "list_activities": ("get", "/api/core/activities", {}),
    "delete_activity": ("delete", "/api/core/activities/7", {}),
    "create_task": ("post", "/api/core/tasks", {"json": {"title": "t", "contact_id": 1}}),
    "list_tasks": ("get", "/api/core/tasks", {}),
    "update_task": ("patch", "/api/core/tasks/7", {"json": {"title": "t2"}}),
    "delete_task": ("delete", "/api/core/tasks/7", {}),
}


@pytest.fixture
def core_client(monkeypatch):
    """A CORE app whose service layer records the scope it was handed."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from core import router as router_module
    from core.router import router as core_router
    from operator_auth.dependencies import require_operator

    injected = owner_ctx()
    seen: dict[str, object] = {}

    def _recorder(name):
        def _fake(ctx, *args, **kwargs):
            seen["name"] = name
            seen["ctx"] = ctx
            return {} if name.startswith(("get", "create", "update", "add", "link")) else []
        return _fake

    for name in ROUTER_CALLS:
        monkeypatch.setattr(router_module.service, name, _recorder(name))

    app = FastAPI()
    app.include_router(core_router)
    app.dependency_overrides[require_operator] = lambda: injected
    return TestClient(app, raise_server_exceptions=False), seen, injected


@pytest.mark.parametrize("handler", sorted(ROUTER_CALLS))
def test_d11_every_core_handler_forwards_the_dependency_scope(handler, core_client):
    client, seen, injected = core_client
    method, path, kwargs = ROUTER_CALLS[handler]

    response = getattr(client, method)(path, **kwargs)

    assert response.status_code < 400, (handler, response.status_code, response.text)
    assert seen.get("name") == handler, seen
    # Identity, not equality: the handler passed through the very object the
    # dependency produced - it did not build one of its own.
    assert seen["ctx"] is injected
    assert isinstance(seen["ctx"], OperatorContext)
    assert not isinstance(seen["ctx"], SystemAgencyContext)


def test_d11_a_client_supplied_agency_id_is_ignored_not_honoured(core_client):
    """A query parameter named agency_id must not reach the scope."""
    client, seen, injected = core_client

    response = client.get("/api/core/contacts?agency_id=999")

    assert response.status_code < 400, response.text
    assert seen["ctx"] is injected
    assert seen["ctx"].agency_id == AGENCY


def test_d11_an_unauthenticated_request_never_reaches_the_service(monkeypatch):
    """Without the dependency the handler must not run at all."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from core import router as router_module
    from core.router import router as core_router

    called = []
    monkeypatch.setattr(
        router_module.service, "list_contacts",
        lambda *a, **k: called.append(a) or [],
    )

    app = FastAPI()
    app.include_router(core_router)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/api/core/contacts")

    assert response.status_code == 401, response.text
    assert called == [], "the service ran without a scope"


# ===========================================================================
# D12 - Task 12: contact and lead assignment
#
# The approved algorithm (plan section 12.1) derives the governing agency from
# the *record*, never from the caller's scope and never from the target
# operator. That is what makes one rule serve every role, including an unbound
# platform admin who has no agency of their own.
#
# Order of checks, per the Task-12 order:
#   1. permission        -> 403 (before any lookup, so an agent learns nothing)
#   2. record in scope   -> 404
#   3. target membership -> 400
#   4. update            -> id + scope predicate
# ===========================================================================

ASSIGNMENT_ROUTES = {
    "contacts": "/api/core/contacts/7/assignment",
    "leads": "/api/core/leads/7/assignment",
}

ASSIGN_FUNCTIONS = {
    "contacts": "set_contact_assignment",
    "leads": "set_lead_assignment",
}

RECORD_AGENCY = 31  # deliberately NOT ctx.agency_id, to prove derivation


def _assignment_cursor(record_agency=RECORD_AGENCY, membership=True, updated=True):
    """resolve record -> membership probe -> UPDATE ... RETURNING."""
    rows = [{"id": 7, "agency_id": record_agency}]
    rows.append({"exists": 1} if membership else None)
    rows.append({"id": 7, "agency_id": record_agency, "assigned_agent_id": TARGET_AGENT}
                if updated else None)
    return RecordingCursor(rows=rows)


@pytest.mark.parametrize("table", sorted(ASSIGN_FUNCTIONS))
def test_d12_owner_can_assign_an_active_member(table, monkeypatch):
    recorder = install(monkeypatch, _assignment_cursor())
    result = getattr(repository, ASSIGN_FUNCTIONS[table])(owner_ctx(), 7, TARGET_AGENT)
    assert result["assigned_agent_id"] == TARGET_AGENT
    assert_scoped(recorder, owner_ctx())


@pytest.mark.parametrize("table", sorted(ASSIGN_FUNCTIONS))
def test_d12_the_membership_probe_uses_the_record_agency(table, monkeypatch):
    """Not ctx.agency_id, and not anything about the target user."""
    recorder = install(monkeypatch, _assignment_cursor(record_agency=RECORD_AGENCY))
    getattr(repository, ASSIGN_FUNCTIONS[table])(owner_ctx(), 7, TARGET_AGENT)

    probe = [c for c in recorder.calls if "agency_memberships" in c.sql]
    assert len(probe) == 1, recorder.calls
    assert RECORD_AGENCY in probe[0].bound, probe[0]
    assert TARGET_AGENT in probe[0].bound, probe[0]
    assert AGENCY not in probe[0].bound, "the caller's agency governed the check"
    assert "status = 'active'" in probe[0].sql, probe[0]


@pytest.mark.parametrize("table", sorted(ASSIGN_FUNCTIONS))
def test_d12_the_record_is_resolved_before_the_membership_probe(table, monkeypatch):
    recorder = install(monkeypatch, _assignment_cursor())
    getattr(repository, ASSIGN_FUNCTIONS[table])(owner_ctx(), 7, TARGET_AGENT)
    kinds = [
        "record" if table in c.sql and "agency_memberships" not in c.sql and "SELECT" in c.sql
        else "membership" if "agency_memberships" in c.sql
        else "update" if "UPDATE" in c.sql else "other"
        for c in recorder.calls
    ]
    assert kinds.index("record") < kinds.index("membership") < kinds.index("update"), kinds


@pytest.mark.parametrize("table", sorted(ASSIGN_FUNCTIONS))
def test_d12_the_update_combines_id_and_scope(table, monkeypatch):
    recorder = install(monkeypatch, _assignment_cursor())
    getattr(repository, ASSIGN_FUNCTIONS[table])(owner_ctx(), 7, TARGET_AGENT)
    update = [c for c in recorder.calls if "UPDATE" in c.sql][-1]
    assert "id = %s" in update.sql, update
    assert "agency_id = %s" in update.sql, update
    assert 7 in update.bound and AGENCY in update.bound, update
    assert "assigned_agent_id = %s" in update.sql, update


@pytest.mark.parametrize("table", sorted(ASSIGN_FUNCTIONS))
def test_d12_a_record_outside_scope_is_not_found(table, monkeypatch):
    recorder = install(monkeypatch, RecordingCursor(rows=[None]))
    with pytest.raises(NotFoundError):
        getattr(repository, ASSIGN_FUNCTIONS[table])(owner_ctx(), 7, TARGET_AGENT)
    assert not any("agency_memberships" in c.sql for c in recorder.calls), (
        "the membership probe ran for a record the caller cannot see"
    )
    assert not any("UPDATE" in c.sql for c in recorder.calls)


@pytest.mark.parametrize("table", sorted(ASSIGN_FUNCTIONS))
def test_d12_a_target_without_active_membership_is_rejected(table, monkeypatch):
    recorder = install(monkeypatch, _assignment_cursor(membership=False))
    with pytest.raises(ValidationError):
        getattr(repository, ASSIGN_FUNCTIONS[table])(owner_ctx(), 7, TARGET_AGENT)
    assert not any("UPDATE" in c.sql for c in recorder.calls), "it assigned anyway"


@pytest.mark.parametrize("table", sorted(ASSIGN_FUNCTIONS))
def test_d12_the_rejection_message_leaks_nothing_about_the_target(table, monkeypatch):
    install(monkeypatch, _assignment_cursor(membership=False))
    with pytest.raises(ValidationError) as exc:
        getattr(repository, ASSIGN_FUNCTIONS[table])(owner_ctx(), 7, TARGET_AGENT)
    message = str(exc.value)
    assert "@" not in message, message
    assert str(RECORD_AGENCY) not in message, message


@pytest.mark.parametrize("table", sorted(ASSIGN_FUNCTIONS))
def test_d12_null_unassigns_and_skips_the_membership_probe(table, monkeypatch):
    recorder = install(
        monkeypatch,
        RecordingCursor(rows=[{"id": 7, "agency_id": RECORD_AGENCY},
                              {"id": 7, "assigned_agent_id": None}]),
    )
    result = getattr(repository, ASSIGN_FUNCTIONS[table])(owner_ctx(), 7, None)
    assert result["assigned_agent_id"] is None
    assert not any("agency_memberships" in c.sql for c in recorder.calls)
    update = [c for c in recorder.calls if "UPDATE" in c.sql][-1]
    assert None in update.bound


@pytest.mark.parametrize("table", sorted(ASSIGN_FUNCTIONS))
def test_d12_an_unbound_platform_admin_assigns_without_binding_an_agency(table, monkeypatch):
    """The approved exception: no ctx.require_agency() on this path."""
    recorder = install(monkeypatch, _assignment_cursor())
    ctx = unbound_platform_admin_ctx()
    result = getattr(repository, ASSIGN_FUNCTIONS[table])(ctx, 7, TARGET_AGENT)

    assert result["assigned_agent_id"] == TARGET_AGENT
    lookup = recorder.calls[0]
    assert "WHERE TRUE" in lookup.sql, lookup
    probe = [c for c in recorder.calls if "agency_memberships" in c.sql][0]
    assert RECORD_AGENCY in probe.bound, "the record's agency must govern"


def test_d12_the_assignment_path_never_calls_require_agency(monkeypatch):
    """An unbound platform admin must not be forced to infer a default."""
    class _Tripwire(OperatorContext):
        pass

    ctx = unbound_platform_admin_ctx()
    calls = []
    monkeypatch.setattr(
        type(ctx), "require_agency",
        lambda self: calls.append(1) or (_ for _ in ()).throw(AssertionError("require_agency called")),
        raising=False,
    )
    install(monkeypatch, _assignment_cursor())
    repository.set_contact_assignment(ctx, 7, TARGET_AGENT)
    assert calls == []


@pytest.mark.parametrize("name", sorted(ASSIGN_FUNCTIONS.values()))
def test_d12_no_agency_id_argument_is_exposed(name):
    """The agency is never an argument any layer can supply."""
    parameters = list(inspect.signature(getattr(repository, name)).parameters)
    assert parameters[0] == "ctx", parameters
    assert len(parameters) == 3, parameters
    for forbidden in ("agency_id", "agency", "slug", "target_agency_id"):
        assert forbidden not in parameters, forbidden


@pytest.mark.parametrize("table", sorted(ASSIGN_FUNCTIONS))
def test_d12_a_target_in_another_agency_is_rejected(table, monkeypatch):
    """Cause 1 of the 400: the probe is bound to the record's agency, so an
    operator of a different agency simply does not match."""
    recorder = install(monkeypatch, _assignment_cursor(membership=False))
    with pytest.raises(ValidationError):
        getattr(repository, ASSIGN_FUNCTIONS[table])(owner_ctx(), 7, TARGET_AGENT)
    probe = [c for c in recorder.calls if "agency_memberships" in c.sql][0]
    assert "agency_id = %s" in probe.sql and RECORD_AGENCY in probe.bound


@pytest.mark.parametrize("table", sorted(ASSIGN_FUNCTIONS))
def test_d12_a_suspended_membership_is_rejected(table, monkeypatch):
    """Cause 2 of the same 400: the probe filters on an active membership."""
    recorder = install(monkeypatch, _assignment_cursor(membership=False))
    with pytest.raises(ValidationError):
        getattr(repository, ASSIGN_FUNCTIONS[table])(owner_ctx(), 7, TARGET_AGENT)
    probe = [c for c in recorder.calls if "agency_memberships" in c.sql][0]
    assert "status = 'active'" in probe.sql, probe


# -- negative controls: the assertions above must actually discriminate -----

def test_d12_negative_control_using_the_caller_agency_is_caught(monkeypatch):
    """A membership probe bound to ctx.agency_id instead of the record's."""
    rogue = RecordingCursor()
    rogue.execute(
        "SELECT 1 FROM agency_memberships WHERE agency_id = %s AND operator_user_id = %s "
        "AND status = 'active' LIMIT 1",
        (AGENCY, TARGET_AGENT),
    )
    probe = [c for c in rogue.calls if "agency_memberships" in c.sql][0]
    with pytest.raises(AssertionError):
        assert RECORD_AGENCY in probe.bound
        assert AGENCY not in probe.bound


def test_d12_negative_control_an_update_by_id_alone_is_caught():
    rogue = RecordingCursor()
    rogue.execute("UPDATE contacts SET assigned_agent_id = %s WHERE id = %s", (TARGET_AGENT, 7))
    with pytest.raises(AssertionError, match="no agency predicate"):
        assert_scoped(rogue, owner_ctx())


def test_d12_negative_control_the_permission_primitive_denies_an_agent():
    """If may_assign_records ever admitted an agent, this fails first."""
    from operator_auth import permissions

    assert permissions.may_assign_records("agent", False) is False
    assert permissions.may_assign_records("agency_owner", False) is True
    assert permissions.may_assign_records("agency_admin", False) is True
    assert permissions.may_assign_records(None, True) is True
    # A system context presents role=None, is_platform_admin=False.
    assert permissions.may_assign_records(None, False) is False


# ---------------------------------------------------------------------------
# D13 - assignment over HTTP: status codes, ordering, and the closed schema
# ---------------------------------------------------------------------------

@pytest.fixture
def assignment_client(monkeypatch):
    """A CORE app with the assignment service recorded and the scope injected."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from core import service as service_module
    from core.router import router as core_router
    from operator_auth.dependencies import require_operator

    state = {"ctx": owner_ctx(), "calls": [], "raises": None}

    def _recorder(name):
        def _fake(ctx, entity_id, assigned_agent_id):
            state["calls"].append((name, ctx, entity_id, assigned_agent_id))
            if state["raises"] is not None:
                raise state["raises"]
            return {"id": entity_id, "assigned_agent_id": assigned_agent_id}
        return _fake

    # Patched at the REPOSITORY level, deliberately. The permission check lives
    # in the service, so replacing the service function would remove the very
    # check these tests exist to prove. Recording the repository call also makes
    # "the agent never reached a lookup" a statement about real control flow.
    for name in ASSIGN_FUNCTIONS.values():
        monkeypatch.setattr(service_module.repository, name, _recorder(name), raising=False)

    app = FastAPI()
    app.include_router(core_router)
    app.dependency_overrides[require_operator] = lambda: state["ctx"]
    return TestClient(app, raise_server_exceptions=False), state


@pytest.mark.parametrize("path", sorted(ASSIGNMENT_ROUTES.values()))
@pytest.mark.parametrize("role_name", ["agency_owner", "agency_admin"])
def test_d13_owner_and_admin_may_assign(path, role_name, assignment_client):
    client, state = assignment_client
    state["ctx"] = _ctx(role=role_name)
    response = client.patch(path, json={"assigned_agent_id": TARGET_AGENT})
    assert response.status_code == 200, response.text
    assert response.json()["assigned_agent_id"] == TARGET_AGENT


@pytest.mark.parametrize("path", sorted(ASSIGNMENT_ROUTES.values()))
def test_d13_an_unbound_platform_admin_may_assign(path, assignment_client):
    client, state = assignment_client
    state["ctx"] = unbound_platform_admin_ctx()
    response = client.patch(path, json={"assigned_agent_id": TARGET_AGENT})
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("path", sorted(ASSIGNMENT_ROUTES.values()))
def test_d13_an_agent_is_refused_with_403(path, assignment_client):
    client, state = assignment_client
    state["ctx"] = agent_ctx()
    response = client.patch(path, json={"assigned_agent_id": TARGET_AGENT})
    assert response.status_code == 403, response.text


@pytest.mark.parametrize("path", sorted(ASSIGNMENT_ROUTES.values()))
def test_d13_the_agent_refusal_happens_before_any_record_lookup(path, assignment_client):
    """403 must not depend on whether the record exists, or in whose agency."""
    client, state = assignment_client
    state["ctx"] = agent_ctx()
    client.patch(path, json={"assigned_agent_id": TARGET_AGENT})
    assert state["calls"] == [], "the repository ran for a caller without permission"


@pytest.mark.parametrize("path", sorted(ASSIGNMENT_ROUTES.values()))
def test_d13_an_agent_cannot_unassign_either(path, assignment_client):
    client, state = assignment_client
    state["ctx"] = agent_ctx()
    response = client.patch(path, json={"assigned_agent_id": None})
    assert response.status_code == 403
    assert state["calls"] == []


@pytest.mark.parametrize("path", sorted(ASSIGNMENT_ROUTES.values()))
def test_d13_a_record_outside_scope_is_404(path, assignment_client):
    client, state = assignment_client
    state["raises"] = NotFoundError("contact 7 not found")
    response = client.patch(path, json={"assigned_agent_id": TARGET_AGENT})
    assert response.status_code == 404, response.text


@pytest.mark.parametrize("path", sorted(ASSIGNMENT_ROUTES.values()))
def test_d13_an_invalid_target_agent_is_400(path, assignment_client):
    client, state = assignment_client
    state["raises"] = ValidationError("not an active member of this record's agency")
    response = client.patch(path, json={"assigned_agent_id": TARGET_AGENT})
    assert response.status_code == 400, response.text


@pytest.mark.parametrize("path", sorted(ASSIGNMENT_ROUTES.values()))
def test_d13_the_scope_is_the_dependency_object(path, assignment_client):
    client, state = assignment_client
    client.patch(path, json={"assigned_agent_id": TARGET_AGENT})
    _, forwarded, entity_id, target = state["calls"][-1]
    assert forwarded is state["ctx"]
    assert entity_id == 7
    assert target == TARGET_AGENT


@pytest.mark.parametrize("path", sorted(ASSIGNMENT_ROUTES.values()))
@pytest.mark.parametrize(
    "body",
    [
        {"assigned_agent_id": 1, "agency_id": 2},
        {"assigned_agent_id": 1, "role": "agency_owner"},
        {"assigned_agent_id": 1, "is_platform_admin": True},
        {"assigned_agent_id": 1, "status": "archived"},
        {"assigned_agent_id": 1, "whatever": "x"},
    ],
)
def test_d13_the_assignment_schema_is_closed(path, body, assignment_client):
    client, state = assignment_client
    response = client.patch(path, json=body)
    assert response.status_code == 422, response.text
    assert state["calls"] == []


@pytest.mark.parametrize(
    "path", ["/api/core/contacts/7", "/api/core/leads/7"]
)
def test_d13_the_generic_patch_still_refuses_an_assignment(path, core_client):
    """Spec section 10: a reassignment must not ride on an ordinary update."""
    client, seen, _ = core_client
    response = client.patch(path, json={"assigned_agent_id": TARGET_AGENT})
    assert response.status_code == 422, response.text


def test_d13_the_assignment_routes_exist_exactly_twice():
    from core.router import router as core_router

    assignment = {
        (method, route.path)
        for route in core_router.routes
        for method in route.methods
        if route.path.endswith("/assignment")
    }
    assert assignment == {
        ("PATCH", "/api/core/contacts/{contact_id}/assignment"),
        ("PATCH", "/api/core/leads/{lead_id}/assignment"),
    }, assignment


# ===========================================================================
# The Agency A vs Agency B fixture (Tasks 16 and 19).
#
# An in-memory CORE store behind a real router, a real service and the real
# permission gate. Only the repository is faked - and it applies the scope by
# calling the *production* `scoped_predicate` and honouring what it returns, so
# the isolation under test is the shipped decision, not a restatement of it.
#
# The SearchProbe control below deliberately bypasses that call, which is how
# these assertions are shown to detect a leak rather than merely coexist with
# one.
# ===========================================================================

AGENCY_A = 10
AGENCY_B = 20

OWNER_A, ADMIN_A, AGENT_A, AGENT_A2, OWNER_B, PLATFORM = 1, 2, 3, 4, 5, 6

CONTACT_A, CONTACT_A_UNASSIGNED, CONTACT_B = 101, 102, 201
LEAD_A, LEAD_B = 301, 401
ACTIVITY_A, ACTIVITY_B = 501, 601
TASK_A, TASK_B = 701, 801

ABSENT_ID = 999999

# A term that legitimately matches rows in BOTH agencies. Searching for it is
# the sharpest leakage probe: a scoped search returns one row, an unscoped one
# returns two, and the difference is invisible to any weaker fixture.
SHARED_SEARCH_TERM = "rossi"
B_ONLY_EMAIL = "solo.b@example.test"


def _operator(user_id, agency_id, role, is_platform_admin=False) -> OperatorContext:
    return OperatorContext(
        user_id=user_id, agency_id=agency_id, role=role,
        is_platform_admin=is_platform_admin, session_id=user_id * 10,
        auth_channel="operator_session",
    )


OPERATORS = {
    "owner_a": lambda: _operator(OWNER_A, AGENCY_A, "agency_owner"),
    "admin_a": lambda: _operator(ADMIN_A, AGENCY_A, "agency_admin"),
    "agent_a": lambda: _operator(AGENT_A, AGENCY_A, "agent"),
    "agent_a2": lambda: _operator(AGENT_A2, AGENCY_A, "agent"),
    "owner_b": lambda: _operator(OWNER_B, AGENCY_B, "agency_owner"),
    "platform_admin": lambda: _operator(PLATFORM, None, None, is_platform_admin=True),
}


def _seed_rows() -> dict[str, list[dict]]:
    return {
        "contacts": [
            {"id": CONTACT_A, "agency_id": AGENCY_A, "assigned_agent_id": AGENT_A,
             "display_name": "Mario Rossi", "email_normalized": "mario@example.test",
             "status": "active", "notes": None},
            {"id": CONTACT_A_UNASSIGNED, "agency_id": AGENCY_A, "assigned_agent_id": None,
             "display_name": "Anna Bianchi", "email_normalized": "anna@example.test",
             "status": "active", "notes": None},
            {"id": CONTACT_B, "agency_id": AGENCY_B, "assigned_agent_id": None,
             "display_name": "Luca Rossi", "email_normalized": B_ONLY_EMAIL,
             "status": "active", "notes": None},
        ],
        "leads": [
            {"id": LEAD_A, "agency_id": AGENCY_A, "assigned_agent_id": AGENT_A,
             "contact_id": CONTACT_A, "status": "open", "notes": None},
            {"id": LEAD_B, "agency_id": AGENCY_B, "assigned_agent_id": None,
             "contact_id": CONTACT_B, "status": "open", "notes": None},
        ],
        "activities": [
            {"id": ACTIVITY_A, "agency_id": AGENCY_A, "contact_id": CONTACT_A,
             "lead_id": None, "stima_id": 9001, "activity_type": "note"},
            {"id": ACTIVITY_B, "agency_id": AGENCY_B, "contact_id": CONTACT_B,
             "lead_id": None, "stima_id": 9002, "activity_type": "note"},
        ],
        "tasks": [
            {"id": TASK_A, "agency_id": AGENCY_A, "contact_id": CONTACT_A,
             "lead_id": None, "stima_id": 9001, "status": "open", "title": "A"},
            {"id": TASK_B, "agency_id": AGENCY_B, "contact_id": CONTACT_B,
             "lead_id": None, "stima_id": 9002, "status": "open", "title": "B"},
        ],
    }


class AgencyStore:
    """Two agencies' CORE rows, filtered by the production scope builder."""

    def __init__(self):
        self.rows = _seed_rows()
        self.next_id = 5000

    def visible(self, ctx, table: str) -> list[dict]:
        predicate, params = scoped_predicate(ctx, table, "x")
        rows = self.rows[table]
        if predicate == "TRUE":
            return list(rows)
        rows = [row for row in rows if row["agency_id"] == params[0]]
        if len(params) == 2:  # the agent narrowing
            rows = [row for row in rows if row.get("assigned_agent_id") == params[1]]
        return rows

    def find(self, ctx, table: str, entity_id: int) -> dict | None:
        return next((r for r in self.visible(ctx, table) if r["id"] == entity_id), None)

    def raw(self, table: str, entity_id: int) -> dict | None:
        return next((r for r in self.rows[table] if r["id"] == entity_id), None)


def _install_store(monkeypatch, store: AgencyStore, *, leaky_search: bool = False):
    """Replace core.repository's functions with in-memory equivalents."""
    from core import service as core_service

    def _not_found(label, entity_id):
        raise NotFoundError(f"{label} {entity_id} not found")

    def list_contacts(ctx, limit, offset, search, status):
        rows = store.rows["contacts"] if leaky_search else store.visible(ctx, "contacts")
        if status:
            rows = [r for r in rows if r["status"] == status]
        if search:
            term = search.strip().lower()
            rows = [
                r for r in rows
                if term in (r["display_name"] or "").lower()
                or term in (r["email_normalized"] or "").lower()
            ]
        return rows[offset: offset + limit]

    def list_leads(ctx, limit, offset, contact_id, pipeline, stage, status):
        rows = store.visible(ctx, "leads")
        if contact_id is not None:
            rows = [r for r in rows if r["contact_id"] == contact_id]
        if status:
            rows = [r for r in rows if r["status"] == status]
        return rows[offset: offset + limit]

    def _list_pivot(table):
        def _inner(ctx, limit, offset, contact_id, lead_id, stima_id, status=None):
            rows = store.visible(ctx, table)
            for column, value in (("contact_id", contact_id), ("lead_id", lead_id),
                                  ("stima_id", stima_id)):
                if value is not None:
                    rows = [r for r in rows if r.get(column) == value]
            if status:
                rows = [r for r in rows if r.get("status") == status]
            return rows[offset: offset + limit]
        return _inner

    def get_contact(ctx, contact_id):
        row = store.find(ctx, "contacts", contact_id)
        if row is None:
            _not_found("contact", contact_id)
        return {**row, "roles": []}

    def get_lead(ctx, lead_id):
        row = store.find(ctx, "leads", lead_id)
        if row is None:
            _not_found("lead", lead_id)
        return {**row, "estimations": []}

    def _update(table, label):
        def _inner(ctx, entity_id, data):
            row = store.find(ctx, table, entity_id)
            if row is None:
                _not_found(label, entity_id)
            row.update(data)
            return dict(row)
        return _inner

    def _delete(table, label):
        def _inner(ctx, entity_id):
            row = store.find(ctx, table, entity_id)
            if row is None:
                _not_found(label, entity_id)
            store.rows[table].remove(row)
        return _inner

    def create_contact(ctx, data):
        store.next_id += 1
        row = {"id": store.next_id, "agency_id": ctx.require_agency(),
               "assigned_agent_id": None, "display_name": data.get("display_name"),
               "email_normalized": data.get("email_normalized"), "status": "active",
               "notes": None}
        store.rows["contacts"].append(row)
        return dict(row)

    def create_lead(ctx, data):
        if store.find(ctx, "contacts", data["contact_id"]) is None:
            _not_found("contact", data["contact_id"])
        store.next_id += 1
        row = {"id": store.next_id, "agency_id": ctx.require_agency(),
               "assigned_agent_id": None, "contact_id": data["contact_id"],
               "status": "open", "notes": None}
        store.rows["leads"].append(row)
        return dict(row)

    def _assign(table, label):
        def _inner(ctx, entity_id, assigned_agent_id):
            row = store.find(ctx, table, entity_id)
            if row is None:
                _not_found(label, entity_id)
            if assigned_agent_id is not None:
                # Membership is checked against the RECORD's agency (Task 12).
                memberships = {
                    (AGENCY_A, OWNER_A), (AGENCY_A, ADMIN_A),
                    (AGENCY_A, AGENT_A), (AGENCY_A, AGENT_A2),
                    (AGENCY_B, OWNER_B),
                }
                if (row["agency_id"], assigned_agent_id) not in memberships:
                    raise ValidationError(
                        "assigned_agent_id is not an active member of this record's agency"
                    )
            row["assigned_agent_id"] = assigned_agent_id
            return dict(row)
        return _inner

    fakes = {
        "list_contacts": list_contacts,
        "list_leads": list_leads,
        "list_activities": lambda ctx, l, o, c, ld, s: _list_pivot("activities")(ctx, l, o, c, ld, s),
        "list_tasks": _list_pivot("tasks"),
        "get_contact": get_contact,
        "get_lead": get_lead,
        "update_contact": _update("contacts", "contact"),
        "update_lead": _update("leads", "lead"),
        "update_task": _update("tasks", "task"),
        "delete_activity": _delete("activities", "activity"),
        "delete_task": _delete("tasks", "task"),
        "create_contact": create_contact,
        "create_lead": create_lead,
        "create_activity": lambda ctx, data: {"id": 1, "agency_id": ctx.require_agency()},
        "create_task": lambda ctx, data: {"id": 1, "agency_id": ctx.require_agency()},
        "set_contact_assignment": _assign("contacts", "contact"),
        "set_lead_assignment": _assign("leads", "lead"),
        "add_contact_role": lambda ctx, cid, data: (
            store.find(ctx, "contacts", cid) or _not_found("contact", cid)) and {"role": "owner"},
        "delete_contact_role": lambda ctx, cid, role: (
            store.find(ctx, "contacts", cid) or _not_found("contact", cid)) and None,
        "link_stima": lambda ctx, lid, sid, rel: (
            store.find(ctx, "leads", lid) or _not_found("lead", lid)) and {"lead_id": lid},
        "unlink_stima": lambda ctx, lid, sid: (
            store.find(ctx, "leads", lid) or _not_found("lead", lid)) and None,
    }
    for name, function in fakes.items():
        monkeypatch.setattr(core_service.repository, name, function, raising=False)
    return store


@pytest.fixture
def hostile(monkeypatch):
    """A CORE app serving two agencies, with the caller switchable per test."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from core.router import router as core_router
    from operator_auth.dependencies import require_operator

    store = AgencyStore()
    _install_store(monkeypatch, store)
    current = {"ctx": OPERATORS["owner_a"]()}

    app = FastAPI()
    app.include_router(core_router)
    app.dependency_overrides[require_operator] = lambda: current["ctx"]
    client = TestClient(app, raise_server_exceptions=False)

    def as_operator(name):
        current["ctx"] = OPERATORS[name]()
        return client

    return SimpleNamespace(client=client, store=store, as_operator=as_operator)


# ---------------------------------------------------------------------------
# D14 - Task 16: global search and CORE leakage (spec section 12 a/b/c)
# ---------------------------------------------------------------------------

def _leakage_assertions(client) -> None:
    """The three clause-(a) probes, as one reusable body.

    Written as a function so the SearchProbe negative control below can run the
    *same* assertions against a deliberately unscoped repository and prove they
    fail. An assertion set never shown to fail has not been shown to detect.
    """
    # 1. Searching for a B contact's unique email finds nothing.
    body = client.get(f"/api/core/contacts?search={B_ONLY_EMAIL}").json()
    assert body["items"] == [], f"searching a foreign email leaked: {body}"

    # 2. A term matching rows in BOTH agencies returns only this agency's.
    body = client.get(f"/api/core/contacts?search={SHARED_SEARCH_TERM}").json()
    returned = {row["id"] for row in body["items"]}
    assert CONTACT_B not in returned, f"shared search term leaked B: {returned}"
    assert returned == {CONTACT_A}, returned

    # 3. Pivoting leads on a foreign contact id returns empty, not 404
    #    (spec section 12 item 2: an empty list, never an error).
    response = client.get(f"/api/core/leads?contact_id={CONTACT_B}")
    assert response.status_code == 200, response.text
    assert response.json()["items"] == [], response.text


def test_d14_search_does_not_leak_across_agencies(hostile):
    _leakage_assertions(hostile.as_operator("owner_a"))


def test_d14_the_shared_term_really_does_match_both_agencies(hostile):
    """Without this, probe 2 could pass because B simply had no match."""
    client = hostile.as_operator("owner_b")
    body = client.get(f"/api/core/contacts?search={SHARED_SEARCH_TERM}").json()
    assert {row["id"] for row in body["items"]} == {CONTACT_B}


def test_d14_negative_control_a_leaky_search_is_detected(monkeypatch):
    """SearchProbe: a repository whose list_contacts omits the predicate.

    Defined here, in the test module. No production file is modified, and no
    production symbol is patched on disk - the fake is installed into a
    throwaway app for the duration of this test only.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from core.router import router as core_router
    from operator_auth.dependencies import require_operator

    store = AgencyStore()
    _install_store(monkeypatch, store, leaky_search=True)

    app = FastAPI()
    app.include_router(core_router)
    app.dependency_overrides[require_operator] = lambda: OPERATORS["owner_a"]()
    client = TestClient(app, raise_server_exceptions=False)

    with pytest.raises(AssertionError):
        _leakage_assertions(client)


def test_d14_the_agent_narrowing_also_applies_to_search(hostile):
    """An agent's search must not surface a colleague's unassigned contact."""
    client = hostile.as_operator("agent_a")
    body = client.get("/api/core/contacts?search=bianchi").json()
    assert body["items"] == [], body
    body = client.get("/api/core/contacts?search=mario").json()
    assert {row["id"] for row in body["items"]} == {CONTACT_A}


# ===========================================================================
# D15 - Task 19: the full Agency A vs Agency B hostile matrix.
#
# Spec section 15, items 23-46. Every case is driven over HTTP through the real
# router, the real service and the real permission gate.
#
# Two properties get special care because status parity alone would not prove
# them: a refused mutation must leave the row *byte-for-byte unchanged* (27,
# 28, 35), and a cross-agency 404 body must be *byte-identical* to a genuinely
# absent id (45). A scoped UPDATE that quietly matched zero rows and returned
# 200, or a 404 whose detail named the id, would pass a weaker test.
# ===========================================================================

def _snapshot(store: AgencyStore, table: str, entity_id: int) -> dict | None:
    row = store.raw(table, entity_id)
    return dict(row) if row else None


# -- 23, 24: cross-agency list ---------------------------------------------

@pytest.mark.parametrize("caller,visible,hidden", [
    ("owner_a", CONTACT_A, CONTACT_B),
    ("owner_b", CONTACT_B, CONTACT_A),
])
def test_d15_23_contact_lists_never_cross_the_boundary(caller, visible, hidden, hostile):
    body = hostile.as_operator(caller).get("/api/core/contacts").json()
    ids = {row["id"] for row in body["items"]}
    assert visible in ids and hidden not in ids, ids


@pytest.mark.parametrize("caller,visible,hidden", [
    ("owner_a", LEAD_A, LEAD_B),
    ("owner_b", LEAD_B, LEAD_A),
])
def test_d15_24_lead_lists_never_cross_the_boundary(caller, visible, hidden, hostile):
    body = hostile.as_operator(caller).get("/api/core/leads").json()
    ids = {row["id"] for row in body["items"]}
    assert visible in ids and hidden not in ids, ids


# -- 25, 26: cross-agency detail -> 404 ------------------------------------

@pytest.mark.parametrize("path", [
    f"/api/core/contacts/{CONTACT_B}",
    f"/api/core/leads/{LEAD_B}",
])
def test_d15_25_cross_agency_detail_is_404(path, hostile):
    assert hostile.as_operator("owner_a").get(path).status_code == 404


# -- 27, 28: cross-agency PATCH -> 404 AND the row is unmodified -----------

@pytest.mark.parametrize("path,table,entity_id", [
    (f"/api/core/contacts/{CONTACT_B}", "contacts", CONTACT_B),
    (f"/api/core/leads/{LEAD_B}", "leads", LEAD_B),
])
def test_d15_27_a_cross_agency_patch_changes_nothing(path, table, entity_id, hostile):
    before = _snapshot(hostile.store, table, entity_id)
    response = hostile.as_operator("owner_a").patch(path, json={"notes": "hijacked"})
    assert response.status_code == 404, response.text
    after = _snapshot(hostile.store, table, entity_id)
    assert after == before, f"the foreign row changed: {before} -> {after}"


# -- 29, 30: forged agency_id in a body -> 422 -----------------------------

@pytest.mark.parametrize("method,path", [
    ("post", "/api/core/contacts"),
    ("post", "/api/core/leads"),
])
def test_d15_29_a_forged_agency_id_in_a_create_body_is_422(method, path, hostile):
    client = hostile.as_operator("owner_a")
    response = getattr(client, method)(
        path, json={"display_name": "X", "contact_id": CONTACT_A, "agency_id": AGENCY_B}
    )
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("path", [
    f"/api/core/contacts/{CONTACT_A}",
    f"/api/core/leads/{LEAD_A}",
])
def test_d15_30_a_forged_agency_id_in_an_update_body_is_422(path, hostile):
    response = hostile.as_operator("owner_a").patch(path, json={"agency_id": AGENCY_B})
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("field", ["role", "is_platform_admin", "created_by_user_id",
                                   "assigned_agent_id", "whatever"])
def test_d15_30_every_other_forged_field_is_also_422(field, hostile):
    response = hostile.as_operator("owner_a").post(
        "/api/core/contacts", json={"display_name": "X", field: "x"}
    )
    assert response.status_code == 422, (field, response.text)


# -- 31: creating a lead against a foreign contact -------------------------

def test_d15_31_a_lead_on_a_foreign_contact_is_404_and_creates_nothing(hostile):
    client = hostile.as_operator("owner_a")
    before = len(hostile.store.rows["leads"])
    response = client.post("/api/core/leads", json={"contact_id": CONTACT_B})
    assert response.status_code == 404, response.text
    assert len(hostile.store.rows["leads"]) == before


# -- 32: a forged query parameter is ignored -------------------------------

def test_d15_32_a_forged_agency_query_parameter_is_ignored(hostile):
    body = hostile.as_operator("owner_a").get(
        f"/api/core/contacts?agency_id={AGENCY_B}"
    ).json()
    assert CONTACT_B not in {row["id"] for row in body["items"]}


# -- 33, 34: the stima_id pivot ---------------------------------------------

@pytest.mark.parametrize("collection", ["activities", "tasks"])
def test_d15_33_the_stima_pivot_does_not_cross_agencies(collection, hostile):
    body = hostile.as_operator("owner_a").get(
        f"/api/core/{collection}?stima_id=9002"
    ).json()
    assert body["items"] == [], body


# -- 35: cross-agency delete -> 404 and the row survives --------------------

@pytest.mark.parametrize("collection,table,entity_id", [
    ("activities", "activities", ACTIVITY_B),
    ("tasks", "tasks", TASK_B),
])
def test_d15_35_a_cross_agency_delete_is_404_and_the_row_survives(
    collection, table, entity_id, hostile
):
    before = _snapshot(hostile.store, table, entity_id)
    response = hostile.as_operator("owner_a").delete(f"/api/core/{collection}/{entity_id}")
    assert response.status_code == 404, response.text
    assert _snapshot(hostile.store, table, entity_id) == before


# -- 38, 39: the agent narrowing -------------------------------------------

def test_d15_38_an_agent_sees_only_their_own_lead(hostile):
    assert hostile.as_operator("agent_a").get(f"/api/core/leads/{LEAD_A}").status_code == 200
    assert hostile.as_operator("agent_a2").get(f"/api/core/leads/{LEAD_A}").status_code == 404


def test_d15_39_an_unassigned_contact_is_invisible_to_an_agent(hostile):
    path = f"/api/core/contacts/{CONTACT_A_UNASSIGNED}"
    assert hostile.as_operator("agent_a").get(path).status_code == 404
    assert hostile.as_operator("owner_a").get(path).status_code == 200


# -- 40, 41: assignment ----------------------------------------------------

def test_d15_40_an_agent_may_not_assign(hostile):
    response = hostile.as_operator("agent_a").patch(
        f"/api/core/contacts/{CONTACT_A}/assignment", json={"assigned_agent_id": AGENT_A2}
    )
    assert response.status_code == 403, response.text


def test_d15_41_assigning_an_operator_of_another_agency_is_400(hostile):
    response = hostile.as_operator("owner_a").patch(
        f"/api/core/contacts/{CONTACT_A}/assignment", json={"assigned_agent_id": OWNER_B}
    )
    assert response.status_code == 400, response.text
    assert hostile.store.raw("contacts", CONTACT_A)["assigned_agent_id"] == AGENT_A


def test_d15_41_assigning_a_member_of_the_records_agency_succeeds(hostile):
    response = hostile.as_operator("owner_a").patch(
        f"/api/core/contacts/{CONTACT_A}/assignment", json={"assigned_agent_id": AGENT_A2}
    )
    assert response.status_code == 200, response.text
    assert hostile.store.raw("contacts", CONTACT_A)["assigned_agent_id"] == AGENT_A2


def test_d15_41_unassignment_clears_the_agent(hostile):
    response = hostile.as_operator("owner_a").patch(
        f"/api/core/contacts/{CONTACT_A}/assignment", json={"assigned_agent_id": None}
    )
    assert response.status_code == 200, response.text
    assert hostile.store.raw("contacts", CONTACT_A)["assigned_agent_id"] is None


# -- 42: the agency admin --------------------------------------------------

def test_d15_42_an_agency_admin_sees_every_record_of_their_agency(hostile):
    body = hostile.as_operator("admin_a").get("/api/core/contacts").json()
    ids = {row["id"] for row in body["items"]}
    assert ids == {CONTACT_A, CONTACT_A_UNASSIGNED}, ids


def test_d15_42_an_agency_admin_may_assign(hostile):
    response = hostile.as_operator("admin_a").patch(
        f"/api/core/leads/{LEAD_A}/assignment", json={"assigned_agent_id": AGENT_A2}
    )
    assert response.status_code == 200, response.text


# -- 44: the platform admin ------------------------------------------------

def test_d15_44_an_unbound_platform_admin_reads_both_agencies(hostile):
    body = hostile.as_operator("platform_admin").get("/api/core/contacts").json()
    ids = {row["id"] for row in body["items"]}
    assert {CONTACT_A, CONTACT_B} <= ids, ids


def test_d15_44_an_unbound_platform_admin_is_refused_a_generic_create(hostile):
    """Plan section 11.2: no default agency is inferred for a platform write."""
    response = hostile.as_operator("platform_admin").post(
        "/api/core/contacts", json={"display_name": "X"}
    )
    assert response.status_code == 403, response.text


def test_d15_44_a_platform_admin_may_assign_within_the_records_agency(hostile):
    """The agency comes from the record, so no binding is needed."""
    response = hostile.as_operator("platform_admin").patch(
        f"/api/core/contacts/{CONTACT_B}/assignment", json={"assigned_agent_id": OWNER_B}
    )
    assert response.status_code == 200, response.text
    assert hostile.store.raw("contacts", CONTACT_B)["assigned_agent_id"] == OWNER_B


def test_d15_44_a_platform_admin_cannot_assign_across_the_records_agency(hostile):
    """B's record, A's operator: rejected, because the record governs."""
    response = hostile.as_operator("platform_admin").patch(
        f"/api/core/contacts/{CONTACT_B}/assignment", json={"assigned_agent_id": AGENT_A}
    )
    assert response.status_code == 400, response.text


# -- 45: the cross-agency 404 body is byte-identical to an absent one -------

@pytest.mark.parametrize("collection,foreign_id", [
    ("contacts", CONTACT_B),
    ("leads", LEAD_B),
])
def test_d15_45_a_foreign_404_is_byte_identical_to_an_absent_404(
    collection, foreign_id, hostile
):
    """Status parity is not enough: the body must not disclose which it was.

    If the detail named the id, an attacker could not tell existence from
    absence either - but if it named anything about the row, they could. This
    asserts the strongest available form: the raw bytes match.
    """
    client = hostile.as_operator("owner_a")
    foreign = client.get(f"/api/core/{collection}/{foreign_id}")
    absent = client.get(f"/api/core/{collection}/{ABSENT_ID}")

    assert foreign.status_code == absent.status_code == 404
    assert foreign.content == absent.content, (foreign.content, absent.content)


def test_d15_45_the_404_detail_discloses_no_identifier(hostile):
    response = hostile.as_operator("owner_a").get(f"/api/core/contacts/{CONTACT_B}")
    detail = response.json()["detail"]
    assert str(CONTACT_B) not in detail, detail
    assert not any(character.isdigit() for character in detail), detail


# -- the SystemAgencyContext may never act as an operator -------------------

def test_d15_a_system_context_cannot_be_used_as_an_http_caller(monkeypatch):
    """Negative control: if one ever reached a route, it must be powerless."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from core.router import router as core_router
    from operator_auth.dependencies import require_operator

    store = AgencyStore()
    _install_store(monkeypatch, store)

    app = FastAPI()
    app.include_router(core_router)
    app.dependency_overrides[require_operator] = lambda: SystemAgencyContext(
        agency_id=AGENCY_A, origin="public_stima"
    )
    client = TestClient(app, raise_server_exceptions=False)

    # It reads its own agency - it is a legitimate scope - but it can never
    # assign, because assignment authority needs a principal it does not have.
    assert client.get("/api/core/contacts").status_code == 200
    assert client.patch(
        f"/api/core/contacts/{CONTACT_A}/assignment", json={"assigned_agent_id": AGENT_A2}
    ).status_code == 403


def test_d9_the_ctx_free_registry_is_exactly_the_documented_three():
    assert set(CTX_FREE_PUBLIC_FUNCTIONS) == {
        "bridge_public_stima",
        "create_activity_with_cursor",
        "create_task_with_cursor",
    }
