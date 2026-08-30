"""Explicit response DTOs for OWNER Admin P8.1 lookup endpoints."""
from __future__ import annotations

from datetime import date, datetime
from pydantic import BaseModel, ConfigDict


class LookupDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContactLookupDTO(LookupDTO):
    id: int
    display_name: str | None = None
    email: str | None = None


class PropertyLookupDTO(LookupDTO):
    id: int
    code: str | None = None
    title: str
    address: str | None = None
    city: str | None = None


class DocumentLookupDTO(LookupDTO):
    id: int
    title: str
    document_type: str
    status: str
    expires_at: date | None = None


class VisitLookupDTO(LookupDTO):
    id: int
    scheduled_at: datetime
    status: str


class ContactLookupResponse(LookupDTO):
    items: list[ContactLookupDTO]


class PropertyLookupResponse(LookupDTO):
    items: list[PropertyLookupDTO]


class DocumentLookupResponse(LookupDTO):
    items: list[DocumentLookupDTO]


class VisitLookupResponse(LookupDTO):
    items: list[VisitLookupDTO]
