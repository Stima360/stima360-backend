from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

from seller_intent import repository


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self.current = None

    def execute(self, query, params=None):
        sql = " ".join(str(query).split()).lower()
        self.db.sql.append((sql, params))
        assert " insert " not in f" {sql} "
        assert " update " not in f" {sql} "
        assert " delete " not in f" {sql} "
        self.current = self.db.row

    def fetchone(self):
        return self.current


class FakeDb:
    def __init__(self):
        self.sql = []
        self.row = None

    @contextmanager
    def cursor(self):
        yield self, FakeCursor(self)


def test_repository_is_read_only_and_returns_row(monkeypatch):
    db = FakeDb()
    db.row = {
        "lead_id": 14,
        "lead_status": "open",
        "lead_stage": "qualified",
        "has_stima_completata": True,
        "has_p18_followup_in_progress": True,
        "has_p18_followup_overdue": True,
        "latest_seller_origin_event_at": datetime(2026, 9, 1, tzinfo=timezone.utc),
    }
    monkeypatch.setattr(repository, "seller_intent_cursor", db.cursor)

    result = repository.get_lead_intent_inputs(14)

    assert result["lead_id"] == 14
    sql = db.sql[0][0]
    assert "from leads" in sql
    assert "from lead_stime" in sql
    assert "from seller_timeline_events" in sql
    assert "from tasks" in sql


def test_recency_query_includes_only_seller_origin_event_types(monkeypatch):
    db = FakeDb()
    db.row = {
        "lead_id": 14,
        "lead_status": "open",
        "lead_stage": "new",
        "has_stima_completata": False,
        "has_p18_followup_in_progress": False,
        "has_p18_followup_overdue": False,
        "latest_seller_origin_event_at": None,
    }
    monkeypatch.setattr(repository, "seller_intent_cursor", db.cursor)

    repository.get_lead_intent_inputs(14)

    sql = db.sql[0][0]
    assert "ste.event_type in ('stima_richiesta', 'stima_completata')" in sql
    assert "email_stima_inviata" not in sql


def test_missing_lead_returns_none(monkeypatch):
    db = FakeDb()
    db.row = None
    monkeypatch.setattr(repository, "seller_intent_cursor", db.cursor)
    assert repository.get_lead_intent_inputs(99999) is None

