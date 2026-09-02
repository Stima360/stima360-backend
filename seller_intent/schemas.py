from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SellerIntentFactor(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    points: int


class SellerIntentOperationalFlag(BaseModel):
    code: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)


class SellerIntentScoreResponse(BaseModel):
    lead_id: int
    score: int = Field(ge=0, le=100)
    band: str
    state: str
    computed_at: datetime
    factors: list[SellerIntentFactor]
    operational_flags: list[SellerIntentOperationalFlag]
