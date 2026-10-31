"""BLOCCO-4B - `/api/message-replies`: risposta WhatsApp manuale a una conversazione.

Montato in main.py con `require_authenticated_operator`; ogni rotta prende lo
scope da `require_operator` (solo sessione). Il corpo e' chiuso
(`extra=forbid`): solo testo, conferma e chiave della richiesta. Destinatario
e numero mittente non si possono passare. In consultazione in sola lettura
la POST e' bloccata dalla guardia globale.

    GET  /api/message-replies/conversations/{sender_phone}   stato: finestra, STOP, blocchi, risposte
    POST /api/message-replies/conversations/{sender_phone}   invia una risposta di testo (una volta sola)
    POST /api/message-replies/conversations/{sender_phone}/replies/{reply_id}/resume
         riprende una risposta rimasta in coda e mai presa in carico (mai una incerta)
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field

from communication.exceptions import ConflictError, NotFoundError, ValidationError
from operator_auth.context import OperatorContext
from operator_auth.dependencies import require_operator
from operator_auth.exceptions import PlatformAdminAgencyRequired

from . import service
from .enums import TEXT_MAX

router = APIRouter(prefix="/api/message-replies", tags=["message-replies"])

SENDER = Path(..., pattern=r"^[0-9]{6,15}$")


class ReplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=1, max_length=TEXT_MAX)
    client_request_id: UUID
    confirm: bool = False


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = False


def _call(fn, *args):
    try:
        return fn(*args)
    except PlatformAdminAgencyRequired as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except service.ReplyError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": str(exc)}) from exc
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Conversazione non trovata."}) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail={"code": "CONFLICT", "message": str(exc)}) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID", "message": str(exc)}) from exc


@router.get("/conversations/{sender_phone}")
def state(sender_phone: str = SENDER, ctx: OperatorContext = Depends(require_operator)):
    return _call(service.reply_state, ctx, sender_phone)


@router.post("/conversations/{sender_phone}")
def reply(body: ReplyRequest, sender_phone: str = SENDER, ctx: OperatorContext = Depends(require_operator)):
    return _call(service.send_reply, ctx, sender_phone, body.text, str(body.client_request_id), body.confirm)


@router.post("/conversations/{sender_phone}/replies/{reply_id}/resume")
def resume(body: ResumeRequest, sender_phone: str = SENDER, reply_id: int = Path(..., ge=1),
           ctx: OperatorContext = Depends(require_operator)):
    return _call(service.resume_reply, ctx, sender_phone, reply_id, body.confirm)
