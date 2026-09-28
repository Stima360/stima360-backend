"""A30-2 - la superficie HTTP dell'Agenda: `/api/appointments`.

MONTATO in `main.py` (mount A30, chiude D1): l'API e' attiva ovunque questo
codice viene deployato. Mount come i domini operatore - ammissione con
`require_authenticated_operator` - e `main.py` nomina l'Agenda solo per
l'import e il mount. La UI dell'Agenda NON e' ancora montata ne' pubblicata.

Lo scope arriva da `require_operator` su OGNI rotta, scritto per esteso e non
attraverso un alias di modulo (P26-4: un alias fa leggere le rotte come non
scopate al prover AST). Nessuna rotta accetta `agency_id`, un attore,
`source` o `test_run_id`: il tenant viene da `ctx.require_agency()`, l'attore
da `ctx.user_id`, e `extra="forbid"` rende ogni tentativo un 422.

I corpi si validano QUI, non dalla firma della rotta: cosi' ogni errore -
anche di validazione - esce nella stessa forma `{"detail", "code", ...}`
senza toccare gli handler globali dell'applicazione (che non sono di A30-2).
"""
from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Body, Depends, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError

from core.exceptions import ConflictError, NotFoundError, PermissionDenied, ValidationError
from operator_auth.context import OperatorContext
from operator_auth.dependencies import require_operator
from operator_auth.exceptions import PlatformAdminAgencyRequired

from . import errors, service
from .schemas import (
    AppointmentCreateBody,
    AvailabilityCheckBody,
    CancelBody,
    CompleteBody,
    ConfirmBody,
    NoShowBody,
    PatchBody,
    ReassignBody,
    RescheduleBody,
    ScheduleBody,
)
from . import working_hours_service
from .working_hours_schemas import (
    AgencyClosureCreate,
    AvailabilityExceptionCreate,
    WorkingHoursReplaceBody,
)

# A30-12: la gestione operatore dei link di booking pubblico vive nel
# pacchetto `public_booking/` (CRUD, permessi D2) - queste rotte sono solo
# la superficie HTTP, stessa forma delle rotte A30-11 sopra.
from public_booking import service as booking_link_service
from public_booking.schemas import BookingLinkCreateBody, BookingLinkPatchBody

router = APIRouter(prefix="/api/appointments", tags=["appointments"])

#: Tipi d'errore pydantic che hanno un codice proprio nel contratto A30-2.
_CODICI_VALIDAZIONE = {
    "agent_required": errors.AGENT_REQUIRED,
    "timezone_required": errors.TIMEZONE_REQUIRED,
}


class _Richiesta(Exception):
    """Un errore della richiesta, prima del service."""

    def __init__(self, code, detail, status=422):
        super().__init__(detail)
        self.code, self.detail, self.status = code, detail, status


def _risposta(http_status, code, detail, **extra):
    corpo = dict(extra)
    corpo.update({"detail": detail, "code": code})
    return JSONResponse(status_code=http_status, content=jsonable_encoder(corpo))


def _errore(exc):
    """Traduce ogni errore in `{"detail", "code", ...}`. Mai un errore
    tecnico grezzo: il `detail` e' sempre un messaggio pensato per l'utente."""
    if isinstance(exc, _Richiesta):
        return _risposta(exc.status, exc.code, exc.detail)
    if isinstance(exc, PydanticValidationError):
        # Un errore con un codice proprio (agent_required, timezone_required)
        # vince su quelli generici: e' quello che la UI sa spiegare.
        tutti = exc.errors()
        primo = next((e for e in tutti if e.get("type") in _CODICI_VALIDAZIONE),
                     tutti[0] if tutti else {})
        code = _CODICI_VALIDAZIONE.get(primo.get("type"), errors.VALIDATION_ERROR)
        campo = ".".join(str(p) for p in primo.get("loc", ()) if p != "body")
        messaggio = primo.get("msg", "Dati non validi")
        return _risposta(422, code, f"{campo}: {messaggio}" if campo else messaggio)
    if isinstance(exc, PlatformAdminAgencyRequired):
        return _risposta(403, errors.PLATFORM_ADMIN_AGENCY_REQUIRED,
                         "Scegli un'agenzia per usare l'Agenda")
    extra = dict(getattr(exc, "extra", {}) or {})
    if isinstance(exc, errors.AppointmentConflict):
        extra["conflicts"] = exc.conflicts
        extra["alternatives"] = exc.alternatives
    code = getattr(exc, "code", None)
    if isinstance(exc, NotFoundError):
        return _risposta(404, errors.NOT_FOUND, "Risorsa non trovata")
    if isinstance(exc, PermissionDenied):
        return _risposta(403, code or errors.FORBIDDEN_ROLE, str(exc), **extra)
    if isinstance(exc, ConflictError):
        return _risposta(409, code or errors.INVALID_TRANSITION, str(exc), **extra)
    if isinstance(exc, ValidationError):
        return _risposta(422, code or errors.VALIDATION_ERROR, str(exc), **extra)
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
    if valore is None:
        return None
    try:
        esito = datetime.fromisoformat(valore.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _Richiesta(errors.VALIDATION_ERROR, f"{nome}: data e ora non valide") from exc
    if esito.tzinfo is None or esito.utcoffset() is None:
        raise _Richiesta(errors.TIMEZONE_REQUIRED, f"{nome}: l'orario deve indicare il fuso")
    return esito


def _elenco(valore):
    if valore is None or valore.strip() == "":
        return None
    return [v.strip() for v in valore.split(",") if v.strip()]


def _data(valore, nome):
    """Una data semplice `YYYY-MM-DD` (A30-11: eccezioni/chiusure sono per
    GIORNO, non per istante: niente fuso da richiedere qui)."""
    try:
        return date.fromisoformat(valore)
    except ValueError as exc:
        raise _Richiesta(errors.VALIDATION_ERROR, f"{nome}: data non valida (YYYY-MM-DD)") from exc


def _interi(valore, nome):
    voci = _elenco(valore)
    if voci is None:
        return None
    try:
        return [int(v) for v in voci]
    except ValueError as exc:
        raise _Richiesta(errors.VALIDATION_ERROR, f"{nome}: elenco di id non valido") from exc


# ---------------------------------------------------------------------------
# LETTURE
# ---------------------------------------------------------------------------

@router.get("/calendar")
def calendar(
    date_from: str = Query(..., alias="from"),
    date_to: str = Query(..., alias="to"),
    agents: str | None = None,
    types: str | None = None,
    statuses: str | None = None,
    show_colleagues: bool = True,
    ctx: OperatorContext = Depends(require_operator),
):
    try:
        args = dict(date_from=_istante(date_from, "from"), date_to=_istante(date_to, "to"),
                    agent_ids=_interi(agents, "agents"), types=_elenco(types),
                    statuses=_elenco(statuses), show_colleagues=show_colleagues)
    except _Richiesta as exc:
        return _errore(exc)
    return _x(service.calendar, ctx, **args)


@router.get("/agents")
def agents(ctx: OperatorContext = Depends(require_operator)):
    return _x(lambda: {"items": service.list_agents(ctx)})


@router.get("/availability")
def availability(
    user_id: int,
    date_from: str = Query(..., alias="from"),
    date_to: str = Query(..., alias="to"),
    duration: int = 60,
    step: int = 30,
    buffer_before_minutes: int = 0,
    buffer_after_minutes: int = 0,
    exclude_appointment_id: int | None = None,
    ctx: OperatorContext = Depends(require_operator),
):
    try:
        args = dict(assigned_user_id=user_id, date_from=_istante(date_from, "from"),
                    date_to=_istante(date_to, "to"), duration=duration, step=step,
                    buffer_before_minutes=buffer_before_minutes,
                    buffer_after_minutes=buffer_after_minutes,
                    exclude_appointment_id=exclude_appointment_id)
    except _Richiesta as exc:
        return _errore(exc)
    return _x(service.availability_slots, ctx, **args)


@router.post("/availability/check")
def availability_check(dati: dict = Body(...), ctx: OperatorContext = Depends(require_operator)):
    try:
        corpo = _corpo(AvailabilityCheckBody, dati)
    except (_Richiesta, PydanticValidationError) as exc:
        return _errore(exc)
    return _x(service.availability_check, ctx, corpo)


@router.get("")
def list_appointments(
    statuses: str | None = None,
    types: str | None = None,
    stima_id: int | None = None,
    lead_id: int | None = None,
    contact_id: int | None = None,
    property_id: int | None = None,
    date_from: str | None = Query(None, alias="from"),
    date_to: str | None = Query(None, alias="to"),
    limit: int = 50,
    offset: int = 0,
    ctx: OperatorContext = Depends(require_operator),
):
    try:
        args = dict(statuses=_elenco(statuses), types=_elenco(types), stima_id=stima_id,
                    lead_id=lead_id, contact_id=contact_id, property_id=property_id,
                    date_from=_istante(date_from, "from"), date_to=_istante(date_to, "to"),
                    limit=limit, offset=offset)
    except _Richiesta as exc:
        return _errore(exc)
    return _x(lambda: {"items": service.list_appointments(ctx, **args)})


# A30-5: la ricerca delle stime per il collegamento manuale (sola lettura).
# Dichiarata PRIMA di "/{appointment_id}": due segmenti, nessuna ambiguita',
# ma l'ordine resta quello leggibile.
@router.get("/lookups/stime")
def lookup_stime(
    search: str | None = None,
    lead_id: int | None = None,
    contact_id: int | None = None,
    limit: int = 10,
    ctx: OperatorContext = Depends(require_operator),
):
    return _x(lambda: {"items": service.lookup_stime(
        ctx, search=search, lead_id=lead_id, contact_id=contact_id, limit=limit)})


# ---------------------------------------------------------------------------
# A30-11 - ORARI DI LAVORO, ECCEZIONI, CHIUSURE AGENZIA. Dichiarate PRIMA di
# "/{appointment_id}" per lo stesso motivo di "/lookups/stime": i segmenti
# letterali ("agents", "closures") non devono essere consumati dalla rotta a
# un solo segmento dinamico.
# ---------------------------------------------------------------------------

@router.get("/agents/{user_id}/working-hours")
def get_working_hours(user_id: int, ctx: OperatorContext = Depends(require_operator)):
    return _x(lambda: {"items": working_hours_service.get_weekly_hours(ctx, user_id)})


@router.put("/agents/{user_id}/working-hours")
def put_working_hours(user_id: int, dati: dict = Body(...),
                      ctx: OperatorContext = Depends(require_operator)):
    try:
        corpo = _corpo(WorkingHoursReplaceBody, dati)
    except (_Richiesta, PydanticValidationError) as exc:
        return _errore(exc)
    return _x(lambda: {"items": working_hours_service.replace_weekly_hours(ctx, user_id, corpo)})


@router.get("/agents/{user_id}/availability-exceptions")
def get_availability_exceptions(
    user_id: int,
    date_from: str = Query(..., alias="from"),
    date_to: str = Query(..., alias="to"),
    ctx: OperatorContext = Depends(require_operator),
):
    try:
        args = dict(date_from=_data(date_from, "from"), date_to=_data(date_to, "to"))
    except _Richiesta as exc:
        return _errore(exc)
    return _x(lambda: {"items": working_hours_service.list_exceptions(ctx, user_id, **args)})


@router.post("/agents/{user_id}/availability-exceptions")
def post_availability_exception(user_id: int, dati: dict = Body(...),
                                ctx: OperatorContext = Depends(require_operator)):
    try:
        corpo = _corpo(AvailabilityExceptionCreate, dati)
    except (_Richiesta, PydanticValidationError) as exc:
        return _errore(exc)
    return _x(working_hours_service.create_exception, ctx, user_id, corpo, status=201)


@router.delete("/agents/{user_id}/availability-exceptions/{exception_id}")
def delete_availability_exception(user_id: int, exception_id: int,
                                  ctx: OperatorContext = Depends(require_operator)):
    def _elimina():
        working_hours_service.delete_exception(ctx, user_id, exception_id)
        return {"deleted": True}
    return _x(_elimina)


@router.get("/closures")
def get_agency_closures(
    date_from: str = Query(..., alias="from"),
    date_to: str = Query(..., alias="to"),
    ctx: OperatorContext = Depends(require_operator),
):
    try:
        args = dict(date_from=_data(date_from, "from"), date_to=_data(date_to, "to"))
    except _Richiesta as exc:
        return _errore(exc)
    return _x(lambda: {"items": working_hours_service.list_closures(ctx, **args)})


@router.post("/closures")
def post_agency_closure(dati: dict = Body(...), ctx: OperatorContext = Depends(require_operator)):
    try:
        corpo = _corpo(AgencyClosureCreate, dati)
    except (_Richiesta, PydanticValidationError) as exc:
        return _errore(exc)
    return _x(working_hours_service.create_closure, ctx, corpo, status=201)


@router.delete("/closures/{closure_id}")
def delete_agency_closure(closure_id: int, ctx: OperatorContext = Depends(require_operator)):
    def _elimina():
        working_hours_service.delete_closure(ctx, closure_id)
        return {"deleted": True}
    return _x(_elimina)


# ---------------------------------------------------------------------------
# A30-12 - GESTIONE OPERATORE DEI LINK DI BOOKING PUBBLICO. Dichiarate PRIMA
# di "/{appointment_id}" per lo stesso motivo delle rotte A30-11 sopra: il
# segmento letterale "booking-links" non deve essere consumato dalla rotta a
# un solo segmento dinamico.
# ---------------------------------------------------------------------------

@router.get("/booking-links")
def list_booking_links(ctx: OperatorContext = Depends(require_operator)):
    return _x(lambda: {"items": booking_link_service.list_links(ctx)})


@router.post("/booking-links")
def create_booking_link(dati: dict = Body(...), ctx: OperatorContext = Depends(require_operator)):
    try:
        corpo = _corpo(BookingLinkCreateBody, dati)
    except (_Richiesta, PydanticValidationError) as exc:
        return _errore(exc)
    return _x(booking_link_service.create_link, ctx, corpo, status=201)


@router.patch("/booking-links/{link_id}")
def patch_booking_link(link_id: int, dati: dict = Body(...),
                       ctx: OperatorContext = Depends(require_operator)):
    try:
        corpo = _corpo(BookingLinkPatchBody, dati)
    except (_Richiesta, PydanticValidationError) as exc:
        return _errore(exc)
    return _x(booking_link_service.patch_link, ctx, link_id, corpo)


@router.post("/booking-links/{link_id}/rotate")
def rotate_booking_link(link_id: int, ctx: OperatorContext = Depends(require_operator)):
    return _x(booking_link_service.rotate_link, ctx, link_id)


@router.post("/booking-links/{link_id}/disable")
def disable_booking_link(link_id: int, ctx: OperatorContext = Depends(require_operator)):
    return _x(booking_link_service.disable_link, ctx, link_id)


@router.get("/{appointment_id}")
def get_appointment(appointment_id: int, ctx: OperatorContext = Depends(require_operator)):
    return _x(service.get_appointment_detail, ctx, appointment_id)


@router.get("/{appointment_id}/events")
def get_events(appointment_id: int, ctx: OperatorContext = Depends(require_operator)):
    return _x(lambda: {"items": service.list_events(ctx, appointment_id)})


# ---------------------------------------------------------------------------
# SCRITTURE
# ---------------------------------------------------------------------------

@router.post("")
def create_appointment(dati: dict = Body(...), ctx: OperatorContext = Depends(require_operator)):
    """201 alla prima creazione; 200 + `Idempotent-Replay: true` alla replica
    della stessa richiesta (A30-2 §6)."""
    try:
        corpo = _corpo(AppointmentCreateBody, dati)
        riga, replica = service.create_appointment_idempotent(ctx, corpo)
    except Exception as exc:  # noqa: BLE001
        return _errore(exc)
    risposta = JSONResponse(status_code=200 if replica else 201,
                            content=jsonable_encoder(riga))
    if replica:
        risposta.headers["Idempotent-Replay"] = "true"
    return risposta


def _azione(funzione, modello, appointment_id, dati, ctx):
    try:
        corpo = _corpo(modello, dati)
    except (_Richiesta, PydanticValidationError) as exc:
        return _errore(exc)
    return _x(funzione, ctx, appointment_id, corpo)


@router.patch("/{appointment_id}")
def patch_appointment(appointment_id: int, dati: dict = Body(...),
                      ctx: OperatorContext = Depends(require_operator)):
    return _azione(service.patch_appointment, PatchBody, appointment_id, dati, ctx)


@router.post("/{appointment_id}/schedule")
def schedule_appointment(appointment_id: int, dati: dict = Body(...),
                         ctx: OperatorContext = Depends(require_operator)):
    return _azione(service.schedule_appointment, ScheduleBody, appointment_id, dati, ctx)


@router.post("/{appointment_id}/confirm")
def confirm_appointment(appointment_id: int, dati: dict = Body(...),
                        ctx: OperatorContext = Depends(require_operator)):
    return _azione(service.confirm_appointment, ConfirmBody, appointment_id, dati, ctx)


@router.post("/{appointment_id}/reschedule")
def reschedule_appointment(appointment_id: int, dati: dict = Body(...),
                           ctx: OperatorContext = Depends(require_operator)):
    try:
        corpo = _corpo(RescheduleBody, dati)
    except (_Richiesta, PydanticValidationError) as exc:
        return _errore(exc)
    return _x(service.reschedule_appointment, ctx, appointment_id, corpo, status=201)


@router.post("/{appointment_id}/reassign")
def reassign_appointment(appointment_id: int, dati: dict = Body(...),
                         ctx: OperatorContext = Depends(require_operator)):
    return _azione(service.reassign_appointment, ReassignBody, appointment_id, dati, ctx)


@router.post("/{appointment_id}/cancel")
def cancel_appointment(appointment_id: int, dati: dict = Body(...),
                       ctx: OperatorContext = Depends(require_operator)):
    return _azione(service.cancel_appointment, CancelBody, appointment_id, dati, ctx)


@router.post("/{appointment_id}/complete")
def complete_appointment(appointment_id: int, dati: dict = Body(...),
                         ctx: OperatorContext = Depends(require_operator)):
    return _azione(service.complete_appointment, CompleteBody, appointment_id, dati, ctx)


@router.post("/{appointment_id}/no-show")
def no_show_appointment(appointment_id: int, dati: dict = Body(...),
                        ctx: OperatorContext = Depends(require_operator)):
    return _azione(service.no_show_appointment, NoShowBody, appointment_id, dati, ctx)
