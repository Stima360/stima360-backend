"""A30-11 - il servizio di orari/eccezioni/chiusure: scope, attore,
permessi (D6). Nessun lock d'agente, nessun EXCLUDE applicativo: la
garanzia contro le sovrapposizioni e' TUTTA nel database (076), come per
`appointments` (072) - qui non serve il livello 2/3 perche' non c'e' un
motivo di concorrenza analogo (due agenti non litigano per lo stesso slot
di CONFIGURAZIONE come litigano per lo stesso slot di AGENDA).

PERMESSI (D6): owner/admin (`ctx.may_assign_records()`) gestiscono gli
orari/le eccezioni di QUALUNQUE agente della propria agenzia; un `agent`
gestisce SOLO i propri. Le chiusure agenzia sono sempre owner/admin (nessun
concetto di "chiusura di se stesso" per un agente). Nessun canale speciale
per un platform admin: vale il normale `ctx` (agente/agenzia in atto), come
il resto del CRM.

TENANT: cross-agenzia e' indistinguibile da non trovato/forbidden, stesso
principio del resto del dominio Agenda (`_controlla_stima`, in
`appointments/service.py`): un chiamante non impara che l'altra agenzia
esiste.
"""
from __future__ import annotations

from core.database import core_cursor
from core.exceptions import NotFoundError, ValidationError

from . import errors, repository as appt_repository, working_hours_repository as repository


def _attore(ctx) -> int:
    user_id = getattr(ctx, "user_id", None)
    if user_id is None:
        raise errors.SessionRequired(
            "La gestione degli orari richiede una sessione operatore")
    return int(user_id)


def _puo_gestire_tutti(ctx) -> bool:
    return bool(getattr(ctx, "may_assign_records", False))


def _controlla_target(ctx, cur, agency_id, target_user_id):
    """Chi puo' scrivere l'orario/le eccezioni di CHI (D6)."""
    if not _puo_gestire_tutti(ctx) and int(target_user_id) != _attore(ctx):
        raise errors.ForbiddenRole(
            "Un agente puo' gestire solo i propri orari di lavoro")
    if not appt_repository.active_membership(cur, agency_id, target_user_id):
        raise errors.AgentNotActive(
            "L'agente indicato non e' un membro attivo di questa agenzia")


def _valida_finestra_date(date_from, date_to):
    if date_to < date_from:
        raise ValidationError("La finestra deve terminare dopo il suo inizio")


# ---------------------------------------------------------------------------
# orario settimanale
# ---------------------------------------------------------------------------

def get_weekly_hours(ctx, user_id: int) -> list[dict]:
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        _controlla_target(ctx, cur, agency_id, user_id)
        return repository.weekly_hours(cur, agency_id, user_id)


def replace_weekly_hours(ctx, user_id: int, body) -> list[dict]:
    agency_id = ctx.require_agency()
    righe = [{"day_of_week": s.day_of_week, "start_minute": s.start_minute,
              "end_minute": s.end_minute} for s in body.slots]
    with core_cursor(commit=True) as (_, cur):
        _controlla_target(ctx, cur, agency_id, user_id)
        try:
            return repository.replace_weekly_hours(cur, agency_id, user_id, righe)
        except Exception as exc:  # noqa: BLE001 - tradotta sotto
            traduzione = _traduci_overlap(exc)
            if traduzione is not None:
                raise traduzione from exc
            raise


# ---------------------------------------------------------------------------
# eccezioni per agente (ferie, assenze, aperture straordinarie)
# ---------------------------------------------------------------------------

def list_exceptions(ctx, user_id: int, *, date_from, date_to) -> list[dict]:
    agency_id = ctx.require_agency()
    _valida_finestra_date(date_from, date_to)
    with core_cursor() as (_, cur):
        _controlla_target(ctx, cur, agency_id, user_id)
        return repository.exceptions_in_range(cur, agency_id, user_id, date_from, date_to)


def create_exception(ctx, user_id: int, body) -> dict:
    agency_id = ctx.require_agency()
    with core_cursor(commit=True) as (_, cur):
        _controlla_target(ctx, cur, agency_id, user_id)
        try:
            return repository.create_exception(
                cur, agency_id, user_id, exception_date=body.exception_date,
                start_minute=body.start_minute, end_minute=body.end_minute,
                is_available=body.is_available, reason_code=body.reason_code)
        except Exception as exc:  # noqa: BLE001
            traduzione = _traduci_overlap(exc)
            if traduzione is not None:
                raise traduzione from exc
            raise


def delete_exception(ctx, user_id: int, exception_id: int) -> None:
    agency_id = ctx.require_agency()
    with core_cursor(commit=True) as (_, cur):
        _controlla_target(ctx, cur, agency_id, user_id)
        if not repository.delete_exception(cur, agency_id, user_id, exception_id):
            raise NotFoundError("Eccezione non trovata")


# ---------------------------------------------------------------------------
# chiusure agenzia (D5) - sempre owner/admin, mai un agente su se stesso
# ---------------------------------------------------------------------------

def _richiedi_gestione_agenzia(ctx):
    if not _puo_gestire_tutti(ctx):
        raise errors.ForbiddenRole(
            "Solo owner/admin possono gestire le chiusure dell'agenzia")


def list_closures(ctx, *, date_from, date_to) -> list[dict]:
    agency_id = ctx.require_agency()
    _valida_finestra_date(date_from, date_to)
    with core_cursor() as (_, cur):
        return repository.closures_in_range(cur, agency_id, date_from, date_to)


def create_closure(ctx, body) -> dict:
    agency_id = ctx.require_agency()
    _richiedi_gestione_agenzia(ctx)
    with core_cursor(commit=True) as (_, cur):
        try:
            return repository.create_closure(
                cur, agency_id, closure_date=body.closure_date,
                start_minute=body.start_minute, end_minute=body.end_minute,
                reason_code=body.reason_code)
        except Exception as exc:  # noqa: BLE001
            traduzione = _traduci_overlap(exc)
            if traduzione is not None:
                raise traduzione from exc
            raise


def delete_closure(ctx, closure_id: int) -> None:
    agency_id = ctx.require_agency()
    _richiedi_gestione_agenzia(ctx)
    with core_cursor(commit=True) as (_, cur):
        if not repository.delete_closure(cur, agency_id, closure_id):
            raise NotFoundError("Chiusura non trovata")


# ---------------------------------------------------------------------------
# traduzione dell'EXCLUDE del database in un errore leggibile (stesso
# principio di `appointments.service._traduci_esclusione`)
# ---------------------------------------------------------------------------

try:
    from psycopg2.errors import ExclusionViolation as _EXCLUSION_VIOLATION
except ImportError:  # pragma: no cover - stub di test senza psycopg2 reale
    _EXCLUSION_VIOLATION = None


def _traduci_overlap(exc):
    if _EXCLUSION_VIOLATION is not None and isinstance(exc, _EXCLUSION_VIOLATION):
        return ValidationError(
            "La fascia si sovrappone a un'altra gia' presente per lo stesso giorno/data")
    return None
