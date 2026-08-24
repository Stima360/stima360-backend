from __future__ import annotations

import copy
import inspect
from contextlib import contextmanager

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from fastapi.security import HTTPBasicCredentials

from flow import repository, service
from flow.rules.registry import RULES, OWNER_RULES
from owner.router_admin import require_owner_admin
import flow.router as flow_router_module


def _owner_entity():
    return {
        "entity_type": "owner_feedback",
        "entity_id": 101,
        "owner_request_type": "contact_request",
        "property_id": 34,
        "contact_id": 77,
        "linked_activity_id": 501,
        "event_payload": {"owner_request_type": "contact_request"},
    }


def _owner_action():
    return {
        "action_type": "create_core_task",
        "title": "Contattare proprietario",
        "description": "Richiesta ricevuta dal portale proprietario.",
        "priority": "high",
        "due_hours": 4,
        "contact_id": 77,
        "lead_id": None,
        "assigned_to": None,
    }


class FakeState:
    def __init__(self):
        self.execution_seq = 800
        self.action_seq = 900
        self.task_seq = 1000
        self.executions = {}
        self.actions = {}
        self.tasks = {}
        self.fail_create_task_once = False
        self.fail_finalize_once = False
        self.create_task_calls = 0


class FakeCursor:
    def __init__(self, state: FakeState):
        self.state = state
        self.current = None
        self.rowcount = 0

    def execute(self, sql, params=None):
        q = " ".join(str(sql).split())
        self.current = None
        self.rowcount = 0

        if q.startswith("INSERT INTO flow_executions"):
            self.state.execution_seq += 1
            eid = self.state.execution_seq
            self.state.executions[eid] = {
                "id": eid,
                "event_id": params[0],
                "status": params[4],
                "retry_of_execution_id": params[-1],
                "actions_result": {},
                "error_message": None,
            }
            self.current = dict(self.state.executions[eid])
            self.rowcount = 1
            return

        if q.startswith("UPDATE flow_executions SET status='not_matched'"):
            eid = params[0]
            self.state.executions[eid]["status"] = "not_matched"
            self.current = dict(self.state.executions[eid])
            self.rowcount = 1
            return

        if q.startswith("INSERT INTO flow_action_records") and "'pending'" in q:
            ex_id, action_type, idem, payload = params
            if idem not in self.state.actions:
                self.state.action_seq += 1
                aid = self.state.action_seq
                self.state.actions[idem] = {
                    "id": aid,
                    "execution_id": ex_id,
                    "action_type": action_type,
                    "target_entity_type": "task",
                    "target_entity_id": None,
                    "idempotency_key": idem,
                    "payload": payload,
                    "status": "pending",
                    "error_message": None,
                }
                self.current = dict(self.state.actions[idem])
                self.rowcount = 1
            return

        if q.startswith("SELECT * FROM flow_action_records WHERE idempotency_key="):
            idem = params[0]
            row = self.state.actions.get(idem)
            self.current = dict(row) if row else None
            self.rowcount = 1 if row else 0
            return

        if q.startswith("SELECT id FROM tasks WHERE metadata->>'idempotency_key'="):
            idem = params[0]
            task = self.state.tasks.get(idem)
            self.current = {"id": task["id"]} if task else None
            self.rowcount = 1 if task else 0
            return

        if q.startswith("UPDATE flow_action_records SET execution_id=") and "target_entity_type='task'" in q:
            if self.state.fail_finalize_once:
                self.state.fail_finalize_once = False
                raise RuntimeError("finalize failed")
            ex_id, task_id, action_id = params
            for row in self.state.actions.values():
                if row["id"] == action_id:
                    row.update(
                        execution_id=ex_id,
                        target_entity_type="task",
                        target_entity_id=task_id,
                        status="completed",
                        error_message=None,
                    )
                    self.rowcount = 1
                    return
            return

        if q.startswith("UPDATE flow_executions SET status='executed'"):
            result, eid = params
            self.state.executions[eid].update(status="executed", actions_result=result, error_message=None)
            self.current = dict(self.state.executions[eid])
            self.rowcount = 1
            return

        if q.startswith("UPDATE flow_executions SET status='skipped'"):
            result, eid = params
            self.state.executions[eid].update(status="skipped", actions_result=result)
            self.current = dict(self.state.executions[eid])
            self.rowcount = 1
            return

        if q.startswith("UPDATE flow_action_records SET execution_id=") and "status='failed'" in q:
            ex_id, error_message, idem = params
            row = self.state.actions.get(idem)
            if row and row["status"] != "completed":
                row.update(execution_id=ex_id, status="failed", error_message=error_message)
                self.rowcount = 1
            return

        if q.startswith("INSERT INTO flow_action_records") and "'failed'" in q:
            ex_id, action_type, idem, payload, error_message = params
            if idem not in self.state.actions:
                self.state.action_seq += 1
                self.state.actions[idem] = {
                    "id": self.state.action_seq,
                    "execution_id": ex_id,
                    "action_type": action_type,
                    "target_entity_type": "task",
                    "target_entity_id": None,
                    "idempotency_key": idem,
                    "payload": payload,
                    "status": "failed",
                    "error_message": error_message,
                }
                self.rowcount = 1
            return

        if q.startswith("UPDATE flow_executions SET status='failed'"):
            error_message, eid = params
            self.state.executions[eid].update(status="failed", error_message=error_message)
            self.current = dict(self.state.executions[eid])
            self.rowcount = 1
            return

        raise AssertionError(f"Unexpected SQL: {q}")

    def fetchone(self):
        return self.current


def _install_fake_runtime(monkeypatch, state: FakeState):
    @contextmanager
    def fake_cursor(commit=False):
        actions_snapshot = copy.deepcopy(state.actions)
        executions_snapshot = copy.deepcopy(state.executions)
        cursor = FakeCursor(state)
        try:
            yield object(), cursor
        except Exception:
            if commit:
                state.actions = actions_snapshot
                state.executions = executions_snapshot
            raise

    monkeypatch.setattr(repository, "core_cursor", fake_cursor)
    monkeypatch.setattr(
        repository,
        "get_rule_row",
        lambda code: {
            "id": 8 if code == "FLOW-R008" else 1,
            "is_active": True,
            "parameters": dict((OWNER_RULES if code == "FLOW-R008" else RULES)[code].default_parameters),
        },
    )
    monkeypatch.setattr(repository, "is_suppressed", lambda *args: False)

    def fake_create_task(data):
        state.create_task_calls += 1
        if state.fail_create_task_once:
            state.fail_create_task_once = False
            raise RuntimeError("create_task failed")
        idem = data["metadata"]["idempotency_key"]
        if idem in state.tasks:
            raise AssertionError("duplicate CORE task creation attempted")
        state.task_seq += 1
        task = {"id": state.task_seq, **data}
        state.tasks[idem] = task
        return task

    monkeypatch.setattr(repository.core_repository, "create_task", fake_create_task)


def test_failed_task_creation_is_recoverable_without_duplicate(monkeypatch):
    state = FakeState()
    state.fail_create_task_once = True
    _install_fake_runtime(monkeypatch, state)

    first = repository.execute_live("FLOW-R008", _owner_entity(), True, [], _owner_action(), event_id=701)
    assert first["status"] == "failed"
    assert state.actions["FLOW-R008:event:701"]["status"] == "failed"
    assert state.tasks == {}

    recovered = repository.execute_live(
        "FLOW-R008", _owner_entity(), True, [], _owner_action(), event_id=701, retry_of_execution_id=first["id"]
    )
    assert recovered["status"] == "executed"
    assert len(state.tasks) == 1
    action = state.actions["FLOW-R008:event:701"]
    assert action["status"] == "completed"
    assert action["target_entity_id"] == next(iter(state.tasks.values()))["id"]

    replay = repository.execute_live("FLOW-R008", _owner_entity(), True, [], _owner_action(), event_id=701)
    assert replay["status"] == "skipped"
    assert len(state.tasks) == 1


def test_task_created_then_finalize_failure_reconciles_existing_task(monkeypatch):
    state = FakeState()
    state.fail_finalize_once = True
    _install_fake_runtime(monkeypatch, state)

    first = repository.execute_live("FLOW-R008", _owner_entity(), True, [], _owner_action(), event_id=702)
    assert first["status"] == "failed"
    assert len(state.tasks) == 1
    assert state.actions["FLOW-R008:event:702"]["status"] == "failed"
    assert state.create_task_calls == 1

    recovered = repository.execute_live(
        "FLOW-R008", _owner_entity(), True, [], _owner_action(), event_id=702, retry_of_execution_id=first["id"]
    )
    assert recovered["status"] == "executed"
    assert len(state.tasks) == 1
    assert state.create_task_calls == 1
    action = state.actions["FLOW-R008:event:702"]
    assert action["status"] == "completed"
    assert action["target_entity_id"] == next(iter(state.tasks.values()))["id"]


def test_process_saved_event_failed_execution_sets_failed_then_recovery_processed(monkeypatch):
    saved = {
        "id": 703,
        "event_type": "owner.request_submitted",
        "entity_type": "owner_feedback",
        "entity_id": 101,
        "payload": {"owner_request_type": "contact_request"},
    }
    rows = [{
        "code": "FLOW-R008",
        "is_active": True,
        "event_type": "owner.request_submitted",
        "entity_type": "owner_feedback",
        "parameters": dict(OWNER_RULES["FLOW-R008"].default_parameters),
    }]
    statuses = []
    outcomes = iter([{"id": 1, "status": "failed"}, {"id": 2, "status": "executed"}])

    monkeypatch.setattr(service.repository, "get_event", lambda event_id: saved)
    monkeypatch.setattr(service.repository, "list_rules", lambda: rows)
    monkeypatch.setattr(service, "load_entity", lambda *args: _owner_entity())
    monkeypatch.setattr(service.repository, "execute_live", lambda *args, **kwargs: next(outcomes))
    monkeypatch.setattr(
        service.repository,
        "update_event_status",
        lambda event_id, status, error_message=None: statuses.append(status) or {**saved, "status": status},
    )

    first = service.process_saved_event(703)
    second = service.process_saved_event(703)
    assert first["event"]["status"] == "failed"
    assert second["event"]["status"] == "processed"
    assert statuses == ["failed", "processed"]


def test_flow_router_is_protected_by_existing_owner_admin_dependency():
    src = inspect.getsource(flow_router_module)
    assert "from owner.router_admin import require_owner_admin" in src
    assert "dependencies=[Depends(require_owner_admin)]" in src


def test_flow_router_http_auth_anonymous_bad_and_valid(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "p8admin")
    monkeypatch.setenv("ADMIN_PASS", "p8secret")
    monkeypatch.setattr(flow_router_module.service, "dashboard", lambda: {"ok": True})
    app = FastAPI()
    app.include_router(flow_router_module.router)
    client = TestClient(app)

    assert client.get("/api/flow/dashboard").status_code == 401
    assert client.get("/api/flow/dashboard", auth=("p8admin", "wrong")).status_code == 401
    response = client.get("/api/flow/dashboard", auth=("p8admin", "p8secret"))
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_existing_admin_auth_contract_is_fail_closed_and_accepts_valid_credentials(monkeypatch):
    monkeypatch.delenv("ADMIN_USER", raising=False)
    monkeypatch.delenv("ADMIN_PASS", raising=False)
    with pytest.raises(HTTPException) as missing:
        require_owner_admin(None)
    assert missing.value.status_code == 503

    monkeypatch.setenv("ADMIN_USER", "p8admin")
    monkeypatch.setenv("ADMIN_PASS", "p8secret")

    with pytest.raises(HTTPException) as anonymous:
        require_owner_admin(None)
    assert anonymous.value.status_code == 401

    with pytest.raises(HTTPException) as bad:
        require_owner_admin(HTTPBasicCredentials(username="p8admin", password="wrong"))
    assert bad.value.status_code == 401

    assert require_owner_admin(HTTPBasicCredentials(username="p8admin", password="p8secret")) == "p8admin"


def test_legacy_and_owner_idempotency_scopes_remain_unchanged():
    assert all(rule.idempotency_scope == "cooldown" for rule in RULES.values())
    assert all(rule.idempotency_scope == "event" for rule in OWNER_RULES.values())
