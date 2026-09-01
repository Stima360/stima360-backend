"""P18-B service tests.

followup.repository.execute_followup_action is monkeypatched with a plain
Python fake - this file tests validation and idempotency-key construction
in run_followup()/safe_run_followup(), not the repository's DB
orchestration (see tests/test_followup_repository.py for that).
"""

from __future__ import annotations

import logging

import pytest

from followup import service
from followup.exceptions import ValidationError


@pytest.fixture
def fake_repository(monkeypatch):
    calls = []

    def _execute(**kwargs):
        calls.append(kwargs)
        return {"task_id": 1, "followup_action_id": 1, "status": "completed"}

    monkeypatch.setattr(service.repository, "execute_followup_action", _execute)
    return calls


# --- happy path ----------------------------------------------------------------

def test_run_followup_builds_deterministic_idempotency_key(fake_repository):
    service.run_followup(
        rule_code="FOLLOWUP_STIMA_RICHIESTA",
        trigger_type="event",
        contact_id=16,
        lead_id=12,
        stima_id=501,
    )

    assert fake_repository[0]["idempotency_key"] == "followup:stima_richiesta:501"


def test_run_followup_passes_rule_fields_through_unchanged(fake_repository):
    service.run_followup(
        rule_code="FOLLOWUP_STIMA_RICHIESTA",
        trigger_type="event",
        stima_id=501,
    )

    call = fake_repository[0]
    assert call["rule_code"] == "FOLLOWUP_STIMA_RICHIESTA"
    assert call["trigger_type"] == "event"
    assert call["task_title"] == "Contattare proprietario"
    assert call["task_type"] == "automated_followup"
    assert call["priority"] == "normal"
    assert call["stima_id"] == 501


def test_run_followup_tolerates_none_contact_and_lead_id(fake_repository):
    # bridge_result can be None upstream (CORE bridge failed) - the rule
    # must still fire using stima_id alone, exactly like P17's events do.
    result = service.run_followup(
        rule_code="FOLLOWUP_STIMA_RICHIESTA",
        trigger_type="event",
        contact_id=None,
        lead_id=None,
        stima_id=501,
    )
    assert result["status"] == "completed"
    call = fake_repository[0]
    assert call["contact_id"] is None
    assert call["lead_id"] is None


def test_run_followup_due_at_is_24_hours_from_now(fake_repository):
    from datetime import datetime, timezone

    before = datetime.now(timezone.utc)
    service.run_followup(rule_code="FOLLOWUP_STIMA_RICHIESTA", trigger_type="event", stima_id=501)
    after = datetime.now(timezone.utc)

    due_at = fake_repository[0]["due_at"]
    delta_low = (due_at - before).total_seconds()
    delta_high = (due_at - after).total_seconds()
    assert 24 * 3600 - 5 <= delta_low <= 24 * 3600 + 5
    assert 24 * 3600 - 5 <= delta_high <= 24 * 3600 + 5


# --- validation ------------------------------------------------------------------

def test_run_followup_rejects_unknown_rule_code(fake_repository):
    with pytest.raises(ValidationError):
        service.run_followup(rule_code="DOES_NOT_EXIST", trigger_type="event", stima_id=501)
    assert fake_repository == []


def test_run_followup_rejects_wrong_trigger_type(fake_repository):
    with pytest.raises(ValidationError):
        service.run_followup(rule_code="FOLLOWUP_STIMA_RICHIESTA", trigger_type="time", stima_id=501)
    assert fake_repository == []


def test_run_followup_rejects_missing_stima_id_for_event_rule(fake_repository):
    with pytest.raises(ValidationError):
        service.run_followup(rule_code="FOLLOWUP_STIMA_RICHIESTA", trigger_type="event", stima_id=None)
    assert fake_repository == []


def test_run_followup_rejects_disabled_rule(monkeypatch, fake_repository):
    from followup.rules import FollowupRule

    disabled = FollowupRule(
        rule_code="FOLLOWUP_DISABLED_TEST", trigger_type="event", event_type="x",
        action="create_core_task", title="x", task_type="x", priority="normal",
        due_hours=1, enabled=False,
    )
    monkeypatch.setitem(service.get_rule.__globals__["ALL_RULES"], "FOLLOWUP_DISABLED_TEST", disabled)

    with pytest.raises(ValidationError):
        service.run_followup(rule_code="FOLLOWUP_DISABLED_TEST", trigger_type="event", stima_id=501)
    assert fake_repository == []


# --- safe_run_followup: never raises --------------------------------------------

def test_safe_run_followup_returns_result_on_success(fake_repository):
    result = service.safe_run_followup(
        rule_code="FOLLOWUP_STIMA_RICHIESTA", trigger_type="event", stima_id=501,
    )
    assert result == {"task_id": 1, "followup_action_id": 1, "status": "completed"}


def test_safe_run_followup_swallows_validation_error_and_returns_none(fake_repository):
    result = service.safe_run_followup(
        rule_code="DOES_NOT_EXIST", trigger_type="event", stima_id=501,
    )
    assert result is None


def test_safe_run_followup_swallows_repository_exception_and_returns_none(monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("simulated total followup outage")

    monkeypatch.setattr(service.repository, "execute_followup_action", _boom)

    result = service.safe_run_followup(
        rule_code="FOLLOWUP_STIMA_RICHIESTA", trigger_type="event", stima_id=501,
    )
    assert result is None


def test_safe_run_followup_logs_rule_code_and_ids_on_failure(monkeypatch, caplog):
    def _boom(**kwargs):
        raise RuntimeError("simulated total followup outage")

    monkeypatch.setattr(service.repository, "execute_followup_action", _boom)

    with caplog.at_level(logging.ERROR):
        service.safe_run_followup(
            rule_code="FOLLOWUP_STIMA_RICHIESTA", trigger_type="event",
            stima_id=501, contact_id=16, lead_id=12,
        )

    assert any(
        "FOLLOWUP_STIMA_RICHIESTA" in record.message and "501" in record.message
        for record in caplog.records
    )
