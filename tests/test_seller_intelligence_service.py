"""P17-A service-layer tests: the "at least one reference" rule.

service.record_event is the plain-Python entry point that a later phase
(P17-B, not authorized yet) would call directly from main.py. This is
where the rule the design review insisted must NOT be a SQL CHECK is
actually enforced.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from seller_intelligence import service
from seller_intelligence.exceptions import ValidationError


def test_record_event_rejects_creation_with_no_reference_at_all(monkeypatch):
    called = []
    monkeypatch.setattr(service.repository, "insert_event", lambda data: called.append(data))

    with pytest.raises(ValidationError, match="at least one"):
        service.record_event(event_type="nota_agente")

    assert called == [], "repository.insert_event non deve essere chiamato se la validazione fallisce"


@pytest.mark.parametrize("field", ["contact_id", "lead_id", "stima_id", "property_id"])
def test_record_event_accepts_creation_with_exactly_one_reference(monkeypatch, field):
    captured = {}
    monkeypatch.setattr(service.repository, "insert_event", lambda data: captured.update(data) or {**data, "id": 1})

    service.record_event(event_type="nota_agente", **{field: 42})

    assert captured[field] == 42
    other_fields = {"contact_id", "lead_id", "stima_id", "property_id"} - {field}
    assert all(captured[other] is None for other in other_fields)


def test_record_event_requires_non_empty_event_type(monkeypatch):
    monkeypatch.setattr(service.repository, "insert_event", lambda data: data)
    with pytest.raises(ValidationError, match="event_type"):
        service.record_event(event_type="", contact_id=1)
    with pytest.raises(ValidationError, match="event_type"):
        service.record_event(event_type="   ", contact_id=1)


def test_record_event_accepts_unknown_event_type(monkeypatch):
    captured = {}
    monkeypatch.setattr(service.repository, "insert_event", lambda data: captured.update(data) or {**data, "id": 1})

    service.record_event(event_type="un_evento_futuro_p24", contact_id=1)

    assert captured["event_type"] == "un_evento_futuro_p24"


def test_record_event_defaults_occurred_at_to_now_when_omitted(monkeypatch):
    captured = {}
    monkeypatch.setattr(service.repository, "insert_event", lambda data: captured.update(data) or {**data, "id": 1})

    before = datetime.now(timezone.utc)
    service.record_event(event_type="stima_richiesta", stima_id=501)
    after = datetime.now(timezone.utc)

    assert before <= captured["occurred_at"] <= after


def test_record_event_preserves_explicit_occurred_at(monkeypatch):
    captured = {}
    monkeypatch.setattr(service.repository, "insert_event", lambda data: captured.update(data) or {**data, "id": 1})
    when = datetime(2026, 1, 1, tzinfo=timezone.utc)

    service.record_event(event_type="stima_richiesta", stima_id=501, occurred_at=when)

    assert captured["occurred_at"] == when


def test_record_event_defaults_payload_to_empty_dict(monkeypatch):
    captured = {}
    monkeypatch.setattr(service.repository, "insert_event", lambda data: captured.update(data) or {**data, "id": 1})

    service.record_event(event_type="stima_richiesta", stima_id=501)

    assert captured["payload"] == {}


def test_list_timeline_delegates_to_repository_unchanged(monkeypatch):
    calls = []
    monkeypatch.setattr(
        service.repository, "list_timeline",
        lambda **kwargs: calls.append(kwargs) or [{"id": 1}],
    )

    result = service.list_timeline(contact_id=7, limit=10, offset=5)

    assert result == [{"id": 1}]
    assert calls == [{
        "contact_id": 7, "lead_id": None, "stima_id": None, "property_id": None,
        "limit": 10, "offset": 5,
    }]
