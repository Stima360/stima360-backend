"""P23 — repository tests against a fake in-memory cursor.

Mirrors the FakeCursor/FakeDb pattern used by
tests/test_seller_intent_repository.py: monkeypatch the module's own
`next_best_action_cursor` context manager so no real DB connection is
needed, while still exercising the exact SQL this repository issues.

Covers section 12.E (idempotency), 12.F (invalidation/pruning) and part
of 12.G (UNIQUE(subject_type, subject_id) behaviour at the application
level - the DB-level constraint itself is checked by the migration test).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

from next_best_action import repository


class FakeCursor:
    def __init__(self, db: "FakeDb"):
        self.db = db
        self._result: list[dict] = []

    def execute(self, query, params=None):
        sql = " ".join(str(query).split()).lower()
        if sql.startswith("insert into next_best_actions"):
            key = (params["subject_type"], params["subject_id"])
            row = dict(self.db.rows.get(key, {}))
            row.update(params)
            row.setdefault("id", self.db.next_id)
            self.db.next_id += 1
            row["updated_at"] = self.db.clock()
            self.db.rows[key] = row
        elif sql.startswith("select subject_type, subject_id from next_best_actions"):
            self._result = [{"subject_type": k[0], "subject_id": k[1]} for k in self.db.rows]
        elif sql.startswith("delete from next_best_actions where subject_type"):
            self.db.rows.pop(tuple(params), None)
        # P25.7: list_current/get_current now LEFT JOIN contacts for the
        # additive subject_label enrichment (Gap C fix) - the query text no
        # longer starts with "select * from next_best_actions". This fake
        # has no contacts table, so it returns the stored nba-only columns
        # unchanged; repository._row() already tolerates the missing
        # _contact_* keys (subject_label simply comes back None), which is
        # exactly the fallback behaviour these tests don't otherwise assert.
        elif "from next_best_actions nba" in sql and "where nba.subject_type" in sql:
            row = self.db.rows.get(tuple(params))
            self._result = [row] if row else []
        elif "from next_best_actions nba" in sql:
            self._result = list(self.db.rows.values())
        else:
            raise AssertionError(f"unexpected query in test fake: {sql}")

    def fetchall(self):
        return list(self._result)

    def fetchone(self):
        return self._result[0] if self._result else None

    def close(self):
        pass


class FakeConn:
    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class FakeDb:
    def __init__(self):
        self.rows: dict[tuple[str, int], dict] = {}
        self.next_id = 1
        self._tick = 0

    def clock(self):
        self._tick += 1
        return self._tick

    @contextmanager
    def cursor(self, *, commit: bool = False):
        yield FakeConn(), FakeCursor(self)


def _row(subject_type="lead", subject_id=1, **overrides):
    base = {
        "subject_type": subject_type,
        "subject_id": subject_id,
        "contact_id": 10,
        "lead_id": subject_id,
        "stima_id": None,
        "action_type": "contact_overdue_followup",
        "priority": "urgent",
        "reason": "Follow-up scaduto",
        "source_signal": "followup_overdue",
        "cta_route": "contatti",
        "cta_params": [10],
        "generated_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "valid_until": None,
    }
    base.update(overrides)
    return base


def test_replace_current_actions_creates_new_rows(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr(repository, "next_best_action_cursor", db.cursor)

    result = repository.replace_current_actions([_row()])

    assert result == {"created": 1, "updated": 0, "removed": 0}
    assert ("lead", 1) in db.rows


def test_replace_current_actions_is_idempotent(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr(repository, "next_best_action_cursor", db.cursor)

    repository.replace_current_actions([_row()])
    result = repository.replace_current_actions([_row()])

    assert result == {"created": 0, "updated": 1, "removed": 0}
    assert len(db.rows) == 1


def test_replace_current_actions_prunes_obsolete_rows(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr(repository, "next_best_action_cursor", db.cursor)

    repository.replace_current_actions([_row(subject_id=1), _row(subject_id=2)])
    assert len(db.rows) == 2

    result = repository.replace_current_actions([_row(subject_id=1)])

    assert result == {"created": 0, "updated": 1, "removed": 1}
    assert ("lead", 2) not in db.rows
    assert ("lead", 1) in db.rows


def test_list_current_orders_by_priority_then_recency_then_subject_id(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr(repository, "next_best_action_cursor", db.cursor)

    repository.replace_current_actions(
        [
            _row(subject_id=1, priority="normal", generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
            _row(subject_id=2, priority="urgent", generated_at=datetime(2026, 1, 2, tzinfo=timezone.utc)),
            _row(subject_id=3, priority="urgent", generated_at=datetime(2026, 1, 3, tzinfo=timezone.utc)),
        ]
    )

    items = repository.list_current(limit=10)

    assert [item["subject_id"] for item in items] == [3, 2, 1]


def test_get_current_returns_none_when_missing(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr(repository, "next_best_action_cursor", db.cursor)

    assert repository.get_current("lead", 999) is None


def test_get_current_returns_matching_row(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr(repository, "next_best_action_cursor", db.cursor)
    repository.replace_current_actions([_row(subject_id=7)])

    result = repository.get_current("lead", 7)

    assert result["subject_id"] == 7
