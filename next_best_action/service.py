"""Orchestration for P23: signals -> single-action engine -> anti-dup ->
materialization. This is the only module that ties the pieces together;
signals.py, engine.py and repository.py each stay independently testable.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from core import repository as core_repository
from database_revival import service as database_revival_service

from . import repository as nba_repository
from .engine import select_winner
from .signals import DEFAULT_LIMIT, collect_all_signals

OPEN_TASK_STATUSES = {"open", "in_progress"}


def _has_open_equivalent_task(candidate: dict[str, Any]) -> bool:
    """Anti-duplication (section 4 / frozen rule): if CORE already has an
    open or in_progress task linked to this subject's contact_id, lead_id
    or stima_id, an equivalent follow-up already exists and P23 must NOT
    propose a duplicate NBA for it.

    Reuses core.repository.list_tasks (the same public function
    followup/property_watch already rely on for their own task lookups)
    - no second task system is introduced. list_tasks ANDs its filters, so
    each available id is queried separately and the open/in_progress
    filter is applied client-side afterwards; results are merged.
    """
    ids_to_check = [
        ("contact_id", candidate.get("contact_id")),
        ("lead_id", candidate.get("lead_id")),
        ("stima_id", candidate.get("stima_id")),
    ]
    for column, value in ids_to_check:
        if value is None:
            continue
        kwargs = {"contact_id": None, "lead_id": None, "stima_id": None}
        kwargs[column] = value
        tasks = core_repository.list_tasks(limit=50, offset=0, status=None, **kwargs)
        if any(t.get("status") in OPEN_TASK_STATUSES for t in tasks):
            return True
    return False


def refresh(limit: int = DEFAULT_LIMIT) -> dict[str, int]:
    """Full on-demand refresh (section 7, frozen model: no scheduler/queue).

    1. collect current signals (signals.py, read-only against P17-P22);
    2. group by (subject_type, subject_id);
    3. pick the single winner per subject (engine.select_winner, the
       frozen precedence order);
    4. drop any winner that already has an equivalent open task/follow-up
       (anti-duplication, mandatory);
    5. materialize the resulting set (repository.replace_current_actions),
       which also prunes any previously materialized NBA no longer among
       the winners (invalidation).

    Deterministic and idempotent: given the same underlying P17-P22 data,
    two consecutive calls produce the same winners and the same stored
    rows (see repository.replace_current_actions).

    P24 addition: before collecting signals, ensure today's seller
    database-revival batch is created/topped-up (rank #6 - see
    next_best_action/engine.py PRECEDENCE and signals.py
    ::collect_database_revival_signals). Always through the non-raising
    safe_ensure_today_batch() wrapper: a P24 failure must never prevent
    the other five P23 signals from refreshing.
    """
    database_revival_service.safe_ensure_today_batch()

    candidates = collect_all_signals(limit)

    grouped: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        grouped[(candidate["subject_type"], candidate["subject_id"])].append(candidate)

    now = datetime.now(timezone.utc)
    winners: list[dict[str, Any]] = []
    suppressed_duplicates = 0
    for subject_candidates in grouped.values():
        winner = select_winner(subject_candidates)
        if winner is None:
            continue
        if _has_open_equivalent_task(winner):
            suppressed_duplicates += 1
            continue
        signal_at = winner.get("signal_at")
        winners.append(
            {
                **winner,
                "generated_at": signal_at if signal_at is not None else now,
            }
        )

    result = nba_repository.replace_current_actions(winners)
    return {
        "evaluated_subjects": len(grouped),
        "created": result["created"],
        "updated": result["updated"],
        "removed": result["removed"],
        "suppressed_duplicates": suppressed_duplicates,
        "total_active": len(winners),
    }


def list_next_best_actions(limit: int) -> list[dict[str, Any]]:
    return nba_repository.list_current(limit)


def get_next_best_action(subject_type: str, subject_id: int) -> dict[str, Any] | None:
    return nba_repository.get_current(subject_type, subject_id)
