"""Inoltro firmato dal ricevitore WhatsApp esistente, e filtro dei mittenti.

Il numero WhatsApp Business ha UN solo callback Meta, oggi sul servizio di
produzione. Quel ricevitore puo' inoltrare il corpo ricevuto a QUESTO webhook
(`POST /webhook/whatsapp`), che resta l'unico punto di ricezione del CRM: stesso
parser, stessa transazione, stessa deduplicazione sugli identificativi Meta.

Autenticazione dell'inoltro, alternativa alla firma Meta e mai insieme a essa:
- un segreto proprio dell'inoltro (`WHATSAPP_RELAY_SECRET`, almeno 32
  caratteri), cosi' l'App Secret di Meta non esce dalla produzione;
- `X-Stima360-Relay-Timestamp`: secondi Unix dell'inoltro, entro 5 minuti;
- `X-Stima360-Relay-Signature`: `sha256=` + HMAC-SHA256 di `<timestamp>.<corpo>`
  sul corpo ORIGINALE, confrontato a tempo costante.
Senza segreto configurato l'inoltro e' rifiutato (503); firma o ora non valide
sono rifiutate (403) prima di qualsiasi accesso al database.

Filtro dei mittenti (`WHATSAPP_INBOUND_ALLOWED_SENDERS`): numeri in formato
internazionale separati da virgola, oppure `*` per tutti. Con un elenco, il
payload e' ridotto ai soli messaggi e stati di consegna di quei numeri PRIMA del
parser: gli altri non vengono salvati ne' registrati. Un inoltro richiede che il
filtro sia configurato (anche solo `*`): un ambiente non riceve conversazioni
reali per una semplice dimenticanza. La consegna diretta da Meta, senza
variabile, resta quella del 4A (tutti i mittenti).
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import os
import re
import time

RELAY_SECRET_ENV = "WHATSAPP_RELAY_SECRET"
ALLOWED_SENDERS_ENV = "WHATSAPP_INBOUND_ALLOWED_SENDERS"
SIGNATURE_HEADER = "X-Stima360-Relay-Signature"
TIMESTAMP_HEADER = "X-Stima360-Relay-Timestamp"
MAX_SKEW_SECONDS = 300
MIN_SECRET_LENGTH = 32

ALL_SENDERS = "*"


class RelayRejected(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def signature(secret: str, timestamp: str, raw_body: bytes) -> str:
    mac = hmac.new(secret.encode("utf-8"), timestamp.encode("ascii") + b"." + raw_body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def is_relay(headers) -> bool:
    return headers.get(SIGNATURE_HEADER) is not None or headers.get(TIMESTAMP_HEADER) is not None


def verify(raw_body: bytes, headers, *, now: float | None = None) -> None:
    secret = os.getenv(RELAY_SECRET_ENV) or ""
    if len(secret) < MIN_SECRET_LENGTH:
        raise RelayRejected(503, "Relay verification unavailable")
    if headers.get("X-Hub-Signature-256") is not None:
        # Una sola autenticazione per consegna: mai una scelta fra due firme.
        raise RelayRejected(403, "Invalid webhook signature")
    timestamp = headers.get(TIMESTAMP_HEADER) or ""
    given = headers.get(SIGNATURE_HEADER) or ""
    if not re.fullmatch(r"\d{1,12}", timestamp) or not given:
        raise RelayRejected(403, "Invalid webhook signature")
    now = time.time() if now is None else now
    if abs(now - int(timestamp)) > MAX_SKEW_SECONDS:
        raise RelayRejected(403, "Invalid webhook signature")
    if not hmac.compare_digest(signature(secret, timestamp, raw_body), given):
        raise RelayRejected(403, "Invalid webhook signature")


def allowed_senders(raw: str | None = None):
    """None se la variabile manca; ALL_SENDERS con `*`; altrimenti l'insieme
    dei numeri (solo cifre, da 6 a 15). Un elenco senza voci valide vale come
    elenco vuoto: nessun mittente ammesso."""
    raw = os.getenv(ALLOWED_SENDERS_ENV) if raw is None else raw
    if raw is None or not raw.strip():
        return None
    if raw.strip() == ALL_SENDERS:
        return ALL_SENDERS
    out = set()
    for item in raw.split(","):
        digits = re.sub(r"\D", "", item)
        if 6 <= len(digits) <= 15:
            out.add(digits)
    return frozenset(out)


def _digits(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def filter_payload(payload: dict, allowed) -> tuple[dict, int]:
    """(payload ridotto, elementi scartati). Con `allowed` None o `*` il
    payload resta com'e'. Altrimenti restano solo messaggi (`from`) e stati
    (`recipient_id`) dei numeri ammessi, con i soli profili di quei numeri; i
    cambiamenti diversi da `messages` e quelli rimasti vuoti cadono."""
    if allowed is None or allowed == ALL_SENDERS:
        return payload, 0
    dropped = 0
    out = copy.deepcopy(payload)
    entries = []
    for entry in out.get("entry") or []:
        changes = []
        for change in entry.get("changes") or []:
            value = change.get("value") if isinstance(change.get("value"), dict) else {}
            field = change.get("field")
            if field is None and (value.get("messages") or value.get("statuses")):
                field = "messages"
            if field != "messages":
                dropped += 1
                continue
            messages = [m for m in value.get("messages") or []
                        if isinstance(m, dict) and _digits(m.get("from")) in allowed]
            statuses = [s for s in value.get("statuses") or []
                        if isinstance(s, dict) and _digits(s.get("recipient_id")) in allowed]
            dropped += len(value.get("messages") or []) - len(messages)
            dropped += len(value.get("statuses") or []) - len(statuses)
            if not messages and not statuses:
                continue
            value = dict(value)
            value["messages"], value["statuses"] = messages, statuses
            value["contacts"] = [c for c in value.get("contacts") or []
                                 if isinstance(c, dict) and _digits(c.get("wa_id")) in allowed]
            value.pop("errors", None)
            changes.append({**change, "value": value})
        if changes:
            entries.append({**entry, "changes": changes})
    out["entry"] = entries
    return out, dropped
