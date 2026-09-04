from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

SUBJECT_TYPES = {"lead", "buy_request", "stima", "match"}
PRIORITIES = {"low", "normal", "high", "urgent"}


class NextBestAction(BaseModel):
    id: int
    subject_type: str
    subject_id: int
    contact_id: int | None = None
    lead_id: int | None = None
    stima_id: int | None = None
    action_type: str = Field(min_length=1, max_length=50)
    priority: str
    reason: str = Field(min_length=1, max_length=300)
    source_signal: str = Field(min_length=1, max_length=50)
    cta_route: str | None = None
    cta_params: list[int] = Field(default_factory=list)
    generated_at: datetime
    valid_until: datetime | None = None


class NextBestActionListResponse(BaseModel):
    items: list[NextBestAction]


class NextBestActionRefreshResult(BaseModel):
    evaluated_subjects: int
    created: int
    updated: int
    removed: int
    suppressed_duplicates: int
    total_active: int
