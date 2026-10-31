"""BLOCCO-4D - il motore che decide come rispondere ai nuovi messaggi WhatsApp.

SOLO SIMULAZIONE. Il motore registra la decisione che prenderebbe; non accoda
nulla nel registro delle comunicazioni, non chiama Meta, non crea attivita'
e non notifica nessuno. Spento per impostazione predefinita, due volte:

    * arresto generale: la variabile `WHATSAPP_AUTO_REPLY_MODE` deve valere
      `simulate`, altrimenti nessun giro fa nulla per nessuna agenzia;
    * per agenzia: `whatsapp_auto_reply_settings.mode` (di partenza 'off').

QUANDO GIRA

Il webhook (4A) persiste il messaggio e risponde: non elabora nulla. Il motore
gira in un giro separato (`POST /api/auto-replies/tick`), come i giri gia'
esistenti di dispatch e promemoria: lo chiama un cron esterno o un
amministratore, con la sessione di un titolare o amministratore dell'agenzia.
Dentro il giro il motore agisce come SISTEMA DELL'AGENZIA
(`SystemAgencyContext`, origine `whatsapp_inbound`): nessun operatore umano
inventato, nessuna regola del 4B aggirata (il 4B resta l'unica via d'invio).

L'ORDINE DEI CONTROLLI (il primo che vale decide)

    1. finestra di 24 ore chiusa                       -> skipped
    2. STOP in questa conversazione (agenzia)          -> none (il messaggio
       STOP) / skipped (ogni altro messaggio)
    3. rinuncia di piattaforma sul numero              -> skipped
    4. conversazione in pausa, o risposta manuale di
       un operatore (4B) nelle ultime 24 ore           -> skipped
    5. allegato o messaggio non di testo               -> skipped (umano)
    6. mittente senza contatto                         -> skipped (umano)
    7. regole del 4C sull'immobile di QUEL messaggio   -> answer /
       ask_reference / handoff
    8. BLOCCO-5B: richiesta di visita, immobile identificato e prenotazione
       abilitata (link attivo, agente attivo, incarico in corso) -> answer con
       il collegamento (segnaposto) e nessuna conferma d'appuntamento

Ogni decisione si registra UNA volta per (messaggio, versione delle regole):
`ON CONFLICT DO NOTHING` sull'unicita' della 119. Un passaggio all'agente
registra l'attivita' che servirebbe (`handoff.task`), SENZA crearla: in
simulazione nessun operatore reale riceve nulla, e quindi nessun testo
promette un ricontatto (regole 4C, `followup_assigned`).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from communication import service as communication
from consent import repository as consent_repository
from core.database import core_cursor
from inbound_messages import repository as inbound_repository
from inbound_messages.service import INBOUND_ORIGIN
from message_replies.enums import WINDOW_HOURS, WINDOW_SAFETY_MINUTES
from operator_auth.context import SystemAgencyContext
from operator_auth.permissions import sees_all_agency_records
from reply_suggestions import repository as suggestion_repository
from reply_suggestions import service as suggestions
from reply_suggestions.fields import AVAILABLE_STATUS
from visit_booking import service as visit_booking

from . import repository
from .enums import (
    ANSWER_REASONS,
    BOOKING_LINK_PLACEHOLDER,
    DECISION_LABELS,
    GLOBAL_ENV,
    GLOBAL_SIMULATE,
    MODE_OFF,
    MODE_SIMULATE,
    MODES,
    OPERATOR_PAUSE_HOURS,
    RULES_VERSION,
    SIMULATION_LABEL,
    SKIP_REASONS,
    SKIPPED,
    TICK_LIMIT,
    VISIT_BOOKING_OWNER_TEXT,
    VISIT_BOOKING_TEXT,
)


def _available(cur, agency_id: int, prop: dict | None) -> bool:
    """L'immobile del messaggio e' «Attivo» (la stessa condizione del 4C)."""
    if not prop:
        return False
    row = suggestion_repository.communicable_property(cur, agency_id, prop["id"])
    return row is not None and row.get("commercial_status") == AVAILABLE_STATUS


NOT_INSTALLED_MESSAGE = "Le risposte automatiche non sono ancora installate su questo ambiente (migrazione 119)."
FORBIDDEN_MESSAGE = "Solo il titolare o un amministratore dell'agenzia può gestire le risposte automatiche."
NOT_FOUND_MESSAGE = "Conversazione non trovata."


class AutoReplyError(Exception):
    status = 400

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class NotInstalled(AutoReplyError):
    status = 503


class Forbidden(AutoReplyError):
    status = 403


class NotFound(AutoReplyError):
    status = 404


def _now() -> datetime:
    return datetime.now(timezone.utc)


def global_enabled() -> bool:
    return (os.getenv(GLOBAL_ENV) or "").strip() == GLOBAL_SIMULATE


def _agent_scope(ctx) -> int | None:
    return None if sees_all_agency_records(ctx.role, ctx.is_platform_admin) else ctx.user_id


def _require_manager(ctx) -> None:
    if not sees_all_agency_records(ctx.role, ctx.is_platform_admin):
        raise Forbidden("AUTO_REPLY_FORBIDDEN", FORBIDDEN_MESSAGE)


def _require_installed(cur) -> None:
    if not repository.installed(cur):
        raise NotInstalled("NOT_INSTALLED", NOT_INSTALLED_MESSAGE)


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


# ---------------------------------------------------------------------------
# la decisione per UN messaggio (pura rispetto al registro: legge e basta)
# ---------------------------------------------------------------------------

def _customer_at(m: dict):
    received = m["received_at"]
    stamp = m.get("provider_timestamp") or received
    return min(stamp, received)                     # un orario futuro non allunga la finestra


def _operator_replied(sys_ctx, contact_id: int, sender_phone: str, now) -> bool:
    since = now - timedelta(hours=OPERATOR_PAUSE_HOURS)
    for row in communication.list_for_contact(sys_ctx, contact_id, limit=50):
        if (row["channel"] == "whatsapp" and row["reason_code"] == "operator_reply"
                and row["destination_snapshot"] == sender_phone and row["created_at"] >= since):
            return True
    return False


def decide(cur, agency_id: int, message: dict, *, now=None) -> dict:
    """La decisione per un messaggio dell'agenzia, senza scriverla."""
    now = now or _now()
    sys_ctx = SystemAgencyContext(agency_id=agency_id, origin=INBOUND_ORIGIN)
    sender = message["sender_phone"]
    closes_at = _customer_at(message) + timedelta(hours=WINDOW_HOURS)
    base = {"agency_id": agency_id, "inbound_message_id": message["id"], "rules_version": RULES_VERSION,
            "data_used": {"window_closes_at": _iso(closes_at)}}

    def skipped(code):
        return {**base, "decision": SKIPPED, "reason_code": code}

    if now >= closes_at - timedelta(minutes=WINDOW_SAFETY_MINUTES):
        return skipped("window_closed")
    if repository.stop_in_conversation(cur, agency_id, sender):
        suggestion = suggestions.rules.decide(message, None, [], reference_problem=None)
        if suggestion.outcome == suggestions.rules.NONE:            # e' proprio il messaggio STOP
            return {**base, "decision": "none", "reason_code": "stop"}
        return skipped("stop_in_conversation")
    contact = repository.contact_row(cur, agency_id, message["contact_id"]) if message.get("contact_id") else None
    for address in {sender, (contact or {}).get("phone_normalized")} - {None, ""}:
        if consent_repository.latest_platform_optout(cur, "whatsapp", address) is not None:
            return skipped("platform_optout")
    if repository.open_pause(cur, agency_id, sender) is not None:
        return skipped("conversation_paused")
    if contact is not None and _operator_replied(sys_ctx, contact["id"], sender, now):
        return skipped("operator_replied")
    if message.get("message_type") != "text":
        return skipped("attachment")
    if contact is None:
        return skipped("no_contact")

    s = suggestions.compute(cur, agency_id, message)
    decision = {**base, "decision": s["outcome"], "reason_code": (s["handoff"] or {}).get("code"),
                "proposed_text": s["text"]}
    decision["data_used"] = {**base["data_used"], "property_id": (s["property"] or {}).get("id"),
                             "topics": s["topics"], "missing": s["missing"],
                             "fields": [{"field": x["field"], "value": x["value"]} for x in s["sources"]],
                             "followup_assigned": s["followup_assigned"]}
    # BLOCCO-5B: una richiesta di visita sull'immobile IDENTIFICATO di questo
    # messaggio, con la prenotazione davvero abilitata, riceve il collegamento
    # (come segnaposto: il token non si conserva) e nessuna conferma.
    # Come per i dati della scheda, SOLO per un immobile «Attivo»: un incarico
    # in corso ma riservato, in trattativa o non ancora attivo va all'agente.
    if (s["handoff"] or {}).get("code") == "visit" and _available(cur, agency_id, s["property"]):
        link = visit_booking.booking_link_for_visit(cur, agency_id, s["property"]["id"])
        if link is not None:
            text = VISIT_BOOKING_TEXT.format(link=BOOKING_LINK_PLACEHOLDER)
            if link["owner_authorization"] == "per_visit":
                text += VISIT_BOOKING_OWNER_TEXT
            decision.update({"decision": "answer", "reason_code": "visit_booking_link", "proposed_text": text})
            decision["data_used"]["booking_link_id"] = link["id"]
            decision["data_used"]["owner_approval_required"] = link["owner_authorization"] == "per_visit"
            return decision
    if s["outcome"] == "handoff":
        decision["handoff"] = {
            "task": "planned", "created": False, "simulation": True,
            "title": "Risposta WhatsApp da seguire",
            "reason": (s["handoff"] or {}).get("reason"),
            "contact_id": contact["id"], "assignee_user_id": contact.get("assigned_agent_id"),
        }
    return decision


# ---------------------------------------------------------------------------
# il giro
# ---------------------------------------------------------------------------

def tick(ctx) -> dict:
    """Un giro per l'agenzia della sessione (titolare o amministratore)."""
    _require_manager(ctx)
    agency_id = ctx.require_agency()
    summary = {"status": "stopped", "mode": MODE_OFF, "processed": 0, "recorded": 0, "duplicates": 0,
               "by_decision": {}, "rules_version": RULES_VERSION, "sent": 0, "queued": 0}
    with core_cursor() as (_, cur):
        _require_installed(cur)
        conf = repository.settings(cur, agency_id) or {"mode": MODE_OFF, "enabled_at": None}
    if not global_enabled():
        return summary                                   # arresto generale: nulla per nessuno
    summary["mode"] = conf["mode"]
    if conf["mode"] != MODE_SIMULATE:
        summary["status"] = "off"
        return summary
    summary["status"] = "simulated"
    with core_cursor() as (_, cur):
        ids = repository.pending_message_ids(cur, agency_id, conf["enabled_at"], RULES_VERSION, TICK_LIMIT)
    for message_id in ids:
        with core_cursor(commit=True) as (_, cur):
            message = inbound_repository.get_message(cur, agency_id, message_id)
            if message is None:
                continue
            data = decide(cur, agency_id, message)
            row = repository.record_decision(cur, data)
        summary["processed"] += 1
        if row is None:
            summary["duplicates"] += 1
            continue
        summary["recorded"] += 1
        summary["by_decision"][row["decision"]] = summary["by_decision"].get(row["decision"], 0) + 1
    return summary


# ---------------------------------------------------------------------------
# impostazioni, pausa, stato di una conversazione
# ---------------------------------------------------------------------------

def _settings_view(conf: dict | None, ctx) -> dict:
    conf = conf or {"mode": MODE_OFF, "enabled_at": None}
    return {"global_enabled": global_enabled(), "mode": conf["mode"], "enabled_at": _iso(conf.get("enabled_at")),
            "effective": "simulate" if global_enabled() and conf["mode"] == MODE_SIMULATE else "off",
            "sending": "disabled", "rules_version": RULES_VERSION,
            "can_configure": sees_all_agency_records(ctx.role, ctx.is_platform_admin)}


def get_settings(ctx) -> dict:
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        _require_installed(cur)
        return _settings_view(repository.settings(cur, agency_id), ctx)


def set_settings(ctx, mode: str) -> dict:
    _require_manager(ctx)
    agency_id = ctx.require_agency()
    if mode not in MODES:
        raise AutoReplyError("INVALID_MODE", f"Modo non valido: {sorted(MODES)}")
    with core_cursor(commit=True) as (_, cur):
        _require_installed(cur)
        return _settings_view(repository.set_mode(cur, agency_id, mode, ctx.user_id), ctx)


def _visible_conversation(cur, ctx, sender_phone: str) -> list[dict]:
    rows = inbound_repository.conversation_messages(cur, ctx.require_agency(), sender_phone,
                                                    agent_user_id=_agent_scope(ctx), limit=100)
    if not rows:
        raise NotFound("NOT_FOUND", NOT_FOUND_MESSAGE)
    return rows


def _decision_view(d: dict) -> dict:
    reason = (SKIP_REASONS.get(d["reason_code"]) if d["decision"] == SKIPPED
              else ANSWER_REASONS.get(d["reason_code"]))
    handoff = d.get("handoff") or None
    return {"message_id": d["inbound_message_id"], "decision": d["decision"],
            "decision_label": DECISION_LABELS[d["decision"]], "reason_code": d["reason_code"],
            "reason": reason or (handoff or {}).get("reason"), "proposed_text": d["proposed_text"],
            "data_used": d["data_used"], "handoff": handoff, "rules_version": d["rules_version"],
            "created_at": _iso(d["created_at"]), "label": SIMULATION_LABEL, "sent": False}


def conversation_state(ctx, sender_phone: str) -> dict:
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        _require_installed(cur)
        rows = _visible_conversation(cur, ctx, sender_phone)
        decisions = repository.decisions_for_messages(cur, agency_id, [r["id"] for r in rows])
        pause = repository.open_pause(cur, agency_id, sender_phone)
        conf = repository.settings(cur, agency_id)
    return {"sender_phone": sender_phone, "settings": _settings_view(conf, ctx),
            "paused": pause is not None, "paused_at": _iso((pause or {}).get("paused_at")),
            "decisions": [_decision_view(d) for d in decisions]}


def set_pause(ctx, sender_phone: str, paused: bool) -> dict:
    """Pausa/ripresa di una conversazione: chiunque la veda (non in sola lettura,
    che la guardia globale ferma prima)."""
    agency_id = ctx.require_agency()
    if ctx.user_id is None:
        raise Forbidden("AUTO_REPLY_FORBIDDEN", FORBIDDEN_MESSAGE)
    with core_cursor(commit=True) as (_, cur):
        _require_installed(cur)
        _visible_conversation(cur, ctx, sender_phone)
        if paused:
            repository.pause(cur, agency_id, sender_phone, ctx.user_id)
        else:
            repository.resume(cur, agency_id, sender_phone, ctx.user_id)
    return conversation_state(ctx, sender_phone)
