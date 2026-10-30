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
       concorrenti sullo stesso messaggio, una sola lo prende
    4. invio fuori da ogni transazione
    5. `finalize_sent` (accettato, con il wamid) / `finalize_failed` (rifiuto
       certo) / `finalize_indeterminate` (esito incerto: MAI ritentato da solo)

La consegna e la lettura arrivano dopo, come stati del webhook (4A) collegati
per wamid: il registro dice «accettato», la conversazione dice anche
«consegnato», «letto» o «non consegnato».

STOP: rinuncia al marketing su WhatsApp, registrata dalla ricezione (4A).
Una risposta di assistenza nella finestra resta consentita, con l'avviso;
nessuna risposta del cliente vale mai come consenso marketing.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from communication import service as communication
from core.database import core_cursor
from inbound_messages import repository as inbound_repository
from inbound_messages import routing
from operator_auth.permissions import sees_all_agency_records

from . import transport as transport_module
from .enums import (
    CONFIRM_REQUIRED_MESSAGE,
    CREDENTIALS_MISSING_MESSAGE,
    DELIVERY_LABELS,
    EMPTY_TEXT_MESSAGE,
    NO_CONTACT_MESSAGE,
    NOT_FOUND_MESSAGE,
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
    from consent import repository as consent_repository

    optout = consent_repository.latest_platform_optout(cur, CHANNEL, sender_phone)
    blocks = []
    if not transport_module.enabled():
        blocks.append(("OUTBOUND_DISABLED", OUTBOUND_DISABLED_MESSAGE))
    elif not transport_module.credentials_present():
        blocks.append(("CREDENTIALS_MISSING", CREDENTIALS_MISSING_MESSAGE))
    if agencies != {agency_id}:
        blocks.append(("SENDER_NOT_CONFIGURED", SENDER_NOT_CONFIGURED_MESSAGE))
    if last["contact_id"] is None:
        blocks.append(("NO_CONTACT", NO_CONTACT_MESSAGE))
    if not window_open:
        blocks.append(("WINDOW_CLOSED", WINDOW_CLOSED_MESSAGE))
    return {"last": last, "closes_at": closes_at, "window_open": window_open, "optout": optout, "blocks": blocks}


def _replies(cur, ctx, conv: dict, sender_phone: str) -> list[dict]:
    contact_id = conv["last"]["contact_id"]
    if contact_id is None:
        return []
    rows = [m for m in communication.list_for_contact(ctx, contact_id, limit=REPLIES_LIMIT, cur=cur)
            if m["channel"] == CHANNEL and m["reason_code"] == "operator_reply"
            and m["destination_snapshot"] == sender_phone]
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
            "operator": names.get(m.get("actor_user_id")), "created_at": _iso(m["created_at"]),
            "sent_at": _iso(m.get("sent_at")),
        })
    return out


def reply_state(ctx, sender_phone: str) -> dict:
    with core_cursor() as (_, cur):
        conv = _conversation(cur, ctx, sender_phone)
        replies = _replies(cur, ctx, conv, sender_phone)
    optout = conv["optout"]
    return {
        "sender_phone": sender_phone,
        "can_reply": not conv["blocks"],
        "blocks": [{"code": c, "message": m} for c, m in conv["blocks"]],
        "window": {"open": conv["window_open"], "last_customer_message_at": _iso(conv["last"]["customer_at"]),
                   "closes_at": _iso(conv["closes_at"]), "hours": WINDOW_HOURS},
        "stop": ({"requested": True, "at": _iso(optout["opted_out_at"]), "notice": STOP_NOTICE} if optout
                 else {"requested": False}),
        "text_max": TEXT_MAX,
        "has_uncertain_reply": any(r["status"] == "indeterminate" for r in replies),
        "replies": replies,
    }


def send_reply(ctx, sender_phone: str, text: str, client_request_id: str, confirmed: bool) -> dict:
    agency_id = ctx.require_agency()
    text = (text or "").strip()
    if not text:
        raise Blocked("EMPTY_TEXT", EMPTY_TEXT_MESSAGE)
    if not confirmed:
        raise Blocked("CONFIRM_REQUIRED", CONFIRM_REQUIRED_MESSAGE)
    with core_cursor() as (_, cur):
        conv = _conversation(cur, ctx, sender_phone)
    if conv["blocks"]:
        code, message = conv["blocks"][0]
        raise (Unavailable if code in ("OUTBOUND_DISABLED", "CREDENTIALS_MISSING") else Blocked)(code, message)
    transport = transport_module.current()
    if transport is None:
        raise Unavailable("OUTBOUND_DISABLED", OUTBOUND_DISABLED_MESSAGE)
    last = conv["last"]

    queued = communication.enqueue(
        ctx, contact_id=last["contact_id"], channel=CHANNEL, communication_type="service", mode="manual",
        reason_code="operator_reply", rendered_body=text, destination_snapshot=sender_phone,
        idempotency_key=f"wa_reply:{agency_id}:{client_request_id}", property_id=last["property_id"],
        metadata={"service_basis": "customer_initiated_whatsapp", "inbound_message_id": last["id"],
                  "phone_number_id": last["phone_number_id"], "window_closes_at": _iso(conv["closes_at"])})
    message = queued["message"]
    if queued["created"]:
        claimed = communication.claim_message(ctx, message["id"], provider=PROVIDER_LABEL, channel=CHANNEL)
        if claimed is not None:
            token = claimed["message"]["claim_token"]
            try:
                result = transport.send(last["phone_number_id"], sender_phone, text)
            except Exception as exc:          # un trasporto che solleva: esito incerto, mai un reinvio
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
    state = reply_state(ctx, sender_phone)
    state["reply"] = next((r for r in state["replies"] if r["id"] == message["id"]), None)
    state["created"] = queued["created"]
    return state
