"""BLOCCO-6B - `/api/signature-requests`: richieste di firma dei fogli visita.

Montato in main.py con `require_authenticated_operator`; scope da `require_operator`.
Oggi l'unico fornitore e' l'adattatore SIMULATO: nessuna firma valida.

    POST /api/signature-requests                          crea (idempotente sulla chiave)
    GET  /api/signature-requests?visit_sheet_id=          richieste di un foglio
    POST /api/signature-requests/{id}/cancel              annulla
    POST /api/signature-requests/{id}/simulate            esito simulato: completed | declined
    GET  /api/signature-requests/{id}/document            documento ricevuto (ricevuta di simulazione)
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from operator_auth.context import OperatorContext
from operator_auth.dependencies import require_operator
from operator_auth.exceptions import PlatformAdminAgencyRequired

from . import providers, service

router = APIRouter(prefix="/api/signature-requests", tags=["signature-requests"])
#: BLOCCO-5: gli eventi di un fornitore reale. Pubblico (lo chiama il
#: fornitore), autenticato dalla firma del corpo; nessun fornitore e'
#: configurato, quindi oggi risponde sempre 404 neutro.
public_router = APIRouter(prefix="/api/public/esign", tags=["esign-public"])
PROVIDER = Path(..., pattern=r"^[a-z][a-z0-9_]{1,29}$")
MAX_EVENT_BYTES = 256 * 1024


class CreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visit_sheet_id: int = Field(..., ge=1)
    signer_name: str = Field(..., min_length=1, max_length=120)
    signer_contact: str = Field(..., min_length=3, max_length=200)
    expires_in_days: int = Field(7, ge=1, le=30)
    idempotency_key: UUID


class OutcomeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["completed", "declined"]


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except PlatformAdminAgencyRequired as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except service.SignatureError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": str(exc)}) from exc


@router.post("")
def create(body: CreateRequest, ctx: OperatorContext = Depends(require_operator)):
    return _call(service.create, ctx, visit_sheet_id=body.visit_sheet_id, signer_name=body.signer_name.strip(),
                 signer_contact=body.signer_contact.strip(), expires_in_days=body.expires_in_days,
                 idempotency_key=str(body.idempotency_key))


@router.get("")
def list_requests(visit_sheet_id: int = Query(..., ge=1), ctx: OperatorContext = Depends(require_operator)):
    return _call(service.list_for_sheet, ctx, visit_sheet_id)


@router.post("/{request_id}/cancel")
def cancel(request_id: int = Path(..., ge=1), ctx: OperatorContext = Depends(require_operator)):
    return _call(service.cancel, ctx, request_id)


@router.post("/{request_id}/simulate")
def simulate(body: OutcomeRequest, request_id: int = Path(..., ge=1), ctx: OperatorContext = Depends(require_operator)):
    return _call(service.simulate_outcome, ctx, request_id, body.outcome)


@router.get("/{request_id}/document")
def document(request_id: int = Path(..., ge=1), ctx: OperatorContext = Depends(require_operator)):
    out = _call(service.document, ctx, request_id)
    return Response(content=out["data"], media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{out["filename"]}"', "Cache-Control": "no-store"})


@router.get("/{request_id}/audit-trail")
def audit_trail(request_id: int = Path(..., ge=1), ctx: OperatorContext = Depends(require_operator)):
    out = _call(service.audit_trail, ctx, request_id)
    return Response(content=out["data"], media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{out["filename"]}"', "Cache-Control": "no-store"})


@public_router.post("/webhooks/{provider}")
async def provider_event(request: Request, provider: str = PROVIDER):
    raw = await request.body()
    if len(raw) > MAX_EVENT_BYTES:
        raise HTTPException(status_code=413, detail="Evento troppo grande")
    try:
        return _call(service.handle_provider_event, provider, dict(request.headers), raw)
    except providers.InvalidEventSignature as exc:
        raise HTTPException(status_code=401, detail="Evento non autentico") from exc
