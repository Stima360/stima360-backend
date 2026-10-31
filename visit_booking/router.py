"""BLOCCO-5A - rotte della prenotazione online delle visite.

Operatore (montato con `require_authenticated_operator`, scope da `require_operator`):
    POST /api/visit-booking-links                       crea un collegamento (token mostrato una volta)
    GET  /api/visit-booking-links?property_id=          collegamenti di un immobile
    POST /api/visit-booking-links/{id}/disable          disattiva

Pubblico (nessuna sessione; ogni rifiuto del link e' la stessa risposta neutra):
    GET  /api/public/visit-booking/{token}                       dati minimi
    GET  /api/public/visit-booking/{token}/slots?date_from&date_to  orari prenotabili
    POST /api/public/visit-booking/{token}/book                  prenota
    GET  /api/public/visit-booking/manage/{key}                  stato della prenotazione
    GET  /api/public/visit-booking/manage/{key}/slots            orari per riprogrammare
    POST /api/public/visit-booking/manage/{key}/cancel           annulla
    POST /api/public/visit-booking/manage/{key}/reschedule       riprogramma
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.exceptions import ValidationError
from operator_auth.context import OperatorContext
from operator_auth.dependencies import require_operator
from operator_auth.exceptions import PlatformAdminAgencyRequired

from . import service

router = APIRouter(prefix="/api/visit-booking-links", tags=["visit-booking"])
public_router = APIRouter(prefix="/api/public/visit-booking", tags=["visit-booking-public"])

TOKEN = Path(..., pattern=r"^[A-Za-z0-9_-]{16,200}$")
KEY = Path(..., pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


class LinkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    property_id: int = Field(..., ge=1)
    assigned_user_id: int = Field(..., ge=1)
    duration_minutes: int = Field(45, ge=15, le=240)
    step_minutes: Literal[15, 30, 60] = 30
    travel_minutes: int = Field(15, ge=0, le=120)
    min_notice_minutes: int = Field(720, ge=60, le=10080)
    horizon_days: int = Field(21, ge=1, le=60)
    owner_authorization: Literal["preauthorized", "per_visit"]
    owner_authorization_note: str | None = Field(None, max_length=500)
    expires_at: datetime | None = None


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("serve un istante con fuso orario")
    return value


class BookRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=120)
    phone: str | None = Field(None, max_length=40)
    email: str | None = Field(None, max_length=200)
    start_at: datetime
    key: UUID
    privacy_accepted: Literal[True]

    @field_validator("start_at")
    @classmethod
    def _con_fuso(cls, value: datetime) -> datetime:
        return _aware(value)


class RescheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_at: datetime
    key: UUID

    @field_validator("start_at")
    @classmethod
    def _con_fuso(cls, value: datetime) -> datetime:
        return _aware(value)


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except PlatformAdminAgencyRequired as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except service.VisitBookingError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": str(exc)},
                            headers=NO_STORE) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID", "message": str(exc)}, headers=NO_STORE) from exc


def _ip(request: Request) -> str:
    return request.client.host if request.client else "0.0.0.0"


def _window(date_from: datetime, date_to: datetime) -> tuple[datetime, datetime]:
    try:
        return _aware(date_from), _aware(date_to)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID", "message": str(exc)}) from exc


@router.post("", status_code=201)
def create(body: LinkCreate, ctx: OperatorContext = Depends(require_operator)):
    return _call(service.create_link, ctx, body.model_dump())


@router.get("")
def list_links(property_id: int = Query(..., ge=1), ctx: OperatorContext = Depends(require_operator)):
    return _call(service.list_links, ctx, property_id)


@router.post("/{link_id}/disable")
def disable(link_id: int = Path(..., ge=1), ctx: OperatorContext = Depends(require_operator)):
    return _call(service.disable_link, ctx, link_id)


@public_router.get("/manage/{key}")
def manage(key: str = KEY):
    return _call(service.manage_state, key)


@public_router.get("/manage/{key}/slots")
def manage_slots(key: str = KEY, date_from: datetime = Query(...), date_to: datetime = Query(...)):
    start, end = _window(date_from, date_to)
    return _call(service.manage_slots, key, date_from=start, date_to=end)


@public_router.post("/manage/{key}/cancel")
def manage_cancel(key: str = KEY):
    return _call(service.manage_cancel, key)


@public_router.post("/manage/{key}/reschedule")
def manage_reschedule(body: RescheduleRequest, key: str = KEY):
    return _call(service.manage_reschedule, key, start_at=body.start_at, new_key=str(body.key))


@public_router.get("/{token}")
def info(request: Request, token: str = TOKEN):
    return _call(service.public_info, token, client_ip=_ip(request))


@public_router.get("/{token}/slots")
def slots(request: Request, token: str = TOKEN, date_from: datetime = Query(...), date_to: datetime = Query(...)):
    start, end = _window(date_from, date_to)
    return _call(service.public_slots, token, date_from=start, date_to=end, client_ip=_ip(request))


@public_router.post("/{token}/book")
def book(body: BookRequest, request: Request, token: str = TOKEN):
    return _call(service.book, token, name=body.name, phone=body.phone, email=body.email, start_at=body.start_at,
                 key=str(body.key), client_ip=_ip(request))
