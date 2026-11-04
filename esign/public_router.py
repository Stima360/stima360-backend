"""FIRMA OTP - `/api/public/esign/sign/{token}`: la pagina del firmatario, autenticata dal suo token personale.

Nessuna sessione, nessun cookie: il token (256 bit, solo la sua impronta nel database) e' l'unica credenziale, e i POST
sono JSON senza cookie, quindi non c'e' un CSRF da difendere. Ogni risposta e' `no-store`. Un token sconosciuto riceve
un 404 neutro; uno scaduto o revocato lo dice, senza altro.

    GET  /{token}            stato, documento (titolo, versione, impronta), dichiarazione, limiti
    GET  /{token}/pdf        il PDF congelato (inline)
    POST /{token}/otp        chiede il codice (email)
    POST /{token}/confirm    {code, consent_accepted, consent_version}
    POST /{token}/decline    {reason?}
    GET  /{token}/signed.pdf il documento sottoscritto, a procedimento completato
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import otp_service, providers, service

router = APIRouter(prefix="/api/public/esign/sign", tags=["esign-public"])
TOKEN = Path(..., pattern=r"^[A-Za-z0-9_-]{16,200}$")
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow",
           "X-Content-Type-Options": "nosniff"}


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., min_length=1, max_length=12)
    consent_accepted: bool
    consent_version: str = Field(..., min_length=1, max_length=60)


class DeclineBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(None, max_length=300)


def _client(request: Request) -> tuple[str | None, str | None]:
    return (request.client.host if request.client else None), request.headers.get("user-agent")


def _call(fn, *args, **kwargs):
    if not providers.otp_enabled():
        raise HTTPException(status_code=404, detail="Non trovato")
    try:
        return fn(*args, **kwargs)
    except service.NotFound as exc:
        raise HTTPException(status_code=404, detail={"code": exc.code, "message": "Collegamento non valido."}) from exc
    except service.SignatureError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code, "message": str(exc)}) from exc


@router.get("/{token}")
def state(request: Request, token: str = TOKEN):
    ip, ua = _client(request)
    return JSONResponse(_call(otp_service.public_state, token, ip=ip, user_agent=ua), headers=HEADERS)


@router.get("/{token}/pdf")
def pdf(request: Request, token: str = TOKEN):
    ip, ua = _client(request)
    out = _call(otp_service.public_pdf, token, ip=ip, user_agent=ua)
    return Response(content=out["data"], media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{out["filename"]}"', **HEADERS})


@router.post("/{token}/otp")
def request_otp(request: Request, token: str = TOKEN):
    ip, ua = _client(request)
    return JSONResponse(_call(otp_service.public_request_otp, token, ip=ip, user_agent=ua), headers=HEADERS)


@router.post("/{token}/confirm")
def confirm(body: ConfirmBody, request: Request, token: str = TOKEN):
    ip, ua = _client(request)
    return JSONResponse(_call(otp_service.public_confirm, token, code=body.code.strip(), consent_accepted=body.consent_accepted,
                              consent_version=body.consent_version, ip=ip, user_agent=ua), headers=HEADERS)


@router.post("/{token}/decline")
def decline(body: DeclineBody, request: Request, token: str = TOKEN):
    ip, ua = _client(request)
    return JSONResponse(_call(otp_service.public_decline, token, reason=body.reason, ip=ip, user_agent=ua), headers=HEADERS)


@router.get("/{token}/signed.pdf")
def signed(token: str = TOKEN):
    out = _call(otp_service.public_signed_document, token)
    return Response(content=out["data"], media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{out["filename"]}"', **HEADERS})
