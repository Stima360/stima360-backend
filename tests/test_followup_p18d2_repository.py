from __future__ import annotations

import copy
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from followup import repository


def _unwrap_json_adapter(value):
    return getattr(value, "adapted", value)


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self.rows = []

    def execute(self, query, params=None):
        sql = " ".join(str(query).split()).lower()
        self.db.sql.append((sql, params))

        if "insert into followup_actions" in sql:
            key = params["idempotency_key"]
            existing = next((x for x in self.db.actions if x["idempotency_key"] == key), None)
            if existing is not None:
                self.rows = []
                return
            row = {
                "id": len(self.db.actions) + 1,
                "rule_code": params["rule_code"],
                "trigger_type": params["trigger_type"],
                "contact_id": params["contact_id"],
                "lead_id": params["lead_id"],
                "stima_id": params["stima_id"],
                "idempotency_key": key,
                "task_id": None,
                "status": "pending",
                "error_message": None,
                "created_at": datetime.now(timezone.utc),
            }
            self.db.actions.append(row)
            self.rows = [copy.deepcopy(row)]
            return

        if "from followup_actions where idempotency_key" in sql:
            key = params[0]
            existing = next((x for x in self.db.actions if x["idempotency_key"] == key), None)
            self.rows = [copy.deepcopy(existing)] if existing else []
            return

        if sql.startswith("update followup_actions"):
            if "status = 'completed'" in sql:
                task_id, action_id = params
                for row in self.db.actions:
                    if row["id"] == action_id:
                        row["status"] = "completed"
                        row["task_id"] = task_id
                        row["error_message"] = None
                self.rows = []
                return
            if "status = 'failed'" in sql:
                error_message, action_id = params
                for row in self.db.actions:
                    if row["id"] == action_id and row["status"] == "pending":
                        row["status"] = "failed"
                        row["error_message"] = error_message
                self.rows = []
                return

        if "select id, priority, metadata from tasks where id =" in sql:
            task_id = params[0]
            row = next((t for t in self.db.tasks if t["id"] == task_id), None)
            self.rows = [copy.deepcopy({"id": row["id"], "priority": row["priority"], "metadata": row["metadata"]})] if row else []
            return

        if "select t.id, t.contact_id, t.lead_id, t.stima_id, t.priority, t.metadata from tasks t" in sql:
            rule_code, limit = params
            now = datetime.now(timezone.utc)
            items = []
            for task in self.db.tasks:
                if task["status"] != "open":
                    continue
                if task["priority"] not in {"low", "normal"}:
                    continue
                if task["task_type"] != "automated_followup":
                    continue
                if task["title"] != "Contattare proprietario":
                    continue
                if task["due_at"] is None or task["due_at"] > now - timedelta(hours=24):
                    continue
                md = task.get("metadata") or {}
                if md.get("source") != "followup":
                    continue
                if md.get("rule_code") != "FOLLOWUP_STIMA_RICHIESTA":
                    continue
                lead_id = task.get("lead_id")
                if lead_id is not None:
                    lead = self.db.leads.get(lead_id)
                    if not lead:
                        continue
                    if lead["status"] in {"closed", "paused"}:
                        continue
                    if lead["stage"] != "new":
                        continue
                key = f"followup:time:{rule_code}:task:{task['id']}:v1"
                if any(a["idempotency_key"] == key and a["status"] == "completed" for a in self.db.actions):
                    continue
                items.append(
                    {
                        "id": task["id"],
                        "contact_id": task.get("contact_id"),
                        "lead_id": task.get("lead_id"),
                        "stima_id": task.get("stima_id"),
                        "priority": task["priority"],
                        "metadata": copy.deepcopy(task.get("metadata") or {}),
                    }
                )
            self.rows = items[:limit]
            return

        if sql.startswith("update tasks t set priority = 'high'"):
            metadata_patch, task_id = params
            metadata_patch = _unwrap_json_adapter(metadata_patch)
            now = datetime.now(timezone.utc)
            task = next((t for t in self.db.tasks if t["id"] == task_id), None)
            if not task:
                self.rows = []
                return
            md = task.get("metadata") or {}
            lead_id = task.get("lead_id")
            lead_ok = True
            if lead_id is not None:
                lead = self.db.leads.get(lead_id)
                lead_ok = bool(lead) and lead["status"] not in {"closed", "paused"} and lead["stage"] == "new"
            if not (
                task["status"] == "open"
                and task["priority"] in {"low", "normal"}
                and task["task_type"] == "automated_followup"
                and task["title"] == "Contattare proprietario"
                and task["due_at"] is not None
                and task["due_at"] <= now - timedelta(hours=24)
                and md.get("source") == "followup"
                and md.get("rule_code") == "FOLLOWUP_STIMA_RICHIESTA"
                and lead_ok
            ):
                self.rows = []
                return
            task["priority"] = "high"
            temporal_escalations = md.get("temporal_escalations")
            if not isinstance(temporal_escalations, dict):
                temporal_escalations = {}
            task["metadata"] = {
                **md,
                "temporal_escalations": {**temporal_escalations, **metadata_patch},
            }
            self.rows = [{"id": task_id}]
            return

        raise AssertionError(f"unexpected SQL: {sql}")

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class FakeDb:
    def __init__(self):
        self.tasks = []
        self.actions = []
        self.leads = {}
        self.sql = []

    @contextmanager
    def cursor(self, *, commit=False):
        yield self, FakeCursor(self)


def _base_task(task_id: int, **overrides):
    row = {
        "id": task_id,
        "contact_id": 1,
        "lead_id": 10,
        "stima_id": 100,
        "title": "Contattare proprietario",
        "task_type": "automated_followup",
        "status": "open",
        "priority": "normal",
        "due_at": datetime.now(timezone.utc) - timedelta(hours=25),
        "assigned_to": None,
        "metadata": {"source": "followup", "rule_code": "FOLLOWUP_STIMA_RICHIESTA"},
    }
    row.update(overrides)
    return row


def test_candidate_query_filters_only_v1_eligible_tasks(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr(repository, "followup_cursor", db.cursor)
    db.leads[10] = {"status": "open", "stage": "new"}
    db.tasks.extend(
        [
            _base_task(1),
            _base_task(2, status="in_progress"),
            _base_task(3, priority="high"),
            _base_task(4, due_at=datetime.now(timezone.utc) - timedelta(hours=2)),
            _base_task(5, metadata={"source": "followup", "rule_code": "OTHER"}),
            _base_task(6, lead_id=11),
            _base_task(7, lead_id=None),
        ]
    )
    db.leads[11] = {"status": "open", "stage": "contacted"}

    rows = repository.list_temporal_escalation_candidates(
        limit=100,
        rule_code="FOLLOWUP_TASK_STALE_ESCALATE_V1",
    )

    assert [r["id"] for r in rows] == [1, 7]


def test_candidate_query_excludes_task_with_completed_temporal_action(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr(repository, "followup_cursor", db.cursor)
    db.leads[10] = {"status": "open", "stage": "new"}
    db.tasks.append(_base_task(1))
    db.actions.append(
        {
            "id": 1,
            "idempotency_key": "followup:time:FOLLOWUP_TASK_STALE_ESCALATE_V1:task:1:v1",
            "status": "completed",
        }
    )

    rows = repository.list_temporal_escalation_candidates(
        limit=100,
        rule_code="FOLLOWUP_TASK_STALE_ESCALATE_V1",
    )
    assert rows == []


def test_execute_temporal_escalation_updates_priority_and_preserves_due_at(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr(repository, "followup_cursor", db.cursor)
    db.leads[10] = {"status": "open", "stage": "new"}
    due_at = datetime.now(timezone.utc) - timedelta(hours=30)
    db.tasks.append(_base_task(1, due_at=due_at, priority="low"))

    result = repository.execute_temporal_escalation(
        rule_code="FOLLOWUP_TASK_STALE_ESCALATE_V1",
        trigger_type="time",
        task_id=1,
        contact_id=1,
        lead_id=10,
        stima_id=100,
        idempotency_key="followup:time:FOLLOWUP_TASK_STALE_ESCALATE_V1:task:1:v1",
        created_by="FOLLOWUP",
    )

    assert result["status"] == "completed"
    assert db.tasks[0]["priority"] == "high"
    assert db.tasks[0]["due_at"] == due_at
    assert db.actions[0]["status"] == "completed"


def test_execute_temporal_escalation_preserves_other_temporal_entries(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr(repository, "followup_cursor", db.cursor)
    db.leads[10] = {"status": "open", "stage": "new"}
    db.tasks.append(
        _base_task(
            1,
            metadata={
                "source": "followup",
                "rule_code": "FOLLOWUP_STIMA_RICHIESTA",
                "temporal_escalations": {
                    "FOLLOWUP_TASK_STALE_ESCALATE_V0": {
                        "previous_priority": "low",
                        "new_priority": "normal",
                    }
                },
            },
        )
    )

    repository.execute_temporal_escalation(
        rule_code="FOLLOWUP_TASK_STALE_ESCALATE_V1",
        trigger_type="time",
        task_id=1,
        contact_id=1,
        lead_id=10,
        stima_id=100,
        idempotency_key="followup:time:FOLLOWUP_TASK_STALE_ESCALATE_V1:task:1:v1",
        created_by="FOLLOWUP",
    )

    temporal = db.tasks[0]["metadata"]["temporal_escalations"]
    assert "FOLLOWUP_TASK_STALE_ESCALATE_V0" in temporal
    assert "FOLLOWUP_TASK_STALE_ESCALATE_V1" in temporal
