from __future__ import annotations

from datetime import datetime, timedelta, timezone

from seller_intent.scoring import compute_score


def _score(**overrides):
    now = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
    payload = {
        "lead_id": 14,
        "lead_stage": "new",
        "lead_status": "open",
        "has_stima_completata": False,
        "latest_seller_origin_event_at": None,
        "has_followup_in_progress": False,
        "has_followup_overdue": False,
        "now_utc": now,
    }
    payload.update(overrides)
    return compute_score(**payload)


def test_new_without_other_signals():
    result = _score()
    assert result["score"] == 10


def test_contacted():
    assert _score(lead_stage="contacted")["score"] == 20


def test_qualified_plus_stima_completed():
    assert _score(lead_stage="qualified", has_stima_completata=True)["score"] == 40


def test_appointment_plus_recent_event():
    result = _score(
        lead_stage="appointment",
        latest_seller_origin_event_at=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
    )
    assert result["score"] == 55


def test_proposal():
    assert _score(lead_stage="proposal")["score"] == 50


def test_won_is_exactly_100():
    result = _score(lead_stage="won", lead_status="closed")
    assert result["score"] == 100
    assert result["state"] == "converted"


def test_lost_not_zero_and_state_recovery():
    result = _score(lead_stage="lost")
    assert result["score"] == 20
    assert result["state"] == "da_recuperare"


def test_lost_with_signals_can_rise():
    result = _score(
        lead_stage="lost",
        has_stima_completata=True,
        latest_seller_origin_event_at=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
    )
    assert result["score"] == 45


def test_paused_cap_35():
    result = _score(
        lead_stage="proposal",
        lead_status="paused",
        has_stima_completata=True,
        latest_seller_origin_event_at=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
    )
    assert result["score"] == 35
    assert result["state"] == "paused"


def test_closed_non_won_cap_60_and_not_zero():
    result = _score(
        lead_stage="proposal",
        lead_status="closed",
        has_stima_completata=True,
        latest_seller_origin_event_at=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
    )
    assert result["score"] == 60
    assert result["state"] == "da_recuperare"


def test_task_in_progress_does_not_modify_score():
    baseline = _score(lead_stage="qualified")["score"]
    with_flag = _score(lead_stage="qualified", has_followup_in_progress=True)["score"]
    assert with_flag == baseline


def test_task_overdue_does_not_modify_score():
    baseline = _score(lead_stage="qualified")["score"]
    with_flag = _score(lead_stage="qualified", has_followup_overdue=True)["score"]
    assert with_flag == baseline


def test_operational_flags_are_reported_with_zero_score_impact():
    result = _score(
        lead_stage="qualified",
        has_followup_in_progress=True,
        has_followup_overdue=True,
    )
    assert result["score"] == 30
    assert result["operational_flags"] == [
        {"code": "followup_in_progress", "label": "Follow-up in lavorazione"},
        {"code": "followup_overdue", "label": "Follow-up scaduto"},
    ]


def test_score_clamped_zero_to_100():
    low = _score(lead_stage="new")
    high = _score(lead_stage="won")
    assert low["score"] == 10
    assert high["score"] == 100


def test_recency_is_timezone_aware():
    now = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
    recent_local = (now - timedelta(days=6)).astimezone(timezone(timedelta(hours=2)))
    result = compute_score(
        lead_id=14,
        lead_stage="appointment",
        lead_status="open",
        has_stima_completata=False,
        latest_seller_origin_event_at=recent_local,
        has_followup_in_progress=False,
        has_followup_overdue=False,
        now_utc=now,
    )
    assert result["score"] == 55

