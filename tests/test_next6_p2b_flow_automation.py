from __future__ import annotations

import copy
import importlib
import json
import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flow import repository, router as flow_router, service
from flow.rules.registry import OWNER_RULES, RULES


ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
NOW = datetime(2030, 1, 1, 13, 0, tzinfo=timezone.utc)


def _json_value(value):
    return copy.deepcopy(getattr(value, "adapted", value))


def _rule_row(code="FLOW-R001"):
    rule = RULES.get(code) or OWNER_RULES[code]
    return {
        "id": int(code.rsplit("R", 1)[1]),
        "code": code,
        "is_active": True,
        "parameters": copy.deepcopy(rule.default_parameters),
        "event_type": rule.event_type,
        "entity_type": rule.entity_type,
    }


def _entity(entity_id=10):
    return {
        "id": entity_id,
        "entity_type": "lead",
        "entity_id": entity_id,
        "status": "open",
    }


def _action():
    return {
        "action_type": "create_core_task",
        "title": "Primo contatto lead",
        "description": "Follow-up FLOW",
        "priority": "high",
        "due_hours": 4,
        "contact_id": None,
        "lead_id": 10,
        "assigned_to": None,
    }


class CooldownCursor:
    def __init__(self, database):
        self.database = database
        self.state = database.state
        self.current = None
        self.rowcount = -1
        self.savepoint = None
        self.held_locks = []

    def execute(self, query, params=()):
        normalized = " ".join(str(query).split())
        lowered = normalized.lower()
        params = params or ()
        self.database.sql.append((normalized, params))
        self.current = None
        self.rowcount = -1

        if lowered.startswith("select * from flow_rules"):
            self.current = copy.deepcopy(_rule_row(params[0]))
            return
        if "select 1 from flow_suppressions" in lowered:
            self.current = {"exists": 1} if self.database.suppressed else None
            return
        if lowered.startswith("insert into flow_executions"):
            with self.database.state_lock:
                execution = {
                    "id": len(self.state["executions"]) + 1,
                    "rule_id": _rule_row(params and "FLOW-R001")["id"],
                    "entity_type": params[2],
                    "entity_id": params[3],
                    "status": params[4],
                    "completed_at": None,
                    "actions_result": {},
                    "error_message": None,
                }
                self.state["executions"].append(execution)
            self.current = copy.deepcopy(execution)
            return
        if "pg_advisory_xact_lock" in lowered:
            scope = params[0]
            assert "hashtextextended" in lowered
            with self.database.state_lock:
                lock = self.database.locks.setdefault(scope, threading.Lock())
                self.database.advisory_attempts.append(scope)
                attempt = len(self.database.advisory_attempts)
                if self.database.coordinate_concurrency and attempt == 2:
                    self.database.second_attempt.set()
            lock.acquire()
            self.held_locks.append(lock)
            if self.database.coordinate_concurrency and attempt == 1:
                assert self.database.second_attempt.wait(timeout=5)
            self.current = {"locked": True}
            return
        if "from flow_action_records" in lowered and "join flow_executions" in lowered:
            rule_id, entity_type, entity_id, action_type, cooldown_minutes = params
            cutoff = self.database.now - timedelta(minutes=cooldown_minutes)
            is_inside=lambda timestamp: timestamp>=cutoff if ">= NOW()" in normalized else timestamp>cutoff
            candidates = []
            for item in self.database.history:
                if (
                    item["rule_id"] == rule_id
                    and item["entity_type"] == entity_type
                    and item["entity_id"] == entity_id
                    and item["action_type"] == action_type
                    and item["status"] in {"completed", "pending"}
                    and is_inside(item["at"])
                ):
                    candidates.append(item)
            for action in self.state["actions"]:
                execution = next(x for x in self.state["executions"] if x["id"] == action["execution_id"])
                effective_at = execution["completed_at"] or action["created_at"]
                if (
                    execution["rule_id"] == rule_id
                    and execution["entity_type"] == entity_type
                    and execution["entity_id"] == entity_id
                    and action["action_type"] == action_type
                    and action["status"] in {"completed", "pending"}
                    and is_inside(effective_at)
                ):
                    candidates.append({"at": effective_at, **action})
            self.current = copy.deepcopy(max(candidates, key=lambda item: item["at"], default=None))
            return
        if lowered.startswith("savepoint "):
            self.savepoint = copy.deepcopy(self.state)
            return
        if lowered.startswith("rollback to savepoint "):
            self.state.clear()
            self.state.update(copy.deepcopy(self.savepoint))
            return
        if lowered.startswith("release savepoint "):
            self.savepoint = None
            return
        if lowered.startswith("insert into flow_action_records"):
            key = params[2]
            with self.database.state_lock:
                existing = next((item for item in self.state["actions"] if item["idempotency_key"] == key), None)
                if existing:
                    return
                action = {
                    "id": len(self.state["actions"]) + 1,
                    "execution_id": params[0],
                    "action_type": params[1],
                    "idempotency_key": key,
                    "status": "pending",
                    "target_entity_id": None,
                    "created_at": self.database.now,
                }
                self.state["actions"].append(action)
            self.current = copy.deepcopy(action)
            return
        if lowered.startswith("select * from flow_action_records"):
            self.current = copy.deepcopy(next((x for x in self.state["actions"] if x["idempotency_key"] == params[0]), None))
            return
        if "select id from tasks where metadata->>'idempotency_key'" in lowered:
            task = next((x for x in self.state["tasks"] if x["idempotency_key"] == params[0]), None)
            self.current = {"id": task["id"]} if task else None
            return
        if lowered.startswith("update flow_action_records set execution_id"):
            action = next(x for x in self.state["actions"] if x["id"] == params[2])
            action.update(status="completed", target_entity_id=params[1])
            return
        if lowered.startswith("update flow_executions"):
            execution = next(x for x in self.state["executions"] if x["id"] == params[-1])
            if "status='executed'" in lowered:
                execution.update(status="executed", actions_result=_json_value(params[0]), completed_at=self.database.now)
            elif "status='skipped'" in lowered:
                execution.update(status="skipped", actions_result=_json_value(params[0]), completed_at=self.database.now)
            elif "status='not_matched'" in lowered:
                execution.update(status="not_matched", completed_at=self.database.now)
            elif "status='failed'" in lowered:
                execution.update(status="failed", error_message=params[0], completed_at=self.database.now)
            self.current = copy.deepcopy(execution)
            return
        raise AssertionError(f"SQL non gestito dal test P2B: {normalized}")

    def fetchone(self):
        return self.current


class CooldownDatabase:
    def __init__(self, history=(), *, suppressed=False, coordinate_concurrency=False):
        self.state = {"executions": [], "actions": [], "tasks": []}
        self.history = list(history)
        self.suppressed = suppressed
        self.coordinate_concurrency = coordinate_concurrency
        self.now = NOW
        self.sql = []
        self.calls = []
        self.locks = {}
        self.advisory_attempts = []
        self.second_attempt = threading.Event()
        self.state_lock = threading.RLock()

    @contextmanager
    def cursor(self, commit=False):
        self.calls.append(commit)
        cursor = CooldownCursor(self)
        try:
            yield object(), cursor
        finally:
            for lock in reversed(cursor.held_locks):
                lock.release()


def _history(status, age_minutes, *, at=None):
    return {
        "rule_id": 1,
        "entity_type": "lead",
        "entity_id": 10,
        "action_type": "create_core_task",
        "status": status,
        "at": at or NOW - timedelta(minutes=age_minutes),
    }


def _install_cooldown_runtime(monkeypatch, database):
    monkeypatch.setattr(repository, "core_cursor", database.cursor)

    def create_task(cur, data):
        with database.state_lock:
            task = {
                "id": len(database.state["tasks"]) + 100,
                "idempotency_key": data["metadata"]["idempotency_key"],
            }
            database.state["tasks"].append(task)
        return task

    monkeypatch.setattr(repository.core_repository, "create_task_with_cursor", create_task)


def _run_cooldown(monkeypatch, database, *, retry_of=None):
    _install_cooldown_runtime(monkeypatch, database)
    return repository.execute_live(
        "FLOW-R001",
        _entity(),
        True,
        ["matched"],
        _action(),
        requested_by="test",
        retry_of_execution_id=retry_of,
    )


@pytest.mark.parametrize("status", ["completed", "pending"])
def test_recent_completed_or_pending_action_blocks_new_task(monkeypatch, status):
    database = CooldownDatabase([_history(status, 30)])
    result = _run_cooldown(monkeypatch, database)
    assert result["status"] == "skipped"
    assert database.state["tasks"] == []
    assert database.state["actions"] == []


@pytest.mark.parametrize("status", ["failed", "skipped"])
def test_failed_or_skipped_action_does_not_extend_cooldown(monkeypatch, status):
    database = CooldownDatabase([_history(status, 30)])
    result = _run_cooldown(monkeypatch, database)
    assert result["status"] == "executed"
    assert len(database.state["tasks"]) == 1


def test_completed_outside_cooldown_creates_new_task(monkeypatch):
    database = CooldownDatabase([_history("completed", 1441)])
    result = _run_cooldown(monkeypatch, database)
    assert result["status"] == "executed"
    assert len(database.state["tasks"]) == 1


def test_completed_exactly_at_cooldown_boundary_creates_new_task(monkeypatch):
    database = CooldownDatabase([_history("completed", 1440)])
    result = _run_cooldown(monkeypatch, database)
    assert result["status"] == "executed"
    assert len(database.state["tasks"]) == 1


def test_rolling_cooldown_crossing_old_bucket_boundary_still_blocks(monkeypatch):
    database = CooldownDatabase([_history("completed", 0, at=NOW - timedelta(milliseconds=200))])
    result = _run_cooldown(monkeypatch, database)
    assert result["status"] == "skipped"
    assert database.state["tasks"] == []


def test_suppression_creates_no_action_and_cannot_extend_cooldown(monkeypatch):
    database = CooldownDatabase(suppressed=True)
    result = _run_cooldown(monkeypatch, database)
    assert result["status"] == "not_matched"
    assert database.state["tasks"] == []
    assert database.state["actions"] == []
    assert database.advisory_attempts == []


def test_retry_after_failed_is_allowed_but_recent_completed_is_skipped(monkeypatch):
    failed_db = CooldownDatabase([_history("failed", 1)])
    completed_db = CooldownDatabase([_history("completed", 1)])
    assert _run_cooldown(monkeypatch, failed_db, retry_of=90)["status"] == "executed"
    assert _run_cooldown(monkeypatch, completed_db, retry_of=91)["status"] == "skipped"


def test_cooldown_lock_is_stable_db_hash_and_scope_fields_are_rechecked(monkeypatch):
    database = CooldownDatabase()
    _run_cooldown(monkeypatch, database)
    lock_sql, lock_params = next((sql, params) for sql, params in database.sql if "pg_advisory_xact_lock" in sql)
    rolling_sql, rolling_params = next((sql, params) for sql, params in database.sql if "JOIN flow_executions" in sql)
    assert "hashtextextended" in lock_sql
    assert lock_params == ("flow:cooldown:1:lead:10:create_core_task",)
    assert rolling_params[:4] == (1, "lead", 10, "create_core_task")
    assert all(field in rolling_sql for field in ("e.rule_id", "e.entity_type", "e.entity_id", "a.action_type"))
    assert "COALESCE(e.completed_at,a.created_at)" in rolling_sql
    assert "ELSE a.created_at" in rolling_sql


def test_new_cooldown_action_key_is_execution_based_but_event_key_is_unchanged(monkeypatch):
    cooldown_db = CooldownDatabase()
    _run_cooldown(monkeypatch, cooldown_db)
    assert cooldown_db.state["actions"][0]["idempotency_key"] == "FLOW-R001:lead:10:create_core_task:execution:1"

    event_db = CooldownDatabase()
    _install_cooldown_runtime(monkeypatch, event_db)
    owner_entity = {"entity_type": "owner_feedback", "entity_id": 101}
    owner_action = {**_action(), "contact_id": 7, "lead_id": None}
    repository.execute_live("FLOW-R008", owner_entity, True, ["matched"], owner_action, event_id=701)
    assert event_db.state["actions"][0]["idempotency_key"] == "FLOW-R008:event:701"


def test_recovery_after_event_processing_crash_reuses_event_key_without_duplicate_task(monkeypatch):
    event_db = CooldownDatabase()
    _install_cooldown_runtime(monkeypatch, event_db)
    owner_entity = {"entity_type": "owner_feedback", "entity_id": 101}
    owner_action = {**_action(), "contact_id": 7, "lead_id": None}
    first = repository.execute_live("FLOW-R008", owner_entity, True, ["matched"], owner_action, event_id=702)
    recovered = repository.execute_live("FLOW-R008", owner_entity, True, ["matched"], owner_action, event_id=702)
    assert (first["status"], recovered["status"]) == ("executed", "skipped")
    assert len(event_db.state["tasks"]) == 1
    assert len(event_db.state["actions"]) == 1


def test_two_concurrent_transactions_same_scope_create_one_task(monkeypatch):
    database = CooldownDatabase(coordinate_concurrency=True)
    _install_cooldown_runtime(monkeypatch, database)
    results = []
    errors = []

    def invoke():
        try:
            results.append(repository.execute_live("FLOW-R001", _entity(), True, ["matched"], _action()))
        except Exception as exc:  # pragma: no cover - assertion reports thread failures
            errors.append(exc)

    threads = [threading.Thread(target=invoke), threading.Thread(target=invoke)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(item["status"] for item in results) == ["executed", "skipped"]
    assert len(database.state["tasks"]) == 1
    assert len(database.advisory_attempts) == 2


@contextmanager
def _claim(saved, claim_status="claimed", lock_state=None):
    if lock_state is not None:
        lock_state["held"] = True
    try:
        yield {"claim_status": claim_status, "event": copy.deepcopy(saved)}
    finally:
        if lock_state is not None:
            lock_state["held"] = False


def _saved_event(event_id=701, status="received", received_at=None):
    return {
        "id": event_id,
        "event_type": "owner.request_submitted",
        "entity_type": "owner_feedback",
        "entity_id": 101,
        "source_module": "owner",
        "status": status,
        "payload": {"owner_request_type": "contact_request"},
        "received_at": received_at or NOW,
    }


def test_event_claim_uses_stable_advisory_lock_and_checks_exact_status(monkeypatch):
    class Cursor:
        def __init__(self):
            self.calls = []
            self.current = None

        def execute(self, sql, params=()):
            compact = " ".join(str(sql).split())
            self.calls.append((compact, params))
            if "pg_try_advisory_xact_lock" in compact:
                self.current = {"acquired": True}
            elif "FROM flow_events" in compact:
                self.current = _saved_event()

        def fetchone(self):
            return self.current

    cursor = Cursor()

    @contextmanager
    def fake_cursor(commit=False):
        assert commit is False
        yield object(), cursor

    monkeypatch.setattr(repository, "core_cursor", fake_cursor)
    claim_fn = getattr(repository, "claim_event_for_processing")
    with claim_fn(701, received_only=True) as claim:
        assert claim["claim_status"] == "claimed"
    lock_sql, lock_params = cursor.calls[0]
    event_sql, event_params = cursor.calls[1]
    assert "hashtextextended" in lock_sql
    assert lock_params == ("flow:event:701",)
    assert "source_module='owner'" in event_sql and "status='received'" in event_sql
    assert event_params == (701,)


def test_process_saved_event_holds_claim_through_terminal_status(monkeypatch):
    saved = _saved_event()
    lock_state = {"held": False}
    statuses = []
    monkeypatch.setattr(repository, "claim_event_for_processing", lambda *args, **kwargs: _claim(saved, lock_state=lock_state))
    monkeypatch.setattr(service.repository, "list_rules", lambda: [])

    def update(event_id, status, error_message=None):
        assert lock_state["held"] is True
        statuses.append(status)
        return {**saved, "status": status}

    monkeypatch.setattr(service.repository, "update_event_status", update)
    result = service.process_saved_event(701)
    assert result["event"]["status"] == "ignored"
    assert statuses == ["ignored"]
    assert lock_state["held"] is False


def test_recovery_selects_only_received_owner_oldest_first_and_respects_limit(monkeypatch):
    seen = []
    monkeypatch.setattr(service.repository, "list_received_owner_event_ids", lambda limit: seen.append(limit) or [2, 3])
    monkeypatch.setattr(
        service,
        "process_saved_event",
        lambda event_id, received_only=False: {
            "claim_status": "claimed",
            "event": {**_saved_event(event_id), "status": "processed"},
            "executions": [],
        },
    )
    result = service.recover_received_events(2)
    assert seen == [2]
    assert [item["event_id"] for item in result["items"]] == [2, 3]
    assert result == {
        "status": "completed",
        "requested_limit": 2,
        "processed": 2,
        "ignored": 0,
        "failed": 0,
        "busy": 0,
        "items": result["items"],
    }


def test_recovery_isolates_failure_and_reports_busy(monkeypatch):
    monkeypatch.setattr(service.repository, "list_received_owner_event_ids", lambda limit: [1, 2, 3])

    def process(event_id, received_only=False):
        assert received_only is True
        if event_id == 1:
            raise RuntimeError("owner adapter failed")
        if event_id == 2:
            return {"claim_status": "busy", "event": _saved_event(2), "executions": []}
        return {"claim_status": "claimed", "event": {**_saved_event(3), "status": "ignored"}, "executions": []}

    monkeypatch.setattr(service, "process_saved_event", process)
    result = service.recover_received_events(3)
    assert result["status"] == "partial_failure"
    assert (result["processed"], result["ignored"], result["failed"], result["busy"]) == (0, 1, 1, 1)
    assert [item["event_id"] for item in result["items"]] == [1, 2, 3]
    assert "owner adapter failed" in result["items"][0]["error_message"]


def test_recovery_query_excludes_processed_ignored_failed_and_orders_oldest(monkeypatch):
    class Cursor:
        current = []
        call = None

        def execute(self, sql, params):
            self.call = (" ".join(str(sql).split()), params)
            self.current = [{"id": 4}, {"id": 9}]

        def fetchall(self):
            return self.current

    cursor = Cursor()

    @contextmanager
    def fake_cursor(commit=False):
        assert commit is False
        yield object(), cursor

    monkeypatch.setattr(repository, "core_cursor", fake_cursor)
    assert repository.list_received_owner_event_ids(2) == [4, 9]
    sql, params = cursor.call
    assert "source_module='owner'" in sql
    assert "status='received'" in sql
    assert "ORDER BY received_at ASC, id ASC" in sql
    assert params == (2,)


def test_busy_event_can_be_recovered_by_next_run(monkeypatch):
    outcomes = iter([
        {"claim_status": "busy", "event": _saved_event(), "executions": []},
        {"claim_status": "claimed", "event": {**_saved_event(), "status": "processed"}, "executions": []},
    ])
    monkeypatch.setattr(service.repository, "list_received_owner_event_ids", lambda limit: [701])
    monkeypatch.setattr(service, "process_saved_event", lambda *args, **kwargs: next(outcomes))
    assert service.recover_received_events(10)["busy"] == 1
    assert service.recover_received_events(10)["processed"] == 1


def test_normal_owner_dispatch_and_recovery_share_claim_and_process_once(monkeypatch):
    saved = _saved_event()
    event_lock = threading.Lock()
    entered = threading.Event()
    release = threading.Event()
    processing_count = 0
    count_lock = threading.Lock()

    @contextmanager
    def claim(event_id, received_only=False):
        acquired = event_lock.acquire(blocking=False)
        if not acquired:
            yield {"claim_status": "busy", "event": saved}
            return
        try:
            yield {"claim_status": "claimed", "event": saved}
        finally:
            event_lock.release()

    def process(saved_event):
        nonlocal processing_count
        with count_lock:
            processing_count += 1
        entered.set()
        assert release.wait(timeout=5)
        return {"event": {**saved_event, "status": "processed"}, "executions": [{"status": "executed"}]}

    monkeypatch.setattr(service.repository, "claim_event_for_processing", claim)
    monkeypatch.setattr(service, "_process_saved_event", process)
    monkeypatch.setattr(service.repository, "list_received_owner_event_ids", lambda limit: [701])
    normal_result = []
    thread = threading.Thread(target=lambda: normal_result.append(service.process_saved_event(701)))
    thread.start()
    assert entered.wait(timeout=5)
    recovery = service.recover_received_events(10)
    release.set()
    thread.join(timeout=5)
    assert processing_count == 1
    assert recovery["busy"] == 1
    assert normal_result[0]["event"]["status"] == "processed"


def _scan_payload():
    return SimpleNamespace(
        dict=lambda exclude_unset=False: {
            "rule_codes": None,
            "limit": 50,
            "simulation": False,
            "requested_by": "cron",
        }
    )


def test_live_scan_without_rule_codes_uses_only_active_rules_and_event_rules_have_no_candidates(monkeypatch):
    rows = []
    for code in RULES:
        rows.append({**_rule_row(code), "is_active": False})
    for code in OWNER_RULES:
        rows.append({**_rule_row(code), "is_active": True})
    scanned = []
    monkeypatch.setattr(service.repository, "sync_rules", lambda: rows)
    monkeypatch.setattr(service.repository, "list_rules", lambda synchronize=False: rows)
    monkeypatch.setattr(service.repository, "get_rule_row", lambda code, synchronize=False: next(x for x in rows if x["code"] == code))
    monkeypatch.setattr(service, "scan_candidates", lambda code, parameters, limit: scanned.append(code) or [])
    monkeypatch.setattr(service.repository, "execute_live", Mock(side_effect=AssertionError("event rules must not become scan actions")))
    result = service.scan(_scan_payload())
    assert scanned == list(OWNER_RULES)
    assert not any(code in scanned for code in RULES)
    assert "FLOW-R005" not in scanned
    assert result["processed"] == 0
    assert result["items"] == []


def test_recovery_schema_bounds_and_router_auth(monkeypatch):
    schemas = importlib.import_module("flow.schemas")
    model = getattr(schemas, "EventRecoveryRequest")
    assert model(limit=1).limit == 1
    with pytest.raises(Exception):
        model(limit=0)
    with pytest.raises(Exception):
        model(limit=501)

    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASS", "secret")
    app = FastAPI()
    app.include_router(flow_router.router)
    client = TestClient(app)
    assert client.post("/api/flow/events/recover", json={"limit": 10}).status_code == 401
    monkeypatch.setattr(flow_router.service, "recover_received_events", lambda limit: {"status": "completed", "requested_limit": limit})
    response = client.post("/api/flow/events/recover", json={"limit": 10}, auth=("admin", "secret"))
    assert response.status_code == 200
    assert response.json()["requested_limit"] == 10


def _runner_module():
    return importlib.import_module("run_flow_p2b_cron")


class FakeResponse:
    def __init__(self, payload, status_code=200, raw=""):
        self.payload = payload
        self.status_code = status_code
        self.text = raw

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return copy.deepcopy(self.payload)


def _runner_env(monkeypatch):
    values = {
        "FLOW_AUTOMATION_BASE_URL": "http://127.0.0.1:8765",
        "ADMIN_USER": "cron-user",
        "ADMIN_PASS": "cron-secret",
        "FLOW_RECOVERY_LIMIT": "25",
        "FLOW_SCAN_LIMIT": "40",
        "FLOW_CONNECT_TIMEOUT_SECONDS": "2",
        "FLOW_READ_TIMEOUT_SECONDS": "30",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return values


def test_runner_requires_base_url_and_never_defaults_to_production(monkeypatch, capsys):
    runner = _runner_module()
    monkeypatch.delenv("FLOW_AUTOMATION_BASE_URL", raising=False)
    called = Mock(side_effect=AssertionError("HTTP must not run without config"))
    monkeypatch.setattr(runner.requests, "post", called)
    assert runner.main() == 1
    assert "configuration" in capsys.readouterr().out.lower()
    called.assert_not_called()


def test_runner_calls_recovery_then_live_scan_with_auth_timeouts_and_no_rule_codes(monkeypatch):
    runner = _runner_module()
    _runner_env(monkeypatch)
    calls = []
    responses = iter([
        FakeResponse({"status": "completed", "requested_limit": 25, "processed": 1, "ignored": 0, "failed": 0, "busy": 0}),
        FakeResponse({"status": "completed", "requested_limit": 40, "processed": 3, "successes": 3, "failures": 0, "skips": 0}),
    ])

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return next(responses)

    monkeypatch.setattr(runner.requests, "post", post)
    assert runner.main() == 0
    assert [url.rsplit("/api/flow/", 1)[1] for url, _ in calls] == ["events/recover", "scan"]
    assert calls[0][1]["json"] == {"limit": 25}
    assert calls[1][1]["json"] == {"simulation": False, "limit": 40}
    assert "rule_codes" not in calls[1][1]["json"]
    assert calls[0][1]["auth"] == ("cron-user", "cron-secret")
    assert calls[0][1]["timeout"] == (2.0, 30.0)


def test_runner_continues_scan_after_application_recovery_failure_and_returns_two(monkeypatch):
    runner = _runner_module()
    _runner_env(monkeypatch)
    calls = []
    responses = iter([
        FakeResponse({"status": "partial_failure", "requested_limit": 25, "processed": 1, "ignored": 0, "failed": 1, "busy": 0}),
        FakeResponse({"status": "completed", "requested_limit": 40, "processed": 1, "successes": 1, "failures": 0, "skips": 0}),
    ])
    monkeypatch.setattr(runner.requests, "post", lambda url, **kwargs: calls.append(url) or next(responses))
    assert runner.main() == 2
    assert len(calls) == 2


@pytest.mark.parametrize("failure", ["network", "http", "json"])
def test_runner_technical_recovery_failure_returns_one_without_http_retry_or_scan(monkeypatch, failure):
    runner = _runner_module()
    _runner_env(monkeypatch)
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        if failure == "network":
            raise runner.requests.ConnectionError("offline")
        if failure == "http":
            return FakeResponse({}, status_code=401, raw="secret response")
        return FakeResponse(ValueError("invalid json"), raw="raw body")

    monkeypatch.setattr(runner.requests, "post", post)
    assert runner.main() == 1
    assert len(calls) == 1


def test_runner_saturation_is_exit_two(monkeypatch):
    runner = _runner_module()
    _runner_env(monkeypatch)
    responses = iter([
        FakeResponse({"status": "completed", "requested_limit": 25, "processed": 0, "ignored": 0, "failed": 0, "busy": 0}),
        FakeResponse({"status": "completed", "requested_limit": 40, "processed": 40, "successes": 40, "failures": 0, "skips": 0}),
    ])
    monkeypatch.setattr(runner.requests, "post", lambda *args, **kwargs: next(responses))
    assert runner.main() == 2


def test_runner_rejects_json_with_invalid_counter_contract(monkeypatch):
    runner = _runner_module()
    _runner_env(monkeypatch)
    calls = []
    monkeypatch.setattr(runner.requests, "post", lambda url, **kwargs: calls.append(url) or FakeResponse({"status": "completed"}))
    assert runner.main() == 1
    assert len(calls) == 1


def test_runner_logs_are_sanitized(monkeypatch, capsys):
    runner = _runner_module()
    values = _runner_env(monkeypatch)
    responses = iter([
        FakeResponse({"status": "completed", "requested_limit": 25, "processed": 0, "ignored": 0, "failed": 0, "busy": 0}, raw="RAW-RECOVERY-BODY"),
        FakeResponse({"status": "completed", "requested_limit": 40, "processed": 0, "successes": 0, "failures": 0, "skips": 0}, raw="RAW-SCAN-BODY"),
    ])
    monkeypatch.setattr(runner.requests, "post", lambda *args, **kwargs: next(responses))
    assert runner.main() == 0
    output = capsys.readouterr().out
    for secret in (values["ADMIN_USER"], values["ADMIN_PASS"], "RAW-RECOVERY-BODY", "RAW-SCAN-BODY", "Authorization"):
        assert secret not in output
    assert "phase=recovery" in output and "phase=scan" in output


def test_runner_invalid_numeric_configuration_is_exit_one_without_http(monkeypatch):
    runner = _runner_module()
    _runner_env(monkeypatch)
    monkeypatch.setenv("FLOW_SCAN_LIMIT", "zero")
    post = Mock()
    monkeypatch.setattr(runner.requests, "post", post)
    assert runner.main() == 1
    post.assert_not_called()


def test_runner_file_compiles_and_has_no_render_or_rule_activation_side_effects(tmp_path):
    path = ROOT / "run_flow_p2b_cron.py"
    environment={**os.environ,"PYTHONPYCACHEPREFIX":str(tmp_path)}
    result = subprocess.run([PYTHON, "-m", "py_compile", str(path)], cwd=ROOT, env=environment, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    source = path.read_text(encoding="utf-8")
    assert "render.com" not in source
    assert "/activate" not in source
    assert "FLOW-R005" not in source
