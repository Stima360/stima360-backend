"""Deterministic single-action engine for P23.

Precedence order is a FROZEN business rule (Fase 3 kickoff instructions),
not a numeric/weighted score: a lower rank always wins over a higher rank,
regardless of any other value.

  1. follow-up scaduto            source_signal="followup_overdue"
  2. next_action scaduto          source_signal="next_action_overdue"
  3. seller intent molto caldo    source_signal="seller_intent_hot"
  4. vendita invisibile ready     source_signal="invisible_sale_ready"
  5. match forte non proposto     source_signal="match_strong_unproposed"
  6. seller database revival      source_signal="database_revival" (P24)

This module is pure and DB-free by design: it only combines already-
collected candidate dicts (produced by signals.py) for ONE
(subject_type, subject_id) into a single winner. It never queries any
P17-P22 module itself - that keeps it trivially unit-testable and keeps
the precedence rule in exactly one place.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

PRECEDENCE: dict[str, int] = {
    "followup_overdue": 1,
    "next_action_overdue": 2,
    "seller_intent_hot": 3,
    "invisible_sale_ready": 4,
    "match_strong_unproposed": 5,
    "database_revival": 6,
}

# Sentinel used only for the tie-break sort key below: a candidate without
# a signal_at timestamp must never win a recency tie-break against one that
# has a real timestamp, so it is treated as "infinitely old".
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def select_winner(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Given 0+ candidate NBAs for a single (subject_type, subject_id),
    return the one winner according to the frozen precedence order, or
    None if there are no candidates.

    Each candidate dict must contain at least:
      - "source_signal": one of the keys in PRECEDENCE
      - "subject_id": int, used only as the final, deterministic tie-break
      - "signal_at": datetime | None, used only as the secondary tie-break
        (most recent wins) when two candidates share the same
        source_signal for the same subject.

    Raises ValueError if a candidate carries an unknown source_signal -
    this is a programmer error in signals.py, never a data problem, and
    must fail loudly rather than silently rank the candidate last.
    """
    if not candidates:
        return None

    def _sort_key(candidate: dict[str, Any]) -> tuple[int, float, int]:
        rank = PRECEDENCE.get(candidate["source_signal"])
        if rank is None:
            raise ValueError(f"unknown source_signal {candidate['source_signal']!r}")
        signal_at = candidate.get("signal_at") or _EPOCH
        return (rank, -signal_at.timestamp(), candidate["subject_id"])

    return min(candidates, key=_sort_key)


def rank_for_display(priority: str) -> int:
    """Stable secondary rank used only for the OGGI list ordering (section
    5.B "ranking globale" - a DIFFERENT concern from select_winner above,
    which decides WHICH single NBA wins per subject, not in what order
    multiple already-decided NBAs are displayed).

    Reuses the exact same priority vocabulary as CORE tasks/leads
    (core.enums.PRIORITIES) - never invented here.
    """
    order = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
    return order.get(priority, len(order))
