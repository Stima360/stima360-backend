"""BLOCCO-5A - prenotazione online delle visite di UN immobile con incarico.

DUE SUPERFICI

* Operatore (`/api/visit-booking-links`, sessione): crea, elenca e disattiva
  i collegamenti di un immobile. Serve chi puo' gestire l'immobile
  (`property.lifecycle.may_manage`); un agente crea link solo per la propria
  agenda. L'immobile deve avere un incarico reale IN CORSO; l'agente deve
  avere un orario pubblico configurato; l'autorizzazione del proprietario e'
  obbligatoria ed esplicita (`preauthorized` / `per_visit`), con autore e data.
  Il token si mostra UNA volta: si conserva solo il suo SHA-256.
* Pubblico (`/api/public/visit-booking`, nessuna sessione): dati minimi del
  link (agenzia, tipologia e comune dell'immobile, durata, se serve l'assenso
  del proprietario), orari prenotabili, prenotazione, e con il collegamento di
  gestione stato, annullamento e riprogrammazione. Ogni rifiuto del link e'
  la stessa risposta neutra. Limiti di frequenza per link e per IP (hash).

LE SCRITTURE DELL'AGENDA sono in `appointments.property_visit_writes`: lock
dell'agente, ricontrollo sotto lock dell'orario prenotabile, conflitti,
EXCLUDE, proiezione sull'immobile. Una visita con `per_visit` nasce
`requested` e la pagina dice che NON e' confermata.

CHIAVE DI INVIO E GESTIONE. Il browser genera una chiave casuale (UUID v4) per
ogni prenotazione. Il server ne conserva solo due impronte: una per
l'idempotenza (lo stesso invio ripetuto ritrova la stessa prenotazione) e una
come token di gestione: il collegamento di gestione e' la chiave stessa, che
il browser possiede gia', anche dopo un errore di rete.

Orari: istanti con fuso; la pagina mostra Europe/Rome. Il cambio d'ora e'
gestito dalle finestre dell'Agenda (`working_hours`), calcolate in ora locale.
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone

from appointments import errors as appointment_errors
from appointments import property_visit_writes as writes
from appointments import repository as appointment_repository
from core.database import core_cursor
from core.exceptions import ValidationError
from core.normalization import normalize_email, normalize_phone
from operator_auth.context import SystemAgencyContext
from operator_auth.permissions import sees_all_agency_records
from property.catalog import PROPERTY_TYPE_LABELS
from property.lifecycle import may_manage
from public_booking import security

from . import repository

PAGE_PATH = "/visita/"
MANAGE_PATH = "/visita-gestione/"
RATE_WINDOW_SECONDS = 60
DEFAULT_LIMITS = {"get_link": 120, "get_ip": 60, "post_link": 20, "post_ip": 6}
MAX_SLOT_WINDOW = timedelta(days=7)
OWNER_AUTHORIZATIONS = ("preauthorized", "per_visit")
STATUS_PUBLIC = {
    "scheduled": ("confirmed", "Visita confermata."),
    "confirmed": ("confirmed", "Visita confermata."),
    "requested": ("pending_owner", "Richiesta registrata: la visita NON è ancora confermata. "
                                   "Serve l'assenso del proprietario; riceverà conferma dall'agenzia."),
    "cancelled": ("cancelled", "Visita annullata."),
    "completed": ("completed", "Visita svolta."),
    "no_show": ("closed", "Visita chiusa."),
    "rescheduled": ("closed", "Visita sostituita da un nuovo orario."),
}
NOT_BOOKABLE_MESSAGE = "Questo collegamento non è valido o non è più attivo."


class VisitBookingError(Exception):
    status = 400

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class NotFound(VisitBookingError):
    status = 404


class Forbidden(VisitBookingError):
    status = 403


class Unavailable(VisitBookingError):
    status = 503


class LinkNotBookable(VisitBookingError):
    status = 404

    def __init__(self):
        super().__init__("NOT_BOOKABLE", NOT_BOOKABLE_MESSAGE)


class RateLimited(VisitBookingError):
    status = 429

    def __init__(self):
        super().__init__("RATE_LIMITED", "Troppe richieste: riprova fra poco.")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _require_installed(cur):
    if not repository.installed(cur):
        raise Unavailable("NOT_INSTALLED", "La prenotazione delle visite non è installata (migrazione 120).")


# ---------------------------------------------------------------------------
# operatore
# ---------------------------------------------------------------------------

def _managed_property(cur, ctx, property_id: int) -> dict:
    agency_id = ctx.require_agency()
    cur.execute("SELECT id, agency_id, assigned_agent_id, (to_jsonb(p)->>'deleted_at') AS deleted_at "
                "FROM properties p WHERE id = %s AND agency_id = %s", (property_id, agency_id))
    prop = cur.fetchone()
    if prop is None or prop["deleted_at"] is not None:
        raise NotFound("PROPERTY_NOT_FOUND", "Immobile non trovato.")
    if not may_manage(ctx, dict(prop)):
        raise Forbidden("NOT_ALLOWED", "Non puoi gestire le visite di questo immobile.")
    return dict(prop)


def _link_view(link: dict, token: str | None = None) -> dict:
    out = {k: _iso(v) for k, v in link.items()}
    out["owner_authorization_label"] = ("Autorizzate dal proprietario negli orari dell'agente"
                                        if link["owner_authorization"] == "preauthorized"
                                        else "Ogni visita richiede l'assenso del proprietario")
    if token is not None:
        out["url"] = PAGE_PATH + token          # mostrato una sola volta
    return out


def create_link(ctx, data: dict) -> dict:
    agency_id = ctx.require_agency()
    if ctx.user_id is None:
        raise Forbidden("NOT_ALLOWED", "Serve una sessione operatore.")
    if data["owner_authorization"] not in OWNER_AUTHORIZATIONS:
        raise ValidationError("Autorizzazione del proprietario obbligatoria: 'preauthorized' o 'per_visit'.")
    agent = int(data["assigned_user_id"])
    if not sees_all_agency_records(ctx.role, ctx.is_platform_admin) and agent != ctx.user_id:
        raise Forbidden("NOT_ALLOWED", "Un agente crea collegamenti solo per la propria agenda.")
    token = security.generate_token()
    with core_cursor(commit=True) as (_, cur):
        _require_installed(cur)
        prop = _managed_property(cur, ctx, data["property_id"])
        if not writes.mandate_open(cur, agency_id, prop["id"]):
            raise VisitBookingError("MANDATE_REQUIRED", "L'immobile non ha un incarico in corso: "
                                    "le visite si prenotano online solo su un incarico attivo.")
        if not appointment_repository.active_membership(cur, agency_id, agent):
            raise VisitBookingError("AGENT_NOT_ACTIVE", "L'agente indicato non è un membro attivo dell'agenzia.")
        from appointments import working_hours_repository
        if not working_hours_repository.has_weekly_configuration(cur, agency_id, agent):
            raise VisitBookingError("NO_WORKING_HOURS", "L'agente non ha un orario settimanale configurato: "
                                    "senza orario non esistono orari prenotabili.")
        link = repository.insert_link(cur, {
            "agency_id": agency_id, "property_id": prop["id"], "assigned_user_id": agent,
            "token_hash": security.hash_token(token), "duration_minutes": data["duration_minutes"],
            "step_minutes": data["step_minutes"], "travel_minutes": data["travel_minutes"],
            "min_notice_minutes": data["min_notice_minutes"], "horizon_days": data["horizon_days"],
            "owner_authorization": data["owner_authorization"],
            "owner_authorization_note": (data.get("owner_authorization_note") or "").strip() or None,
            "owner_authorization_recorded_by": ctx.user_id, "created_by_user_id": ctx.user_id,
            "expires_at": data.get("expires_at"),
        })
    return _link_view(link, token)


def eligible_agents(ctx, property_id: int) -> dict:
    """Gli agenti a cui `create_link` assegnerebbe davvero un collegamento su
    questo immobile: membri attivi con orario settimanale configurato (un
    agente non titolare vede solo se stesso). Stessi controlli di `create_link`."""
    agency_id = ctx.require_agency()
    from appointments import working_hours_repository
    with core_cursor() as (_, cur):
        _require_installed(cur)
        prop = _managed_property(cur, ctx, property_id)
        agents = appointment_repository.agents(cur, agency_id)
        if not sees_all_agency_records(ctx.role, ctx.is_platform_admin):
            agents = [a for a in agents if a["id"] == ctx.user_id]
        items = [{"id": a["id"], "name": a["name"], "is_me": a["id"] == ctx.user_id} for a in agents
                 if working_hours_repository.has_weekly_configuration(cur, agency_id, a["id"])]
    return {"property_id": prop["id"], "items": items}


def list_links(ctx, property_id: int) -> dict:
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        _require_installed(cur)
        prop = _managed_property(cur, ctx, property_id)
        links = repository.links_for_property(cur, agency_id, prop["id"])
    return {"items": [_link_view(link) for link in links]}


def disable_link(ctx, link_id: int) -> dict:
    agency_id = ctx.require_agency()
    with core_cursor(commit=True) as (_, cur):
        _require_installed(cur)
        link = repository.link_by_id(cur, agency_id, link_id, lock=True)
        if link is None:
            raise NotFound("LINK_NOT_FOUND", "Collegamento non trovato.")
        _managed_property(cur, ctx, link["property_id"])
        done = repository.disable_link(cur, agency_id, link_id, ctx.user_id) or link
    return _link_view(done)


# ---------------------------------------------------------------------------
# pubblico
# ---------------------------------------------------------------------------

def _limit(scope: str) -> int:
    try:
        return int(os.environ.get(f"VISIT_BOOKING_RATE_LIMIT_{scope.upper()}", DEFAULT_LIMITS[scope]))
    except ValueError:
        return DEFAULT_LIMITS[scope]


def _gate(link_id: int, kind: str, client_ip: str) -> None:
    ip_hash = security.hash_ip(client_ip)
    epoch = int(_now().timestamp())
    window = datetime.fromtimestamp(epoch - epoch % RATE_WINDOW_SECONDS, tz=timezone.utc)
    with core_cursor(commit=True) as (_, cur):
        for scope, key in ((f"{kind}_link", ""), (f"{kind}_ip", ip_hash)):
            if not repository.increment_and_check(cur, link_id=link_id, scope=scope, window_start=window,
                                                  limit=_limit(scope), client_ip_hash=key):
                raise RateLimited()


def _resolve(cur, token: str) -> dict:
    """Il link prenotabile, o la stessa risposta neutra per ogni rifiuto."""
    if not repository.installed(cur):
        raise LinkNotBookable()
    link = repository.link_by_token_hash(cur, security.hash_token(token or ""))
    if (link is None or link["status"] != "active"
            or (link["expires_at"] is not None and link["expires_at"] <= _now())
            or not writes.mandate_open(cur, link["agency_id"], link["property_id"])):
        raise LinkNotBookable()
    return link


def _public_property(cur, link) -> dict:
    p = repository.public_property(cur, link["agency_id"], link["property_id"]) or {}
    return {"type_label": PROPERTY_TYPE_LABELS.get(p.get("property_type") or "", "Immobile"), "city": p.get("city")}


def public_info(token: str, *, client_ip: str) -> dict:
    with core_cursor() as (_, cur):
        link = _resolve(cur, token)
    _gate(link["id"], "get", client_ip)
    with core_cursor() as (_, cur):
        agency = repository.agency_name(cur, link["agency_id"])
        prop = _public_property(cur, link)
    now = _now()
    return {"agency_name": agency, "property": prop, "duration_minutes": link["duration_minutes"],
            "owner_approval_required": link["owner_authorization"] == "per_visit",
            "timezone": "Europe/Rome",
            "bookable_from": _iso(now + timedelta(minutes=link["min_notice_minutes"])),
            "bookable_until": _iso(now + timedelta(days=link["horizon_days"]))}


def public_slots(token: str, *, date_from: datetime, date_to: datetime, client_ip: str) -> dict:
    if date_to <= date_from or date_to - date_from > MAX_SLOT_WINDOW:
        raise ValidationError("La finestra va da un istante a uno successivo, al massimo 7 giorni.")
    with core_cursor() as (_, cur):
        link = _resolve(cur, token)
    _gate(link["id"], "get", client_ip)
    with core_cursor() as (_, cur):
        slots = writes.bookable_slots(cur, link, date_from, date_to, now=_now())
    return {"timezone": "Europe/Rome",
            "slots": [{"start_at": _iso(s["start_at"]), "end_at": _iso(s["end_at"])} for s in slots]}


def _keys(link_id: int, raw_key: str) -> tuple[str, str]:
    submit = hashlib.sha256(f"submit:{link_id}:{raw_key}".encode()).hexdigest()
    manage = hashlib.sha256(f"manage:{raw_key}".encode()).hexdigest()
    return submit, manage


def _booking_view(cur, booking: dict, *, raw_key: str | None) -> dict:
    appt = repository.appointment_brief(cur, booking["agency_id"], booking["appointment_id"])
    state, message = STATUS_PUBLIC.get(appt["status"], ("closed", "Visita chiusa."))
    if booking.get("replaced_by_id") is not None:
        state, message = "replaced", "Questa prenotazione è stata spostata a un nuovo orario."
    active = appt["status"] in ("requested", "scheduled", "confirmed") and booking.get("replaced_by_id") is None
    future = appt["start_at"] > _now()
    out = {"state": state, "message": message, "start_at": _iso(appt["start_at"]), "end_at": _iso(appt["end_at"]),
           "timezone": "Europe/Rome", "can_cancel": active and future, "can_reschedule": active and future}
    if raw_key is not None:
        out["manage_url"] = MANAGE_PATH + raw_key
    return out


def _contact(name: str, phone: str | None, email: str | None) -> dict:
    name = (name or "").strip()
    phone_n, email_n = normalize_phone(phone), normalize_email(email)
    if not name:
        raise ValidationError("Il nome è obbligatorio.")
    if not phone_n and not email_n:
        raise ValidationError("Serve almeno un telefono o un'email validi.")
    return {"contact_type": "person", "first_name": name, "last_name": None, "company_name": None,
            "display_name": name, "email": email, "email_normalized": email_n, "phone": phone,
            "phone_normalized": phone_n, "secondary_phone": None, "source": "booking_link", "status": "active",
            "notes": None}


def _translate(exc: Exception):
    if isinstance(exc, appointment_errors.PublicSlotUnavailable):
        return VisitBookingError("SLOT_UNAVAILABLE", str(exc))
    if isinstance(exc, appointment_errors.AppointmentConflict):
        return VisitBookingError("SLOT_UNAVAILABLE", "Questo orario non è più disponibile: scegline un altro")
    return None


def book(token: str, *, name: str, phone: str | None, email: str | None, start_at: datetime, key: str,
         client_ip: str) -> dict:
    with core_cursor() as (_, cur):
        link = _resolve(cur, token)
    _gate(link["id"], "post", client_ip)
    submit, manage = _keys(link["id"], key)
    with core_cursor() as (_, cur):
        existing = repository.booking_by_submission(cur, link["id"], submit)
        if existing is not None:
            return _booking_view(cur, existing, raw_key=key)
    contact = _contact(name, phone, email)
    ctx = SystemAgencyContext(agency_id=link["agency_id"], origin="public_booking")

    def legame(cur, riga):
        repository.insert_booking(cur, {"agency_id": link["agency_id"], "link_id": link["id"],
                                        "appointment_id": riga["id"], "submission_key": submit,
                                        "manage_token_hash": manage})
    try:
        writes.create_visit(ctx, link=link, start_at=start_at, contact_data=contact,
                            source_record_id=f"visit:{submit}", after_insert=legame)
    except Exception as exc:
        tradotta = _translate(exc)
        if tradotta is not None:
            raise tradotta from exc
        raise
    with core_cursor() as (_, cur):
        booking = repository.booking_by_submission(cur, link["id"], submit)
        return _booking_view(cur, booking, raw_key=key)


def _managed_booking(cur, raw_key: str) -> dict:
    if not repository.installed(cur):
        raise LinkNotBookable()
    booking = repository.booking_by_manage_hash(cur, _keys(0, raw_key or "")[1])
    if booking is None:
        raise LinkNotBookable()
    return booking


def manage_state(raw_key: str) -> dict:
    with core_cursor() as (_, cur):
        booking = _managed_booking(cur, raw_key)
        return _booking_view(cur, booking, raw_key=None)


def manage_cancel(raw_key: str) -> dict:
    with core_cursor() as (_, cur):
        booking = _managed_booking(cur, raw_key)
        view = _booking_view(cur, booking, raw_key=None)
    if not view["can_cancel"]:
        raise VisitBookingError("NOT_CANCELLABLE", "Questa visita non si può più annullare da qui.")
    ctx = SystemAgencyContext(agency_id=booking["agency_id"], origin="public_booking")
    writes.cancel_visit(ctx, appointment_id=booking["appointment_id"], reason="Annullata dal cliente")
    return manage_state(raw_key)


def manage_slots(raw_key: str, *, date_from: datetime, date_to: datetime) -> dict:
    if date_to <= date_from or date_to - date_from > MAX_SLOT_WINDOW:
        raise ValidationError("La finestra va da un istante a uno successivo, al massimo 7 giorni.")
    with core_cursor() as (_, cur):
        booking = _managed_booking(cur, raw_key)
        link = repository.link_by_id(cur, booking["agency_id"], booking["link_id"])
        if link["status"] != "active":
            return {"timezone": "Europe/Rome", "slots": []}
        slots = writes.bookable_slots(cur, link, date_from, date_to, now=_now(),
                                      exclude_appointment_id=booking["appointment_id"])
    return {"timezone": "Europe/Rome",
            "slots": [{"start_at": _iso(s["start_at"]), "end_at": _iso(s["end_at"])} for s in slots]}


def manage_reschedule(raw_key: str, *, start_at: datetime, new_key: str) -> dict:
    with core_cursor() as (_, cur):
        booking = _managed_booking(cur, raw_key)
        ripetuto = repository.booking_by_submission(cur, booking["link_id"], _keys(booking["link_id"], new_key)[0])
        if ripetuto is not None:                       # stesso invio ripetuto: stessa nuova prenotazione
            return _booking_view(cur, ripetuto, raw_key=new_key)
        view = _booking_view(cur, booking, raw_key=None)
        link = repository.link_by_id(cur, booking["agency_id"], booking["link_id"])
    if not view["can_reschedule"] or link["status"] != "active":
        raise VisitBookingError("NOT_RESCHEDULABLE", "Questa visita non si può più riprogrammare da qui.")
    submit, manage = _keys(link["id"], new_key)
    ctx = SystemAgencyContext(agency_id=link["agency_id"], origin="public_booking")

    def legame(cur, riga):
        nuovo = repository.insert_booking(cur, {"agency_id": link["agency_id"], "link_id": link["id"],
                                                "appointment_id": riga["id"], "submission_key": submit,
                                                "manage_token_hash": manage})
        if nuovo is not None:
            repository.mark_replaced(cur, booking["id"], nuovo["id"])
    try:
        writes.create_visit(ctx, link=link, start_at=start_at, contact_data={}, source_record_id=f"visit:{submit}",
                            replaces_appointment_id=booking["appointment_id"], after_insert=legame)
    except Exception as exc:
        tradotta = _translate(exc)
        if tradotta is not None:
            raise tradotta from exc
        raise
    with core_cursor() as (_, cur):
        nuovo = repository.booking_by_submission(cur, link["id"], submit)
        return _booking_view(cur, nuovo, raw_key=new_key)


def booking_link_for_visit(cur, agency_id: int, property_id: int | None) -> dict | None:
    """BLOCCO-5B: il collegamento da proporre a chi chiede una visita, SOLO se
    l'immobile e' identificato e la prenotazione e' davvero possibile adesso:
    migrazione presente, link attivo e non scaduto, agente ancora attivo,
    incarico reale in corso. Altrimenti None (la richiesta va all'agente)."""
    if property_id is None or not repository.installed(cur):
        return None
    link = repository.active_link_for_property(cur, agency_id, property_id)
    if link is None or not appointment_repository.active_membership(cur, agency_id, link["assigned_user_id"]):
        return None
    if not writes.mandate_open(cur, agency_id, property_id):
        return None
    return link
