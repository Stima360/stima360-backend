"""P23 — pure engine tests (no DB, no mocking needed).

Covers section 12.A (single-signal cases + none), 12.B (precedence pairs +
all-signals-at-once), and 12.C (single action per subject).
"""

from __future__ import annotations

from datetime import datetime, timezone

from next_best_action.engine import PRECEDENCE, rank_for_display, select_winner


def _candidate(source_signal: str, subject_id: int = 1, signal_at: datetime | None = None) -> dict:
    return {
        "subject_type": "lead",
        "subject_id": subject_id,
        "source_signal": source_signal,
        "signal_at": signal_at,
        "action_type": f"action_for_{source_signal}",
        "priority": "high",
        "reason": "test",
        "cta_route": "contatti",
        "cta_params": [subject_id],
    }


def test_no_candidates_returns_none():
    assert select_winner([]) is None


def test_only_followup_overdue():
    winner = select_winner([_candidate("followup_overdue")])
    assert winner["source_signal"] == "followup_overdue"


def test_only_next_action_overdue():
    winner = select_winner([_candidate("next_action_overdue")])
    assert winner["source_signal"] == "next_action_overdue"


def test_only_seller_intent_hot():
    winner = select_winner([_candidate("seller_intent_hot")])
    assert winner["source_signal"] == "seller_intent_hot"


def test_only_invisible_sale_ready():
    winner = select_winner([_candidate("invisible_sale_ready")])
    assert winner["source_signal"] == "invisible_sale_ready"


def test_only_match_strong_unproposed():
    winner = select_winner([_candidate("match_strong_unproposed")])
    assert winner["source_signal"] == "match_strong_unproposed"


def test_followup_beats_next_action():
    winner = select_winner([_candidate("next_action_overdue"), _candidate("followup_overdue")])
    assert winner["source_signal"] == "followup_overdue"


def test_followup_beats_seller_hot():
    winner = select_winner([_candidate("seller_intent_hot"), _candidate("followup_overdue")])
    assert winner["source_signal"] == "followup_overdue"


def test_next_action_beats_seller_hot():
    winner = select_winner([_candidate("seller_intent_hot"), _candidate("next_action_overdue")])
    assert winner["source_signal"] == "next_action_overdue"


def test_seller_hot_beats_invisible_sale():
    winner = select_winner([_candidate("invisible_sale_ready"), _candidate("seller_intent_hot")])
    assert winner["source_signal"] == "seller_intent_hot"


def test_invisible_sale_beats_match():
    winner = select_winner([_candidate("match_strong_unproposed"), _candidate("invisible_sale_ready")])
    assert winner["source_signal"] == "invisible_sale_ready"


def test_all_five_signals_together_picks_highest_precedence():
    all_candidates = [_candidate(signal) for signal in PRECEDENCE]
    winner = select_winner(all_candidates)
    assert winner["source_signal"] == "followup_overdue"


def test_single_action_only_one_winner_for_multiple_candidates_same_subject():
    winner = select_winner([_candidate("match_strong_unproposed"), _candidate("invisible_sale_ready"), _candidate("seller_intent_hot")])
    assert isinstance(winner, dict)
    assert winner["source_signal"] == "seller_intent_hot"


def test_tie_break_prefers_most_recent_signal_at():
    older = _candidate("match_strong_unproposed", signal_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
    newer = _candidate("match_strong_unproposed", signal_at=datetime(2024, 6, 1, tzinfo=timezone.utc))
    winner = select_winner([older, newer])
    assert winner is newer


def test_tie_break_falls_back_to_subject_id_when_no_timestamps():
    a = _candidate("match_strong_unproposed", subject_id=5)
    b = _candidate("match_strong_unproposed", subject_id=2)
    winner = select_winner([a, b])
    assert winner["subject_id"] == 2


def test_unknown_source_signal_raises():
    import pytest

    with pytest.raises(ValueError):
        select_winner([_candidate("something_invented")])


def test_rank_for_display_orders_known_priorities():
    assert rank_for_display("urgent") < rank_for_display("high") < rank_for_display("normal") < rank_for_display("low")


def test_rank_for_display_unknown_priority_sorts_last():
    assert rank_for_display("unknown") > rank_for_display("low")
