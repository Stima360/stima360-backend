"""BLOCCO-4B - risposta WhatsApp manuale a una conversazione ricevuta (4A).

Riusa il registro delle comunicazioni (`communication.service`): la risposta
e' un messaggio `whatsapp`, `service`, `manual`, `operator_reply`, con
operatore, testo, data e stato scritti dal server. Destinatario e numero
mittente vengono dalla conversazione e dalla configurazione del server
(`WHATSAPP_INBOUND_ROUTES`), mai dal browser.

Ordine di un invio, per non mandare mai due volte la stessa risposta:

    1. controlli: agenzia, visibilita' della conversazione, contatto collegato,
       numero mittente configurato per l'agenzia, finestra di 24 ore,
       invio attivo sull'ambiente
    2. `enqueue` con chiave di idempotenza dalla richiesta del browser: un
       doppio clic o un reinvio della stessa richiesta trovano il messaggio
       gia' registrato e NON lo rimandano
    3. `claim_message` di QUEL messaggio (mai gli altri in coda): due richieste
       concorrenti sullo stesso messaggio, una sola lo prende. E' l'unica via:
       il claim generico (`claim_due`) esclude `whatsapp`/`operator_reply`
    4. di nuovo tutti i controlli, con l'ora e i dati di adesso, compreso
       l'operatore (ancora attivo e autorizzato nell'agenzia): un blocco chiude
       il messaggio come soppresso senza chiamare Meta
    5. invio fuori da ogni transazione
    6. `finalize_sent` (accettato, con il wamid) / `finalize_failed` (rifiuto
       certo) / `finalize_indeterminate` (esito incerto: MAI ritentato da solo)

Ripresa dopo un'interruzione: una risposta ancora `queued` e mai presa in
carico (`attempt_count = 0`) riprende i passi 3-6 con la stessa richiesta o
con «Riprendi invio», solo per chi l'ha scritta. Una risposta `sending` o
`indeterminate` non riparte mai: il processo puo' essersi fermato dopo la
chiamata a Meta.

La consegna e la lettura arrivano dopo, come stati del webhook (4A) collegati
per wamid: il registro dice «accettato», la conversazione dice anche
«consegnato», «letto» o «non consegnato».

STOP: rinuncia al marketing su WhatsApp, registrata dalla ricezione (4A).
Una risposta di assistenza nella finestra resta consentita, con l'avviso;
nessuna risposta del cliente vale mai come consenso marketing.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from communication import service as communication
from consent import do_not_contact
from core.database import core_cursor
from inbound_messages import repository as inbound_repository
from inbound_messages import routing
from inbound_messages.service import is_stop_request
from operator_auth.permissions import sees_all_agency_records

from . import repository
from . import transport as transport_module
from .enums import (
    CONFIRM_REQUIRED_MESSAGE,
    CREDENTIALS_MISSING_MESSAGE,
    DO_NOT_CONTACT_MESSAGE,
    DELIVERY_LABELS,
    EMPTY_TEXT_MESSAGE,
    GRAPH_VERSION_MISSING_MESSAGE,
    NO_CONTACT_MESSAGE,
    NOT_FOUND_MESSAGE,
    NOT_RESUMABLE_MESSAGE,
    OUTBOUND_DISABLED_MESSAGE,
    PROVIDER_LABEL,
    SENDER_NOT_CONFIGURED_MESSAGE,
    STATUS_LABELS,
    STOP_NOTICE,
    TEXT_MAX,
    WINDOW_CLOSED_MESSAGE,
    WINDOW_HOURS,
    WINDOW_SAFETY_MINUTES,
)
from .transport import SendResult

CHANNEL = "whatsapp"
REASON = "operator_reply"
REPLIES_LIMIT = 100


class ReplyError(Exception):
    status = 400

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class NotFound(ReplyError):
    status = 404


class Blocked(ReplyError):
    status = 409


class Unavailable(ReplyError):
    status = 503


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _agent_scope(ctx) -> int | None:
    return None if sees_all_agency_records(ctx.role, ctx.is_platform_admin) else ctx.user_id


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _conversation(cur, ctx, sender_phone: str) -> dict:
    """Lo stato della conversazione per rispondere: tutto ricavato dal server."""
    agency_id = ctx.require_agency()
    if not inbound_repository.installed(cur):
        raise NotFound("NOT_FOUND", NOT_FOUND_MESSAGE)
    last = inbound_repository.last_inbound(cur, agency_id, sender_phone, agent_user_id=_agent_scope(ctx))
    if last is None:
        raise NotFound("NOT_FOUND", NOT_FOUND_MESSAGE)
    agencies = routing.configured_routes().get(last["phone_number_id"] or "")
    customer_at = last["customer_at"]
    closes_at = customer_at + timedelta(hours=WINDOW_HOURS)
    window_open = _now() < closes_at - timedelta(minutes=WINDOW_SAFETY_MINUTES)
    # Lo STOP letto dalla conversazione di QUESTA agenzia (mai dal registro di
    # piattaforma, che vale per tutte): un'informazione, non un permesso.
    stop_at = next((m["provider_timestamp"] or m["received_at"]
                    for m in inbound_repository.conversation_messages(cur, agency_id, sender_phone,
                                                                     agent_user_id=_agent_scope(ctx), limit=REPLIES_LIMIT)
                    if is_stop_request(SimpleNamespace(message_type=m["message_type"], text_body=m["text_body"]))), None)
    blocks = []
    if not transport_module.enabled():
        blocks.append(("OUTBOUND_DISABLED", OUTBOUND_DISABLED_MESSAGE))
    elif not transport_module.credentials_present():
        blocks.append(("CREDENTIALS_MISSING", CREDENTIALS_MISSING_MESSAGE))
    elif not transport_module.graph_version():
        blocks.append(("GRAPH_VERSION_MISSING", GRAPH_VERSION_MISSING_MESSAGE))
    if agencies != {agency_id}:
        blocks.append(("SENDER_NOT_CONFIGURED", SENDER_NOT_CONFIGURED_MESSAGE))
    if last["contact_id"] is None:
        blocks.append(("NO_CONTACT", NO_CONTACT_MESSAGE))
    elif do_not_contact.active(cur, agency_id, last["contact_id"]):
        # BLOCCO-6: divieto totale di contatto, anche per una risposta di servizio
        blocks.append(("DO_NOT_CONTACT", DO_NOT_CONTACT_MESSAGE))
    if not window_open:
        blocks.append(("WINDOW_CLOSED", WINDOW_CLOSED_MESSAGE))
    return {"last": last, "closes_at": closes_at, "window_open": window_open, "stop_at": stop_at, "blocks": blocks}


def _ledger_rows(cur, ctx, conv: dict, sender_phone: str) -> list[dict]:
    contact_id = conv["last"]["contact_id"]
    if contact_id is None:
        return []
    return [m for m in communication.list_for_contact(ctx, contact_id, limit=REPLIES_LIMIT, cur=cur)
            if m["channel"] == CHANNEL and m["reason_code"] == REASON
            and m["destination_snapshot"] == sender_phone]


def _resumable(ctx, sender_phone: str, message: dict) -> bool:
    """Una risposta di questa conversazione, scritta da chi chiede, ancora in
    coda e mai presa in carico: e' l'unico caso in cui puo' (ri)partire."""
    return (message["channel"] == CHANNEL and message["reason_code"] == REASON
            and message["destination_snapshot"] == sender_phone
            and message["status"] == "queued" and message["attempt_count"] == 0
            and ctx.user_id is not None and message.get("actor_user_id") == ctx.user_id)


def _replies(cur, ctx, conv: dict, sender_phone: str) -> list[dict]:
    rows = _ledger_rows(cur, ctx, conv, sender_phone)
    deliveries = inbound_repository.delivery_statuses(cur, [m["provider_message_id"] for m in rows
                                                            if m.get("provider_message_id")])
    names = inbound_repository.operator_names(cur, sorted({m["actor_user_id"] for m in rows if m.get("actor_user_id")}))
    out = []
    for m in rows:
        d = deliveries.get(m.get("provider_message_id"))
        out.append({
            "id": m["id"], "text": m["rendered_body"], "status": m["status"],
            "status_label": STATUS_LABELS.get(m["status"], m["status"]),
            "delivery": ({"status": d["status"], "label": DELIVERY_LABELS.get(d["status"], d["status"]),
                          "at": _iso(d["provider_timestamp"])} if d else None),
            "error_code": m.get("error_code"), "error_detail": m.get("error_detail"),
            "suppressed_reason": m.get("suppressed_reason"),
            "operator": names.get(m.get("actor_user_id")), "created_at": _iso(m["created_at"]),
            "sent_at": _iso(m.get("sent_at")),
            "can_resume": not conv["blocks"] and _resumable(ctx, sender_phone, m),
        })
    return out


def _send_time_block(ctx, sender_phone: str, enqueued_from: dict) -> str | None:
    """Il motivo per NON chiamare il trasporto adesso, o None. Ricalcolato con
    l'ora attuale e i dati attuali, non con quelli dell'accodamento."""
    try:
        with core_cursor() as (_, cur):
            if not repository.operator_still_authorized(cur, ctx):
                return "operator_not_authorized"
            conv = _conversation(cur, ctx, sender_phone)
    except NotFound:
        return "conversation_not_visible"
    if conv["blocks"]:
        return conv["blocks"][0][0].lower()
    if conv["last"]["contact_id"] != enqueued_from["contact_id"]:
        return "contact_changed"
    if conv["last"]["phone_number_id"] != enqueued_from["phone_number_id"]:
        return "sender_changed"
    return None


def reply_state(ctx, sender_phone: str) -> dict:
    with core_cursor() as (_, cur):
        conv = _conversation(cur, ctx, sender_phone)
        replies = _replies(cur, ctx, conv, sender_phone)
    return {
        "sender_phone": sender_phone,
        "can_reply": not conv["blocks"],
        "blocks": [{"code": c, "message": m} for c, m in conv["blocks"]],
        "window": {"open": conv["window_open"], "last_customer_message_at": _iso(conv["last"]["customer_at"]),
                   "closes_at": _iso(conv["closes_at"]), "hours": WINDOW_HOURS},
        "stop": ({"requested": True, "at": _iso(conv["stop_at"]), "notice": STOP_NOTICE} if conv["stop_at"]
                 else {"requested": False}),
        "text_max": TEXT_MAX,
        # `sending` fuori da una richiesta in corso: il processo puo' essersi
        # fermato dopo la chiamata a Meta, quindi l'esito e' ignoto quanto
        # quello di `indeterminate`.
        "has_uncertain_reply": any(r["status"] in ("indeterminate", "sending") for r in replies),
        "replies": replies,
    }


def _raise_block(conv: dict) -> None:
    if conv["blocks"]:
        code, message = conv["blocks"][0]
        raise (Unavailable if code in ("OUTBOUND_DISABLED", "CREDENTIALS_MISSING") else Blocked)(code, message)
    if transport_module.current() is None:
        raise Unavailable("OUTBOUND_DISABLED", OUTBOUND_DISABLED_MESSAGE)


def _metadata(message: dict) -> dict:
    value = message.get("metadata")
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else {}


def _deliver(ctx, sender_phone: str, message: dict) -> None:
    """I passi 3-6 per UNA risposta in coda e mai presa in carico."""
    enqueued_from = {"contact_id": message["contact_id"], "phone_number_id": _metadata(message).get("phone_number_id")}
    claimed = communication.claim_message(ctx, message["id"], provider=PROVIDER_LABEL, channel=CHANNEL)
    if claimed is None:                       # un'altra richiesta l'ha gia' presa: nessun secondo invio
        return
    token = claimed["message"]["claim_token"]
    # Tutto di nuovo, DOPO il claim e PRIMA di Meta: operatore, finestra, numero
    # mittente e agenzia, visibilita' e contatto, invio attivo. Un blocco
    # chiude il messaggio come soppresso, senza chiamare il trasporto.
    reason = _send_time_block(ctx, sender_phone, enqueued_from)
    transport = transport_module.current() if reason is None else None
    if reason is None and transport is None:
        reason = "outbound_disabled"
    if reason is not None:
        communication.finalize_suppressed(ctx, message["id"], token, reason=reason)
        return
    try:
        result = transport.send(enqueued_from["phone_number_id"], sender_phone, message["rendered_body"])
    except Exception as exc:      # un trasporto che solleva: esito incerto, mai un reinvio
        result = SendResult("indeterminate", error_code="outcome_unknown", error_detail=type(exc).__name__)
    if result.outcome == "accepted":
        communication.finalize_sent(ctx, message["id"], token, provider_message_id=result.provider_message_id)
    elif result.outcome == "rejected":
        communication.finalize_failed(ctx, message["id"], token, error_code=result.error_code,
                                      error_detail=result.error_detail)
    else:
        communication.finalize_indeterminate(ctx, message["id"], token,
                                             error_code=result.error_code or "outcome_unknown",
                                             error_detail=result.error_detail)


def _state_with(ctx, sender_phone: str, message_id: int) -> dict:
    state = reply_state(ctx, sender_phone)
    state["reply"] = next((r for r in state["replies"] if r["id"] == message_id), None)
    return state


def send_reply(ctx, sender_phone: str, text: str, client_request_id: str, confirmed: bool) -> dict:
    agency_id = ctx.require_agency()
    text = (text or "").strip()
    if not text:
        raise Blocked("EMPTY_TEXT", EMPTY_TEXT_MESSAGE)
    if not confirmed:
        raise Blocked("CONFIRM_REQUIRED", CONFIRM_REQUIRED_MESSAGE)
    with core_cursor() as (_, cur):
        conv = _conversation(cur, ctx, sender_phone)
    _raise_block(conv)
    last = conv["last"]

    queued = communication.enqueue(
        ctx, contact_id=last["contact_id"], channel=CHANNEL, communication_type="service", mode="manual",
        reason_code=REASON, rendered_body=text, destination_snapshot=sender_phone,
        idempotency_key=f"wa_reply:{agency_id}:{client_request_id}", property_id=last["property_id"],
        metadata={"service_basis": "customer_initiated_whatsapp", "inbound_message_id": last["id"],
                  "phone_number_id": last["phone_number_id"], "window_closes_at": _iso(conv["closes_at"])})
    message = queued["message"]
    # Nuova, o la stessa richiesta rimasta in coda dopo un'interruzione: stesso
    # percorso. `sending`, `indeterminate` e ogni esito finale non ripartono.
    if _resumable(ctx, sender_phone, message):
        _deliver(ctx, sender_phone, message)
    state = _state_with(ctx, sender_phone, message["id"])
    state["created"] = queued["created"]
    return state


def resume_reply(ctx, sender_phone: str, reply_id: int, confirmed: bool) -> dict:
    """«Riprendi invio»: la stessa risposta, ancora in coda e mai presa in carico."""
    if not confirmed:
        raise Blocked("CONFIRM_REQUIRED", CONFIRM_REQUIRED_MESSAGE)
    with core_cursor() as (_, cur):
        conv = _conversation(cur, ctx, sender_phone)
        message = next((m for m in _ledger_rows(cur, ctx, conv, sender_phone) if m["id"] == reply_id), None)
    if message is None:
        raise NotFound("NOT_FOUND", NOT_FOUND_MESSAGE)
    if not _resumable(ctx, sender_phone, message):
        raise Blocked("NOT_RESUMABLE", NOT_RESUMABLE_MESSAGE)
    _raise_block(conv)
    _deliver(ctx, sender_phone, message)
    state = _state_with(ctx, sender_phone, message["id"])
    state["created"] = False
    return state


# ---------------------------------------------------------------------------
# BLOCCO-6: risposte in coda che non possono piu' partire
# ---------------------------------------------------------------------------

#: Il motivo registrato sulla risposta chiusa: la finestra di 24 ore aperta dal
#: cliente e' finita mentre era ancora in coda, mai presa in carico.
WINDOW_EXPIRED_REASON = "window_expired"
EXPIRY_REASONS = ("operator_reply", "auto_reply")


def expire_closed_window_replies(agency_id: int, *, now=None, limit: int = 200) -> int:
    """Chiude come «non inviata» ogni risposta WhatsApp (manuale o automatica)
    dell'agenzia ancora in coda, MAI presa in carico, la cui finestra e' chiusa
    (con lo stesso margine di sicurezza dell'invio). Passa per il claim, cosi'
    una richiesta che la sta prendendo adesso vince e nulla parte due volte;
    `sending` e `indeterminate` non si toccano mai. Restituisce quante ne chiude."""
    from operator_auth.context import SystemAgencyContext

    now = now or _now()
    cutoff = now + timedelta(minutes=WINDOW_SAFETY_MINUTES)
    sys_ctx = SystemAgencyContext(agency_id=agency_id, origin="whatsapp_inbound")
    ids = communication.queued_unclaimed(sys_ctx, channel=CHANNEL, reason_codes=EXPIRY_REASONS,
                                         closes_before=cutoff, limit=limit)
    closed = 0
    for message_id in ids:
        claimed = communication.claim_message(sys_ctx, message_id, provider=PROVIDER_LABEL, channel=CHANNEL)
        if claimed is None:
            continue
        communication.finalize_suppressed(sys_ctx, message_id, claimed["message"]["claim_token"],
                                          reason=WINDOW_EXPIRED_REASON)
        closed += 1
    return closed
