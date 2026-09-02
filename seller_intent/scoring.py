from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

STAGE_POINTS = {
    "new": 10,
    "contacted": 20,
    "qualified": 30,
    "appointment": 40,
    "proposal": 50,
}

BANDS = (
    (0, 29, "freddo"),
    (30, 54, "tiepido"),
    (55, 74, "caldo"),
    (75, 100, "molto_caldo"),
)


def _normalize_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _band_for(score: int) -> str:
    for lo, hi, label in BANDS:
        if lo <= score <= hi:
            return label
    return "freddo"


def _build_operational_flags(
    *,
    has_followup_in_progress: bool,
    has_followup_overdue: bool,
) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []
    if has_followup_in_progress:
        flags.append(
            {
                "code": "followup_in_progress",
                "label": "Follow-up in lavorazione",
            }
        )
    if has_followup_overdue:
        flags.append(
            {
                "code": "followup_overdue",
                "label": "Follow-up scaduto",
            }
        )
    return flags


def compute_score(
    *,
    lead_id: int,
    lead_stage: str,
    lead_status: str,
    has_stima_completata: bool,
    latest_seller_origin_event_at: datetime | None,
    has_followup_in_progress: bool,
    has_followup_overdue: bool,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    now = _normalize_utc(now_utc) or datetime.now(timezone.utc)
    factors: list[dict[str, Any]] = []
    operational_flags = _build_operational_flags(
        has_followup_in_progress=has_followup_in_progress,
        has_followup_overdue=has_followup_overdue,
    )

    if lead_stage == "won":
        score = 100
        factors.append({"code": "stage_won", "label": "Lead convertito", "points": 100})
        return {
            "lead_id": lead_id,
            "score": score,
            "band": _band_for(score),
            "state": "converted",
            "computed_at": now,
            "factors": factors,
            "operational_flags": operational_flags,
        }

    score = 0
    state = "active"

    if lead_stage == "lost":
        score += 20
        state = "da_recuperare"
        factors.append(
            {
                "code": "stage_lost_recovery_base",
                "label": "Lead perso ma recuperabile",
                "points": 20,
            }
        )
    else:
        stage_points = STAGE_POINTS.get(lead_stage, 0)
        if stage_points:
            score += stage_points
            factors.append(
                {
                    "code": f"stage_{lead_stage}",
                    "label": f"Lead in fase {lead_stage}",
                    "points": stage_points,
                }
            )

    if has_stima_completata:
        score += 10
        factors.append(
            {
                "code": "stima_completata",
                "label": "Stima completata",
                "points": 10,
            }
        )

    last_event = _normalize_utc(latest_seller_origin_event_at)
    if last_event is not None:
        age = now - last_event
        if age.days <= 7:
            score += 15
            factors.append(
                {
                    "code": "recent_activity_7d",
                    "label": "Segnale seller-origin negli ultimi 7 giorni",
                    "points": 15,
                }
            )
        elif age.days <= 30:
            score += 10
            factors.append(
                {
                    "code": "recent_activity_30d",
                    "label": "Segnale seller-origin negli ultimi 30 giorni",
                    "points": 10,
                }
            )
        elif age.days <= 90:
            score += 5
            factors.append(
                {
                    "code": "recent_activity_90d",
                    "label": "Segnale seller-origin negli ultimi 90 giorni",
                    "points": 5,
                }
            )

    if lead_status == "closed" and lead_stage != "won":
        state = "da_recuperare"
        score = min(score, 60)

    if lead_status == "paused":
        state = "paused"
        score = min(score, 35)

    score = max(0, min(100, score))
    return {
        "lead_id": lead_id,
        "score": score,
        "band": _band_for(score),
        "state": state,
        "computed_at": now,
        "factors": factors,
        "operational_flags": operational_flags,
    }

