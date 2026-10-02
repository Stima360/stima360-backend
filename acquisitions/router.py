"""CRM-OPS-3 - la superficie HTTP delle Acquisizioni: `/api/acquisitions`.

Distinta dal ponte LMC-15 (`/api/acquisition`, singolare), che resta com'e'.

Come l'Agenda (appointments/router.py): lo scope arriva da
`require_operator` su OGNI rotta, scritto per esteso; nessuna rotta accetta
`agency_id` o un attore (`extra="forbid"` -> 422); ogni errore esce nella
forma `{"detail", "code", ...}`. I corpi si validano qui, non dalla firma
della rotta, cosi' anche un errore di validazione ha il suo `code`.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Body, Depends, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from psycopg2 import errors as _pg_errors
from pydantic import ValidationError as PydanticValidationError

from core.exceptions import ConflictError, NotFoundError, PermissionDenied, ValidationError
from operator_auth.context import OperatorContext
from operator_auth.dependencies import require_operator
from operator_auth.exceptions import PlatformAdminAgencyRequired

from . import errors, service
from .schemas import (
    AcquisitionCreate,
    AcquisitionPatch,
    LostBody,
    MandateBody,
    NewAppointmentBody,
    StatusBody,
)

router = APIRouter(prefix="/api/acquisitions", tags=["acquisitions"])

# Il driver finto dei test senza database non ha queste classi: `()` non
# intercetta nulla (stesso idioma di property/repository.py).
_OGGETTO_ASSENTE = tuple(c for c in (getattr(_pg_errors, "UndefinedTable", None),
                                     getattr(_pg_errors, "UndefinedColumn", None)) if c)
_OGGETTI_081 = ('"acquisitions"', '"acquisition_events"', '"acquisition_id"')


class _Richiesta(Exception):
    def __init__(self, code, detail, status=422):
        super().__init__(detail)
        self.code, self.detail, self.status = code, detail, status


def _risposta(http_status, code, detail, **extra):
    corpo = dict(extra)
    corpo.update({"detail": detail, "code": code})
    return JSONResponse(status_code=http_status, content=jsonable_encoder(corpo))


def _errore(exc):
    if isinstance(exc, _Richiesta):
        return _risposta(exc.status, exc.code, exc.detail)
    if isinstance(exc, PydanticValidationError):
        primo = (exc.errors() or [{}])[0]
        campo = ".".join(str(p) for p in primo.get("loc", ()) if p != "body")
        messaggio = primo.get("msg", "Dati non validi")
        return _risposta(422, errors.VALIDATION_ERROR,
                         f"{campo}: {messaggio}" if campo else messaggio)
    if isinstance(exc, PlatformAdminAgencyRequired):
        return _risposta(403, "PLATFORM_ADMIN_AGENCY_REQUIRED",
                         "Scegli un'agenzia per usare le Acquisizioni")
    extra = dict(getattr(exc, "extra", {}) or {})
    # L'Agenda, chiamata dentro la stessa transazione, porta i suoi conflitti
    # d'orario e le alternative: la UI li mostra come nella pagina Agenda.
    for chiave in ("conflicts", "alternatives"):
        valore = getattr(exc, chiave, None)
        if valore is not None:
            extra[chiave] = valore
    code = getattr(exc, "code", None)
    if isinstance(exc, NotFoundError):
        return _risposta(404, errors.NOT_FOUND, str(exc) or "Risorsa non trovata")
    if isinstance(exc, PermissionDenied):
        return _risposta(403, code or errors.FORBIDDEN_ROLE, str(exc), **extra)
    if isinstance(exc, ConflictError):
        return _risposta(409, code or errors.INVALID_TRANSITION, str(exc), **extra)
    if isinstance(exc, ValidationError):
        return _risposta(422, code or errors.VALIDATION_ERROR, str(exc), **extra)
    # Post-commit CRM-OPS-3 (RC-1): codice deployato prima della 081. Un
    # 500 muto diceva solo "Errore 500"; qui si chiude in modo leggibile e
    # l'operatore sa che manca la migration, non che ha sbagliato qualcosa.
    if isinstance(exc, _OGGETTO_ASSENTE) and any(o in str(exc) for o in _OGGETTI_081):
        return _risposta(503, errors.NOT_INSTALLED, errors.NOT_INSTALLED_MESSAGE)
    raise exc


def _x(funzione, *args, status=200, **kwargs):
    try:
        esito = funzione(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - tradotto o rilanciato da _errore
        return _errore(exc)
    return JSONResponse(status_code=status, content=jsonable_encoder(esito))


def _corpo(modello, dati):
    if not isinstance(dati, dict):
        raise _Richiesta(errors.VALIDATION_ERROR, "Il corpo deve essere un oggetto JSON")
    return modello.model_validate(dati)


def _istante(valore, nome):
    if valore is None or valore == "":
        return None
    try:
        esito = datetime.fromisoformat(valore.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _Richiesta(errors.VALIDATION_ERROR, f"{nome}: data e ora non valide") from exc
    if esito.tzinfo is None or esito.utcoffset() is None:
        raise _Richiesta(errors.VALIDATION_ERROR, f"{nome}: l'orario deve indicare il fuso")
    return esito


def _elenco(valore):
    if valore is None or valore.strip() == "":
        return None
    return [v.strip() for v in valore.split(",") if v.strip()]


# ---------------------------------------------------------------------------
# LETTURE
# ---------------------------------------------------------------------------

@router.get("/options")
def options(ctx: OperatorContext = Depends(require_operator)):
    return _x(service.options, ctx)


@router.get("")
def list_acquisitions(
    statuses: str | None = None,
    agent_id: int | None = None,
    date_from: str | None = Query(None, alias="from"),
    date_to: str | None = Query(None, alias="to"),
    city: str | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
    ctx: OperatorContext = Depends(require_operator),
):
    try:
        args = dict(statuses=_elenco(statuses), agent_id=agent_id,
                    date_from=_istante(date_from, "from"), date_to=_istante(date_to, "to"),
                    city=city, search=search, limit=limit, offset=offset)
    except _Richiesta as exc:
        return _errore(exc)
    return _x(lambda: {"items": service.list_acquisitions(ctx, **args)})


@router.get("/{acquisition_id}")
def get_acquisition(acquisition_id: int, ctx: OperatorContext = Depends(require_operator)):
    return _x(service.get_acquisition, ctx, acquisition_id)


# ---------------------------------------------------------------------------
# SCRITTURE
# ---------------------------------------------------------------------------

def _con_replica(funzione, *args):
    try:
        dettaglio, replica = funzione(*args)
    except Exception as exc:  # noqa: BLE001
        return _errore(exc)
    return JSONResponse(status_code=200 if replica else 201,
                        content=jsonable_encoder({**dettaglio, "replayed": replica}))


@router.post("")
def create_acquisition(dati: dict = Body(...), ctx: OperatorContext = Depends(require_operator)):
    try:
        corpo = _corpo(AcquisitionCreate, dati)
    except (_Richiesta, PydanticValidationError) as exc:
        return _errore(exc)
    return _con_replica(service.create_acquisition, ctx, corpo)


@router.patch("/{acquisition_id}")
def patch_acquisition(acquisition_id: int, dati: dict = Body(...),
                      ctx: OperatorContext = Depends(require_operator)):
    try:
        corpo = _corpo(AcquisitionPatch, dati)
    except (_Richiesta, PydanticValidationError) as exc:
        return _errore(exc)
    return _x(service.patch_acquisition, ctx, acquisition_id, corpo)


@router.post("/{acquisition_id}/status")
def change_status(acquisition_id: int, dati: dict = Body(...),
                  ctx: OperatorContext = Depends(require_operator)):
    try:
        corpo = _corpo(StatusBody, dati)
    except (_Richiesta, PydanticValidationError) as exc:
        return _errore(exc)
    return _x(service.change_status, ctx, acquisition_id, corpo)


@router.post("/{acquisition_id}/lost")
def mark_lost(acquisition_id: int, dati: dict = Body(...),
              ctx: OperatorContext = Depends(require_operator)):
    try:
        corpo = _corpo(LostBody, dati)
    except (_Richiesta, PydanticValidationError) as exc:
        return _errore(exc)
    return _x(service.mark_lost, ctx, acquisition_id, corpo)


@router.post("/{acquisition_id}/appointment")
def new_appointment(acquisition_id: int, dati: dict = Body(...),
                    ctx: OperatorContext = Depends(require_operator)):
    try:
        corpo = _corpo(NewAppointmentBody, dati)
    except (_Richiesta, PydanticValidationError) as exc:
        return _errore(exc)
    return _con_replica(service.new_appointment, ctx, acquisition_id, corpo)


@router.post("/{acquisition_id}/mandate")
def generate_mandate(acquisition_id: int, dati: dict = Body(...),
                     ctx: OperatorContext = Depends(require_operator)):
    try:
        corpo = _corpo(MandateBody, dati)
    except (_Richiesta, PydanticValidationError) as exc:
        return _errore(exc)
    return _x(service.generate_mandate, ctx, acquisition_id, corpo)
