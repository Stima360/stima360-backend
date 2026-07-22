from __future__ import annotations
from pydantic import BaseModel, Field, ConfigDict, model_validator
from .enums import (
    COMMERCIAL_STATUSES,
    PRIORITIES,
    FEEDBACK_SOURCES,
    FEEDBACK_TYPES,
    FEEDBACK_REASONS,
    REFRESH_TRIGGER_SOURCES,
)


class MatchModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SingleMatchRequest(MatchModel):
    buy_request_id: int = Field(..., gt=0)
    property_id: int = Field(..., gt=0)
    created_by: str | None = Field(None, max_length=200)


class BatchMatchRequest(MatchModel):
    created_by: str | None = Field(None, max_length=200)


class MatchUpdate(MatchModel):
    commercial_status: str | None = None
    priority: str | None = None
    assigned_to: str | None = Field(None, max_length=200)
    review_required: bool | None = None

    def validate_values(self):
        if self.commercial_status is not None and self.commercial_status not in COMMERCIAL_STATUSES:
            raise ValueError("invalid commercial_status")
        if self.priority is not None and self.priority not in PRIORITIES:
            raise ValueError("invalid priority")
        return self


class OverrideRequest(MatchModel):
    manual_score: float = Field(..., ge=0, le=100)
    manual_reason: str = Field(..., min_length=3, max_length=1000)


class ExclusionCreate(MatchModel):
    buy_request_id: int = Field(..., gt=0)
    property_id: int = Field(..., gt=0)
    exclusion_type: str = "agent_decision"
    reason: str | None = Field(None, max_length=2000)
    expires_at: str | None = None
    created_by: str | None = Field(None, max_length=200)


class DetectStaleRequest(MatchModel):
    match_id: int | None = Field(None, gt=0)
    buy_request_id: int | None = Field(None, gt=0)
    property_id: int | None = Field(None, gt=0)

    @model_validator(mode="after")
    def only_one_scope(self):
        count = sum(getattr(self, k) is not None for k in ("match_id", "buy_request_id", "property_id"))
        if count > 1:
            raise ValueError("specify only one scope")
        return self


class RefreshRequest(MatchModel):
    created_by: str | None = Field(None, max_length=200)
    trigger_reason: str | None = Field(None, max_length=2000)


class FeedbackCreate(MatchModel):
    source: str
    feedback_type: str
    reason_code: str | None = None
    notes: str | None = Field(None, max_length=4000)
    created_by: str | None = Field(None, max_length=200)

    @model_validator(mode="after")
    def validate_feedback(self):
        if self.source not in FEEDBACK_SOURCES:
            raise ValueError("invalid feedback source")
        if self.feedback_type not in FEEDBACK_TYPES:
            raise ValueError("invalid feedback type")
        if self.reason_code is not None and self.reason_code not in FEEDBACK_REASONS:
            raise ValueError("invalid feedback reason")
        if self.feedback_type == "negative" and not self.reason_code:
            raise ValueError("negative feedback requires reason_code")
        return self
