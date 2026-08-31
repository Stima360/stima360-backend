from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, root_validator


class SaleModel(BaseModel):
    class Config:
        extra = "forbid"


class SaleCreate(SaleModel):
    proposal_id: int = Field(..., gt=0)
    sale_price: Decimal | None = Field(None, gt=0)
    notes: str | None = None
    idempotency_key: UUID


class SaleUpdate(SaleModel):
    sale_price: Decimal | None = Field(None, gt=0)
    notes: str | None = None

    @root_validator(pre=True)
    def sale_price_cannot_be_null(cls, values):
        if "sale_price" in values and values["sale_price"] is None:
            raise ValueError("sale_price cannot be null")
        return values
