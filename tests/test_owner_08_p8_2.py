from __future__ import annotations

import inspect
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from core import repository as core_repo
from owner import repository as owner_repo


FEEDBACK_TYPES = (
    "contact_request",
    "correction_request",
    "general_message",
    "strategy_feedback",
    "price_review",
    "availability_update",
    "document_question",
)


@pytest.fixture(autouse=True)
def _p8_2_isolate_newer_flow_side_effect(monkeypatch):
    """P8.2 regression focuses on the validated OWNER↔CORE contract.

    P8.3B adds a FLOW event after the P8.2 link step; stub that newer layer here
    so the historical P8.2 failure-injection tests keep exercising only P8.2.
    """
    monkeypatch.setattr(owner_repo, "add_event_with_cursor", lambda cur, data: {"id": 701})


class TxState:
    def __init__(self, cursor):
        self.cursor = cursor
        self.calls = []
        self.committed = False
        self.rolled_back = False

    def factory(self, commit=False):
        self.calls.append(commit)

        @contextmanager
        def cm():
            try:
                yield object(), self.cursor
            except Exception:
                self.rolled_back = True
                raise
            else:
                if commit:
                    self.committed = True

        return cm()


class OwnerCursor:
    def __init__(self, *, link_rowcount=1, fail_feedback=False, fail_audit=False):
        self.link_rowcount = link_rowcount
        self.fail_feedback = fail_feedback
        self.fail_audit = fail_audit
        self.rowcount = -1
        self.current = None
        self.sql = []
        self.params = []
        self.submitted_at = datetime(2026, 8, 18, 16, 30, tzinfo=timezone.utc)

    def execute(self, sql, params=None):
        normalized = " ".join(str(sql).split())
        self.sql.append(normalized)
        self.params.append(params)
        self.rowcount = -1
        if normalized.startswith("SELECT oa.contact_id"):
            self.current = {"contact_id": 77}
        elif "INSERT INTO owner_feedback" in normalized:
            if self.fail_feedback:
                raise RuntimeError("feedback insert failed")
            self.current = {
                "id": 101,
                "feedback_type": params[2],
                "subject": params[3],
                "message": params[4],
                "status": "new",
                "submitted_at": self.submitted_at,
                "availability_from": params[5],
                "availability_to": params[6],
                "handled_at": None,
                "public_response": None,
            }
        elif normalized.startswith("UPDATE owner_feedback"):
            self.rowcount = self.link_rowcount
            self.current = None
        elif "INSERT INTO owner_audit_log" in normalized:
            if self.fail_audit:
                raise RuntimeError("audit failed")
            self.current = None
        else:
            self.current = None

    def fetchone(self):
        return self.current


class CoreCursor:
    def __init__(self):
        self.current = None
        self.sql = []
        self.params = []

    def execute(self, sql, params=None):
        normalized = " ".join(str(sql).split())
        self.sql.append(normalized)
        self.params.append(params)
        if normalized.startswith("SELECT 1 FROM contacts"):
            self.current = {"exists": 1}
        elif "INSERT INTO activities" in normalized:
            metadata = params["metadata"]
            adapted = getattr(metadata, "adapted", None)
            self.current = {"id": 501, **params, "metadata": adapted}
        else:
            self.current = None

    def fetchone(self):
        return self.current


def feedback_payload(feedback_type):
    payload = {
        "feedback_type": feedback_type,
        "subject": f"Subject {feedback_type}",
        "message": f"Message {feedback_type}",
    }
    if feedback_type == "availability_update":
        payload["availability_from"] = datetime(2026, 8, 20, tzinfo=timezone.utc)
        payload["availability_to"] = datetime(2026, 8, 21, tzinfo=timezone.utc)
    return payload


def run_owner_create(monkeypatch, feedback_type="general_message", *, cursor=None, activity=None):
    cursor = cursor or OwnerCursor()
    tx = TxState(cursor)
    captured = []

    def fake_activity(cur, data):
        captured.append((cur, data))
        if isinstance(activity, Exception):
            raise activity
        return activity or {"id": 501}

    monkeypatch.setattr(owner_repo, "core_cursor", tx.factory)
    monkeypatch.setattr(owner_repo, "create_activity_with_cursor", fake_activity)
    result = owner_repo.create_feedback(12, 34, feedback_payload(feedback_type))
    return result, tx, cursor, captured


@pytest.mark.parametrize("feedback_type", FEEDBACK_TYPES)
def test_all_real_feedback_types_create_one_core_activity(monkeypatch, feedback_type):
    result, tx, cursor, captured = run_owner_create(monkeypatch, feedback_type)
    assert tx.calls == [True]
    assert tx.committed and not tx.rolled_back
    assert len(captured) == 1
    cur, data = captured[0]
    assert cur is cursor
    assert data["activity_type"] == "note"
    assert data["direction"] == "in"
    assert data["channel"] == "owner_portal"
    assert data["subject"] == f"Subject {feedback_type}"
    assert data["description"] == f"Message {feedback_type}"
    assert data["contact_id"] == 77
    assert data["lead_id"] is None
    assert data["stima_id"] is None
    assert data["occurred_at"] == cursor.submitted_at
    assert data["created_by"] is None
    assert data["outcome"] is None
    assert data["metadata"] == {
        "source_module": "owner",
        "owner_feedback_id": 101,
        "owner_request_type": feedback_type,
        "property_id": 34,
    }
    assert set(result) == set(owner_repo.FEEDBACK_PUBLIC_FIELDS)


def test_contact_is_derived_server_side_and_access_is_locked(monkeypatch):
    _, _, cursor, captured = run_owner_create(monkeypatch)
    access_sql = cursor.sql[0]
    assert "SELECT oa.contact_id" in access_sql
    assert "JOIN owner_property_access" in access_sql
    assert "oa.status='active'" in access_sql
    assert "x.access_status='active'" in access_sql
    assert "x.revoked_at IS NULL" in access_sql
    assert "FOR UPDATE OF oa,x" in access_sql
    assert cursor.params[0] == (12, 34)
    assert captured[0][1]["contact_id"] == 77



def test_invalid_owner_or_property_access_creates_no_feedback_or_activity(monkeypatch):
    cursor = OwnerCursor()
    tx = TxState(cursor)
    activity_calls = []

    original_execute = cursor.execute
    def denied_execute(sql, params=None):
        normalized = " ".join(str(sql).split())
        if normalized.startswith("SELECT oa.contact_id"):
            cursor.sql.append(normalized)
            cursor.params.append(params)
            cursor.rowcount = -1
            cursor.current = None
            return
        return original_execute(sql, params)

    cursor.execute = denied_execute
    monkeypatch.setattr(owner_repo, "core_cursor", tx.factory)
    monkeypatch.setattr(owner_repo, "create_activity_with_cursor", lambda *args: activity_calls.append(args))
    with pytest.raises(owner_repo.NotFoundError):
        owner_repo.create_feedback(12, 34, feedback_payload("general_message"))
    assert tx.rolled_back and not tx.committed
    assert activity_calls == []
    assert not any("INSERT INTO owner_feedback" in sql for sql in cursor.sql)

def test_link_is_guarded_and_audit_uses_same_transaction(monkeypatch):
    _, tx, cursor, _ = run_owner_create(monkeypatch)
    link_index = next(i for i, sql in enumerate(cursor.sql) if sql.startswith("UPDATE owner_feedback"))
    link = cursor.sql[link_index]
    assert "linked_activity_id=%s" in link
    assert "linked_activity_id IS NULL" in link
    assert cursor.params[link_index] == (501, 101)
    audit_index = next(i for i, sql in enumerate(cursor.sql) if "INSERT INTO owner_audit_log" in sql)
    assert audit_index > link_index
    audit_params = cursor.params[audit_index]
    assert audit_params[:5] == (12, 34, "feedback_submitted", "owner_feedback", "101")
    assert tx.calls == [True]


def test_activity_failure_rolls_back_feedback(monkeypatch):
    cursor = OwnerCursor()
    tx = TxState(cursor)
    monkeypatch.setattr(owner_repo, "core_cursor", tx.factory)

    def fail_activity(cur, data):
        raise RuntimeError("activity failed")

    monkeypatch.setattr(owner_repo, "create_activity_with_cursor", fail_activity)
    with pytest.raises(RuntimeError, match="activity failed"):
        owner_repo.create_feedback(12, 34, feedback_payload("general_message"))
    assert tx.rolled_back and not tx.committed
    assert any("INSERT INTO owner_feedback" in sql for sql in cursor.sql)
    assert not any("INSERT INTO owner_audit_log" in sql for sql in cursor.sql)


def test_feedback_failure_creates_no_activity(monkeypatch):
    cursor = OwnerCursor(fail_feedback=True)
    tx = TxState(cursor)
    activity_calls = []
    monkeypatch.setattr(owner_repo, "core_cursor", tx.factory)
    monkeypatch.setattr(owner_repo, "create_activity_with_cursor", lambda *args: activity_calls.append(args))
    with pytest.raises(RuntimeError, match="feedback insert failed"):
        owner_repo.create_feedback(12, 34, feedback_payload("general_message"))
    assert tx.rolled_back and not tx.committed
    assert activity_calls == []


def test_link_failure_rolls_back_feedback_and_activity(monkeypatch):
    cursor = OwnerCursor(link_rowcount=0)
    tx = TxState(cursor)
    calls = []
    monkeypatch.setattr(owner_repo, "core_cursor", tx.factory)
    monkeypatch.setattr(owner_repo, "create_activity_with_cursor", lambda cur, data: calls.append(data) or {"id": 501})
    with pytest.raises(owner_repo.ConflictError, match="Collegamento activity OWNER non riuscito"):
        owner_repo.create_feedback(12, 34, feedback_payload("price_review"))
    assert len(calls) == 1
    assert tx.rolled_back and not tx.committed


def test_audit_failure_rolls_back_feedback_and_activity(monkeypatch):
    cursor = OwnerCursor(fail_audit=True)
    tx = TxState(cursor)
    calls = []
    monkeypatch.setattr(owner_repo, "core_cursor", tx.factory)
    monkeypatch.setattr(owner_repo, "create_activity_with_cursor", lambda cur, data: calls.append(data) or {"id": 501})
    with pytest.raises(RuntimeError, match="audit failed"):
        owner_repo.create_feedback(12, 34, feedback_payload("document_question"))
    assert len(calls) == 1
    assert tx.rolled_back and not tx.committed


def test_same_feedback_link_cannot_accept_second_activity(monkeypatch):
    cursor = OwnerCursor(link_rowcount=0)
    tx = TxState(cursor)
    calls = []
    monkeypatch.setattr(owner_repo, "core_cursor", tx.factory)
    monkeypatch.setattr(owner_repo, "create_activity_with_cursor", lambda cur, data: calls.append(data) or {"id": 999})
    with pytest.raises(owner_repo.ConflictError):
        owner_repo.create_feedback(12, 34, feedback_payload("correction_request"))
    assert len(calls) == 1
    assert tx.rolled_back
    assert any("linked_activity_id IS NULL" in sql for sql in cursor.sql)


def test_core_cursor_aware_helper_reuses_exact_activity_insert_without_transaction(monkeypatch):
    cursor = CoreCursor()

    def forbidden_core_cursor(*args, **kwargs):
        raise AssertionError("helper must not open a transaction")

    monkeypatch.setattr(core_repo, "core_cursor", forbidden_core_cursor)
    data = {
        "contact_id": 77,
        "lead_id": None,
        "stima_id": None,
        "activity_type": "note",
        "direction": "in",
        "channel": "owner_portal",
        "subject": "Subject",
        "description": "Message",
        "outcome": None,
        "occurred_at": datetime(2026, 8, 18, tzinfo=timezone.utc),
        "created_by": None,
        "metadata": {"source_module": "owner"},
    }
    result = core_repo.create_activity_with_cursor(cursor, data)
    assert result["id"] == 501
    assert result["metadata"] == {"source_module": "owner"}
    assert any("SELECT 1 FROM contacts" in sql for sql in cursor.sql)
    assert any("INSERT INTO activities" in sql for sql in cursor.sql)


def test_public_core_create_activity_keeps_own_transaction(monkeypatch):
    cursor = object()
    tx = TxState(cursor)
    calls = []
    monkeypatch.setattr(core_repo, "core_cursor", tx.factory)
    monkeypatch.setattr(core_repo, "create_activity_with_cursor", lambda cur, data: calls.append((cur, data)) or {"id": 5})
    payload = {"contact_id": 1}
    assert core_repo.create_activity(payload) == {"id": 5}
    assert tx.calls == [True]
    assert tx.committed
    assert calls == [(cursor, payload)]


def test_portal_feedback_whitelist_remains_internal_id_free():
    forbidden = {
        "id",
        "linked_activity_id",
        "activity_id",
        "contact_id",
        "lead_id",
        "stima_id",
        "metadata",
        "assigned_to",
        "task",
    }
    assert forbidden.isdisjoint(owner_repo.FEEDBACK_PUBLIC_FIELDS)
    row = {key: None for key in owner_repo.FEEDBACK_PUBLIC_FIELDS}
    row.update({"id": 1, "linked_activity_id": 2, "contact_id": 3})
    assert forbidden.isdisjoint(owner_repo._public_feedback(row))


def test_submit_does_not_emit_p5_notification_or_task_and_handled_p5_is_preserved():
    submit_src = inspect.getsource(owner_repo.create_feedback)
    assert "_emit_notification_event" not in submit_src
    assert "create_task" not in submit_src
    assert "_audit_with_cursor" in submit_src
    handled_src = inspect.getsource(owner_repo.update_feedback_status)
    assert "request_handled" in handled_src
    assert "_emit_notification_event" in handled_src
    assert "first_handling" in handled_src


def test_core_helper_is_cursor_aware_by_source_contract():
    helper_src = inspect.getsource(core_repo.create_activity_with_cursor)
    public_src = inspect.getsource(core_repo.create_activity)
    assert "core_cursor" not in helper_src
    assert "_validate_references" in helper_src
    assert "INSERT INTO activities" in helper_src
    assert "with core_cursor(commit=True)" in public_src
    assert "create_activity_with_cursor(cur, data)" in public_src


def test_owner_feedback_and_real_core_helper_share_one_cursor_and_transaction(monkeypatch):
    class IntegratedCursor(OwnerCursor):
        def execute(self, sql, params=None):
            normalized = " ".join(str(sql).split())
            self.sql.append(normalized)
            self.params.append(params)
            self.rowcount = -1
            if normalized.startswith("SELECT oa.contact_id"):
                self.current = {"contact_id": 77}
            elif "INSERT INTO owner_feedback" in normalized:
                self.current = {
                    "id": 101,
                    "feedback_type": params[2],
                    "subject": params[3],
                    "message": params[4],
                    "status": "new",
                    "submitted_at": self.submitted_at,
                    "availability_from": params[5],
                    "availability_to": params[6],
                    "handled_at": None,
                    "public_response": None,
                }
            elif normalized.startswith("SELECT 1 FROM contacts"):
                self.current = {"exists": 1}
            elif "INSERT INTO activities" in normalized:
                self.current = {"id": 501}
            elif normalized.startswith("UPDATE owner_feedback"):
                self.rowcount = 1
                self.current = None
            elif "INSERT INTO owner_audit_log" in normalized:
                self.current = None
            else:
                self.current = None

    cursor = IntegratedCursor()
    tx = TxState(cursor)
    monkeypatch.setattr(owner_repo, "core_cursor", tx.factory)
    monkeypatch.setattr(owner_repo, "create_activity_with_cursor", core_repo.create_activity_with_cursor)

    result = owner_repo.create_feedback(12, 34, feedback_payload("price_review"))

    assert tx.calls == [True]
    assert tx.committed and not tx.rolled_back
    assert set(result) == set(owner_repo.FEEDBACK_PUBLIC_FIELDS)
    expected_order = (
        "SELECT oa.contact_id",
        "INSERT INTO owner_feedback",
        "SELECT 1 FROM contacts",
        "INSERT INTO activities",
        "UPDATE owner_feedback",
        "INSERT INTO owner_audit_log",
    )
    positions = []
    for needle in expected_order:
        positions.append(next(i for i, sql in enumerate(cursor.sql) if needle in sql))
    assert positions == sorted(positions)
    activity_params = next(params for sql, params in zip(cursor.sql, cursor.params) if "INSERT INTO activities" in sql)
    assert activity_params["contact_id"] == 77
    assert activity_params["lead_id"] is None
    assert activity_params["stima_id"] is None
    assert activity_params["activity_type"] == "note"
    assert activity_params["direction"] == "in"
    assert activity_params["channel"] == "owner_portal"
    assert activity_params["occurred_at"] == cursor.submitted_at
    metadata = getattr(activity_params["metadata"], "adapted", None)
    assert metadata == {
        "source_module": "owner",
        "owner_feedback_id": 101,
        "owner_request_type": "price_review",
        "property_id": 34,
    }
