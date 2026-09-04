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
    # P25.7 (Gap C fix): additive read-model field, computed at read time by
    # a dynamic LEFT JOIN on contacts (next_best_action/repository.py -
    # _SELECT_WITH_CONTACT_LABEL/_row). None when no contact/name is
    # available - the frontend (oggi.js) falls back to "{Type} #{id}" in
    # that case, same as before this field existed. No migration, no new
    # column on next_best_actions.
    subject_label: str | None = None


class NextBestActionListResponse(BaseModel):
    items: list[NextBestAction]


class NextBestActionRefreshResult(BaseModel):
    evaluated_subjects: int
    created: int
    updated: int
    removed: int
    suppressed_duplicates: int
    total_active: int
