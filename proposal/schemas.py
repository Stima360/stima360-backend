from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, root_validator

from .enums import PROPOSAL_STATUSES


class ProposalModel(BaseModel):
    class Config:
        extra = "forbid"


class ProposalCreate(ProposalModel):
    match_id: int = Field(..., gt=0)
    amount: Decimal = Field(..., gt=0)
    expires_at: datetime
    notes: str | None = None
    idempotency_key: UUID

    @field_validator("expires_at")
    @classmethod
    def expiry_requires_timezone(cls, value):
        if value.utcoffset() is None:
            raise ValueError("expires_at must include a timezone offset")
        return value


class ProposalUpdate(ProposalModel):
    amount: Decimal | None = Field(None, gt=0)
    expires_at: datetime | None = None
    notes: str | None = None

    @root_validator(pre=True)
    def required_fields_cannot_be_null(cls, values):
        for field in ("amount", "expires_at"):
            if field in values and values[field] is None:
                raise ValueError(f"{field} cannot be null")
        return values

    @field_validator("expires_at")
    @classmethod
    def expiry_requires_timezone(cls, value):
        if value.utcoffset() is None:
            raise ValueError("expires_at must include a timezone offset")
        return value


class ProposalTransition(ProposalModel):
    target_status: str

    @root_validator(skip_on_failure=True)
    def validate_target_status(cls, values):
        target = values.get("target_status")
        if target not in PROPOSAL_STATUSES:
            raise ValueError("invalid proposal status")
        return values
