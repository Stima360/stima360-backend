"""BLOCCO-4A - lettura del payload del webhook Meta, senza effetti.

Tutti gli `entry`, tutti i `changes`, tutti i messaggi e tutti gli stati: mai
solo il primo. Tre categorie distinte: messaggi, aggiornamenti di consegna,
altri eventi. Per gli allegati si conservano tipo e metadati minimi: nessun
download, nessun URL aperto. Un tipo non testuale non diventa mai testo.

WHATSAPP COEXISTENCE (fase C): con il numero dell'app WhatsApp Business
collegato alla Cloud API arrivano anche
  * `smb_message_echoes`  i messaggi scritti dall'app sul telefono (o da un
    dispositivo collegato): diventano messaggi `out`, origine `app_echo`;
  * `history`             lo storico condiviso dal titolare, a blocchi: ogni
    messaggio diventa `in` o `out` secondo chi l'ha scritto, origine `history`,
    con l'ora originale; l'avanzamento resta un evento senza dati personali;
  * `smb_app_state_sync`  la rubrica dell'app: aggiunte e rimozioni di contatti.
`sender_phone` e' SEMPRE il numero del cliente, in entrambe le direzioni.
Degli allegati si tiene anche l'identificativo opaco di Meta (`media_id`),
mai l'indirizzo di scaricamento: l'allegato si apre solo su richiesta di chi
vede la conversazione (whatsapp_coexistence.media).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .enums import META_TEXT_MAX, TEXT_MAX


class MalformedPayload(ValueError):
    pass


#: Direzione e origine (migrazione 132). Le ricezioni di sempre sono ('in', 'webhook').
DIRECTION_IN = "in"
DIRECTION_OUT = "out"
ORIGIN_WEBHOOK = "webhook"
ORIGIN_APP_ECHO = "app_echo"
ORIGIN_HISTORY = "history"

#: I campi del webhook che appartengono a Coexistence.
FIELD_ECHOES = "smb_message_echoes"
FIELD_HISTORY = "history"
FIELD_STATE_SYNC = "smb_app_state_sync"
COEXISTENCE_FIELDS = frozenset({FIELD_ECHOES, FIELD_HISTORY, FIELD_STATE_SYNC})


@dataclass
class InboundMessage:
    provider_message_id: str
    phone_number_id: str | None
    display_phone_number: str | None
    sender_phone: str
    sender_profile_name: str | None
    message_type: str
    text_body: str | None
    metadata: dict
    provider_timestamp: datetime | None
    direction: str = DIRECTION_IN
    origin: str = ORIGIN_WEBHOOK

    @property
    def coexistence(self) -> bool:
        return self.direction != DIRECTION_IN or self.origin != ORIGIN_WEBHOOK


@dataclass
class AppContactChange:
    """Una voce della rubrica dell'app WhatsApp Business (`smb_app_state_sync`)."""
    phone_number_id: str
    contact_phone: str
    full_name: str | None
    first_name: str | None
    action: str                          # 'add' | 'remove'
    changed_at: datetime | None


@dataclass
class WebhookEvent:
    event_kind: str                     # 'status' | 'other'
    dedupe_key: str
    phone_number_id: str | None
    provider_message_id: str | None = None
    status: str | None = None
    provider_timestamp: datetime | None = None
    details: dict = field(default_factory=dict)


@dataclass
class ParsedPayload:
    messages: list[InboundMessage] = field(default_factory=list)
    events: list[WebhookEvent] = field(default_factory=list)
    app_contacts: list[AppContactChange] = field(default_factory=list)

    @property
    def coexistence(self) -> bool:
        return bool(self.app_contacts) or any(m.coexistence for m in self.messages)


def _text(value, limit=META_TEXT_MAX) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] or None


def _timestamp(value) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(str(value)), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _digits(value) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if 6 <= len(digits) <= 15 else None


def _error_codes(errors) -> list[dict]:
    out = []
    for e in errors if isinstance(errors, list) else []:
        if isinstance(e, dict):
            out.append({"code": e.get("code"), "title": _text(e.get("title"), 200)})
    return out


def _metadata(msg: dict, msg_type: str) -> dict:
    """Metadati minimi per tipo: niente identificativi di download, niente URL."""
    body = msg.get(msg_type) if isinstance(msg.get(msg_type), dict) else {}
    meta: dict = {}
    if msg_type in ("image", "video", "audio", "document", "sticker"):
        for key in ("mime_type", "caption", "filename"):
            if _text(body.get(key)):
                meta[key] = _text(body.get(key))
        if body.get("voice") is True:
            meta["voice"] = True
        # COEXISTENCE: l'identificativo opaco dell'allegato presso Meta, per
        # aprirlo a richiesta. Mai l'URL (`url`), che e' temporaneo e firmato.
        media_id = _text(body.get("id"), 100)
        if media_id and re.fullmatch(r"[0-9A-Za-z_.:-]{1,100}", media_id):
            meta["media_id"] = media_id
    elif msg_type == "location":
        for key in ("name", "address"):
            if _text(body.get(key)):
                meta[key] = _text(body.get(key))
    elif msg_type == "button":
        if _text(body.get("text")):
            meta["label"] = _text(body.get("text"))
    elif msg_type == "interactive":
        reply = body.get("button_reply") or body.get("list_reply") or {}
        if isinstance(reply, dict) and _text(reply.get("title")):
            meta["label"] = _text(reply.get("title"))
    elif msg_type == "reaction":
        if _text(body.get("emoji")):
            meta["emoji"] = _text(body.get("emoji"))
    elif msg_type == "contacts":
        meta["count"] = len(msg.get("contacts") or [])
    context = msg.get("context")
    if isinstance(context, dict) and _text(context.get("id")):
        meta["reply_to"] = _text(context.get("id"), 200)
    if msg.get("errors"):
        meta["errors"] = _error_codes(msg.get("errors"))
    return meta


def _other(change: dict, phone_number_id: str | None, reason: str, extra: dict | None = None) -> WebhookEvent:
    canonical = json.dumps(change, sort_keys=True, separators=(",", ":"), default=str)
    return WebhookEvent(
        event_kind="other", dedupe_key="other:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        phone_number_id=phone_number_id, details={"reason": reason, **(extra or {})})


def parse(payload) -> ParsedPayload:
    if not isinstance(payload, dict) or not isinstance(payload.get("entry"), list):
        raise MalformedPayload("payload senza `entry`")
    out = ParsedPayload()
    for entry in payload["entry"]:
        changes = entry.get("changes") if isinstance(entry, dict) else None
        if not isinstance(changes, list):
            raise MalformedPayload("`entry` senza `changes`")
        for change in changes:
            if not isinstance(change, dict):
                raise MalformedPayload("`change` non valido")
            value = change.get("value") if isinstance(change.get("value"), dict) else {}
            metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
            phone_number_id = _text(metadata.get("phone_number_id"), 100)
            display = _text(metadata.get("display_phone_number"), 50)
            # Meta indica sempre `field`; senza, conta il contenuto (messaggi o stati).
            field_name = change.get("field")
            if field_name is None and (value.get("messages") or value.get("statuses")):
                field_name = "messages"
            if field_name == FIELD_ECHOES:
                _echoes(out, value, phone_number_id, display)
                continue
            if field_name == FIELD_HISTORY:
                _history(out, change, value, phone_number_id, display)
                continue
            if field_name == FIELD_STATE_SYNC:
                _state_sync(out, change, value, phone_number_id)
                continue
            if field_name != "messages":
                out.events.append(_other(change, phone_number_id, "field", {"field": _text(change.get("field"), 100)}))
                continue
            names = {}
            for c in value.get("contacts") or []:
                if isinstance(c, dict) and _digits(c.get("wa_id")):
                    names[_digits(c.get("wa_id"))] = _text((c.get("profile") or {}).get("name"), 200)
            for msg in value.get("messages") or []:
                provider_id = _text(msg.get("id"), 200) if isinstance(msg, dict) else None
                sender = _digits(msg.get("from")) if isinstance(msg, dict) else None
                msg_type = _text(msg.get("type"), 30) if isinstance(msg, dict) else None
                if not provider_id or not sender or not msg_type:
                    out.events.append(_other({"message": msg, "phone_number_id": phone_number_id},
                                             phone_number_id, "invalid_message"))
                    continue
                text_body = None
                if msg_type == "text":
                    text_body = _text((msg.get("text") or {}).get("body"), TEXT_MAX)
                out.messages.append(InboundMessage(
                    provider_message_id=provider_id, phone_number_id=phone_number_id or "",
                    display_phone_number=display, sender_phone=sender, sender_profile_name=names.get(sender),
                    message_type=msg_type, text_body=text_body, metadata=_metadata(msg, msg_type),
                    provider_timestamp=_timestamp(msg.get("timestamp"))))
            for st in value.get("statuses") or []:
                if not isinstance(st, dict) or not _text(st.get("id"), 200) or not _text(st.get("status"), 20):
                    out.events.append(_other({"status": st, "phone_number_id": phone_number_id},
                                             phone_number_id, "invalid_status"))
                    continue
                key = f"status:{_text(st.get('id'), 200)}:{_text(st.get('status'), 20)}:{st.get('timestamp')}"
                out.events.append(WebhookEvent(
                    event_kind="status", dedupe_key=key, phone_number_id=phone_number_id,
                    provider_message_id=_text(st.get("id"), 200), status=_text(st.get("status"), 20),
                    provider_timestamp=_timestamp(st.get("timestamp")),
                    details={"errors": _error_codes(st.get("errors"))} if st.get("errors") else {}))
            if value.get("errors"):
                out.events.append(_other(change, phone_number_id, "errors",
                                         {"errors": _error_codes(value.get("errors"))}))
    return out


# ---------------------------------------------------------------------------
# WHATSAPP COEXISTENCE
# ---------------------------------------------------------------------------

def _message_from(msg, *, customer: str | None, phone_number_id, display, direction, origin, profile=None,
                  extra_meta: dict | None = None) -> InboundMessage | None:
    """Un messaggio Coexistence, o None se manca qualcosa di essenziale."""
    if not isinstance(msg, dict):
        return None
    provider_id = _text(msg.get("id"), 200)
    msg_type = _text(msg.get("type"), 30)
    if not provider_id or not customer or not msg_type:
        return None
    text_body = _text((msg.get("text") or {}).get("body"), TEXT_MAX) if msg_type == "text" else None
    meta = _metadata(msg, msg_type)
    if extra_meta:
        meta.update(extra_meta)
    return InboundMessage(
        provider_message_id=provider_id, phone_number_id=phone_number_id or "", display_phone_number=display,
        sender_phone=customer, sender_profile_name=profile, message_type=msg_type, text_body=text_body,
        metadata=meta, provider_timestamp=_timestamp(msg.get("timestamp")), direction=direction, origin=origin)


def _echoes(out: ParsedPayload, value: dict, phone_number_id, display) -> None:
    """`smb_message_echoes`: un messaggio scritto dall'app. Il cliente e' `to`."""
    for msg in value.get("message_echoes") or []:
        m = _message_from(msg, customer=_digits(msg.get("to")) if isinstance(msg, dict) else None,
                          phone_number_id=phone_number_id, display=display,
                          direction=DIRECTION_OUT, origin=ORIGIN_APP_ECHO)
        if m is None:
            out.events.append(_other({"echo": msg, "phone_number_id": phone_number_id},
                                     phone_number_id, "invalid_echo"))
        else:
            out.messages.append(m)


def _history(out: ParsedPayload, change: dict, value: dict, phone_number_id, display) -> None:
    """`history`: blocchi di conversazioni. Il cliente e' l'id del thread; il
    messaggio e' suo se `from` e' il suo numero, altrimenti dell'azienda."""
    for block in value.get("history") or []:
        if not isinstance(block, dict):
            continue
        meta = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
        progress = {"phase": meta.get("phase"), "chunk_order": meta.get("chunk_order"), "progress": meta.get("progress")}
        if block.get("errors"):
            # Lo storico non condiviso (rifiutato sul telefono o non disponibile).
            out.events.append(_other(change, phone_number_id, "history_errors",
                                     {"errors": _error_codes(block.get("errors")), **progress}))
        threads = block.get("threads") or []
        count = 0
        for thread in threads if isinstance(threads, list) else []:
            if not isinstance(thread, dict):
                continue
            customer = _digits(thread.get("id"))
            for msg in thread.get("messages") or []:
                sender = _digits(msg.get("from")) if isinstance(msg, dict) else None
                direction = DIRECTION_IN if sender and sender == customer else DIRECTION_OUT
                context = msg.get("history_context") if isinstance(msg, dict) else None
                extra = {}
                if isinstance(context, dict) and _text(context.get("status"), 20):
                    extra["history_status"] = _text(context.get("status"), 20).lower()
                m = _message_from(msg, customer=customer, phone_number_id=phone_number_id, display=display,
                                  direction=direction, origin=ORIGIN_HISTORY, extra_meta=extra)
                if m is None:
                    out.events.append(_other({"history_message": msg, "phone_number_id": phone_number_id},
                                             phone_number_id, "invalid_history_message"))
                else:
                    out.messages.append(m)
                    count += 1
        # L'avanzamento, senza alcun dato personale: fase, blocco, percentuale, quanti messaggi.
        canonical = json.dumps({"pn": phone_number_id, **progress, "n": count}, sort_keys=True, default=str)
        out.events.append(WebhookEvent(
            event_kind="other", phone_number_id=phone_number_id,
            dedupe_key="history:" + hashlib.sha256((canonical + json.dumps(block, sort_keys=True, default=str))
                                                   .encode("utf-8")).hexdigest(),
            details={"reason": "history_progress", **progress, "messages": count}))


def _state_sync(out: ParsedPayload, change: dict, value: dict, phone_number_id) -> None:
    """`smb_app_state_sync`: la rubrica dell'app. Solo i contatti; gli altri tipi restano eventi."""
    for item in value.get("state_sync") or []:
        if not isinstance(item, dict):
            continue
        contact = item.get("contact") if isinstance(item.get("contact"), dict) else None
        action = _text(item.get("action"), 10)
        phone = _digits(contact.get("phone_number")) if contact else None
        if item.get("type") != "contact" or not phone or action not in ("add", "remove") or not phone_number_id:
            out.events.append(_other({"state_sync": item, "phone_number_id": phone_number_id},
                                     phone_number_id, "state_sync_other"))
            continue
        meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        out.app_contacts.append(AppContactChange(
            phone_number_id=phone_number_id, contact_phone=phone,
            full_name=_text(contact.get("full_name"), 200), first_name=_text(contact.get("first_name"), 200),
            action=action, changed_at=_timestamp(meta.get("timestamp"))))
