"""P26-6C FLOW - agency immutability, proved against a real PostgreSQL.

These tests do NOT use mocks, fake cursors or SQL string matching. Every one of
them creates rows, issues the statement under test and reads what the database
actually did. The defect this file exists for was invisible to all three of
those cheaper techniques: 054's SQL *looked* correct, its per-table triggers
were installed and armed, and every structural assertion in
tests/test_p26_6c_flow_isolation.py passed - while a single UPDATE on a parent
event moved it to another tenant and left its executions behind.

HOW TO RUN

Two things are required, and database configuration alone is deliberately not
enough:

    P26_RUN_FLOW_LIVE_CERT=1 PYTHONDONTWRITEBYTECODE=1 \
      pytest tests/test_p26_6c_flow_immutability_pg.py

The flag is the opt-in. Without it this module skips even where `DB_NAME` is
set - which is every application host, Render included - so a plain
`pytest tests/` during a full suite never opens a connection here and never
writes a fixture row. Gating on the database configuration alone would have
made the module fire by accident exactly where it is most expensive to be
wrong.

The connection itself is built from the same `DB_HOST` / `DB_PORT` / `DB_NAME`
/ `DB_USER` / `DB_PASSWORD` variables `database.py` uses; `P26_PG_DSN`
overrides them when a DSN is more convenient. Neither is ever logged, echoed or
included in an assertion message.

With the flag missing, or with no database configured, every test in this
module is SKIPPED. A skip here is not a pass: the isolation these tests certify
is simply unproven in that run, and the report must say BLOCKED rather than
green.

SAFETY

Nothing is committed. The module opens one connection, and each test runs
inside a SAVEPOINT that is rolled back in teardown; the connection itself is
rolled back and closed at the end. Fixture rows use explicit NEGATIVE ids so
they cannot collide with real data and cannot consume a sequence value - no
nextval, no setval, nothing persistent.

The database guard here is an EXACT name match on `stima360_db_test`, which is
stricter than the migration runner's own rule. `scripts/p26_migrate.py` accepts
any name carrying the TEST marker and merely refuses the known production
names; that is right for a runner that has to serve several test databases.
This module writes fixture rows, so it names the one database it is allowed to
write to and refuses everything else, including another legitimate test
database.
"""
from __future__ import annotations

import os
import uuid

import pytest

psycopg2 = pytest.importorskip("psycopg2")
from psycopg2 import errors as pg_errors  # noqa: E402
from psycopg2.extras import RealDictCursor  # noqa: E402

# The exact - and only - database this module may write to.
REQUIRED_DATABASE = "stima360_db_test"

# The explicit opt-in. Database configuration is NOT sufficient on its own.
RUN_FLAG = "P26_RUN_FLOW_LIVE_CERT"


def _connect_kwargs():
    """The connection, from P26_PG_DSN or from the application's own DB_* set.

    Returns None when neither is configured. Nothing here is ever printed: the
    caller passes the result straight to `psycopg2.connect`.
    """
    dsn = os.getenv("P26_PG_DSN")
    if dsn:
        return {"dsn": dsn}
    if os.getenv("DB_NAME"):
        return {
            "host": os.getenv("DB_HOST"),
            "port": os.getenv("DB_PORT"),
            "dbname": os.getenv("DB_NAME"),
            "user": os.getenv("DB_USER"),
            "password": os.getenv("DB_PASSWORD"),
        }
    return None


def _live_cert_gate():
    """(enabled, reason). Both conditions must hold, and the flag comes first.

    Every application host carries DB_NAME, so gating on configuration alone
    would arm this module during an ordinary `pytest tests/` run - opening a
    connection and writing fixture rows on whatever database happened to be
    configured. The flag makes running it a decision rather than a side effect.
    """
    if os.getenv(RUN_FLAG) != "1":
        return False, (
            f"{RUN_FLAG}=1 is not set - the live certification is opt-in and "
            "does not run during an ordinary suite. Report this as BLOCKED, "
            "never as PASS."
        )
    if _connect_kwargs() is None:
        return False, (
            "neither P26_PG_DSN nor DB_NAME is set - no PostgreSQL to prove "
            "agency immutability against. Report this as BLOCKED, never as PASS."
        )
    return True, ""


LIVE_CERT_ENABLED, LIVE_CERT_SKIP_REASON = _live_cert_gate()

# Resolved only when the gate is open, so a disabled run holds no connection
# parameters at all.
CONNECT_KWARGS = _connect_kwargs() if LIVE_CERT_ENABLED else None

pytestmark = pytest.mark.skipif(not LIVE_CERT_ENABLED, reason=LIVE_CERT_SKIP_REASON)

# Negative ids: outside every sequence, impossible to collide with real rows,
# and obvious in a dump if a rollback were ever missed.
AGENCY_A = -9101
AGENCY_B = -9102
EVENT = -9201
EVENT_B = -9202
EVENT_ORPHAN = -9203      # no execution ever points at it
EXEC_PARENT = -9301
EXEC_RETRY = -9302
EXEC_B = -9303
EXEC_ORPHAN = -9304       # no retry, no action record
SUPPRESSION = -9401
ACTION = -9501
RULE = -9601


# ---------------------------------------------------------------------------
# pg_trigger.tgtype, decoded once.
#
# From PostgreSQL's pg_trigger.h:
#
#     ROW = 1, BEFORE = 2, INSERT = 4, DELETE = 8, UPDATE = 16,
#     TRUNCATE = 32, INSTEAD = 64
#
# BEFORE is a bit that is SET. There is no AFTER bit: AFTER is the absence of
# both BEFORE and INSTEAD. The first version of this file asserted
# `not tgtype & 2` for "must fire BEFORE", which is the exact inverse - it
# would have accepted an AFTER trigger and rejected the correct one. An AFTER
# trigger cannot refuse a write at all, so that mistake would have certified a
# guard that does nothing. `test_the_tgtype_helpers_reject_an_after_trigger`
# below is the regression for it.
# ---------------------------------------------------------------------------

TRIGGER_TYPE_ROW = 1
TRIGGER_TYPE_BEFORE = 2
TRIGGER_TYPE_INSERT = 4
TRIGGER_TYPE_UPDATE = 16
TRIGGER_TYPE_INSTEAD = 64


def _is_row_level(tgtype):
    return bool(tgtype & TRIGGER_TYPE_ROW)


def _is_before(tgtype):
    """BEFORE, and specifically not INSTEAD OF."""
    return bool(tgtype & TRIGGER_TYPE_BEFORE) and not tgtype & TRIGGER_TYPE_INSTEAD


def _covers_insert(tgtype):
    return bool(tgtype & TRIGGER_TYPE_INSERT)


def _covers_update(tgtype):
    return bool(tgtype & TRIGGER_TYPE_UPDATE)


def _raised_by_our_guard(excinfo, *fragments):
    """The refusal must come from the guard under test, not from a typo.

    Checks both the SQLSTATE - P0001, which is what PL/pgSQL `RAISE EXCEPTION`
    produces - and a fragment of the message, so a foreign key violation or an
    undefined column cannot be mistaken for the isolation working.
    """
    error = excinfo.value
    assert isinstance(error, pg_errors.RaiseException), (
        f"expected a PL/pgSQL RAISE (SQLSTATE P0001), got "
        f"{type(error).__name__} / {getattr(error, 'pgcode', None)}: {error}"
    )
    assert error.pgcode == "P0001", error.pgcode
    message = str(error)
    for fragment in fragments:
        assert fragment in message, (fragment, message)


@pytest.fixture(scope="module")
def connection():
    """One connection, never committed, and only ever to `stima360_db_test`.

    The name is compared exactly. A substring test - `"test" in name` - was the
    first version and is too weak for a module that writes rows: it would have
    accepted `stima360_db_test_restore`, a colleague's `my_test_db`, or a
    staging database that happened to carry the word.
    """
    assert LIVE_CERT_ENABLED and CONNECT_KWARGS is not None, (
        "the live certification fixture was reached with the gate closed"
    )
    conn = psycopg2.connect(**CONNECT_KWARGS)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            database = cur.fetchone()[0]
        assert database == REQUIRED_DATABASE, (
            f"refusing to run against {database!r}: this module writes fixture "
            f"rows and may only be pointed at {REQUIRED_DATABASE!r}"
        )
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def db(connection):
    """One savepoint per test, rolled back whatever happens.

    A savepoint rather than a transaction so a statement that raises - which is
    most of these tests - can be recovered from and the fixture teardown can
    still run.
    """
    name = f"p26_6c_{uuid.uuid4().hex[:12]}"
    cur = connection.cursor(cursor_factory=RealDictCursor)
    cur.execute(f"SAVEPOINT {name}")
    try:
        _seed(cur)
        yield cur
    finally:
        cur.execute(f"ROLLBACK TO SAVEPOINT {name}")
        cur.execute(f"RELEASE SAVEPOINT {name}")
        cur.close()


def _seed(cur):
    """Two agencies, one rule, and one row in each FLOW table for agency A."""
    cur.execute(
        "INSERT INTO agencies (id, name, slug, status) VALUES "
        "(%s,'P26 6C A','p26-6c-a','active'),(%s,'P26 6C B','p26-6c-b','active')",
        (AGENCY_A, AGENCY_B),
    )
    cur.execute(
        """INSERT INTO flow_rules
             (id, code, code_version, name, event_type, entity_type, is_active,
              priority, cooldown_minutes, parameters, default_parameters,
              allowed_parameters, last_simulation_status)
           VALUES (%s, %s, 1, 'P26-6C fixture', 'core.lead_created', 'lead',
                   FALSE, 'normal', 0, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb,
                   'never_run')""",
        (RULE, f"P26-6C-{uuid.uuid4().hex[:8]}"),
    )
    cur.execute(
        """INSERT INTO flow_events
             (id, agency_id, event_type, entity_type, entity_id, source_module,
              payload, deduplication_key, status)
           VALUES (%s,%s,'core.lead_created','lead',1,'core','{}'::jsonb,%s,'received'),
                  (%s,%s,'core.lead_created','lead',2,'core','{}'::jsonb,%s,'received'),
                  (%s,%s,'core.lead_created','lead',3,'core','{}'::jsonb,%s,'received')""",
        (EVENT, AGENCY_A, f"p26-6c-{uuid.uuid4().hex}",
         EVENT_B, AGENCY_B, f"p26-6c-{uuid.uuid4().hex}",
         EVENT_ORPHAN, AGENCY_A, f"p26-6c-{uuid.uuid4().hex}"),
    )
    for execution_id, agency_id, event_id in (
        (EXEC_PARENT, AGENCY_A, EVENT),
        (EXEC_B, AGENCY_B, EVENT_B),
    ):
        cur.execute(
            """INSERT INTO flow_executions
                 (id, agency_id, event_id, rule_id, entity_type, entity_id,
                  execution_mode, status, rule_version, parameters_hash)
               VALUES (%s,%s,%s,%s,'lead',1,'live','matched',1,'h')""",
            (execution_id, agency_id, event_id, RULE),
        )
    cur.execute(
        """INSERT INTO flow_executions
             (id, agency_id, event_id, rule_id, entity_type, entity_id,
              execution_mode, status, rule_version, parameters_hash,
              retry_of_execution_id)
           VALUES (%s,%s,%s,%s,'lead',1,'live','matched',1,'h',%s)""",
        (EXEC_RETRY, AGENCY_A, EVENT, RULE, EXEC_PARENT),
    )
    cur.execute(
        """INSERT INTO flow_executions
             (id, agency_id, rule_id, entity_type, entity_id,
              execution_mode, status, rule_version, parameters_hash)
           VALUES (%s,%s,%s,'lead',9,'live','matched',1,'h')""",
        (EXEC_ORPHAN, AGENCY_A, RULE),
    )
    cur.execute(
        """INSERT INTO flow_suppressions
             (id, agency_id, rule_id, entity_type, entity_id, reason)
           VALUES (%s,%s,%s,'lead',1,'fixture')""",
        (SUPPRESSION, AGENCY_A, RULE),
    )
    cur.execute(
        """INSERT INTO flow_action_records
             (id, execution_id, action_type, target_module, idempotency_key,
              payload, status)
           VALUES (%s,%s,'create_core_task','core',%s,'{}'::jsonb,'pending')""",
        (ACTION, EXEC_PARENT, f"p26-6c-{uuid.uuid4().hex}"),
    )


# ---------------------------------------------------------------------------
# The defect, reproduced and then refused.
# ---------------------------------------------------------------------------

def test_parent_event_agency_change_is_refused(db):
    """The exact statement the live hostile probe found accepted.

    Before 055 this UPDATE succeeded and left EXEC_PARENT and EXEC_RETRY -
    both agency A - attached to an event that had become agency B's.
    """
    with pytest.raises(Exception) as excinfo:
        db.execute(
            "UPDATE flow_events SET agency_id = %s WHERE id = %s",
            (AGENCY_B, EVENT),
        )
    _raised_by_our_guard(excinfo, "flow_events.agency_id is immutable")


def test_parent_execution_of_a_retry_cannot_change_agency(db):
    """One link over: EXEC_RETRY points at EXEC_PARENT."""
    with pytest.raises(Exception) as excinfo:
        db.execute(
            "UPDATE flow_executions SET agency_id = %s WHERE id = %s",
            (AGENCY_B, EXEC_PARENT),
        )
    _raised_by_our_guard(excinfo, "flow_executions.agency_id is immutable")


def test_execution_with_an_action_record_cannot_change_agency(db):
    """`flow_action_records` has no tenant of its own - it is a join away.

    Moving the execution would have moved every action record under it without
    touching those rows at all.
    """
    db.execute("SELECT COUNT(*) AS n FROM flow_action_records WHERE execution_id = %s",
               (EXEC_PARENT,))
    assert db.fetchone()["n"] == 1, "fixture did not attach an action record"

    with pytest.raises(Exception) as excinfo:
        db.execute(
            "UPDATE flow_executions SET agency_id = %s WHERE id = %s",
            (AGENCY_B, EXEC_PARENT),
        )
    _raised_by_our_guard(excinfo, "flow_executions.agency_id is immutable")


@pytest.mark.parametrize(
    "table,row_id",
    [
        ("flow_events", EVENT_B),
        ("flow_executions", EXEC_B),
        ("flow_suppressions", SUPPRESSION),
    ],
)
def test_immutability_holds_even_with_no_children(db, table, row_id):
    """Immutability is a property of the row, not of whether anyone depends on it.

    EVENT_B and EXEC_B have no children; the suppression never has any. A guard
    that only fired when a child existed would be a guard with a gap.
    """
    target = AGENCY_A if row_id in (EVENT_B, EXEC_B) else AGENCY_B
    with pytest.raises(Exception) as excinfo:
        db.execute(
            f"UPDATE {table} SET agency_id = %s WHERE id = %s", (target, row_id)
        )
    _raised_by_our_guard(excinfo, f"{table}.agency_id is immutable")


# ---------------------------------------------------------------------------
# 054's checks must survive 055 untouched.
# ---------------------------------------------------------------------------

def test_cross_agency_execution_insert_is_still_refused(db):
    with pytest.raises(Exception) as excinfo:
        db.execute(
            """INSERT INTO flow_executions
                 (id, agency_id, event_id, rule_id, entity_type, entity_id,
                  execution_mode, status, rule_version, parameters_hash)
               VALUES (-9310,%s,%s,%s,'lead',1,'live','matched',1,'h')""",
            (AGENCY_B, EVENT, RULE),
        )
    _raised_by_our_guard(excinfo, "does not match event")


def test_cross_agency_retry_insert_is_still_refused(db):
    with pytest.raises(Exception) as excinfo:
        db.execute(
            """INSERT INTO flow_executions
                 (id, agency_id, event_id, rule_id, entity_type, entity_id,
                  execution_mode, status, rule_version, parameters_hash,
                  retry_of_execution_id)
               VALUES (-9311,%s,%s,%s,'lead',1,'live','matched',1,'h',%s)""",
            (AGENCY_B, EVENT_B, RULE, EXEC_PARENT),
        )
    _raised_by_our_guard(excinfo, "does not match retried execution")


def test_repointing_a_link_across_agencies_is_still_refused(db):
    """An UPDATE that leaves `agency_id` alone and moves the link instead.

    Immutability does not cover this, and must not be allowed to hide it: the
    row stays in agency A while pointing at agency B's event.
    """
    with pytest.raises(Exception) as excinfo:
        db.execute(
            "UPDATE flow_executions SET event_id = %s WHERE id = %s",
            (EVENT_B, EXEC_PARENT),
        )
    _raised_by_our_guard(excinfo, "does not match event")


@pytest.mark.parametrize(
    "table,row_id",
    [("flow_events", EVENT), ("flow_executions", EXEC_PARENT),
     ("flow_suppressions", SUPPRESSION)],
)
def test_a_null_agency_is_still_refused(db, table, row_id):
    """NOT NULL would catch this too; the trigger is asserted separately so a
    later relaxation of the column does not silently remove the guard."""
    with pytest.raises(Exception) as excinfo:
        db.execute(f"UPDATE {table} SET agency_id = NULL WHERE id = %s", (row_id,))
    error = excinfo.value
    assert isinstance(error, (pg_errors.RaiseException, pg_errors.NotNullViolation)), (
        type(error).__name__, error,
    )


# ---------------------------------------------------------------------------
# What must keep working.
# ---------------------------------------------------------------------------

def test_an_update_that_keeps_the_same_agency_is_allowed(db):
    """`IS DISTINCT FROM`, not `<>`: rewriting the same value is not a change."""
    db.execute(
        "UPDATE flow_events SET agency_id = %s, status = 'processed' WHERE id = %s",
        (AGENCY_A, EVENT),
    )
    db.execute("SELECT agency_id, status FROM flow_events WHERE id = %s", (EVENT,))
    row = db.fetchone()
    assert row["agency_id"] == AGENCY_A and row["status"] == "processed"


def test_the_runtimes_own_execution_update_still_works(db):
    """`execute_live` rewrites the row without naming agency_id at all.

    This is the statement the runtime depends on. A guard implemented with `<>`
    instead of `IS DISTINCT FROM`, or one that fired on any UPDATE, would have
    broken every live FLOW execution.
    """
    db.execute(
        "UPDATE flow_executions SET status='executed', completed_at=NOW() "
        "WHERE id = %s RETURNING agency_id, status",
        (EXEC_PARENT,),
    )
    row = db.fetchone()
    assert row["agency_id"] == AGENCY_A and row["status"] == "executed"


def test_an_action_record_is_derived_from_its_execution(db):
    """Its tenant is a join, and the join answers agency A."""
    db.execute(
        """SELECT x.agency_id
             FROM flow_action_records a
             JOIN flow_executions x ON x.id = a.execution_id
            WHERE a.id = %s""",
        (ACTION,),
    )
    assert db.fetchone()["agency_id"] == AGENCY_A


def test_an_action_record_may_be_repointed_within_its_agency(db):
    """Recovery re-points `execution_id`; that must keep working."""
    db.execute(
        "UPDATE flow_action_records SET execution_id = %s, status = 'completed' "
        "WHERE id = %s RETURNING execution_id",
        (EXEC_RETRY, ACTION),
    )
    assert db.fetchone()["execution_id"] == EXEC_RETRY


def test_an_action_record_cannot_be_repointed_across_agencies(db):
    """The gap immutability alone would have left open.

    `execution_id` stays mutable by design, so the guard here is agreement
    rather than immutability: a re-point may happen, inside one tenant.
    """
    with pytest.raises(Exception) as excinfo:
        db.execute(
            "UPDATE flow_action_records SET execution_id = %s WHERE id = %s",
            (EXEC_B, ACTION),
        )
    _raised_by_our_guard(excinfo, "would move record")


# ---------------------------------------------------------------------------
# Schema semantics 055 must not have disturbed.
# ---------------------------------------------------------------------------

def test_the_existing_foreign_keys_and_on_delete_semantics_survive(db):
    """052's RESTRICT to agencies, and 008's SET NULL / CASCADE on the links."""
    db.execute(
        """SELECT c.conname, c.confdeltype, t.relname AS table_name
             FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid
            WHERE c.contype = 'f'
              AND t.relname IN ('flow_events','flow_executions','flow_suppressions',
                                'flow_action_records')"""
    )
    rules = {(r["table_name"], r["conname"]): r["confdeltype"] for r in db.fetchall()}

    agency_fks = {k: v for k, v in rules.items() if k[1].endswith("_agency_id_fk")}
    assert len(agency_fks) == 3, agency_fks
    assert set(agency_fks.values()) == {"r"}, agency_fks  # RESTRICT

    execution_fk = [v for k, v in rules.items()
                    if k[0] == "flow_action_records" and "execution" in k[1]]
    assert execution_fk == ["c"], execution_fk  # CASCADE

    # An agency that still owns FLOW rows cannot be deleted.
    with pytest.raises(pg_errors.ForeignKeyViolation):
        db.execute("DELETE FROM agencies WHERE id = %s", (AGENCY_A,))


def test_the_four_guards_are_installed_and_armed(db):
    db.execute(
        """SELECT t.tgname, c.relname AS table_name, t.tgtype, t.tgenabled,
                  p.proname AS function_name, n.nspname AS function_schema
             FROM pg_trigger t
             JOIN pg_class c ON c.oid = t.tgrelid
             JOIN pg_proc p ON p.oid = t.tgfoid
             JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE NOT t.tgisinternal
              AND t.tgname IN ('trg_flow_event_agency_integrity',
                               'trg_flow_execution_agency_integrity',
                               'trg_flow_suppression_agency_integrity',
                               'trg_flow_action_record_agency_integrity')"""
    )
    found = {r["tgname"]: r for r in db.fetchall()}
    assert len(found) == 4, sorted(found)

    expected_function = {
        "trg_flow_event_agency_integrity": ("flow_events", "flow_event_agency_integrity"),
        "trg_flow_execution_agency_integrity": ("flow_executions", "flow_execution_agency_integrity"),
        "trg_flow_suppression_agency_integrity": ("flow_suppressions", "flow_suppression_agency_integrity"),
        "trg_flow_action_record_agency_integrity": ("flow_action_records", "flow_action_record_agency_integrity"),
    }
    for name, row in found.items():
        table, function = expected_function[name]
        assert row["table_name"] == table, (name, row["table_name"])
        assert row["function_name"] == function, (name, row["function_name"])
        assert row["function_schema"] == "public", (name, row["function_schema"])
        assert row["tgenabled"] == "O", (name, row["tgenabled"])
        assert _is_row_level(row["tgtype"]), name
        # BEFORE is a bit that is SET. There is no AFTER bit - AFTER is the
        # absence of BEFORE and INSTEAD - so this is `!= 0`, not `== 0`.
        assert _is_before(row["tgtype"]), f"{name} must fire BEFORE"
        assert _covers_update(row["tgtype"]), f"{name} must cover UPDATE"

    # The three ROOT guards cover INSERT as well; the action-record guard must
    # NOT, because a new row names its execution for the first time.
    for name in expected_function:
        covers_insert = _covers_insert(found[name]["tgtype"])
        if name == "trg_flow_action_record_agency_integrity":
            assert not covers_insert, f"{name} must not fire on INSERT"
        else:
            assert covers_insert, f"{name} must cover INSERT"


# ---------------------------------------------------------------------------
# Concurrency.
# ---------------------------------------------------------------------------

def test_a_parent_agency_change_is_refused_after_a_child_insert(db):
    """SEQUENTIAL, in one transaction. This is NOT a concurrency test.

    It inserts a child and then attempts the parent UPDATE in the same session,
    and asserts the UPDATE is refused. No second connection is opened and no
    interleaving is exercised, so nothing here observes a race.

    What makes the race irrelevant is an argument, not this test: under READ
    COMMITTED a child INSERT reads the parent's agency and a concurrent parent
    UPDATE could commit between that read and the child's commit, so a
    "re-check the children when the parent changes" design would need
    `SELECT ... FOR SHARE` on the parent. Immutability removes the second half
    of that interleaving entirely - the parent UPDATE is refused unconditionally,
    whatever else is in flight - so there is no window to lose. A true
    concurrency proof would need two sessions and is not attempted here.
    """
    db.execute(
        """INSERT INTO flow_executions
             (id, agency_id, event_id, rule_id, entity_type, entity_id,
              execution_mode, status, rule_version, parameters_hash)
           VALUES (-9320,%s,%s,%s,'lead',1,'live','matched',1,'h')""",
        (AGENCY_A, EVENT, RULE),
    )
    with pytest.raises(Exception) as excinfo:
        db.execute(
            "UPDATE flow_events SET agency_id = %s WHERE id = %s", (AGENCY_B, EVENT)
        )
    _raised_by_our_guard(excinfo, "flow_events.agency_id is immutable")


# ---------------------------------------------------------------------------
# Cases the first draft left out.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "table,row_id,label",
    [
        ("flow_events", EVENT_ORPHAN, "an event nothing points at"),
        ("flow_executions", EXEC_ORPHAN, "an execution with no retry and no action record"),
    ],
)
def test_immutability_holds_for_a_row_with_genuinely_no_children(db, table, row_id, label):
    """The first draft used EVENT_B and EXEC_B for this, and both HAVE children.

    EVENT_B is EXEC_B's parent; EXEC_B has an event. So that test proved the
    same thing as the ones above it. EVENT_ORPHAN and EXEC_ORPHAN exist only
    for this case: nothing references them, and the guard must still refuse -
    immutability is a property of the row, not of who depends on it.
    """
    db.execute(
        f"SELECT COUNT(*) AS n FROM flow_executions WHERE "
        f"{'event_id' if table == 'flow_events' else 'retry_of_execution_id'} = %s",
        (row_id,),
    )
    assert db.fetchone()["n"] == 0, f"{label} is not actually childless"
    if table == "flow_executions":
        db.execute("SELECT COUNT(*) AS n FROM flow_action_records WHERE execution_id = %s",
                   (row_id,))
        assert db.fetchone()["n"] == 0, f"{label} has an action record"

    with pytest.raises(Exception) as excinfo:
        db.execute(
            f"UPDATE {table} SET agency_id = %s WHERE id = %s", (AGENCY_B, row_id)
        )
    _raised_by_our_guard(excinfo, f"{table}.agency_id is immutable")


def test_repointing_a_retry_across_agencies_is_refused(db):
    """The retry counterpart of the event-repoint test.

    The row keeps its own agency and moves the link instead, so immutability
    does not cover it - 054's retry check does, and 055 must not have lost it.
    """
    with pytest.raises(Exception) as excinfo:
        db.execute(
            "UPDATE flow_executions SET retry_of_execution_id = %s WHERE id = %s",
            (EXEC_B, EXEC_RETRY),
        )
    _raised_by_our_guard(excinfo, "does not match retried execution")


# The three NULL-agency INSERTs, as (label, sql, params).
#
# The first version built these with `values.format(rule=RULE)`. The
# flow_events row contains `'{}'::jsonb`, and str.format reads `{}` as a
# positional field - so it raised IndexError before any statement reached the
# database, and the test would have "passed" on the wrong exception if it had
# been written to expect one. Everything variable is a bound parameter now, and
# no SQL string here goes through `.format`.
NULL_AGENCY_INSERTS = (
    (
        "flow_events",
        """INSERT INTO flow_events
             (id, agency_id, event_type, entity_type, entity_id, source_module,
              payload, deduplication_key, status)
           VALUES (%s, NULL, 'core.lead_created', 'lead', 1, 'core',
                   '{}'::jsonb, %s, 'received')""",
        (-9210, "p26-6c-null-event"),
    ),
    (
        "flow_executions",
        """INSERT INTO flow_executions
             (id, agency_id, rule_id, entity_type, entity_id, execution_mode,
              status, rule_version, parameters_hash)
           VALUES (%s, NULL, %s, 'lead', 1, 'live', 'matched', 1, 'h')""",
        (-9330, RULE),
    ),
    (
        "flow_suppressions",
        """INSERT INTO flow_suppressions
             (id, agency_id, rule_id, entity_type, entity_id, reason)
           VALUES (%s, NULL, %s, 'lead', 5, 'null-probe')""",
        (-9410, RULE),
    ),
)


@pytest.mark.parametrize(
    "table,sql,params", NULL_AGENCY_INSERTS, ids=[c[0] for c in NULL_AGENCY_INSERTS]
)
def test_inserting_a_null_agency_is_refused(db, table, sql, params):
    """The first draft only tried NULL on UPDATE.

    NOT NULL catches this too, so the accepted refusal is either the column
    constraint or the trigger - what must not happen is the row landing.
    """
    with pytest.raises(Exception) as excinfo:
        db.execute(sql, params)
    error = excinfo.value
    assert isinstance(error, (pg_errors.RaiseException, pg_errors.NotNullViolation)), (
        type(error).__name__, error,
    )


# ---------------------------------------------------------------------------
# ON DELETE semantics, exercised rather than read from the catalogue.
# ---------------------------------------------------------------------------

def test_on_delete_set_null_still_works_through_the_new_guard(db):
    """Deleting an event UPDATEs its executions, which fires the trigger.

    `flow_executions.event_id` is ON DELETE SET NULL, so the cascade rewrites
    every child row - through the immutability guard. The agency is unchanged,
    so it must pass. A guard that fired on any UPDATE, or that compared the
    wrong pair, would break foreign-key cascade semantics here.
    """
    db.execute("DELETE FROM flow_executions WHERE id IN (%s,%s)", (EXEC_RETRY, EXEC_PARENT))
    db.execute("DELETE FROM flow_events WHERE id = %s", (EVENT,))
    db.execute("SELECT COUNT(*) AS n FROM flow_events WHERE id = %s", (EVENT,))
    assert db.fetchone()["n"] == 0


def test_on_delete_set_null_blanks_the_child_link_and_keeps_its_agency(db):
    """The same cascade, observed on a surviving child."""
    db.execute("DELETE FROM flow_action_records WHERE execution_id = %s", (EXEC_PARENT,))
    db.execute("DELETE FROM flow_executions WHERE id = %s", (EXEC_RETRY,))
    db.execute("DELETE FROM flow_events WHERE id = %s", (EVENT,))

    db.execute("SELECT event_id, agency_id FROM flow_executions WHERE id = %s",
               (EXEC_PARENT,))
    row = db.fetchone()
    assert row["event_id"] is None, "ON DELETE SET NULL did not blank the link"
    assert row["agency_id"] == AGENCY_A, "the child's tenant changed with the cascade"


def test_on_delete_cascade_removes_the_action_records(db):
    """`flow_action_records.execution_id` is NOT NULL ON DELETE CASCADE.

    The action-record guard is BEFORE UPDATE only, so a cascade DELETE does not
    fire it - which is what lets the cascade work at all.
    """
    db.execute("SELECT COUNT(*) AS n FROM flow_action_records WHERE execution_id = %s",
               (EXEC_PARENT,))
    assert db.fetchone()["n"] == 1

    db.execute("DELETE FROM flow_executions WHERE id = %s", (EXEC_RETRY,))
    db.execute("DELETE FROM flow_executions WHERE id = %s", (EXEC_PARENT,))

    db.execute("SELECT COUNT(*) AS n FROM flow_action_records WHERE id = %s", (ACTION,))
    assert db.fetchone()["n"] == 0, "CASCADE did not remove the action record"
