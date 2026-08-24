from __future__ import annotations

import ast
import copy
import inspect
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import get_args

import pytest

from core import repository as core_repo
from flow import repository as flow_repo
from flow.schemas import EventCreate
from owner import repository as owner_repo
import integration_owner_request as owner_request_integration


FEEDBACK_TYPES = (
    "contact_request",
    "correction_request",
    "general_message",
    "strategy_feedback",
    "price_review",
    "availability_update",
    "document_question",
)


class MemoryState:
    def __init__(self):
        self.feedbacks = {}
        self.activities = {}
        self.flow_events = {}
        self.audits = []
        self.next_feedback_id = 101
        self.next_activity_id = 501
        self.next_flow_id = 701


class AtomicCursor:
    def __init__(self, state, *, fail_stage=None, link_rowcount=1):
        self.state = state
        self.fail_stage = fail_stage
        self.link_rowcount = link_rowcount
        self.rowcount = -1
        self.current = None
        self.executed = []
        self.submitted_at = datetime(2026, 8, 18, 18, 5, 7, 123456, tzinfo=timezone.utc)

    def execute(self, sql, params=None):
        query = " ".join(str(sql).split())
        self.executed.append((query, params))
        self.rowcount = -1
        self.current = None

        if query.startswith("SELECT oa.contact_id"):
            if self.fail_stage == "owner":
                self.current = None
            else:
                self.current = {"contact_id": 77}
            return

        if "INSERT INTO owner_feedback" in query:
            if self.fail_stage == "feedback":
                raise RuntimeError("feedback insert failed")
            feedback_id = self.state.next_feedback_id
            self.state.next_feedback_id += 1
            row = {
                "id": feedback_id,
                "owner_account_id": params[0],
                "property_id": params[1],
                "feedback_type": params[2],
                "subject": params[3],
                "message": params[4],
                "status": "new",
                "submitted_at": self.submitted_at,
                "availability_from": params[5],
                "availability_to": params[6],
                "handled_at": None,
                "public_response": None,
                "linked_activity_id": None,
            }
            self.state.feedbacks[feedback_id] = row
            self.current = dict(row)
            return

        if query.startswith("SELECT 1 FROM contacts"):
            self.current = {"exists": 1} if params[0] == 77 else None
            return

        if "INSERT INTO activities" in query:
            if self.fail_stage == "activity":
                raise RuntimeError("activity insert failed")
            activity_id = self.state.next_activity_id
            self.state.next_activity_id += 1
            metadata = getattr(params.get("metadata"), "adapted", params.get("metadata"))
            row = {"id": activity_id, **params, "metadata": metadata}
            self.state.activities[activity_id] = row
            self.current = dict(row)
            return

        if query.startswith("UPDATE owner_feedback"):
            if self.fail_stage == "link" or self.link_rowcount != 1:
                self.rowcount = 0
                return
            activity_id, feedback_id = params
            row = self.state.feedbacks.get(feedback_id)
            if row is None or row.get("linked_activity_id") is not None:
                self.rowcount = 0
                return
            row["linked_activity_id"] = activity_id
            self.rowcount = 1
            return

        if "INSERT INTO flow_events" in query:
            if self.fail_stage == "flow":
                raise RuntimeError("flow insert failed")
            event_type, entity_type, entity_id, source_module, payload_json, key, occurred_at = params
            payload = getattr(payload_json, "adapted", payload_json)
            if key in self.state.flow_events:
                row = self.state.flow_events[key]
            else:
                row = {
                    "id": self.state.next_flow_id,
                    "event_type": event_type,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "source_module": source_module,
                    "payload": payload,
                    "deduplication_key": key,
                    "status": "received",
                    "occurred_at": occurred_at if occurred_at is not None else "DB_NOW",
                    "received_at": "DB_NOW",
                }
                self.state.next_flow_id += 1
                self.state.flow_events[key] = row
            self.current = dict(row)
            return

        if "INSERT INTO owner_audit_log" in query:
            if self.fail_stage == "audit":
                raise RuntimeError("audit failed")
            self.state.audits.append(params)
            return

        raise AssertionError(f"Unexpected SQL in P8.3B test cursor: {query}")

    def fetchone(self):
        return self.current


class TxHarness:
    def __init__(self, state, cursor):
        self.state = state
        self.cursor = cursor
        self.calls = []
        self.committed = False
        self.rolled_back = False

    def factory(self, commit=False):
        self.calls.append(commit)

        @contextmanager
        def cm():
            snapshot = copy.deepcopy(self.state.__dict__)
            try:
                yield object(), self.cursor
            except Exception:
                self.state.__dict__.clear()
                self.state.__dict__.update(snapshot)
                self.rolled_back = True
                raise
            else:
                if commit:
                    self.committed = True

        return cm()


def payload(feedback_type="general_message"):
    data = {
        "feedback_type": feedback_type,
        "subject": f"Subject {feedback_type}",
        "message": f"Message {feedback_type}",
    }
    if feedback_type == "availability_update":
        data["availability_from"] = datetime(2026, 8, 20, tzinfo=timezone.utc)
        data["availability_to"] = datetime(2026, 8, 21, tzinfo=timezone.utc)
    return data


def run_atomic(monkeypatch, *, feedback_type="general_message", fail_stage=None, state=None):
    state = state or MemoryState()
    cursor = AtomicCursor(state, fail_stage=fail_stage)
    tx = TxHarness(state, cursor)
    monkeypatch.setattr(owner_repo, "core_cursor", tx.factory)
    monkeypatch.setattr(owner_repo, "create_activity_with_cursor", core_repo.create_activity_with_cursor)
    monkeypatch.setattr(owner_repo, "record_owner_request_event_with_cursor", owner_request_integration.record_owner_request_event_with_cursor)
    monkeypatch.setattr(owner_repo, "process_saved_owner_request_event", lambda event_id: None)
    result = owner_repo.create_feedback(12, 34, payload(feedback_type))
    return result, state, cursor, tx


@pytest.mark.parametrize("feedback_type", FEEDBACK_TYPES)
def test_each_owner_request_creates_exact_flow_contract(monkeypatch, feedback_type):
    result, state, cursor, tx = run_atomic(monkeypatch, feedback_type=feedback_type)
    assert tx.calls == [True]
    assert tx.committed and not tx.rolled_back
    assert len(state.feedbacks) == len(state.activities) == len(state.flow_events) == 1

    feedback = next(iter(state.feedbacks.values()))
    activity = next(iter(state.activities.values()))
    event = next(iter(state.flow_events.values()))
    assert feedback["linked_activity_id"] == activity["id"]
    assert event == {
        "id": 701,
        "event_type": "owner.request_submitted",
        "entity_type": "owner_feedback",
        "entity_id": feedback["id"],
        "source_module": "owner",
        "payload": {
            "owner_request_type": feedback_type,
            "property_id": 34,
            "contact_id": 77,
            "linked_activity_id": activity["id"],
        },
        "deduplication_key": f"owner:feedback:{feedback['id']}:submitted",
        "status": "received",
        "occurred_at": feedback["submitted_at"],
        "received_at": "DB_NOW",
    }
    assert set(event["payload"]) == {
        "owner_request_type", "property_id", "contact_id", "linked_activity_id"
    }
    assert "subject" not in event["payload"]
    assert "message" not in event["payload"]
    assert set(result) == set(owner_repo.FEEDBACK_PUBLIC_FIELDS)


def test_transaction_order_is_feedback_activity_link_flow_audit_same_cursor(monkeypatch):
    _, _, cursor, tx = run_atomic(monkeypatch, feedback_type="price_review")
    needles = (
        "SELECT oa.contact_id",
        "INSERT INTO owner_feedback",
        "SELECT 1 FROM contacts",
        "INSERT INTO activities",
        "UPDATE owner_feedback",
        "INSERT INTO flow_events",
        "INSERT INTO owner_audit_log",
    )
    positions = [next(i for i, (sql, _) in enumerate(cursor.executed) if needle in sql) for needle in needles]
    assert positions == sorted(positions)
    assert tx.calls == [True]


def test_flow_helper_is_cursor_aware_and_explicit_occurred_at_is_used(monkeypatch):
    state = MemoryState()
    cursor = AtomicCursor(state)
    stamp = datetime(2026, 8, 18, 17, 59, 1, 654321, tzinfo=timezone.utc)

    def forbidden(*args, **kwargs):
        raise AssertionError("add_event_with_cursor must not open core_cursor")

    monkeypatch.setattr(flow_repo, "core_cursor", forbidden)
    event = flow_repo.add_event_with_cursor(cursor, {
        "event_type": "owner.request_submitted",
        "entity_type": "owner_feedback",
        "entity_id": 101,
        "source_module": "owner",
        "payload": {"property_id": 34},
        "deduplication_key": "owner:feedback:101:submitted",
        "occurred_at": stamp,
    })
    assert event["occurred_at"] == stamp
    flow_sql, flow_params = next((sql, params) for sql, params in cursor.executed if "INSERT INTO flow_events" in sql)
    assert "COALESCE(%s,NOW())" in flow_sql
    assert flow_params[-1] == stamp


def test_public_add_event_keeps_own_transaction_and_old_now_fallback(monkeypatch):
    state = MemoryState()
    cursor = AtomicCursor(state)
    tx = TxHarness(state, cursor)
    monkeypatch.setattr(flow_repo, "core_cursor", tx.factory)
    data = {
        "event_type": "core.lead_created",
        "entity_type": "lead",
        "entity_id": 9,
        "source_module": "core",
        "payload": {},
        "deduplication_key": "legacy-flow-event",
    }
    event = flow_repo.add_event(data)
    assert tx.calls == [True]
    assert tx.committed
    assert event["occurred_at"] == "DB_NOW"
    flow_sql, flow_params = next((sql, params) for sql, params in cursor.executed if "INSERT INTO flow_events" in sql)
    assert "COALESCE(%s,NOW())" in flow_sql
    assert flow_params[-1] is None


def test_eventcreate_http_contract_remains_without_occurred_at():
    assert set(EventCreate.model_fields) == {
        "event_type", "entity_type", "entity_id", "source_module", "payload", "deduplication_key"
    }
    assert "occurred_at" not in EventCreate.model_fields
    annotation = EventCreate.model_fields["source_module"].annotation
    assert set(get_args(annotation)) == {"core", "property", "buy", "match", "flow", "owner"}


def test_flow_insert_preserves_dedup_on_conflict_semantics(monkeypatch):
    state = MemoryState()
    cursor = AtomicCursor(state)
    data = {
        "event_type": "owner.request_submitted",
        "entity_type": "owner_feedback",
        "entity_id": 101,
        "source_module": "owner",
        "payload": {"owner_request_type": "general_message"},
        "deduplication_key": "owner:feedback:101:submitted",
        "occurred_at": cursor.submitted_at,
    }
    first = flow_repo.add_event_with_cursor(cursor, data)
    second = flow_repo.add_event_with_cursor(cursor, {**data, "payload": {"owner_request_type": "price_review"}})
    assert first["id"] == second["id"] == 701
    assert len(state.flow_events) == 1
    assert state.flow_events[data["deduplication_key"]]["payload"] == {"owner_request_type": "general_message"}
    sql = next(sql for sql, _ in cursor.executed if "INSERT INTO flow_events" in sql)
    assert "ON CONFLICT(deduplication_key) DO UPDATE SET received_at=flow_events.received_at RETURNING *" in sql


def test_distinct_feedback_ids_generate_distinct_deduplication_keys(monkeypatch):
    state = MemoryState()
    run_atomic(monkeypatch, state=state, feedback_type="general_message")
    run_atomic(monkeypatch, state=state, feedback_type="price_review")
    assert set(state.flow_events) == {
        "owner:feedback:101:submitted",
        "owner:feedback:102:submitted",
    }


@pytest.mark.parametrize(
    ("stage", "expected_exception"),
    [
        ("owner", owner_repo.NotFoundError),
        ("feedback", RuntimeError),
        ("activity", RuntimeError),
        ("link", owner_repo.ConflictError),
        ("flow", RuntimeError),
        ("audit", RuntimeError),
    ],
)
def test_any_failure_rolls_back_feedback_activity_link_flow_and_audit(monkeypatch, stage, expected_exception):
    state = MemoryState()
    with pytest.raises(expected_exception):
        run_atomic(monkeypatch, state=state, fail_stage=stage)
    assert state.feedbacks == {}
    assert state.activities == {}
    assert state.flow_events == {}
    assert state.audits == []


def test_p8_3b_registers_event_only_no_task_rule_action_or_p5_submit_notification():
    submit_src = inspect.getsource(owner_repo.create_feedback)
    flow_helper_src = inspect.getsource(flow_repo.add_event_with_cursor)
    combined = submit_src + "\n" + flow_helper_src
    for forbidden in (
        "create_task", "execute_live", "flow_action_records", "_emit_notification_event", "sync_rules", "get_rule("
    ):
        assert forbidden not in combined
    assert "record_owner_request_event_with_cursor" in submit_src
    assert "INSERT INTO flow_events" in flow_helper_src

    handled_src = inspect.getsource(owner_repo.update_feedback_status)
    assert "request_handled" in handled_src
    assert "_emit_notification_event" in handled_src
    assert "first_handling" in handled_src


def test_portal_feedback_dto_remains_free_of_flow_and_core_internals():
    forbidden = {
        "id", "linked_activity_id", "activity_id", "contact_id", "lead_id", "stima_id",
        "flow_event_id", "deduplication_key", "source_module", "payload", "rule", "action", "task",
    }
    assert forbidden.isdisjoint(owner_repo.FEEDBACK_PUBLIC_FIELDS)
    row = {key: None for key in owner_repo.FEEDBACK_PUBLIC_FIELDS}
    row.update({
        "id": 101,
        "linked_activity_id": 501,
        "flow_event_id": 701,
        "deduplication_key": "secret",
        "source_module": "owner",
        "payload": {"internal": True},
    })
    assert forbidden.isdisjoint(owner_repo._public_feedback(row))


def test_flow_helper_source_keeps_legacy_default_key_when_explicit_key_absent():
    source = inspect.getsource(flow_repo.add_event_with_cursor)
    assert "data.get('deduplication_key') or" in source
    assert "%Y%m%d%H" in source
    assert "data.get('occurred_at')" in source


def test_owner_package_has_no_direct_buy_match_flow_imports():
    """Freeze the integration privacy boundary: OWNER cannot import BUY/MATCH/FLOW."""
    owner_dir = Path(owner_repo.__file__).resolve().parent
    forbidden = {"buy", "match", "flow"}
    violations = []
    for path in sorted(owner_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", 1)[0]
                    if root in forbidden:
                        violations.append((path.name, node.lineno, alias.name))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                root = module.split(".", 1)[0] if module else ""
                if root in forbidden:
                    violations.append((path.name, node.lineno, module))
    assert violations == []


def test_neutral_bridge_delegates_same_cursor_and_payload(monkeypatch):
    cursor = object()
    data = {
        "source_module": "owner",
        "event_type": "owner.request_submitted",
        "entity_type": "owner_feedback",
        "entity_id": 101,
        "deduplication_key": "owner:feedback:101:submitted",
        "payload": {
            "owner_request_type": "general_message",
            "property_id": 34,
            "contact_id": 77,
            "linked_activity_id": 501,
        },
        "occurred_at": datetime(2026, 8, 18, 18, 5, 7, tzinfo=timezone.utc),
    }
    captured = {}

    def fake_add_event_with_cursor(cur, event):
        captured["cur"] = cur
        captured["data"] = event
        return {"id": 701}

    monkeypatch.setattr(owner_request_integration, "_add_event_with_cursor", fake_add_event_with_cursor)
    result = owner_request_integration.record_owner_request_event_with_cursor(cursor, data)
    assert result == {"id": 701}
    assert captured["cur"] is cursor
    assert captured["data"] is data


def test_owner_repository_uses_neutral_bridge_not_direct_flow_import():
    source = Path(owner_repo.__file__).read_text()
    assert "from flow" not in source
    assert "import flow" not in source
    assert "from integration_owner_request import record_owner_request_event_with_cursor" in source
    submit_src = inspect.getsource(owner_repo.create_feedback)
    assert "record_owner_request_event_with_cursor" in submit_src
    assert "add_event_with_cursor" not in submit_src
