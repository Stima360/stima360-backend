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
import pathlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flow import repository, router as flow_router, service
from flow.rules.registry import OWNER_RULES, RULES

# P26-6C: every FLOW write stamps a tenant, so these direct repository calls
# supply one. The value is a fixture id and never a constant the runtime
# knows: these tests are about execution semantics, and the isolation itself
# is proved in tests/test_p26_6c_flow_isolation.py.
P26_TEST_AGENCY = 4242



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
            # P26-6C: the probe now carries the tenant as its last parameter.
            # The double still answers from `database.suppressed`, but the
            # assertion below proves the predicate is actually there - a fake
            # that ignored it would hide an unscoped probe.
            assert "agency_id=%s" in lowered, lowered
            self.current = {"exists": 1} if self.database.suppressed else None
            return
        if lowered.startswith("insert into flow_executions"):
            with self.database.state_lock:
                # P26-6C: agency_id leads the column list, so every positional
                # index after it moved by one. Reading them by the old offsets
                # silently mislabelled entity_type as the rule id, which is why
                # the cooldown stopped matching before this was corrected.
                execution = {
                    "id": len(self.state["executions"]) + 1,
                    "agency_id": params[0],
                    "rule_id": _rule_row(params and "FLOW-R001")["id"],
                    "entity_type": params[3],
                    "entity_id": params[4],
                    "status": params[5],
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
    agency_id=P26_TEST_AGENCY,
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
    repository.execute_live("FLOW-R008", owner_entity, True, ["matched"], owner_action, event_id=701, agency_id=P26_TEST_AGENCY)
    assert event_db.state["actions"][0]["idempotency_key"] == "FLOW-R008:event:701"


def test_recovery_after_event_processing_crash_reuses_event_key_without_duplicate_task(monkeypatch):
    event_db = CooldownDatabase()
    _install_cooldown_runtime(monkeypatch, event_db)
    owner_entity = {"entity_type": "owner_feedback", "entity_id": 101}
    owner_action = {**_action(), "contact_id": 7, "lead_id": None}
    first = repository.execute_live("FLOW-R008", owner_entity, True, ["matched"], owner_action, event_id=702, agency_id=P26_TEST_AGENCY)
    recovered = repository.execute_live("FLOW-R008", owner_entity, True, ["matched"], owner_action, event_id=702, agency_id=P26_TEST_AGENCY)
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
            results.append(repository.execute_live("FLOW-R001", _entity(), True, ["matched"], _action(), agency_id=P26_TEST_AGENCY))
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
    # P26-6C: the route resolves an agency server-side, so the DB-backed
    # resolution is overridden the way every other P26 suite overrides it. The
    # P26-5: la guardia resta vera, e la credenziale che la apre e' la sessione
    # operatore. Il 401 sotto significa esattamente quel che significava prima.
    from operator_auth.context import OperatorContext
    from operator_auth.enums import COOKIE_NAME
    from tests.operator_session_helpers import SessionDouble, TEST_TOKEN

    app.dependency_overrides[flow_router.legacy_basic_agency_context] = lambda: OperatorContext(
        user_id=42, agency_id=4242, role="agency_owner",
        is_platform_admin=False, session_id=1, auth_channel="operator_session",
    )
    sessions = SessionDouble(monkeypatch)
    client = TestClient(app)
    assert client.post("/api/flow/events/recover", json={"limit": 10}).status_code == 401
    monkeypatch.setattr(flow_router.service, "recover_received_events_for_agency", lambda agency_id, limit: {"status": "completed", "requested_limit": limit})

    sessions.login(agency_id=4242, role="agency_owner")
    client.cookies.set(COOKIE_NAME, TEST_TOKEN)
    response = client.post("/api/flow/events/recover", json={"limit": 10})
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


# I sette test che seguivano esercitavano il TRASPORTO HTTP: URL, header di
# autenticazione, timeout, validazione del JSON di risposta. Quel trasporto non
# esiste piu', quindi non e' rimasto niente da esercitare in quella forma.
#
# L'intento pero' sopravvive quasi tutto, e vive qui sotto tradotto: ordine
# delle fasi, scan in modalita' live senza rule_codes, un guasto applicativo
# nella recovery che NON ferma lo scan, un guasto tecnico che invece lo ferma,
# saturazione, configurazione numerica invalida, log sanitizzati.
#
# Una sola copertura sparisce davvero: la validazione del contratto JSON della
# risposta. Era una difesa contro un corpo HTTP malformato, e senza HTTP non
# c'e' un corpo da validare - le funzioni applicative restituiscono dizionari
# costruiti da `_recover_events`, la cui forma e' gia' fissata dai test di
# quel modulo.


def test_runner_scan_e_live_e_non_filtra_per_regola(cron, monkeypatch):
    """Modalita' live, nessun `rule_codes`: era il contratto del runner HTTP e
    resta quello, perche' un cron che girasse in simulazione non produrrebbe
    alcun effetto e nessuno se ne accorgerebbe."""
    from flow import service as flow_service

    visti = []
    monkeypatch.setattr(cron, "_active_agency_ids", lambda: [1])
    monkeypatch.setattr(flow_service, "recover_received_events_for_agency",
                        lambda a, l: {"status": "completed", "requested_limit": l,
                                      "processed": 0, "ignored": 0, "failed": 0,
                                      "busy": 0, "items": []})

    def scan(agency_id, payload):
        visti.append(payload)
        return {"status": "completed", "requested_limit": payload.limit,
                "processed": 0, "successes": 0, "failures": 0, "skips": 0}

    monkeypatch.setattr(flow_service, "scan_for_agency", scan)
    assert cron.main(["--agency-id", "1"]) == 0
    assert visti[0].simulation is False
    assert visti[0].rule_codes is None


def test_runner_un_guasto_applicativo_nella_recovery_non_ferma_lo_scan(cron, monkeypatch):
    """Applicativo non e' tecnico: gli eventi problematici non impediscono di
    valutare le regole, e l'esito resta 2."""
    from flow import service as flow_service

    chiamate = []
    monkeypatch.setattr(cron, "_active_agency_ids", lambda: [1])
    monkeypatch.setattr(flow_service, "recover_received_events_for_agency",
                        lambda a, l: chiamate.append("recovery") or {
                            "status": "partial_failure", "requested_limit": l,
                            "processed": 1, "ignored": 0, "failed": 1, "busy": 0,
                            "items": []})
    monkeypatch.setattr(flow_service, "scan_for_agency",
                        lambda a, p: chiamate.append("scan") or {
                            "status": "completed", "requested_limit": p.limit,
                            "processed": 1, "successes": 1, "failures": 0, "skips": 0})
    assert cron.main(["--agency-id", "1"]) == 2
    assert chiamate == ["recovery", "scan"]


def test_runner_un_guasto_tecnico_nella_recovery_ferma_lo_scan(cron, monkeypatch):
    """Tecnico invece si': se la recovery ha sollevato, lo stato del tenant non
    e' noto e lo scan lavorerebbe alla cieca."""
    from flow import service as flow_service

    chiamate = []
    monkeypatch.setattr(cron, "_active_agency_ids", lambda: [1])
    monkeypatch.setattr(flow_service, "recover_received_events_for_agency",
                        lambda a, l: (_ for _ in ()).throw(RuntimeError("giu'")))
    monkeypatch.setattr(flow_service, "scan_for_agency",
                        lambda a, p: chiamate.append("scan"))
    assert cron.main(["--agency-id", "1"]) == 1
    assert chiamate == [], "lo scan e' partito dopo un guasto tecnico"


def test_runner_saturazione_e_esito_due(cron, monkeypatch, capsys):
    """Il limite raggiunto significa che potrebbe esserci altro da fare."""
    from flow import service as flow_service

    monkeypatch.setattr(cron, "_active_agency_ids", lambda: [1])
    monkeypatch.setattr(flow_service, "recover_received_events_for_agency",
                        lambda a, l: {"status": "completed", "requested_limit": l,
                                      "processed": 0, "ignored": 0, "failed": 0,
                                      "busy": 0, "items": []})
    monkeypatch.setattr(flow_service, "scan_for_agency",
                        lambda a, p: {"status": "completed",
                                      "requested_limit": p.limit, "processed": p.limit,
                                      "successes": p.limit, "failures": 0, "skips": 0})
    assert cron.main(["--agency-id", "1"]) == 2
    assert "possible_saturation" in capsys.readouterr().out


def test_runner_configurazione_numerica_invalida_e_esito_uno(cron, monkeypatch):
    from flow import service as flow_service

    chiamate = Mock()
    monkeypatch.setenv("FLOW_SCAN_LIMIT", "zero")
    monkeypatch.setattr(flow_service, "recover_received_events_for_agency", chiamate)
    monkeypatch.setattr(flow_service, "scan_for_agency", chiamate)
    assert cron.main(["--agency-id", "1"]) == 1
    chiamate.assert_not_called()


def test_runner_i_log_restano_sanitizzati(cron, monkeypatch, capsys):
    """Nessun segreto nei log. Adesso e' piu' facile - non ci sono credenziali
    da stampare - ma il controllo resta: i contatori non devono trascinarsi
    dietro il contenuto degli eventi."""
    from flow import service as flow_service

    monkeypatch.setenv("ADMIN_USER", "utente-che-non-deve-comparire")
    monkeypatch.setenv("ADMIN_PASS", "segreto-che-non-deve-comparire")
    monkeypatch.setattr(cron, "_active_agency_ids", lambda: [1])
    monkeypatch.setattr(flow_service, "recover_received_events_for_agency",
                        lambda a, l: {"status": "completed", "requested_limit": l,
                                      "processed": 0, "ignored": 0, "failed": 0,
                                      "busy": 0,
                                      "items": [{"event_id": 1, "payload": "CORPO-RISERVATO"}]})
    monkeypatch.setattr(flow_service, "scan_for_agency",
                        lambda a, p: {"status": "completed", "requested_limit": p.limit,
                                      "processed": 0, "successes": 0, "failures": 0,
                                      "skips": 0, "runs": ["DETTAGLIO-RISERVATO"]})
    assert cron.main(["--agency-id", "1"]) == 0
    output = capsys.readouterr().out
    for segreto in ("utente-che-non-deve-comparire", "segreto-che-non-deve-comparire",
                    "CORPO-RISERVATO", "DETTAGLIO-RISERVATO"):
        assert segreto not in output
    assert "phase=recovery.agency_1" in output and "phase=scan.agency_1" in output


def test_runner_file_compiles_and_has_no_render_or_rule_activation_side_effects(tmp_path):
    path = ROOT / "run_flow_p2b_cron.py"
    environment={**os.environ,"PYTHONPYCACHEPREFIX":str(tmp_path)}
    result = subprocess.run([PYTHON, "-m", "py_compile", str(path)], cwd=ROOT, env=environment, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    source = path.read_text(encoding="utf-8")
    assert "render.com" not in source
    assert "/activate" not in source
    assert "FLOW-R005" not in source


# ---------------------------------------------------------------------------
# CRON FLOW - il runner in-process
#
# Il cron autenticava con HTTP Basic su route diventate session-only: 401, exit
# 1, e un log che diceva `http_or_network` perche' HTTPError e' sottoclasse di
# RequestException. Invece di dare al cron un'identita' tecnica nuova, e'
# stato tolto il trasporto: le funzioni applicative sono gia' per-agenzia, e
# senza rete non c'e' nulla da autenticare.
#
# Questi test fissano il contratto di quel runner.
# ---------------------------------------------------------------------------

import ast
import importlib

import pytest


@pytest.fixture()
def cron():
    return importlib.import_module("run_flow_p2b_cron")


@pytest.fixture()
def flow_double(monkeypatch, cron):
    """Sostituisce le funzioni applicative e registra l'ordine delle chiamate."""
    from flow import service as flow_service

    chiamate = []

    def recovery(agency_id, limit):
        chiamate.append(("recovery", agency_id, limit))
        if agency_id in getattr(recovery, "esplode", ()):
            raise RuntimeError("recovery rotta")
        return {"status": "completed", "requested_limit": limit,
                "processed": 0, "ignored": 0, "failed": 0, "busy": 0, "items": []}

    def scan(agency_id, payload):
        chiamate.append(("scan", agency_id, payload.limit))
        return {"status": "completed", "requested_limit": payload.limit,
                "processed": 0, "successes": 0, "failures": 0, "skips": 0}

    monkeypatch.setattr(flow_service, "recover_received_events_for_agency", recovery)
    monkeypatch.setattr(flow_service, "scan_for_agency", scan)
    monkeypatch.setattr(cron, "_active_agency_ids", lambda: [1, 2])
    return chiamate, recovery


def test_cron_modalita_singola_elabora_una_sola_agenzia(cron, flow_double):
    chiamate, _ = flow_double
    assert cron.main(["--agency-id", "2"]) == 0
    assert [(fase, agenzia) for fase, agenzia, _ in chiamate] == [
        ("recovery", 2), ("scan", 2)]


def test_cron_modalita_multipla_elabora_tutte_le_attive(cron, flow_double):
    chiamate, _ = flow_double
    assert cron.main(["--all-agencies"]) == 0
    assert [(fase, agenzia) for fase, agenzia, _ in chiamate] == [
        ("recovery", 1), ("scan", 1), ("recovery", 2), ("scan", 2)]


def test_cron_recovery_precede_sempre_lo_scan(cron, flow_double):
    """La recovery sblocca gli eventi che lo scan deve poter valutare."""
    chiamate, _ = flow_double
    cron.main(["--all-agencies"])
    for agenzia in (1, 2):
        fasi = [f for f, a, _ in chiamate if a == agenzia]
        assert fasi.index("recovery") < fasi.index("scan"), (agenzia, fasi)


def test_cron_senza_modalita_non_parte(cron, flow_double, capsys):
    """Nessun default implicito: il runner HTTP risolveva la Default senza
    dirlo, e con una seconda agenzia attiva avrebbe continuato a servire solo
    la prima."""
    chiamate, _ = flow_double
    assert cron.main([]) == 1
    assert chiamate == []
    assert "phase=config status=failed" in capsys.readouterr().out


def test_cron_le_due_modalita_si_escludono(cron, flow_double):
    chiamate, _ = flow_double
    assert cron.main(["--agency-id", "1", "--all-agencies"]) == 1
    assert chiamate == []


def test_cron_rifiuta_una_agenzia_non_attiva(cron, flow_double, capsys):
    """Sospesa non e' "attiva con zero righe": elaborarla la farebbe risultare
    lavorata con successo."""
    chiamate, _ = flow_double
    assert cron.main(["--agency-id", "99"]) == 1
    assert chiamate == []
    assert "agenzia_non_attiva" in capsys.readouterr().out


def test_cron_un_errore_su_A_non_impedisce_B_ma_esce_non_zero(cron, flow_double):
    """La regola che rende utile la modalita' multipla: un tenant rotto non
    sospende la piattaforma. Ma "abbiamo continuato" non e' "e' andato bene"."""
    chiamate, recovery = flow_double
    recovery.esplode = (1,)
    esito = cron.main(["--all-agencies"])
    agenzie = {a for _f, a, _l in chiamate}
    assert 2 in agenzie, "B non e' stata elaborata dopo il fallimento di A"
    assert ("scan", 1) not in [(f, a) for f, a, _ in chiamate], (
        "lo scan di A e' partito nonostante la recovery di A sia fallita")
    assert esito == 1, "un fallimento tecnico deve produrre un esito non-zero"


def test_cron_rispetta_i_limiti_configurati(cron, flow_double, monkeypatch):
    monkeypatch.setenv("FLOW_RECOVERY_LIMIT", "7")
    monkeypatch.setenv("FLOW_SCAN_LIMIT", "9")
    chiamate, _ = flow_double
    cron.main(["--agency-id", "1"])
    assert [(f, l) for f, _a, l in chiamate] == [("recovery", 7), ("scan", 9)]


def test_cron_un_problema_applicativo_esce_2(cron, monkeypatch, flow_double):
    from flow import service as flow_service

    monkeypatch.setattr(flow_service, "scan_for_agency", lambda a, p: {
        "status": "partial_failure", "requested_limit": p.limit,
        "processed": 1, "successes": 0, "failures": 1, "skips": 0})
    assert cron.main(["--agency-id", "1"]) == 2


def test_cron_non_usa_ne_http_ne_basic(cron):
    """LA REGRESSIONE DELL'INCIDENTE.

    Strutturale e non testuale: il modulo non deve IMPORTARE `requests` ne'
    leggere le credenziali condivise. Un controllo sulla stringa sarebbe
    soddisfatto anche cancellando il commento che spiega perche' il trasporto
    e' stato tolto.
    """
    albero = ast.parse(pathlib.Path(cron.__file__).read_text(encoding="utf-8"))
    importati = {a.name.split(".")[0] for n in ast.walk(albero)
                 if isinstance(n, ast.Import) for a in n.names}
    importati |= {n.module.split(".")[0] for n in ast.walk(albero)
                  if isinstance(n, ast.ImportFrom) and n.module}
    for vietato in ("requests", "httpx", "urllib", "http"):
        assert vietato not in importati, f"il cron importa {vietato}"

    letture = {n.args[0].value for n in ast.walk(albero)
               if isinstance(n, ast.Call)
               and getattr(n.func, "attr", None) == "getenv"
               and n.args and isinstance(n.args[0], ast.Constant)}
    assert not (letture & {"ADMIN_USER", "ADMIN_PASS"}), (
        f"il cron legge ancora le credenziali condivise: {letture}")


def test_cron_porta_lo_scope_in_tutta_la_catena(cron):
    """Chiama solo i gemelli per-agenzia.

    `recover_received_events` e `scan` senza `agency_id` esistono ancora per il
    router: una chiamata a quelli da qui sarebbe una passata cross-tenant che
    nessun errore segnalerebbe.
    """
    albero = ast.parse(pathlib.Path(cron.__file__).read_text(encoding="utf-8"))
    invocate = {n.func.attr for n in ast.walk(albero)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "recover_received_events_for_agency" in invocate
    assert "scan_for_agency" in invocate
    for vietata in ("recover_received_events", "scan_for_all_agencies"):
        assert vietata not in invocate, (
            f"il cron chiama {vietata}: lo scope non e' piu' esplicito per agenzia")


def test_cron_usa_solo_wrapper_db_approvati(cron):
    """Nessun accesso alternativo: le agenzie attive arrivano dalla funzione
    gia' certificata, non da una query scritta qui."""
    albero = ast.parse(pathlib.Path(cron.__file__).read_text(encoding="utf-8"))
    invocate = {n.func.attr for n in ast.walk(albero)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "list_active_agency_ids" in invocate

    # Strutturale: un divieto sulla stringa "SELECT" sarebbe violato dal
    # commento che spiega DOVE vive il predicato di tenant - ed e' proprio il
    # commento che serve a chi legge.
    importati = {a.name.split(".")[0] for n in ast.walk(albero)
                 if isinstance(n, ast.Import) for a in n.names}
    importati |= {n.module.split(".")[0] for n in ast.walk(albero)
                  if isinstance(n, ast.ImportFrom) and n.module}
    assert "psycopg2" not in importati
    for vietata in ("core_cursor", "execute", "cursor"):
        assert vietata not in invocate, f"il cron apre un accesso proprio: {vietata}"


def test_cron_carica_la_configurazione_in_un_processo_separato(tmp_path):
    """Il cron e' un processo a se': non deve importare il web server.

    Se `main` finisse nella catena di import, il cron dipenderebbe dall'avvio
    dell'applicazione web - e un errore la' dentro lo farebbe fallire per una
    ragione che non lo riguarda.
    """
    import subprocess
    import sys

    codice = (
        "import sys; import run_flow_p2b_cron as c;"
        "cfg = c.load_config(['--agency-id','3']);"
        "print('agency', cfg.agency_id, 'all', cfg.all_agencies,"
        " 'limits', cfg.recovery_limit, cfg.scan_limit);"
        "print('main_importato', 'main' in sys.modules);"
        "print('requests_importato', 'requests' in sys.modules)"
    )
    esito = subprocess.run(
        [sys.executable, "-c", codice], cwd=str(ROOT), capture_output=True, text=True,
        env={"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1",
             "FLOW_RECOVERY_LIMIT": "11", "FLOW_SCAN_LIMIT": "13",
             "HOME": os.environ.get("HOME", "/tmp")},
        timeout=60)
    assert esito.returncode == 0, esito.stderr[-800:]
    assert "agency 3 all False limits 11 13" in esito.stdout
    assert "main_importato False" in esito.stdout, (
        "importare il cron tira dentro il web server")
    assert "requests_importato False" in esito.stdout
