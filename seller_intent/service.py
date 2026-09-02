from __future__ import annotations

from datetime import datetime, timezone

from .exceptions import NotFoundError
from .repository import get_lead_intent_inputs
from .scoring import compute_score


def get_seller_intent_score(*, lead_id: int, now_utc: datetime | None = None) -> dict:
    inputs = get_lead_intent_inputs(lead_id)
    if inputs is None:
        raise NotFoundError(f"lead {lead_id} not found")

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
