from __future__ import annotations

from datetime import datetime, timezone

from .exceptions import NotFoundError
from . import repository
from .repository import get_lead_intent_inputs
from .scoring import compute_score


def _score_from_inputs(inputs: dict, *, now_utc: datetime | None = None) -> dict:
    """The mapping from repository row to score, shared by both entry points.

    Extracted rather than duplicated: the scoped and legacy paths must differ
    only in which rows they are allowed to read, never in how they score them.
    """
    return compute_score(
        lead_id=int(inputs["lead_id"]),
        lead_stage=inputs["lead_stage"],
        lead_status=inputs["lead_status"],
        has_stima_completata=bool(inputs["has_stima_completata"]),
        latest_seller_origin_event_at=inputs.get("latest_seller_origin_event_at"),
        has_followup_in_progress=bool(inputs["has_p18_followup_in_progress"]),
        has_followup_overdue=bool(inputs["has_p18_followup_overdue"]),
        now_utc=now_utc or datetime.now(timezone.utc),
    )


def get_seller_intent_score(*, lead_id: int, now_utc: datetime | None = None) -> dict:
    inputs = get_lead_intent_inputs(lead_id)
    if inputs is None:
        raise NotFoundError(f"lead {lead_id} not found")

    return _score_from_inputs(inputs, now_utc=now_utc)


def get_seller_intent_score_scoped(ctx, *, lead_id: int, now_utc: datetime | None = None) -> dict:
    """P26-6A. Identical scoring, restricted to the caller's own agency.

    The legacy `get_seller_intent_score` above is untouched:
    next_best_action/signals.py calls it by keyword for every open lead it
    already resolved in its own scope.
    """
    inputs = repository.get_lead_intent_inputs_scoped(ctx, lead_id)
    if inputs is None:
        raise NotFoundError(f"lead {lead_id} not found")
    return _score_from_inputs(inputs, now_utc=now_utc)
