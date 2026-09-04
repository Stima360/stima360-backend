"""P23 — signals.py tests for the lead-side next_action_overdue completion
(FASE 5 fix).

Monkeypatches next_best_action.signals' own imported names
(core_repository.list_leads, get_seller_intent_score) - no real DB, same
technique as tests/test_seller_intent_router.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from next_best_action import signals
from next_best_action.engine import select_winner


def _lead(lead_id=14, contact_id=3, next_action_at=None):
    return {"id": lead_id, "contact_id": contact_id, "next_action_at": next_action_at}


def _score(band="tiepido", followup_overdue=False, computed_at=None):
    return {
        "band": band,
        "computed_at": computed_at or datetime(2026, 1, 1, tzinfo=timezone.utc),
        "operational_flags": (
            [{"code": "followup_overdue", "label": "Follow-up scaduto"}] if followup_overdue else []
        ),
    }


def test_lead_with_future_next_action_produces_no_next_action_overdue(monkeypatch):
    now = datetime.now(timezone.utc)
    lead = _lead(next_action_at=now + timedelta(days=1))
    monkeypatch.setattr(signals.core_repository, "list_leads", lambda **kwargs: [lead])
    monkeypatch.setattr(signals, "get_seller_intent_score", lambda *, lead_id: _score())

    candidates = signals.collect_lead_signals()

    assert all(c["source_signal"] != "next_action_overdue" for c in candidates)


def test_lead_with_overdue_next_action_produces_candidate(monkeypatch):
    now = datetime.now(timezone.utc)
    overdue_at = now - timedelta(hours=2)
    lead = _lead(next_action_at=overdue_at)
    monkeypatch.setattr(signals.core_repository, "list_leads", lambda **kwargs: [lead])
    monkeypatch.setattr(signals, "get_seller_intent_score", lambda *, lead_id: _score())

    candidates = signals.collect_lead_signals()

    matching = [c for c in candidates if c["source_signal"] == "next_action_overdue"]
    assert len(matching) == 1
    assert matching[0]["subject_type"] == "lead"
    assert matching[0]["subject_id"] == 14
    assert matching[0]["contact_id"] == 3
    assert matching[0]["signal_at"] == overdue_at


def test_lead_with_null_next_action_produces_no_candidate_and_no_crash(monkeypatch):
    lead = _lead(next_action_at=None)
    monkeypatch.setattr(signals.core_repository, "list_leads", lambda **kwargs: [lead])
    monkeypatch.setattr(signals, "get_seller_intent_score", lambda *, lead_id: _score())

    candidates = signals.collect_lead_signals()

    assert all(c["source_signal"] != "next_action_overdue" for c in candidates)


def test_next_action_overdue_beats_seller_intent_hot(monkeypatch):
    now = datetime.now(timezone.utc)
    lead = _lead(next_action_at=now - timedelta(hours=1))
    monkeypatch.setattr(signals.core_repository, "list_leads", lambda **kwargs: [lead])
    monkeypatch.setattr(signals, "get_seller_intent_score", lambda *, lead_id: _score(band="molto_caldo"))

    candidates = signals.collect_lead_signals()
    winner = select_winner(candidates)

    assert winner["source_signal"] == "next_action_overdue"


def test_followup_overdue_beats_next_action_overdue(monkeypatch):
    now = datetime.now(timezone.utc)
    lead = _lead(next_action_at=now - timedelta(hours=1))
    monkeypatch.setattr(signals.core_repository, "list_leads", lambda **kwargs: [lead])
    monkeypatch.setattr(
        signals, "get_seller_intent_score", lambda *, lead_id: _score(followup_overdue=True)
    )

    candidates = signals.collect_lead_signals()
    winner = select_winner(candidates)

    assert winner["source_signal"] == "followup_overdue"


def test_buy_request_next_action_signal_unchanged(monkeypatch):
    """Regression (case 7): the buy_request branch is untouched by this
    fix - it still goes through flow.adapters/flow.engine exactly as
    before, independent of collect_lead_signals."""
    entity = {
        "contact_id": 9,
        "lead_id": None,
        "next_action_at": datetime.now(timezone.utc) - timedelta(hours=3),
        "status": "active",
    }
    monkeypatch.setattr(signals.flow_adapters, "scan_candidates", lambda code, params, limit: [("buy_request", 42)])
    monkeypatch.setattr(signals.flow_adapters, "load_entity", lambda entity_type, entity_id: entity)

    candidates = signals.collect_next_action_signals()

    assert len(candidates) == 1
    assert candidates[0]["subject_type"] == "buy_request"
    assert candidates[0]["subject_id"] == 42
    assert candidates[0]["source_signal"] == "next_action_overdue"
    assert candidates[0]["cta_route"] == "acquirenti"
