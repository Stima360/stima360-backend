"""BLOCCO-4A - `/api/inbound-messages`: messaggi WhatsApp ricevuti, sola consultazione
e associazione. Nessuna rotta invia messaggi.

Montato in main.py con `require_authenticated_operator`; ogni rotta prende lo
scope da `require_operator` (solo sessione). L'agenzia viene sempre dalla
sessione; il corpo dell'associazione e' chiuso (`extra=forbid`). In
consultazione in sola lettura la POST e' bloccata dalla guardia globale.

    GET  /api/inbound-messages/conversations?only_unlinked=   conversazioni
    GET  /api/inbound-messages/conversations/{sender_phone}   messaggi di un mittente
    GET  /api/inbound-messages/{id}                           un messaggio (con lo storico associazioni)
    POST /api/inbound-messages/{id}/link                      associa contatto e immobile
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field

from operator_auth.context import OperatorContext
from operator_auth.dependencies import require_operator
from operator_auth.exceptions import PlatformAdminAgencyRequired

from . import service

router = APIRouter(prefix="/api/inbound-messages", tags=["inbound-messages"])


class LinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contact_id: int | None = Field(None, gt=0)
    property_id: int | None = Field(None, gt=0)


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except PlatformAdminAgencyRequired as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except service.InboundError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": str(exc)}) from exc


@router.get("/conversations")
def conversations(only_unlinked: bool = Query(False), ctx: OperatorContext = Depends(require_operator)):
    return _call(service.list_conversations, ctx, only_unlinked=only_unlinked)


@router.get("/conversations/{sender_phone}")
def conversation(sender_phone: str = Path(..., pattern=r"^[0-9]{6,15}$"),
                 ctx: OperatorContext = Depends(require_operator)):
    return _call(service.conversation, ctx, sender_phone)


@router.get("/{message_id}")
def message(message_id: int, ctx: OperatorContext = Depends(require_operator)):
    return _call(service.get_message, ctx, message_id)


@router.post("/{message_id}/link")
def link(message_id: int, body: LinkRequest, ctx: OperatorContext = Depends(require_operator)):
    return _call(service.link, ctx, message_id, body.contact_id, body.property_id)
