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
from appointments import repository as appointment_repository
from inbound_messages import repository as inbound_repository
from inbound_messages import routing
from inbound_messages.service import INBOUND_ORIGIN
from message_replies import transport as transport_module
from message_replies.enums import WINDOW_HOURS, WINDOW_SAFETY_MINUTES
from message_replies.transport import SendResult
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
    COMMUNICATION_REASON,
    DECISION_LABELS,
    GLOBAL_ENV,
    GLOBAL_SEND,
    GLOBAL_SIMULATE,
    MODE_OFF,
    MODE_RANK,
    MODE_SEND,
    MODE_SIMULATE,
    MODES,
    NO_SEND_LABEL,
    OPERATOR_PAUSE_HOURS,
    PROVIDER_LABEL,
    RULES_VERSION,
    SEND_LABELS,
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
SEND_NOT_INSTALLED_MESSAGE = "L'invio delle risposte automatiche non è ancora installato su questo ambiente (migrazione 123)."
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


def global_mode() -> str:
    """L'arresto generale dell'ambiente: 'off' salvo `simulate` o `send` esatti."""
    value = (os.getenv(GLOBAL_ENV) or "").strip()
    return value if value in (GLOBAL_SIMULATE, GLOBAL_SEND) else MODE_OFF


def global_enabled() -> bool:
    return global_mode() != MODE_OFF


def effective_mode(agency_mode: str | None) -> str:
    """Il MINORE fra l'ambiente e l'agenzia (off < simulate < send)."""
    agency_mode = agency_mode if agency_mode in MODE_RANK else MODE_OFF
    return min(global_mode(), agency_mode, key=MODE_RANK.get)


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

HANDOFF_TASK_TITLE = "Risposta WhatsApp da seguire"
HANDOFF_TASK_DESCRIPTION = ("Il cliente ha scritto su WhatsApp e la risposta automatica ha passato la conversazione "
                            "a un operatore. Motivo: {reason}. Leggi la conversazione in Messaggi e rispondi.")


class _Duplicate(Exception):
    """Un giro concorrente ha gia' deciso questo messaggio: si annulla tutto."""


def _assignee(cur, agency_id: int, contact: dict) -> tuple[int | None, str | None]:
    user_id = contact.get("assigned_agent_id")
    if user_id is None or not appointment_repository.active_membership(cur, agency_id, user_id):
        return None, None
    return user_id, inbound_repository.operator_names(cur, [user_id]).get(user_id)


def _decide_for_send(cur, agency_id: int, message: dict) -> tuple[dict, dict | None]:
    """Modo d'invio, nella transazione (con il lock del messaggio) del
    chiamante: decisione, attivita' reale per un passaggio all'agente,
    messaggio accodato nel registro e riga di invio. Tutto o niente."""
    data = decide(cur, agency_id, message)
    task = None
    if data["decision"] == "handoff":
        contact = repository.contact_row(cur, agency_id, message["contact_id"])
        user_id, name = _assignee(cur, agency_id, contact)
        task = repository.create_handoff_task(
            cur, agency_id=agency_id, contact_id=contact["id"], title=HANDOFF_TASK_TITLE,
            description=HANDOFF_TASK_DESCRIPTION.format(reason=(data.get("handoff") or {}).get("reason") or "-"),
            assigned_name=name, assigned_user_id=user_id, inbound_message_id=message["id"])
        # di nuovo, ora che l'attivita' esiste: il testo puo' dire che l'agente
        # ricontattera' SOLO se l'attivita' e' davvero assegnata (regola 4C)
        again = decide(cur, agency_id, message)
        if again["decision"] == "handoff":
            data = again
        data["handoff"] = {**(data.get("handoff") or {}), "task": "created", "created": True, "simulation": False,
                           "task_id": task["id"], "assignee_user_id": user_id, "assigned": user_id is not None}
    row = repository.record_decision(cur, data, mode=MODE_SEND)
    if row is None:
        raise _Duplicate()
    queued = None
    if row["proposed_text"] and row["decision"] in ("answer", "ask_reference", "handoff"):
        sys_ctx = SystemAgencyContext(agency_id=agency_id, origin=INBOUND_ORIGIN)
        used = row["data_used"] or {}
        queued = communication.enqueue(
            sys_ctx, contact_id=message["contact_id"], channel="whatsapp", communication_type="service",
            mode="automatic", reason_code=COMMUNICATION_REASON, rendered_body=row["proposed_text"],
            destination_snapshot=message["sender_phone"], idempotency_key=f"wa_auto:{agency_id}:{row['id']}",
            property_id=used.get("property_id"),
            metadata={"service_basis": "customer_initiated_whatsapp", "decision_id": row["id"],
                      "inbound_message_id": message["id"],
                      "phone_number_id": repository.inbound_phone_number_id(cur, agency_id, message["id"]),
                      "window_closes_at": used.get("window_closes_at"),
                      "booking_parent_link_id": used.get("booking_link_id"),
                      "property_data": bool(used.get("fields"))},
            cur=cur)["message"]
    if queued is not None or task is not None:
        repository.insert_dispatch(cur, agency_id=agency_id, decision_id=row["id"],
                                   communication_message_id=(queued or {}).get("id"), task_id=(task or {}).get("id"))
    return row, queued


def _metadata(message: dict) -> dict:
    value = message.get("metadata")
    if isinstance(value, str):
        import json
        value = json.loads(value)
    return value if isinstance(value, dict) else {}


def _public_base_url() -> str | None:
    value = (os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    return value if value.startswith("https://") else None


def _send_time_check(agency_id: int, message: dict, *, now=None) -> tuple[str | None, str | None]:
    """Subito prima di Meta, con l'ora e i dati di ADESSO: il motivo per non
    inviare, o (None, testo da consegnare). Il collegamento dedicato si emette
    per ultimo, quando ogni altro controllo e' superato, e il suo indirizzo
    entra SOLO nel testo restituito (mai nel registro)."""
    now = now or _now()
    meta = _metadata(message)
    sender = message["destination_snapshot"]
    with core_cursor(commit=True) as (_, cur):
        conf = repository.settings(cur, agency_id) or {}
        if effective_mode(conf.get("mode")) != MODE_SEND:
            return "auto_reply_not_in_send_mode", None
        if not transport_module.enabled():
            return "outbound_disabled", None
        if not transport_module.credentials_present():
            return "credentials_missing", None
        if not transport_module.graph_version():
            return "graph_version_missing", None
        if routing.configured_routes().get(meta.get("phone_number_id") or "") != {agency_id}:
            return "sender_not_configured", None
        inbound = inbound_repository.get_message(cur, agency_id, meta.get("inbound_message_id") or 0)
        if inbound is None or inbound["contact_id"] != message["contact_id"]:
            return "contact_changed", None
        if now >= _customer_at(inbound) + timedelta(hours=WINDOW_HOURS) - timedelta(minutes=WINDOW_SAFETY_MINUTES):
            return "window_closed", None
        if repository.stop_in_conversation(cur, agency_id, sender):
            return "stop_in_conversation", None
        contact = repository.contact_row(cur, agency_id, message["contact_id"])
        for address in {sender, (contact or {}).get("phone_normalized")} - {None, ""}:
            if consent_repository.latest_platform_optout(cur, "whatsapp", address) is not None:
                return "platform_optout", None
        if repository.open_pause(cur, agency_id, sender) is not None:
            return "conversation_paused", None
        sys_ctx = SystemAgencyContext(agency_id=agency_id, origin=INBOUND_ORIGIN)
        if _operator_replied(sys_ctx, message["contact_id"], sender, now):
            return "operator_replied", None
        if meta.get("property_data") and not _available(cur, agency_id, {"id": message.get("property_id")}):
            return "property_no_longer_active", None
        text = message["rendered_body"]
        parent = meta.get("booking_parent_link_id")
        if parent is not None:
            base = _public_base_url()
            if base is None:
                return "public_base_url_missing", None
            path = visit_booking.issue_dedicated_link(cur, agency_id, int(parent), decision_id=meta["decision_id"],
                                                      contact_id=message["contact_id"], now=now)
            if path is None:
                return "booking_link_unavailable", None
            text = text.replace(BOOKING_LINK_PLACEHOLDER, base + path)
        if BOOKING_LINK_PLACEHOLDER in text:
            return "placeholder_left", None
    return None, text


def _deliver(agency_id: int, message: dict) -> str:
    """Claim di QUEL messaggio, controlli, invio fuori transazione, esito.
    Un esito incerto non si ritenta mai; un messaggio gia' preso non riparte."""
    sys_ctx = SystemAgencyContext(agency_id=agency_id, origin=INBOUND_ORIGIN)
    claimed = communication.claim_message(sys_ctx, message["id"], provider=PROVIDER_LABEL, channel="whatsapp")
    if claimed is None:
        return "lost"
    token = claimed["message"]["claim_token"]
    try:
        reason, text = _send_time_check(agency_id, message)
    except Exception:                        # un controllo che non si puo' fare e' un blocco, non un invio
        reason, text = "send_check_failed", None
    transport = transport_module.current() if reason is None else None
    if reason is None and transport is None:
        reason = "outbound_disabled"
    if reason is not None:
        communication.finalize_suppressed(sys_ctx, message["id"], token, reason=reason)
        return "suppressed"
    try:
        result = transport.send(_metadata(message)["phone_number_id"], message["destination_snapshot"], text)
    except Exception as exc:      # noqa: BLE001 - esito incerto, mai un reinvio
        result = SendResult("indeterminate", error_code="outcome_unknown", error_detail=type(exc).__name__)
    if result.outcome == "accepted":
        communication.finalize_sent(sys_ctx, message["id"], token, provider_message_id=result.provider_message_id)
        return "sent"
    if result.outcome == "rejected":
        communication.finalize_failed(sys_ctx, message["id"], token, error_code=result.error_code,
                                      error_detail=result.error_detail)
        return "failed"
    communication.finalize_indeterminate(sys_ctx, message["id"], token, error_code=result.error_code or "outcome_unknown",
                                         error_detail=result.error_detail)
    return "indeterminate"


def tick(ctx) -> dict:
    """Un giro per l'agenzia della sessione (titolare o amministratore)."""
    _require_manager(ctx)
    return run_for_agency(ctx.require_agency())


def run_for_agency(agency_id: int) -> dict:
    """Il giro di UN'agenzia: decisioni (con il lock di ogni messaggio, cosi'
    due giri concorrenti non decidono due volte) e, nel modo d'invio, la
    consegna di cio' che si e' appena accodato o era rimasto in coda."""
    summary = {"status": "stopped", "mode": MODE_OFF, "processed": 0, "recorded": 0, "duplicates": 0,
               "by_decision": {}, "rules_version": RULES_VERSION, "sent": 0, "queued": 0,
               "suppressed": 0, "failed": 0, "indeterminate": 0, "lost": 0}
    with core_cursor() as (_, cur):
        _require_installed(cur)
        conf = repository.settings(cur, agency_id) or {"mode": MODE_OFF, "enabled_at": None}
        send_ready = repository.send_installed(cur)
    if not global_enabled():
        return summary                                   # arresto generale: nulla per nessuno
    summary["mode"] = conf["mode"]
    mode = effective_mode(conf["mode"])
    if mode == MODE_SEND and not send_ready:
        mode = MODE_SIMULATE                             # 123 non applicata: mai un invio
    if mode == MODE_OFF:
        summary["status"] = "off"
        return summary
    summary["status"] = "sending" if mode == MODE_SEND else "simulated"
    with core_cursor() as (_, cur):
        ids = repository.pending_message_ids(cur, agency_id, conf["enabled_at"], RULES_VERSION, TICK_LIMIT)
        resumable = repository.resumable_message_ids(cur, agency_id, TICK_LIMIT) if mode == MODE_SEND else []
    to_deliver = []
    for message_id in ids:
        try:
            with core_cursor(commit=True) as (_, cur):
                message = inbound_repository.get_message(cur, agency_id, message_id, lock=True)
                if message is None:
                    continue
                if repository.decision_exists(cur, message_id, RULES_VERSION):
                    raise _Duplicate()
                if mode == MODE_SEND:
                    row, queued = _decide_for_send(cur, agency_id, message)
                else:
                    row, queued = repository.record_decision(cur, decide(cur, agency_id, message)), None
                    if row is None:
                        raise _Duplicate()
        except _Duplicate:
            summary["processed"] += 1
            summary["duplicates"] += 1
            continue
        summary["processed"] += 1
        summary["recorded"] += 1
        summary["by_decision"][row["decision"]] = summary["by_decision"].get(row["decision"], 0) + 1
        if queued is not None:
            summary["queued"] += 1
            to_deliver.append(queued)
    if mode == MODE_SEND:
        with core_cursor() as (_, cur):
            sys_ctx = SystemAgencyContext(agency_id=agency_id, origin=INBOUND_ORIGIN)
            pending = [communication.get_message(sys_ctx, i, cur=cur) for i in resumable]
        seen = {m["id"] for m in to_deliver}
        for message in to_deliver + [m for m in pending if m["id"] not in seen]:
            outcome = _deliver(agency_id, message)
            summary[outcome] += 1
    return summary


# ---------------------------------------------------------------------------
# impostazioni, pausa, stato di una conversazione
# ---------------------------------------------------------------------------

def _settings_view(conf: dict | None, ctx, *, send_ready: bool = False) -> dict:
    conf = conf or {"mode": MODE_OFF, "enabled_at": None}
    effective = effective_mode(conf["mode"])
    if effective == MODE_SEND and not send_ready:
        effective = MODE_SIMULATE
    sending = effective == MODE_SEND and transport_module.enabled()
    return {"global_enabled": global_enabled(), "global_mode": global_mode(), "mode": conf["mode"],
            "enabled_at": _iso(conf.get("enabled_at")), "effective": effective,
            "sending": "enabled" if sending else "disabled", "send_available": send_ready,
            "rules_version": RULES_VERSION,
            "can_configure": sees_all_agency_records(ctx.role, ctx.is_platform_admin)}


def get_settings(ctx) -> dict:
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        _require_installed(cur)
        return _settings_view(repository.settings(cur, agency_id), ctx, send_ready=repository.send_installed(cur))


def set_settings(ctx, mode: str) -> dict:
    _require_manager(ctx)
    agency_id = ctx.require_agency()
    if mode not in MODES:
        raise AutoReplyError("INVALID_MODE", f"Modo non valido: {sorted(MODES)}")
    with core_cursor(commit=True) as (_, cur):
        _require_installed(cur)
        ready = repository.send_installed(cur)
        if mode == MODE_SEND and not ready:
            raise NotInstalled("SEND_NOT_INSTALLED", SEND_NOT_INSTALLED_MESSAGE)
        return _settings_view(repository.set_mode(cur, agency_id, mode, ctx.user_id), ctx, send_ready=ready)


def _visible_conversation(cur, ctx, sender_phone: str) -> list[dict]:
    rows = inbound_repository.conversation_messages(cur, ctx.require_agency(), sender_phone,
                                                    agent_user_id=_agent_scope(ctx), limit=100)
    if not rows:
        raise NotFound("NOT_FOUND", NOT_FOUND_MESSAGE)
    return rows


def _decision_view(d: dict, dispatch: dict | None = None) -> dict:
    reason = (SKIP_REASONS.get(d["reason_code"]) if d["decision"] == SKIPPED
              else ANSWER_REASONS.get(d["reason_code"]))
    handoff = d.get("handoff") or None
    view = {"message_id": d["inbound_message_id"], "decision": d["decision"],
            "decision_label": DECISION_LABELS[d["decision"]], "reason_code": d["reason_code"],
            "reason": reason or (handoff or {}).get("reason"), "proposed_text": d["proposed_text"],
            "data_used": d["data_used"], "handoff": handoff, "rules_version": d["rules_version"],
            "created_at": _iso(d["created_at"]), "mode": d.get("mode", MODE_SIMULATE),
            "label": SIMULATION_LABEL, "sent": False, "delivery": None}
    if view["mode"] == MODE_SEND:
        status = (dispatch or {}).get("status")
        view["label"] = SEND_LABELS.get(status, NO_SEND_LABEL) if status else NO_SEND_LABEL
        view["sent"] = status == "sent"
        view["delivery"] = ({"status": status, "sent_at": _iso(dispatch.get("sent_at")),
                             "suppressed_reason": dispatch.get("suppressed_reason"),
                             "error_code": dispatch.get("error_code"), "task_id": dispatch.get("task_id")}
                            if dispatch else None)
    return view


def conversation_state(ctx, sender_phone: str) -> dict:
    agency_id = ctx.require_agency()
    with core_cursor() as (_, cur):
        _require_installed(cur)
        rows = _visible_conversation(cur, ctx, sender_phone)
        decisions = repository.decisions_for_messages(cur, agency_id, [r["id"] for r in rows])
        dispatches = repository.dispatches_for_decisions(cur, agency_id, [d["id"] for d in decisions])
        pause = repository.open_pause(cur, agency_id, sender_phone)
        conf = repository.settings(cur, agency_id)
        ready = repository.send_installed(cur)
    return {"sender_phone": sender_phone, "settings": _settings_view(conf, ctx, send_ready=ready),
            "paused": pause is not None, "paused_at": _iso((pause or {}).get("paused_at")),
            "decisions": [_decision_view(d, dispatches.get(d["id"])) for d in decisions]}


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
