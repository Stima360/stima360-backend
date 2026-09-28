"""A30-12 - la rotta PUBBLICA del booking: `/api/public/booking`.

Montata in `main.py` SENZA `require_authenticated_operator` (stesso schema
di `communication/public_router.py`): qui non c'e' un operatore, c'e' il
client pubblico che apre un link. GET non scrive MAI. Ogni risposta porta
`Cache-Control: no-store` (D privacy).

D privacy: un token inesistente, disabilitato, scaduto o revocato produce
SEMPRE la stessa risposta generica - mai un dettaglio che li distingua.
Stesso principio per un rate limit superato (D9): risposta neutra, nessun
"quanti te ne restano".
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Body, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError

from core.exceptions import ValidationError

from . import service
from .schemas import PublicBookingSubmitBody

router = APIRouter(prefix="/api/public/booking", tags=["public-booking"])

_NO_STORE = {"Cache-Control": "no-store"}

#: D privacy: la STESSA forma, qualunque sia la causa (token inesistente,
#: disabilitato, scaduto, revocato, o rate limit superato).
_GENERIC_UNAVAILABLE = {"available": False}


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "0.0.0.0"


def _istante(valore: str, nome: str) -> datetime:
    try:
        esito = datetime.fromisoformat(valore.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"{nome}: data e ora non valide") from exc
    if esito.tzinfo is None:
        raise ValidationError(f"{nome}: l'orario deve indicare il fuso")
    return esito


@router.get("/{token}")
def get_link_info(token: str, request: Request):
    """NON scrive mai (nessun prefetch di link puo' consumare nulla)."""
    try:
        dati = service.get_public_link_info(token, client_ip=_client_ip(request))
    except (service.LinkNotBookable, service.RateLimited):
        return JSONResponse(status_code=404, content=_GENERIC_UNAVAILABLE,
                            headers=_NO_STORE)
    return JSONResponse(status_code=200, content=jsonable_encoder(dati), headers=_NO_STORE)


@router.get("/{token}/slots")
def get_slots(token: str, request: Request,
             date_from: str = Query(..., alias="from"),
             date_to: str = Query(..., alias="to")):
    """D3: NESSUN duration/user_id/agency_id/buffer - solo il range."""
    try:
        inizio = _istante(date_from, "from")
        fine = _istante(date_to, "to")
        dati = service.get_public_slots(token, date_from=inizio, date_to=fine,
                                        client_ip=_client_ip(request))
    except (service.LinkNotBookable, service.RateLimited):
        return JSONResponse(status_code=404, content=_GENERIC_UNAVAILABLE,
                            headers=_NO_STORE)
    except ValidationError as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)}, headers=_NO_STORE)
    return JSONResponse(status_code=200, content=jsonable_encoder(dati), headers=_NO_STORE)


@router.post("/{token}/submit")
def submit(token: str, request: Request, dati: dict = Body(...)):
    try:
        corpo = PublicBookingSubmitBody.model_validate(dati)
    except PydanticValidationError as exc:
        primo = exc.errors()[0] if exc.errors() else {}
        campo = ".".join(str(p) for p in primo.get("loc", ()) if p != "body")
        messaggio = primo.get("msg", "Dati non validi")
        return JSONResponse(
            status_code=422,
            content={"detail": f"{campo}: {messaggio}" if campo else messaggio},
            headers=_NO_STORE)

    try:
        esito = service.submit_booking(token, corpo, client_ip=_client_ip(request))
    except (service.LinkNotBookable, service.RateLimited):
        return JSONResponse(status_code=404, content=_GENERIC_UNAVAILABLE,
                            headers=_NO_STORE)
    except service.SubmissionPayloadMismatch:
        return JSONResponse(
            status_code=409,
            content={"detail": "Questa richiesta risulta gia' inviata con dati diversi"},
            headers=_NO_STORE)
    except ValidationError as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)}, headers=_NO_STORE)
    except Exception as exc:  # noqa: BLE001
        # Copre `appointments.errors.PublicSlotUnavailable` (409, TOCTOU/HARD
        # recheck/conflitto) senza importare il modulo dell'Agenda qui: il
        # router pubblico non deve conoscere i codici interni dell'Agenda,
        # solo che "non e' andata" merita un 409 generico e nessun dettaglio.
        codice = getattr(exc, "code", None)
        if codice == "PUBLIC_SLOT_UNAVAILABLE":
            return JSONResponse(
                status_code=409,
                content={"detail": "Questo orario non e' piu' disponibile"},
                headers=_NO_STORE)
        raise
    return JSONResponse(status_code=201, content=jsonable_encoder(esito), headers=_NO_STORE)
