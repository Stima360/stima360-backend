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

WHATSAPP COEXISTENCE: il filtro si puo' fissare PER CASELLA con
`WHATSAPP_INBOUND_ALLOWED_SENDERS__<phone_number_id>` (stessa sintassi), che
vale al posto di quello generale per i cambiamenti di quel numero. Cosi' il
numero personale collegato puo' accettare tutti (`*`) mentre il numero 1 resta
limitato ai mittenti di prova. Il filtro conta il numero del CLIENTE anche per
le eco dall'app (`to`), lo storico (l'id del thread) e la rubrica
(`phone_number`); gli altri campi, con un elenco, cadono come prima.
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


def allowed_for(phone_number_id, default):
    """Il filtro di QUELLA casella, se configurato; altrimenti quello generale."""
    number = re.sub(r"\D", "", str(phone_number_id or ""))
    if number:
        own = os.getenv(f"{ALLOWED_SENDERS_ENV}__{number}")
        if own is not None and own.strip():
            return allowed_senders(own)
    return default


def _per_inbox_overrides() -> bool:
    return any(k.startswith(f"{ALLOWED_SENDERS_ENV}__") and (v or "").strip() for k, v in os.environ.items())


def _filter_coexistence(field: str, value: dict, allowed) -> tuple[dict | None, int]:
    """(valore ridotto o None se vuoto, elementi scartati) per i campi Coexistence."""
    dropped = 0
    value = dict(value)
    if field == "smb_message_echoes":
        items = value.get("message_echoes") or []
        kept = [m for m in items if isinstance(m, dict) and _digits(m.get("to")) in allowed]
        dropped += len(items) - len(kept)
        value["message_echoes"] = kept
        return (value if kept else None), dropped
    if field == "history":
        blocks = []
        for block in value.get("history") or []:
            if not isinstance(block, dict):
                continue
            threads = block.get("threads") or []
            kept = [t for t in threads if isinstance(t, dict) and _digits(t.get("id")) in allowed]
            dropped += sum(len(t.get("messages") or []) for t in threads
                           if isinstance(t, dict) and t not in kept)
            # L'avanzamento (senza thread) resta: dice a che punto e' lo storico.
            blocks.append({**block, "threads": kept})
        value["history"] = blocks
        return (value if blocks else None), dropped
    if field == "smb_app_state_sync":
        items = value.get("state_sync") or []
        kept = [i for i in items if isinstance(i, dict) and isinstance(i.get("contact"), dict)
                and _digits(i["contact"].get("phone_number")) in allowed]
        dropped += len(items) - len(kept)
        value["state_sync"] = kept
        return (value if kept else None), dropped
    return None, 1


def filter_payload(payload: dict, allowed) -> tuple[dict, int]:
    """(payload ridotto, elementi scartati). Con `allowed` None o `*` il
    payload resta com'e'. Altrimenti restano solo messaggi (`from`) e stati
    (`recipient_id`) dei numeri ammessi, con i soli profili di quei numeri; i
    cambiamenti diversi da `messages` e quelli rimasti vuoti cadono."""
    if (allowed is None or allowed == ALL_SENDERS) and not _per_inbox_overrides():
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
            metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
            inbox_allowed = allowed_for(metadata.get("phone_number_id"), allowed)
            if inbox_allowed is None or inbox_allowed == ALL_SENDERS:
                changes.append(change)
                continue
            if field in ("smb_message_echoes", "history", "smb_app_state_sync"):
                reduced, lost = _filter_coexistence(field, value, inbox_allowed)
                dropped += lost
                if reduced is not None:
                    changes.append({**change, "value": reduced})
                continue
            if field != "messages":
                dropped += 1
                continue
            allowed_here = inbox_allowed
            messages = [m for m in value.get("messages") or []
                        if isinstance(m, dict) and _digits(m.get("from")) in allowed_here]
            statuses = [s for s in value.get("statuses") or []
                        if isinstance(s, dict) and _digits(s.get("recipient_id")) in allowed_here]
            dropped += len(value.get("messages") or []) - len(messages)
            dropped += len(value.get("statuses") or []) - len(statuses)
            if not messages and not statuses:
                continue
            value = dict(value)
            value["messages"], value["statuses"] = messages, statuses
            value["contacts"] = [c for c in value.get("contacts") or []
                                 if isinstance(c, dict) and _digits(c.get("wa_id")) in allowed_here]
            value.pop("errors", None)
            changes.append({**change, "value": value})
        if changes:
            entries.append({**entry, "changes": changes})
    out["entry"] = entries
    return out, dropped
