import sys
import types
from types import SimpleNamespace

# Lightweight psycopg2 fallback for developer environments where the
# PostgreSQL driver is unavailable.  Prefer the real package whenever it can
# be imported; checking only ``sys.modules`` used to shadow an installed
# psycopg2 during full-suite collection.
try:
    import psycopg2  # noqa: F401
except ImportError:
    psycopg2 = types.ModuleType("psycopg2")
    psycopg2.connect = lambda *args, **kwargs: None
    psycopg2.errors = types.SimpleNamespace(UniqueViolation=type("UniqueViolation", (Exception,), {}))

    extras = types.ModuleType("psycopg2.extras")
    extras.Json = lambda value: value
    extras.RealDictCursor = object

    sql = types.ModuleType("psycopg2.sql")
    class _Composable:
        def __init__(self, value=""):
            self.value = value
        def format(self, *args, **kwargs):
            return self
        def join(self, seq):
            return self
    sql.SQL = _Composable
    sql.Identifier = _Composable
    sql.Literal = _Composable

    psycopg2.extras = extras
    psycopg2.sql = sql
    sys.modules["psycopg2"] = psycopg2
    sys.modules["psycopg2.extras"] = extras
    sys.modules["psycopg2.sql"] = sql

from core import service


def test_update_contact_rebuilds_display_name(monkeypatch):
    monkeypatch.setattr(service.repository, "get_contact", lambda contact_id: {
        "id": contact_id,
        "contact_type": "person",
        "first_name": "Mario",
        "last_name": "Rossi",
        "display_name": "Mario Rossi",
    })
    captured = {}
    monkeypatch.setattr(service.repository, "update_contact", lambda contact_id, data: captured.update(data) or data)

    payload = SimpleNamespace(model_dump=lambda exclude_unset=False: {"first_name": "Luigi"})
    service.update_contact(1, payload)

    assert captured["display_name"] == "Luigi Rossi"


def test_reopen_lead_clears_closed_at(monkeypatch):
    captured = {}
    monkeypatch.setattr(service.repository, "update_lead", lambda lead_id, data: captured.update(data) or data)
    payload = SimpleNamespace(model_dump=lambda exclude_unset=False: {"status": "open"})

    service.update_lead(1, payload)

    assert "closed_at" in captured
    assert captured["closed_at"] is None


def test_reopen_task_clears_completed_at(monkeypatch):
    captured = {}
    monkeypatch.setattr(service.repository, "update_task", lambda task_id, data: captured.update(data) or data)
    payload = SimpleNamespace(model_dump=lambda exclude_unset=False: {"status": "in_progress"})

    service.update_task(1, payload)

    assert "completed_at" in captured
    assert captured["completed_at"] is None
