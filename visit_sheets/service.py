"""BLOCCO-6A - generazione e download del foglio visita.

Dalla visita in agenda (`appointments`, tipo `buyer_visit`, collegata a un
immobile): immobile, incarico, agente, partecipanti, data e ora. Ogni
generazione salva PDF, SHA-256, versione del modello, fotografia dei dati e
autore (migrazione 121). Se i dati non sono cambiati dall'ultima versione si
restituisce quella, senza una copia nuova; se sono cambiati (per esempio
l'esito registrato, un nuovo orario) nasce la versione successiva.

TRE FATTI DISTINTI
    prenotazione   la visita esiste in agenda (`scheduled`/`confirmed`);
    visita svolta  solo dopo l'esito registrato dall'operatore (`completed`):
                   prima, il foglio dice esplicitamente che NON lo attesta;
    sottoscrizione mai registrata qui: BLOCCO-6B.

PERMESSI: la stessa visibilita' dell'Agenda (titolare e amministratori tutta
l'agenzia, l'agente le proprie visite). Generare e' una scrittura (bloccata
in consultazione in sola lettura dalla guardia globale). Il PDF non viene mai
pubblicato al proprietario da qui.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from appointments import repository as appointment_repository
from core.database import core_cursor
from core.property_mandate import is_real_mandate, signed_link_mandate
from operator_auth.permissions import may_assign_records
from property.catalog import PROPERTY_TYPE_LABELS

from . import repository
from .pdf_render import TEMPLATE_VERSION, render_visit_sheet_pdf

ROME = ZoneInfo("Europe/Rome")
SHEET_STATUSES = ("scheduled", "confirmed", "completed")
STATE_LABELS = {"booked": "Prenotata (esito non registrato)", "completed": "Svolta (esito registrato)"}


class VisitSheetError(Exception):
    status = 400

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class NotFound(VisitSheetError):
    status = 404


class Unavailable(VisitSheetError):
    status = 503


class Integrity(VisitSheetError):
    status = 500


def _manager(ctx) -> bool:
    return may_assign_records(ctx.role, ctx.is_platform_admin)


def _visible(ctx, visit: dict) -> bool:
    return _manager(ctx) or (ctx.user_id is not None and visit["assigned_user_id"] == ctx.user_id)


def _require_installed(cur):
    if not repository.installed(cur):
        raise Unavailable("NOT_INSTALLED", "I fogli visita non sono ancora installati (migrazione 121).")


def _rome(value) -> datetime:
    return value.astimezone(ROME)


def _sheet_view(sheet: dict) -> dict:
    return {"id": sheet["id"], "appointment_id": sheet["appointment_id"], "version": sheet["sheet_version"],
            "template_version": sheet["template_version"], "visit_state": sheet["visit_state"],
            "visit_state_label": STATE_LABELS[sheet["visit_state"]], "sha256": sheet["sha256"],
            "generated_at": sheet["generated_at"].isoformat(), "signed": False}


def list_for_property(ctx, property_id: int) -> dict:
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        _require_installed(cur)
        prop = repository.property_row(cur, agency_id, property_id)
        if prop is None or prop["trash_marker"] is not None:
            raise NotFound("PROPERTY_NOT_FOUND", "Immobile non trovato.")
        visits = repository.visits_for_property(cur, agency_id, property_id, None if _manager(ctx) else ctx.user_id)
        sheets = repository.sheets_for_appointments(cur, agency_id, [v["id"] for v in visits])
    by_visit: dict[int, list] = {}
    for s in sheets:
        by_visit.setdefault(s["appointment_id"], []).append(_sheet_view(s))
    return {"items": [{"appointment_id": v["id"], "status": v["status"], "start_at": v["start_at"].isoformat(),
                       "end_at": v["end_at"].isoformat(), "can_generate": v["status"] in SHEET_STATUSES,
                       "sheets": by_visit.get(v["id"], [])} for v in visits]}


def _snapshot(cur, ctx, agency_id: int, visit: dict, prop: dict) -> dict:
    prop["signed_mandate"] = signed_link_mandate(cur, prop["id"])
    real = is_real_mandate(prop)
    start, end = _rome(visit["start_at"]), _rome(visit["end_at"])
    address = ", ".join(x for x in (" ".join(y for y in (prop.get("address"), prop.get("civic_number")) if y),
                                    prop.get("city")) if x)
    state = "completed" if visit["status"] == "completed" else "booked"
    return {
        "visit_state": state,
        "property": {"id": prop["id"], "code": prop.get("code"), "address": address or None,
                     "type_label": PROPERTY_TYPE_LABELS.get(prop.get("property_type") or "", "Immobile")},
        "mandate": {"exists": real, "type": prop.get("mandate_type") if real else None,
                    "start": prop["mandate_start"].strftime("%d/%m/%Y") if real and prop.get("mandate_start") else None,
                    "end": prop["mandate_end"].strftime("%d/%m/%Y") if real and prop.get("mandate_end") else None},
        "visit": {"appointment_id": visit["id"], "status": visit["status"],
                  "date_text": start.strftime("%d/%m/%Y"), "time_text": f"{start:%H:%M}–{end:%H:%M} (ora italiana)",
                  "agent_name": appointment_repository.agent_name(cur, agency_id, visit["assigned_user_id"])
                  if visit.get("assigned_user_id") else None,
                  "visitor_name": repository.contact_name(cur, agency_id, visit.get("contact_id")),
                  "participants": [],
                  "completed_at_text": _rome(visit["completed_at"]).strftime("%d/%m/%Y %H:%M")
                  if state == "completed" and visit.get("completed_at") else None},
        "author_name": appointment_repository.agent_name(cur, agency_id, ctx.user_id),
    }


def generate(ctx, appointment_id: int) -> dict:
    from agency_profile import service as agency_profile

    agency_id = ctx.require_agency()
    if ctx.user_id is None:
        raise VisitSheetError("SESSION_REQUIRED", "Serve una sessione operatore.")
    identity = agency_profile.report_identity(ctx)
    logo = identity.get("logo")
    if logo is not None and hashlib.sha256(logo["data"]).hexdigest() != logo["sha256"]:
        raise Integrity("LOGO_UNREADABLE", "Il logo dell'agenzia non è leggibile.")
    with core_cursor(commit=True) as (_, cur):
        _require_installed(cur)
        visit = repository.visit(cur, agency_id, appointment_id, lock=True)
        if visit is None or not _visible(ctx, visit) or visit["appointment_type"] != "buyer_visit":
            raise NotFound("VISIT_NOT_FOUND", "Visita non trovata.")
        if visit["property_id"] is None:
            raise VisitSheetError("NO_PROPERTY", "La visita non è collegata a un immobile.")
        if visit["status"] not in SHEET_STATUSES:
            raise VisitSheetError("VISIT_NOT_SHEETABLE", "Il foglio si genera per una visita fissata o svolta: "
                                  "non per una richiesta, una visita annullata o non avvenuta.")
        prop = repository.property_row(cur, agency_id, visit["property_id"])
        if prop is None or prop["trash_marker"] is not None:
            raise NotFound("PROPERTY_NOT_FOUND", "Immobile non trovato.")
        snapshot = _snapshot(cur, ctx, agency_id, visit, prop)
        last = repository.latest(cur, appointment_id)
        comparable = {k: v for k, v in snapshot.items() if k != "author_name"}
        if last is not None and {k: v for k, v in last["data_snapshot"].items()
                                 if k not in ("author_name", "sheet_version")} == json.loads(json.dumps(comparable)):
            return {"sheet": _sheet_view(last), "created": False}
        version = (last["sheet_version"] if last else 0) + 1
        snapshot["sheet_version"] = version
        now = datetime.now(timezone.utc)
        data = render_visit_sheet_pdf(snapshot, identity, generated_at_text=_rome(now).strftime("%d/%m/%Y %H:%M"))
        row = repository.insert(cur, {
            "agency_id": agency_id, "appointment_id": appointment_id, "property_id": prop["id"],
            "sheet_version": version, "template_version": TEMPLATE_VERSION, "visit_state": snapshot["visit_state"],
            "data_snapshot": snapshot, "pdf_bytes": data, "sha256": hashlib.sha256(data).hexdigest(),
            "generated_by_user_id": ctx.user_id})
        if row is None:                                   # gia' scritta da una richiesta concorrente
            return {"sheet": _sheet_view(repository.latest(cur, appointment_id)), "created": False}
    return {"sheet": _sheet_view(row), "created": True}


def download(ctx, sheet_id: int) -> dict:
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        _require_installed(cur)
        sheet = repository.pdf(cur, agency_id, sheet_id)
        visit = repository.visit(cur, agency_id, sheet["appointment_id"]) if sheet else None
        if sheet is None or visit is None or not _visible(ctx, visit):
            raise NotFound("SHEET_NOT_FOUND", "Foglio visita non trovato.")
    data = bytes(sheet["pdf_bytes"])
    if hashlib.sha256(data).hexdigest() != sheet["sha256"]:
        raise Integrity("SHEET_INTEGRITY", "Il foglio visita salvato non corrisponde alla sua impronta.")
    return {"data": data, "sha256": sheet["sha256"],
            "filename": f"foglio-visita-{sheet['appointment_id']}-v{sheet['sheet_version']}.pdf"}
