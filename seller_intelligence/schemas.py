"""Pydantic request/response schemas for the Seller Intelligence API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, root_validator


class SellerIntelligenceModel(BaseModel):
    class Config:
        extra = "forbid"


class SellerTimelineEventCreate(SellerIntelligenceModel):
    contact_id: int | None = None
    lead_id: int | None = None
    stima_id: int | None = None
    property_id: int | None = None
    event_type: str = Field(min_length=1, max_length=50)
    event_source: str | None = Field(default=None, max_length=30)
    occurred_at: datetime | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=255)
    created_by: str | None = Field(default=None, max_length=200)

    # Mirrors core.schemas.ActivityCreate/TaskCreate: "at least one
    # reference" is validated here (request boundary) AND again in
    # seller_intelligence.service.record_event (the plain-Python entry
    # point future callers such as main.py will use directly, bypassing
    # this Pydantic model entirely). Never enforced in SQL - see
    # migrations/017_seller_intelligence_01.sql for why.
    @root_validator(skip_on_failure=True)
    def validate_reference(cls, values):
        if not any(
            values.get(field) is not None
            for field in ("contact_id", "lead_id", "stima_id", "property_id")
        ):
            raise ValueError(
                "at least one of contact_id, lead_id, stima_id or property_id is required"
            )
        return values
