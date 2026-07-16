from __future__ import annotations
from pydantic import BaseModel, Field
from .enums import COMMERCIAL_STATUSES, PRIORITIES

class MatchModel(BaseModel):
    class Config:
        extra = "forbid"

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

    def validate_values(self):
        if self.commercial_status is not None and self.commercial_status not in COMMERCIAL_STATUSES:
            raise ValueError("invalid commercial_status")
        if self.priority is not None and self.priority not in PRIORITIES:
            raise ValueError("invalid priority")
        return self

class OverrideRequest(MatchModel):
    manual_score: float = Field(..., ge=0, le=100)
    manual_reason: str = Field(..., min_length=3)

class ExclusionCreate(MatchModel):
    buy_request_id: int = Field(..., gt=0)
    property_id: int = Field(..., gt=0)
    exclusion_type: str = "agent_decision"
    reason: str | None = None
    expires_at: str | None = None
    created_by: str | None = Field(None, max_length=200)
