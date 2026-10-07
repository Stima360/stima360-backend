"""F04/F06: explicit follow-up recovery preserves the original action and task.

The unit tests use the existing repository fake plus transaction row-locks.
The full PostgreSQL submission tests cover the database guarantee separately.
No external provider or shared database is involved.
"""
from __future__ import annotations

import copy
import inspect
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from followup import repository, service
from followup.exceptions import ConflictError, ValidationError
from tests.test_followup_repository import FakeCursor, FakeDatabase, _kwargs


class LockedCursor(FakeCursor):
    def __init__(self, database):
        super().__init__(database)
        self.owns_action_lock = False

    def execute(self, query, params=None):
        sql = " ".join(str(query).split()).lower()
        if "from followup_actions where id =" in sql and "for update" in sql:
            self.database.lock_attempts.append(threading.get_ident())
            if self.database.first_creator.is_set():
                self.database.second_lock_attempt.set()
            self.database.action_lock.acquire()
            self.owns_action_lock = True
            self.database.sql.append((sql, params))
            match = next((a for a in self.database.actions if a["id"] == params[0]), None)
            self.rows = [copy.deepcopy(match)] if match else []
            return
        return super().execute(query, params)


class LockedDatabase(FakeDatabase):
    def __init__(self):
        super().__init__()
        self.action_lock = threading.RLock()
        self.lock_attempts = []
        self.first_creator = threading.Event()
        self.second_lock_attempt = threading.Event()

    @contextmanager
    def cursor(self, *, commit=False):
        cur = LockedCursor(self)
        try:
            yield self, cur
            if commit:
                self.commits += 1
        finally:
            if cur.owns_action_lock:
                self.action_lock.release()


@pytest.fixture
def recovery_db(monkeypatch):
    db = LockedDatabase()
    monkeypatch.setattr(repository, "followup_cursor", db.cursor)

    def create(cur, data):
        row = {"id": len(db.tasks) + 1, **copy.deepcopy(data)}
        db.tasks.append(row)
        return {"id": row["id"]}

    monkeypatch.setattr(repository.core_repository, "create_task_with_cursor", create)
    return db


def prior_action(db, status):
    db.actions.append({
        "id": 99, "rule_code": "FOLLOWUP_STIMA_RICHIESTA", "trigger_type": "event",
        "contact_id": 16, "lead_id": 12, "stima_id": 501,
        "idempotency_key": "followup:stima_richiesta:501", "task_id": None,
        "status": status, "error_message": "synthetic failure" if status == "failed" else None,
        "created_at": datetime(2026, 10, 7, tzinfo=timezone.utc),
        "agency_id": 1,
    })


def recover(**overrides):
    # Before the opt-in exists, exercise the real legacy refusal as RED.
    kwargs = _kwargs(**overrides)
    if "recover" in inspect.signature(repository.execute_followup_action).parameters:
        kwargs["recover"] = True
    return repository.execute_followup_action(**kwargs)


@pytest.mark.parametrize("status", ["pending", "failed"])
def test_explicit_recovery_completes_the_original_action_once(recovery_db, status):
    prior_action(recovery_db, status)
    with pytest.raises(ConflictError):
        repository.execute_followup_action(**_kwargs())
    first = recover()
    replay = recover()
    assert first["followup_action_id"] == replay["followup_action_id"] == 99
    assert first["task_id"] == replay["task_id"]
    assert replay["status"] == "already_completed"
    assert len(recovery_db.actions) == len(recovery_db.tasks) == 1
    assert recovery_db.actions[0]["status"] == "completed"
    assert recovery_db.actions[0]["error_message"] is None


def test_recovery_after_task_commit_before_receipt_checkpoint_reuses_the_task(recovery_db):
    first = repository.execute_followup_action(**_kwargs())
    replay = recover()
    assert replay["status"] == "already_completed"
    assert replay["task_id"] == first["task_id"]
    assert len(recovery_db.tasks) == 1


def test_recovery_reuses_task_found_with_the_existing_idempotency_key(recovery_db):
    prior_action(recovery_db, "failed")
    recovery_db.tasks.append({"id": 77, "metadata": {"idempotency_key": "followup:stima_richiesta:501"}})
    result = recover()
    assert result["task_id"] == 77
    assert recovery_db.actions[0]["task_id"] == 77
    assert len(recovery_db.tasks) == 1


def test_two_recoveries_serialize_and_create_only_one_task(recovery_db, monkeypatch):
    prior_action(recovery_db, "pending")
    continue_first = threading.Event()

    def create(cur, data):
        recovery_db.first_creator.set()
        assert continue_first.wait(5), "first task creation was not released"
        row = {"id": len(recovery_db.tasks) + 1, **copy.deepcopy(data)}
        recovery_db.tasks.append(row)
        return {"id": row["id"]}

    monkeypatch.setattr(repository.core_repository, "create_task_with_cursor", create)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(recover)
        try:
            assert recovery_db.first_creator.wait(2), "pending action could not be recovered"
            second = pool.submit(recover)
            assert recovery_db.second_lock_attempt.wait(2), "second recovery did not reach the action lock"
            assert not second.done(), "second recovery passed the active task transaction"
        finally:
            continue_first.set()
        outcomes = [first.result(timeout=5), second.result(timeout=5)]
    assert len(recovery_db.tasks) == 1
    assert {r["task_id"] for r in outcomes} == {recovery_db.tasks[0]["id"]}
    assert {r["status"] for r in outcomes} == {"completed", "already_completed"}


def test_initial_worker_and_recovery_serialize_on_the_same_action(recovery_db, monkeypatch):
    continue_first = threading.Event()

    def create(cur, data):
        recovery_db.first_creator.set()
        assert continue_first.wait(5)
        row = {"id": len(recovery_db.tasks) + 1, **copy.deepcopy(data)}
        recovery_db.tasks.append(row)
        return {"id": row["id"]}

    monkeypatch.setattr(repository.core_repository, "create_task_with_cursor", create)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(repository.execute_followup_action, **_kwargs())
        try:
            assert recovery_db.first_creator.wait(2)
            second = pool.submit(recover)
            assert recovery_db.second_lock_attempt.wait(2), "recovery bypassed the initial worker's row lock"
            assert not second.done()
        finally:
            continue_first.set()
        outcomes = [first.result(timeout=5), second.result(timeout=5)]
    assert len(recovery_db.tasks) == 1
    assert outcomes[0]["task_id"] == outcomes[1]["task_id"]


def test_service_recovery_preserves_the_frozen_deadline(monkeypatch):
    calls = []
    monkeypatch.setattr(service.repository, "execute_followup_action", lambda **kwargs: calls.append(kwargs) or {"status": "completed"})
    frozen = "2026-10-08T12:34:56+00:00"
    service.run_followup(rule_code="FOLLOWUP_STIMA_RICHIESTA", trigger_type="event", stima_id=501,
                         recover=True, due_at_override=frozen)
    service.run_followup(rule_code="FOLLOWUP_STIMA_RICHIESTA", trigger_type="event", stima_id=501,
                         recover=True, due_at_override=datetime.fromisoformat(frozen))
    assert calls[0]["due_at"] == calls[1]["due_at"] == datetime.fromisoformat(frozen)
    assert all(call["recover"] is True for call in calls)


@pytest.mark.parametrize("field,value", [
    ("contact_id", 88), ("lead_id", 77), ("stima_id", 502),
    ("rule_code", "another_rule"), ("trigger_type", "another_trigger"),
])
def test_recovery_refuses_a_different_relationship_tuple(recovery_db, field, value):
    prior_action(recovery_db, "pending")
    with pytest.raises(ConflictError):
        recover(**{field: value})
    assert recovery_db.tasks == []
    assert recovery_db.actions[0]["status"] == "pending"


def test_new_followup_action_derives_its_agency_from_persisted_references(recovery_db):
    recovery_db.reference_agencies = {("contacts", 16): 7, ("leads", 12): 7, ("stime", 501): 7}
    recover()
    assert recovery_db.actions[0]["agency_id"] == 7


@pytest.mark.parametrize("references", [
    {("contacts", 16): 2, ("leads", 12): 1, ("stime", 501): 1},
    {("contacts", 16): 1, ("leads", 12): 1, ("stime", 501): None},
])
def test_followup_refuses_mixed_or_unresolved_agencies_before_writing(recovery_db, references):
    recovery_db.reference_agencies = references
    with pytest.raises(ValidationError):
        recover()
    assert recovery_db.actions == recovery_db.tasks == []


@pytest.mark.parametrize("deadline", ["not-a-date", "2026-10-08T12:34:56", datetime(2026, 10, 8), 12])
def test_service_refuses_invalid_or_unzoned_frozen_deadline(monkeypatch, deadline):
    monkeypatch.setattr(service.repository, "execute_followup_action", lambda **kwargs: pytest.fail("invalid deadline reached persistence"))
    with pytest.raises(ValidationError):
        service.run_followup(rule_code="FOLLOWUP_STIMA_RICHIESTA", trigger_type="event", stima_id=501,
                             recover=True, due_at_override=deadline)
