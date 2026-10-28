"""BLOCCO-1B - `/api/agency-profile`: dati dell'agenzia e logo.

Montato in main.py con `require_authenticated_operator`, come gli altri router
di dominio; ogni route prende lo scope da `require_operator` (solo sessione).
Nessuna route accetta un `agency_id`: l'agenzia e' quella della sessione.

    GET    /api/agency-profile          dati salvati + metadati del logo
    PUT    /api/agency-profile          salva i dati (owner/admin)
    GET    /api/agency-profile/logo     il logo dell'agenzia (404 se assente)
    PUT    /api/agency-profile/logo     carica/sostituisce (corpo = il file)
    DELETE /api/agency-profile/logo     rimuove (owner/admin)

Il logo si invia come corpo grezzo, non multipart: un file solo, letto a pezzi
e mai oltre 2 MB.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from operator_auth.context import OperatorContext
from operator_auth.dependencies import require_operator
from operator_auth.exceptions import PlatformAdminAgencyRequired

from . import service
from .enums import LOGO_MAX_BYTES, LOGO_TOO_LARGE_MESSAGE, PROFILE_CORRUPTED_MESSAGE
from .image_validation import InvalidLogo
from .schemas import AgencyProfile, AgencyProfileResponse, LogoMetadata

router = APIRouter(prefix="/api/agency-profile", tags=["agency-profile"])

_PRIVATE_HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, (PlatformAdminAgencyRequired, service.ProfileForbidden)):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, (service.AgencyNotFound, service.LogoNotFound)):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, service.LogoUnavailable):
        # 503 come i moduli del censimento senza la loro migrazione: mai un 500 muto.
        return HTTPException(status_code=503, detail={"code": "logo_unavailable", "message": str(exc)})
    if isinstance(exc, service.ProfileCorrupted):
        # Messaggio costante: il dettaglio resta nel log del server.
        return HTTPException(status_code=500, detail=PROFILE_CORRUPTED_MESSAGE)
    if isinstance(exc, InvalidLogo):
        status = 413 if exc.code == "too_large" else 422
        return HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)})
    raise exc


def _call(fn, *args):
    try:
        return fn(*args)
    except (PlatformAdminAgencyRequired, service.ProfileForbidden, service.AgencyNotFound,
            service.LogoNotFound, service.LogoUnavailable, service.ProfileCorrupted, InvalidLogo) as exc:
        raise _translate(exc) from exc


@router.get("", response_model=AgencyProfileResponse)
def read_profile(ctx: OperatorContext = Depends(require_operator)):
    return _call(service.get_profile, ctx)


@router.put("", response_model=AgencyProfileResponse)
def save_profile(body: AgencyProfile, ctx: OperatorContext = Depends(require_operator)):
    return _call(service.update_profile, ctx, body)


@router.get("/logo")
def read_logo(ctx: OperatorContext = Depends(require_operator)):
    logo = _call(service.get_logo, ctx)
    extension = "png" if logo["content_type"] == "image/png" else "jpg"
    return Response(content=logo["data"], media_type=logo["content_type"], headers={
        **_PRIVATE_HEADERS,
        "Content-Disposition": f'inline; filename="logo.{extension}"',
        "Content-Security-Policy": "default-src 'none'",
    })


async def _read_bounded(request: Request) -> bytes:
    """Il corpo, mai oltre LOGO_MAX_BYTES: oltre si risponde 413 subito."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > LOGO_MAX_BYTES:
        raise HTTPException(status_code=413, detail={"code": "too_large", "message": LOGO_TOO_LARGE_MESSAGE})
    received = bytearray()
    async for chunk in request.stream():
        received.extend(chunk)
        if len(received) > LOGO_MAX_BYTES:
            raise HTTPException(status_code=413, detail={"code": "too_large", "message": LOGO_TOO_LARGE_MESSAGE})
    return bytes(received)


@router.put("/logo", response_model=LogoMetadata)
async def upload_logo(request: Request, ctx: OperatorContext = Depends(require_operator)):
    # Permesso e modulo installato si verificano PRIMA di leggere il corpo: un
    # agente (o un ambiente senza la 115) non fa nemmeno arrivare il file in memoria.
    await run_in_threadpool(_call, service.require_logo_upload, ctx)
    data = await _read_bounded(request)
    return await run_in_threadpool(_call, service.put_logo, ctx, data)


@router.delete("/logo", status_code=204)
def remove_logo(ctx: OperatorContext = Depends(require_operator)):
    _call(service.delete_logo, ctx)
    return Response(status_code=204)
