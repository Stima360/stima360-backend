"""BLOCCO-4D - `/api/auto-replies`: motore delle risposte automatiche, SOLO SIMULAZIONE.

Montato in main.py con `require_authenticated_operator`; lo scope viene da
`require_operator`. Nessuna rotta invia o accoda messaggi.

    GET  /settings                              stato (arresto generale, modo dell'agenzia)
    PUT  /settings                              modo dell'agenzia: off | simulate (titolare/amministratore)
    POST /tick                                  un giro di decisioni simulate (titolare/amministratore)
    GET  /conversations/{sender_phone}          decisioni simulate e pausa della conversazione
    POST /conversations/{sender_phone}/pause    mette in pausa la conversazione
    POST /conversations/{sender_phone}/resume   riprende la conversazione
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict

from operator_auth.context import OperatorContext
from operator_auth.dependencies import require_operator
from operator_auth.exceptions import PlatformAdminAgencyRequired

from . import service

router = APIRouter(prefix="/api/auto-replies", tags=["auto-replies"])

SENDER = Path(..., pattern=r"^[0-9]{6,15}$")


class SettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["off", "simulate"]


def _call(fn, *args):
    try:
        return fn(*args)
    except PlatformAdminAgencyRequired as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except service.AutoReplyError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": str(exc)}) from exc


@router.get("/settings")
def get_settings(ctx: OperatorContext = Depends(require_operator)):
    return _call(service.get_settings, ctx)


@router.put("/settings")
def put_settings(body: SettingsRequest, ctx: OperatorContext = Depends(require_operator)):
    return _call(service.set_settings, ctx, body.mode)


@router.post("/tick")
def tick(ctx: OperatorContext = Depends(require_operator)):
    return _call(service.tick, ctx)


@router.get("/conversations/{sender_phone}")
def conversation(sender_phone: str = SENDER, ctx: OperatorContext = Depends(require_operator)):
    return _call(service.conversation_state, ctx, sender_phone)


@router.post("/conversations/{sender_phone}/pause")
def pause(sender_phone: str = SENDER, ctx: OperatorContext = Depends(require_operator)):
    return _call(service.set_pause, ctx, sender_phone, True)


@router.post("/conversations/{sender_phone}/resume")
def resume(sender_phone: str = SENDER, ctx: OperatorContext = Depends(require_operator)):
    return _call(service.set_pause, ctx, sender_phone, False)
