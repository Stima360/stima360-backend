"""WHATSAPP COEXISTENCE - `/api/whatsapp-coexistence`: collegare il numero
dell'app WhatsApp Business con la procedura ufficiale di Meta.

Montato in main.py con `require_authenticated_operator`; lo scope viene da
`require_operator`. I corpi sono chiusi (`extra=forbid`). Nessuna rotta invia
messaggi a clienti.

    GET  /api/whatsapp-coexistence/config                    valori pubblici della procedura
    GET  /api/whatsapp-coexistence/onboardings               collegamenti e prove di ricezione
    POST /api/whatsapp-coexistence/onboardings               fine della procedura Meta (titolare)
    POST /api/whatsapp-coexistence/onboardings/{id}/verify   chiede a Meta lo stato del numero
    POST /api/whatsapp-coexistence/onboardings/{id}/sync     nuova richiesta di rubrica o storico (titolare)
    GET  /api/whatsapp-coexistence/messages/{id}/media       l'allegato di un messaggio visibile, letto da Meta
                                                             a richiesta (vedi media.py)
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from operator_auth.context import OperatorContext
from operator_auth.dependencies import require_operator
from operator_auth.exceptions import PlatformAdminAgencyRequired

from inbound_messages import service as inbound_service

from . import media, service

router = APIRouter(prefix="/api/whatsapp-coexistence", tags=["whatsapp-coexistence"])


class OnboardingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., min_length=4, max_length=2000)
    waba_id: str = Field(..., pattern=r"^[0-9]{5,25}$")
    phone_number_id: str | None = Field(None, pattern=r"^[0-9]{5,20}$")
    business_id: str | None = Field(None, pattern=r"^[0-9]{5,25}$")


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["contacts", "history"]


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except PlatformAdminAgencyRequired as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except service.CoexistenceError as exc:
        raise HTTPException(status_code=exc.status,
                            detail={"code": exc.code, "message": str(exc), **exc.extra}) from exc


@router.get("/config")
def config(ctx: OperatorContext = Depends(require_operator)):
    return _call(service.config, ctx)


@router.get("/onboardings")
def onboardings(ctx: OperatorContext = Depends(require_operator)):
    return _call(service.list_onboardings, ctx)


@router.post("/onboardings")
def start(body: OnboardingRequest, ctx: OperatorContext = Depends(require_operator)):
    return _call(service.start_onboarding, ctx, code=body.code, waba_id=body.waba_id,
                 phone_number_id=body.phone_number_id, business_id=body.business_id)


@router.post("/onboardings/{onboarding_id}/verify")
def verify(onboarding_id: int, ctx: OperatorContext = Depends(require_operator)):
    return _call(service.verify, ctx, onboarding_id)


@router.post("/onboardings/{onboarding_id}/sync")
def sync(body: SyncRequest, onboarding_id: int, ctx: OperatorContext = Depends(require_operator)):
    return _call(service.request_sync, ctx, onboarding_id, body.kind)


@router.get("/messages/{message_id}/media")
def message_media(message_id: int, ctx: OperatorContext = Depends(require_operator)):
    try:
        row = inbound_service.visible_message_row(ctx, message_id)
        content, media_type, filename, inline = media.fetch(row)
    except PlatformAdminAgencyRequired as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except inbound_service.InboundError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": str(exc)}) from exc
    except media.MediaError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": str(exc)}) from exc
    disposition = "inline" if inline else "attachment"
    return Response(content=content, media_type=media_type, headers={
        "Content-Disposition": f'{disposition}; filename="{filename}"',
        "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store",
        "Content-Security-Policy": "default-src 'none'; img-src 'self'; media-src 'self'; sandbox"})
