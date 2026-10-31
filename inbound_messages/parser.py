"""BLOCCO-4A - lettura del payload del webhook Meta, senza effetti.

Tutti gli `entry`, tutti i `changes`, tutti i messaggi e tutti gli stati: mai
solo il primo. Tre categorie distinte: messaggi, aggiornamenti di consegna,
altri eventi. Per gli allegati si conservano tipo e metadati minimi: nessun
download, nessun URL aperto. Un tipo non testuale non diventa mai testo.
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
