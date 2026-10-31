"""REPORT-PROPRIETARIO-1 - il report attivita' di un immobile per il suo proprietario.

Riusa i dati gia' nel CRM, in sola lettura: immobile, incarico
(`core.property_mandate`), contatti interessati (`property_leads`, data del
collegamento), visite (`appointments` buyer_visit completati, piu' le visite
storiche non doppie), visite gia' fissate nei prossimi giorni, proposte di
acquisto (situazione attuale: nessuna data di invio registrata), storico del
prezzo richiesto e degli stati. Le «richieste ricevute» non hanno ancora una
fonte attendibile: il report le dichiara non disponibili, mai zero. Non scrive nulla e non salva
il PDF: ogni download lo rigenera dai dati di quel momento, e il documento
dice quando e' stato generato.

Nel report non entrano MAI dati interni: note interne, prezzo minimo,
interazioni, feedback dei visitatori, nomi o recapiti degli acquirenti,
importi delle proposte. Le «indicazioni dell'agente» sono il testo che il
consulente scrive per il proprietario al momento di generare il PDF.

Permessi: chi puo' gestire l'immobile (`property.lifecycle.may_manage`):
titolare e amministratori su tutta l'agenzia, l'agente sui suoi immobili.
"""
from __future__ import annotations

import hashlib
from collections import OrderedDict
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from core.database import core_cursor
from core.property_mandate import as_date, is_real_mandate, mandate_state, today_rome
from property.lifecycle import may_manage

from . import repository
from .enums import (
    COMMERCIAL_STATUS_LABELS,
    DEFAULT_PERIOD_DAYS,
    DEFINITIONS,
    LOGO_UNREADABLE_MESSAGE,
    MANDATE_STATE_LABELS,
    MAX_PERIOD_DAYS,
    NOT_ALLOWED_MESSAGE,
    PERIOD_FUTURE_MESSAGE,
    PERIOD_INVALID_MESSAGE,
    PERIOD_TOO_LONG_MESSAGE,
    PROPERTY_NOT_FOUND_MESSAGE,
    PROPOSAL_STATUS_LABELS,
    UPCOMING_DAYS,
)

ROME = ZoneInfo("Europe/Rome")
MONTHS = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre",
          "ottobre", "novembre", "dicembre")


class ReportError(Exception):
    status = 400

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class NotFound(ReportError):
    status = 404


class Forbidden(ReportError):
    status = 403


class InvalidPeriod(ReportError):
    status = 422


class ReportPdfError(ReportError):
    status = 500


def _money(value):
    return None if value is None else str(value)


def _day(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        value = value.astimezone(ROME) if value.tzinfo else value
        return value.date().isoformat()
    return as_date(value).isoformat()


def _rome_midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=ROME)


def _month_key(value) -> str:
    local = value.astimezone(ROME) if getattr(value, "tzinfo", None) else value
    return f"{local.year:04d}-{local.month:02d}"


def _months(date_from: date, date_to: date) -> "OrderedDict[str, dict]":
    out: OrderedDict[str, dict] = OrderedDict()
    year, month = date_from.year, date_from.month
    while (year, month) <= (date_to.year, date_to.month):
        out[f"{year:04d}-{month:02d}"] = {"month": f"{year:04d}-{month:02d}", "label": f"{MONTHS[month - 1]} {year}",
                                          "interested_contacts": 0, "visits": 0}
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def resolve_period(prop: dict, date_from: date | None, date_to: date | None) -> tuple[date, date]:
    """Il periodo del report. Predefinito: dall'inizio dell'incarico (se reale)
    a oggi, altrimenti gli ultimi 90 giorni."""
    today = today_rome()
    end = date_to or today
    if end > today:
        raise InvalidPeriod("PERIOD_INVALID", PERIOD_FUTURE_MESSAGE)
    if date_from is None:
        start_mandate = as_date(prop.get("mandate_start")) if is_real_mandate(prop) else None
        start = start_mandate if start_mandate and start_mandate <= end else end - timedelta(days=DEFAULT_PERIOD_DAYS)
    else:
        start = date_from
    if start > end:
        raise InvalidPeriod("PERIOD_INVALID", PERIOD_INVALID_MESSAGE)
    if (end - start).days > MAX_PERIOD_DAYS:
        raise InvalidPeriod("PERIOD_INVALID", PERIOD_TOO_LONG_MESSAGE)
    return start, end


def _property(cur, ctx, property_id: int) -> dict:
    prop = repository.get_property(cur, ctx.require_agency(), property_id)
    if prop is None or prop.pop("trash_marker") is not None:
        raise NotFound("PROPERTY_NOT_FOUND", PROPERTY_NOT_FOUND_MESSAGE)
    if not may_manage(ctx, prop):
        raise Forbidden("NOT_ALLOWED", NOT_ALLOWED_MESSAGE)
    return prop


def build_report(ctx, property_id: int, date_from: date | None = None, date_to: date | None = None) -> dict:
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        prop = _property(cur, ctx, property_id)
        return _compose(cur, agency_id, prop, date_from, date_to)


def _compose(cur, agency_id: int, prop: dict, date_from: date | None, date_to: date | None) -> dict:
    """Il report di un immobile GIA' autorizzato dal chiamante (operatore o
    proprietario): stessi dati, stesse esclusioni."""
    property_id = prop["id"]
    start_day, end_day = resolve_period(prop, date_from, date_to)
    start, end = _rome_midnight(start_day), _rome_midnight(end_day + timedelta(days=1))
    interested = repository.interested_contacts(cur, agency_id, property_id, start, end)
    visits = repository.completed_visits(cur, agency_id, property_id, start, end)
    now = datetime.now(timezone.utc)
    upcoming_end = now + timedelta(days=UPCOMING_DAYS)
    upcoming = repository.upcoming_visits(cur, agency_id, property_id, now, upcoming_end)
    proposals = repository.proposals_by_status(cur, agency_id, property_id)
    prices = repository.price_history(cur, property_id, start, end)
    statuses = repository.status_history(cur, property_id, start, end)

    months = _months(start_day, end_day)
    for at in interested:
        months[_month_key(at)]["interested_contacts"] += 1
    for at in visits:
        months[_month_key(at)]["visits"] += 1
    real = is_real_mandate(prop)
    state = mandate_state(prop) if real else None
    address = ", ".join(x for x in (" ".join(y for y in (prop.get("address"), prop.get("civic_number")) if y),
                                    prop.get("city")) if x)
    return {
        "property": {
            "id": prop["id"], "code": prop.get("code"), "title": prop.get("title"), "address": address or None,
            "asking_price": _money(prop.get("asking_price")),
            "commercial_status": prop.get("commercial_status"),
            "commercial_status_label": COMMERCIAL_STATUS_LABELS.get(prop.get("commercial_status"),
                                                                    prop.get("commercial_status")),
        },
        "mandate": {
            "exists": real, "type": prop.get("mandate_type") if real else None,
            "start": _day(prop.get("mandate_start")) if real else None,
            "end": _day(prop.get("mandate_end")) if real else None,
            "state": state, "state_label": MANDATE_STATE_LABELS.get(state) if state else None,
        },
        "period": {"from": start_day.isoformat(), "to": end_day.isoformat(),
                   "days": (end_day - start_day).days + 1},
        "interested_contacts": {"total": len(interested)},
        # Nessuna fonte attendibile: mai uno zero al posto del dato mancante.
        "requests": {"available": False, "total": None},
        "visits": {"completed": len(visits),
                   "upcoming": {"count": upcoming, "from": _day(now), "to": _day(upcoming_end)}},
        "monthly": list(months.values()),
        "proposals": {"scope": "current", "total": sum(proposals.values()),
                      "by_status": [{"status": s, "label": label, "count": proposals.get(s, 0)}
                                    for s, label in PROPOSAL_STATUS_LABELS.items() if proposals.get(s)]},
        "price_history": [{"date": _day(p["created_at"]), "old_price": _money(p["old_price"]),
                           "new_price": _money(p["new_price"])} for p in prices],
        "status_history": [{"date": _day(s["created_at"]),
                            "old_label": COMMERCIAL_STATUS_LABELS.get(s["old_value"], s["old_value"]),
                            "new_label": COMMERCIAL_STATUS_LABELS.get(s["new_value"], s["new_value"])}
                           for s in statuses],
        "definitions": list(DEFINITIONS),
    }


def _identity(ctx) -> dict:
    """L'intestazione dell'agenzia (BLOCCO-1B). Logo assente: lecito; logo
    presente ma alterato: errore, mai un PDF silenziosamente senza logo."""
    from agency_profile import service as agency_profile

    identity = agency_profile.report_identity(ctx)
    logo = identity.get("logo")
    if logo is not None and hashlib.sha256(logo["data"]).hexdigest() != logo["sha256"]:
        raise ReportPdfError("LOGO_UNREADABLE", LOGO_UNREADABLE_MESSAGE)
    return identity


def render_pdf(ctx, property_id: int, date_from: date | None, date_to: date | None,
               agent_notes: str | None) -> dict:
    from property_valuations.pdf_render import PdfLogoError

    from .pdf_render import render_owner_report_pdf

    report = build_report(ctx, property_id, date_from, date_to)
    identity = _identity(ctx)
    try:
        data = render_owner_report_pdf(report, identity, agent_notes=agent_notes,
                                       generated_at=datetime.now(timezone.utc))
    except PdfLogoError as exc:
        raise ReportPdfError("LOGO_UNREADABLE", LOGO_UNREADABLE_MESSAGE) from exc
    period = report["period"]
    return {"data": data, "sha256": hashlib.sha256(data).hexdigest(),
            "filename": f"report-proprietario-immobile-{property_id}-{period['from']}-{period['to']}.pdf"}


# ---------------------------------------------------------------------------
# BLOCCO-7: lo stesso report nell'area proprietario
# ---------------------------------------------------------------------------

def _owner_property(cur, owner_account_id: int, property_id: int) -> dict:
    """L'immobile, solo se il proprietario ha ADESSO un'autorizzazione attiva
    (`owner_property_access`, riletta a ogni richiesta: la revoca vale subito)."""
    from owner import repository as owner_repository

    from core.exceptions import NotFoundError

    try:
        access = owner_repository.require_property(owner_account_id, property_id)
    except NotFoundError:
        access = None
    if not access:
        raise NotFound("PROPERTY_NOT_FOUND", PROPERTY_NOT_FOUND_MESSAGE)
    cur.execute("SELECT agency_id FROM properties WHERE id = %s", (property_id,))
    row = cur.fetchone()
    prop = repository.get_property(cur, row["agency_id"], property_id) if row else None
    if prop is None or prop.pop("trash_marker") is not None:
        raise NotFound("PROPERTY_NOT_FOUND", PROPERTY_NOT_FOUND_MESSAGE)
    return prop


def build_owner_report(owner_account_id: int, property_id: int, date_from: date | None = None,
                       date_to: date | None = None) -> dict:
    """Il report per il proprietario autorizzato: identico a quello del
    consulente (stesse esclusioni: note interne, prezzo minimo, recapiti degli
    interessati, importi delle proposte), senza «indicazioni dell'agente»."""
    with core_cursor() as (_, cur):
        prop = _owner_property(cur, owner_account_id, property_id)
        report = _compose(cur, prop["agency_id"], prop, date_from, date_to)
    report["audience"] = "owner"
    return report


def render_owner_pdf_for_owner(owner_account_id: int, property_id: int, date_from: date | None,
                               date_to: date | None) -> dict:
    from operator_auth.context import SystemAgencyContext
    from property_valuations.pdf_render import PdfLogoError

    from .pdf_render import render_owner_report_pdf

    report = build_owner_report(owner_account_id, property_id, date_from, date_to)
    with core_cursor() as (_, cur):
        cur.execute("SELECT agency_id FROM properties WHERE id = %s", (property_id,))
        agency_id = cur.fetchone()["agency_id"]
    # L'intestazione dell'agenzia dell'immobile, letta come sistema per conto del
    # proprietario autenticato (origine `owner_login`, il principale proprietario).
    identity = _identity(SystemAgencyContext(agency_id=agency_id, origin="owner_login"))
    try:
        data = render_owner_report_pdf(report, identity, agent_notes=None, generated_at=datetime.now(timezone.utc))
    except PdfLogoError as exc:
        raise ReportPdfError("LOGO_UNREADABLE", LOGO_UNREADABLE_MESSAGE) from exc
    period = report["period"]
    return {"data": data, "sha256": hashlib.sha256(data).hexdigest(),
            "filename": f"report-immobile-{property_id}-{period['from']}-{period['to']}.pdf"}
