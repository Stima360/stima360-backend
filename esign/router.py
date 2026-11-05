"""BLOCCO-6B / FIRMA OTP - `/api/signature-requests`: richieste di firma dei fogli visita (operatore).

Montato in main.py con `require_authenticated_operator`; scope da `require_operator`.

    POST /api/signature-requests                          crea (idempotente sulla chiave): simulazione o firma OTP
    GET  /api/signature-requests?visit_sheet_id=          richieste di un foglio, con stato operativo dal server
    GET  /api/signature-requests/config/provider          quale modo e' attivo (nessun segreto)
    POST /api/signature-requests/{id}/cancel              revoca
    POST /api/signature-requests/{id}/simulate            esito simulato: completed | declined (solo simulazione)
    POST /api/signature-requests/{id}/links               nuovo collegamento del cliente: {send: true} email, false = URL una volta
    POST /api/signature-requests/{id}/sign-as-agent       la firma dell'agente assegnato (password + dichiarazione)
    GET  /api/signature-requests/{id}/document            documento sottoscritto (o ricevuta di simulazione)
    GET  /api/signature-requests/{id}/audit-trail         rapporto delle prove (PDF)
    GET  /api/signature-requests/{id}/events              cronologia delle prove e verifica della catena
    GET  /api/signature-requests/{id}/verify              verifica di integrita' (catena, sigilli, impronte)
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from operator_auth.context import OperatorContext
from operator_auth.dependencies import require_operator
from operator_auth.exceptions import PlatformAdminAgencyRequired

from . import otp_service, providers, service

router = APIRouter(prefix="/api/signature-requests", tags=["signature-requests"])
NO_STORE = {"Cache-Control": "no-store"}


class CreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visit_sheet_id: int = Field(..., ge=1)
    signer_name: str = Field(..., min_length=1, max_length=120)
    signer_contact: str = Field(..., min_length=3, max_length=200)
    expires_in_days: int = Field(7, ge=1, le=30)
    idempotency_key: UUID
    include_agent: bool = True


class OutcomeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["completed", "declined"]


class LinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    send: bool = True


class AgentSignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(..., min_length=1, max_length=200)
    consent_accepted: bool
    consent_version: str = Field(..., min_length=1, max_length=60)


class AgentSignNow(BaseModel):
    """«Firmo anche io adesso»: sempre dichiarazione accettata e password, mai implicita."""
    model_config = ConfigDict(extra="forbid")

    password: str = Field(..., min_length=1, max_length=200)
    consent_accepted: bool
    consent_version: str = Field(..., min_length=1, max_length=60)


class SendRequest(BaseModel):
    """FIRMA SEMPLIFICATA: «Invia per firma» dalla visita (il foglio si genera o si riusa sul server)."""
    model_config = ConfigDict(extra="forbid")

    appointment_id: int = Field(..., ge=1)
    known_sheet_id: int | None = Field(None, ge=1)
    signer_name: str = Field(..., min_length=1, max_length=120)
    signer_contact: str = Field(..., min_length=3, max_length=200)
    idempotency_key: UUID
    agent_sign: AgentSignNow | None = None


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except PlatformAdminAgencyRequired as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except service.SignatureError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": str(exc)}) from exc


def _client(request: Request) -> tuple[str | None, str | None]:
    return (request.client.host if request.client else None), request.headers.get("user-agent")


@router.post("")
def create(body: CreateRequest, ctx: OperatorContext = Depends(require_operator)):
    return _call(service.create, ctx, visit_sheet_id=body.visit_sheet_id, signer_name=body.signer_name.strip(),
                 signer_contact=body.signer_contact.strip(), expires_in_days=body.expires_in_days,
                 idempotency_key=str(body.idempotency_key), include_agent=body.include_agent)


@router.post("/send")
def send(body: SendRequest, request: Request, ctx: OperatorContext = Depends(require_operator)):
    """Un gesto: foglio pronto (generato o riusato), richiesta, collegamento al cliente e, se chiesto, firma
    dell'agente con password verificata PRIMA di qualunque invio."""
    from . import send_flow

    ip, ua = _client(request)
    return JSONResponse(_call(send_flow.send_for_signature, ctx, appointment_id=body.appointment_id,
                              known_sheet_id=body.known_sheet_id, signer_name=body.signer_name,
                              signer_email=body.signer_contact, idempotency_key=str(body.idempotency_key),
                              agent_sign=body.agent_sign.model_dump() if body.agent_sign else None, ip=ip, user_agent=ua),
                        headers=NO_STORE)


@router.get("/notifications")
def notifications(days: int = Query(14, ge=1, le=60), ctx: OperatorContext = Depends(require_operator)):
    """Gli avvisi per la pagina «Oggi» (completate, rifiutate, scadute, invii non riusciti), uno per visita."""
    from . import summary

    return JSONResponse(_call(summary.notifications, ctx, days=days), headers=NO_STORE)


@router.get("")
def list_requests(visit_sheet_id: int = Query(..., ge=1), ctx: OperatorContext = Depends(require_operator)):
    return _call(service.list_for_sheet, ctx, visit_sheet_id)


@router.get("/config/provider")
def provider_config(ctx: OperatorContext = Depends(require_operator)):
    """Quale modo di firma e' attivo (per mostrare il riquadro giusto). Nessun segreto, nessun indirizzo."""
    ctx.require_agency()
    try:
        modo = providers.mode()
    except providers.ProviderNotConfigured:
        return {"configured": False, "provider": None, "simulation": False, "otp": False}
    out = {"configured": True, "provider": modo, "simulation": modo == providers.SIMULATED, "otp": modo == providers.OTP_PROVIDER}
    if out["otp"]:
        from . import otp_security
        from . import consent

        out["consent_version"] = consent.VERSION
        try:
            otp_security.require_configuration()
            out["otp_ready"] = True
        except otp_security.NotConfigured as exc:
            out["otp_ready"] = False
            out["otp_problem"] = str(exc)
    return out


@router.post("/{request_id}/cancel")
def cancel(request_id: int = Path(..., ge=1), ctx: OperatorContext = Depends(require_operator)):
    return _call(service.cancel, ctx, request_id)


@router.post("/{request_id}/simulate")
def simulate(body: OutcomeRequest, request_id: int = Path(..., ge=1), ctx: OperatorContext = Depends(require_operator)):
    return _call(service.simulate_outcome, ctx, request_id, body.outcome)


@router.post("/{request_id}/links")
def links(body: LinkRequest, request_id: int = Path(..., ge=1), ctx: OperatorContext = Depends(require_operator)):
    """`send: true` reinvia l'email con un collegamento nuovo; `false` restituisce il collegamento UNA volta (mai in cache)."""
    return JSONResponse(_call(otp_service.issue_link, ctx, request_id, send=body.send), headers=NO_STORE)


@router.post("/{request_id}/sign-as-agent")
def sign_as_agent(body: AgentSignRequest, request: Request, request_id: int = Path(..., ge=1),
                  ctx: OperatorContext = Depends(require_operator)):
    ip, ua = _client(request)
    return JSONResponse(_call(otp_service.sign_as_agent, ctx, request_id, password=body.password,
                              consent_accepted=body.consent_accepted, consent_version=body.consent_version, ip=ip, user_agent=ua),
                        headers=NO_STORE)


@router.get("/{request_id}/document")
def document(request_id: int = Path(..., ge=1), ctx: OperatorContext = Depends(require_operator)):
    out = _call(service.document, ctx, request_id)
    return Response(content=out["data"], media_type=out["media_type"], headers={
        "Content-Disposition": f'attachment; filename="{out["filename"]}"', **NO_STORE})


@router.get("/{request_id}/audit-trail")
def audit_trail(request_id: int = Path(..., ge=1), ctx: OperatorContext = Depends(require_operator)):
    out = _call(service.audit_trail, ctx, request_id)
    return Response(content=out["data"], media_type=out["media_type"], headers={
        "Content-Disposition": f'attachment; filename="{out["filename"]}"', **NO_STORE})


@router.get("/{request_id}/events")
def events(request_id: int = Path(..., ge=1), ctx: OperatorContext = Depends(require_operator)):
    return JSONResponse(_call(otp_service.events, ctx, request_id), headers=NO_STORE)


@router.get("/{request_id}/verify")
def verify(request_id: int = Path(..., ge=1), ctx: OperatorContext = Depends(require_operator)):
    return JSONResponse(_call(otp_service.verify_integrity, ctx, request_id), headers=NO_STORE)
