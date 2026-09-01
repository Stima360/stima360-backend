"""P18-B repository tests.

Follows the exact convention already used for seller_intelligence
(tests/test_seller_intelligence_repository.py) and for FLOW itself
(tests/test_next6_p2b_flow_automation.py): no real database connection.

Two independent fakes are monkeypatched:
- followup.repository.followup_cursor -> an in-memory FakeDatabase that
  understands only the followup_actions / tasks.metadata SQL shapes this
  repository actually emits.
- followup.repository.core_repository.create_task_with_cursor -> a plain
  Python fake, exactly the technique tests/test_next6_p2b_flow_automation.py
  already uses to test FLOW's own call into the same CORE function. CORE's
  own task-creation internals (FK validation, INSERT shape, tasks_reference_chk)
  are already covered by CORE's own test suite; this file tests followup's
  orchestration around that call, not CORE's guts.
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from followup import repository
from followup.exceptions import ConflictError


class FakeCursor:
    def __init__(self, database):
        self.database = database
        self.rows = []

    def execute(self, query, params=None):
        sql = " ".join(str(query).split()).lower()
        self.database.sql.append((sql, params))

        if "insert into followup_actions" in sql:
            self._handle_insert(params)
            return

        if "from followup_actions where idempotency_key" in sql:
            key = params[0] if not isinstance(params, dict) else params["idempotency_key"]
            match = next(
                (r for r in self.database.actions if r["idempotency_key"] == key),
                None,
            )
            self.rows = [copy.deepcopy(match)] if match else []
            return

        if sql.startswith("update followup_actions"):
            self._handle_update(sql, params)
            return

        if "select id from tasks where metadata->>'idempotency_key'" in sql:
            key = params[0]
            match = next(
                (t for t in self.database.tasks if t["metadata"].get("idempotency_key") == key),
                None,
            )
            self.rows = [{"id": match["id"]}] if match else []
            return

        raise AssertionError(f"unexpected followup SQL: {sql}")

    def _handle_insert(self, params):
        idempotency_key = params["idempotency_key"]
        existing = next(
            (r for r in self.database.actions if r["idempotency_key"] == idempotency_key),
            None,
        )
        if existing is not None:
            self.rows = []  # ON CONFLICT DO NOTHING
            return
        row = {
            "id": self.database.next_action_id,
            "rule_code": params["rule_code"],
            "trigger_type": params["trigger_type"],
            "contact_id": params["contact_id"],
            "lead_id": params["lead_id"],
            "stima_id": params["stima_id"],
            "idempotency_key": idempotency_key,
            "task_id": None,
            "status": "pending",
            "error_message": None,
            "created_at": datetime.now(timezone.utc),
        }
        self.database.next_action_id += 1
        self.database.actions.append(row)
        self.rows = [copy.deepcopy(row)]

    def _handle_update(self, sql, params):
        if "status = 'completed'" in sql:
            task_id, action_id = params
            for row in self.database.actions:
                if row["id"] == action_id:
                    row["status"] = "completed"
                    row["task_id"] = task_id
                    row["error_message"] = None
            self.rows = []
            return
        if "status = 'failed'" in sql:
            error_message, action_id = params
            for row in self.database.actions:
                if row["id"] == action_id and row["status"] == "pending":
                    row["status"] = "failed"
                    row["error_message"] = error_message
            self.rows = []
            return
        raise AssertionError(f"unexpected UPDATE followup_actions: {sql}")

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class FakeDatabase:
    def __init__(self):
        self.actions = []
        self.tasks = []
        self.next_action_id = 1
        self.sql = []
        self.commits = 0

    @contextmanager
    def cursor(self, *, commit=False):
        yield self, FakeCursor(self)
        if commit:
            self.commits += 1


@pytest.fixture
def fake_db(monkeypatch):
    database = FakeDatabase()
    monkeypatch.setattr(repository, "followup_cursor", database.cursor)
    return database


@pytest.fixture
def fake_create_task(monkeypatch, fake_db):
    calls = []

    def _create(cur, data):
        new_id = len(fake_db.tasks) + 1
        row = {"id": new_id, **data}
        fake_db.tasks.append(row)
        calls.append(data)
        return {"id": new_id}

    monkeypatch.setattr(repository.core_repository, "create_task_with_cursor", _create)
    return calls


def _kwargs(**overrides):
    base = dict(
        rule_code="FOLLOWUP_STIMA_RICHIESTA",
        trigger_type="event",
        idempotency_key="followup:stima_richiesta:501",
        contact_id=16,
        lead_id=12,
        stima_id=501,
        task_title="Contattare proprietario",
        task_description=None,
        task_type="automated_followup",
        priority="high",
        due_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        created_by="FOLLOWUP",
    )
    base.update(overrides)
    return base


# --- happy path --------------------------------------------------------------

def test_creates_one_task_and_marks_action_completed(fake_db, fake_create_task):
    result = repository.execute_followup_action(**_kwargs())

    assert result["status"] == "completed"
    assert result["task_id"] == 1
    assert len(fake_db.tasks) == 1
    assert len(fake_db.actions) == 1
    assert fake_db.actions[0]["status"] == "completed"
    assert fake_db.actions[0]["task_id"] == 1


def test_task_metadata_is_tagged_with_source_rule_and_idempotency_key(fake_db, fake_create_task):
    repository.execute_followup_action(**_kwargs())

    created = fake_create_task[0]
    assert created["metadata"] == {
        "source": "followup",
        "rule_code": "FOLLOWUP_STIMA_RICHIESTA",
        "idempotency_key": "followup:stima_richiesta:501",
        "trigger_type": "event",
    }


def test_task_fields_match_the_rule(fake_db, fake_create_task):
    repository.execute_followup_action(**_kwargs())

    created = fake_create_task[0]
    assert created["title"] == "Contattare proprietario"
    assert created["task_type"] == "automated_followup"
    assert created["priority"] == "high"
    assert created["status"] == "open"
    assert created["assigned_to"] is None
    assert created["contact_id"] == 16
    assert created["lead_id"] == 12
    assert created["stima_id"] == 501


# --- idempotency ---------------------------------------------------------------

def test_second_call_with_same_idempotency_key_does_not_create_a_second_task(fake_db, fake_create_task):
    first = repository.execute_followup_action(**_kwargs())
    second = repository.execute_followup_action(**_kwargs())

    assert second["status"] == "already_completed"
    assert second["task_id"] == first["task_id"]
    assert len(fake_db.tasks) == 1
    assert len(fake_db.actions) == 1


def test_different_idempotency_key_creates_a_second_independent_task(fake_db, fake_create_task):
    repository.execute_followup_action(**_kwargs())
    repository.execute_followup_action(**_kwargs(
        idempotency_key="followup:stima_richiesta:502", stima_id=502,
    ))

    assert len(fake_db.tasks) == 2
    assert len(fake_db.actions) == 2


def test_pending_prior_attempt_raises_conflict_error_without_retrying(fake_db, fake_create_task):
    # Simulate a previous attempt that inserted the pending row but never
    # got to complete it (e.g. process crashed between step 1 and step 2).
    fake_db.actions.append({
        "id": 99, "rule_code": "FOLLOWUP_STIMA_RICHIESTA", "trigger_type": "event",
        "contact_id": 16, "lead_id": 12, "stima_id": 501,
        "idempotency_key": "followup:stima_richiesta:501", "task_id": None,
        "status": "pending", "error_message": None,
        "created_at": datetime.now(timezone.utc),
    })

    with pytest.raises(ConflictError):
        repository.execute_followup_action(**_kwargs())

    assert len(fake_db.tasks) == 0, "non deve mai creare un task per un tentativo pending ambiguo"


def test_failed_prior_attempt_raises_conflict_error_without_retrying(fake_db, fake_create_task):
    fake_db.actions.append({
        "id": 99, "rule_code": "FOLLOWUP_STIMA_RICHIESTA", "trigger_type": "event",
        "contact_id": 16, "lead_id": 12, "stima_id": 501,
        "idempotency_key": "followup:stima_richiesta:501", "task_id": None,
        "status": "failed", "error_message": "boom",
        "created_at": datetime.now(timezone.utc),
    })

    with pytest.raises(ConflictError):
        repository.execute_followup_action(**_kwargs())

    assert len(fake_db.tasks) == 0


# --- best-effort duplicate check against tasks.metadata ----------------------

def test_reuses_existing_task_found_via_metadata_idempotency_key_without_calling_core(fake_db, fake_create_task):
    # A task already tagged with this idempotency_key exists (e.g. created
    # by a previous, out-of-band run) - the defensive metadata check must
    # find it and skip calling core_repository.create_task_with_cursor.
    fake_db.tasks.append({
        "id": 777,
        "metadata": {"idempotency_key": "followup:stima_richiesta:501"},
    })

    result = repository.execute_followup_action(**_kwargs())

    assert result["task_id"] == 777
    assert len(fake_create_task) == 0, "create_task_with_cursor non deve essere chiamato se il task esiste gia'"


# --- failure isolation ---------------------------------------------------------

def test_core_task_creation_failure_marks_action_failed_and_reraises(fake_db, monkeypatch):
    def _boom(cur, data):
        raise RuntimeError("simulated CORE task creation failure")

    monkeypatch.setattr(repository.core_repository, "create_task_with_cursor", _boom)

    with pytest.raises(RuntimeError):
        repository.execute_followup_action(**_kwargs())

    assert len(fake_db.actions) == 1
    assert fake_db.actions[0]["status"] == "failed"
    assert "simulated CORE task creation failure" in fake_db.actions[0]["error_message"]
    assert len(fake_db.tasks) == 0
