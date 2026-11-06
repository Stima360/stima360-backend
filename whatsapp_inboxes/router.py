"""WHATSAPP DUE CASELLE - `/api/whatsapp-inboxes`: le caselle dell'agenzia.

Montato in main.py con `require_authenticated_operator`; lo scope viene da
`require_operator`. Nessuna rotta invia messaggi.

    GET  /api/whatsapp-inboxes                       le caselle (visibili; tutte per chi configura)
    GET  /api/whatsapp-inboxes/members               i membri attivi dell'agenzia (chi configura)
    PUT  /api/whatsapp-inboxes/{phone_number_id}     impostazioni di una casella (titolare)
    PUT  /api/whatsapp-inboxes/{phone_number_id}/access  operatori autorizzati (titolare)
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field

from operator_auth.context import OperatorContext
from operator_auth.dependencies import require_operator
from operator_auth.exceptions import PlatformAdminAgencyRequired

from . import service
from .enums import LABEL_MAX

router = APIRouter(prefix="/api/whatsapp-inboxes", tags=["whatsapp-inboxes"])

NUMBER = Path(..., pattern=r"^[0-9]{5,20}$")


class InboxRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(None, min_length=1, max_length=LABEL_MAX)
    kind: Literal["cloud_api", "coexistence"] | None = None
    automations_enabled: bool | None = None
    access_mode: Literal["agency", "restricted"] | None = None
    status: Literal["active", "disabled"] | None = None
    display_phone_number: str | None = Field(None, pattern=r"^[0-9]{6,15}$")


class AccessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_ids: list[int] = Field(default_factory=list, max_length=200)


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except PlatformAdminAgencyRequired as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except service.InboxError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": str(exc)}) from exc


@router.get("")
def list_inboxes(ctx: OperatorContext = Depends(require_operator)):
    return _call(service.list_inboxes, ctx)


@router.get("/members")
def members(ctx: OperatorContext = Depends(require_operator)):
    return _call(service.members, ctx)


@router.put("/{phone_number_id}")
def save(body: InboxRequest, phone_number_id: str = NUMBER, ctx: OperatorContext = Depends(require_operator)):
    return _call(service.save_inbox, ctx, phone_number_id, label=body.label, kind=body.kind,
                 automations_enabled=body.automations_enabled, access_mode=body.access_mode, status=body.status,
                 display_phone_number=body.display_phone_number)


@router.put("/{phone_number_id}/access")
def access(body: AccessRequest, phone_number_id: str = NUMBER, ctx: OperatorContext = Depends(require_operator)):
    return _call(service.set_access, ctx, phone_number_id, body.user_ids)
