"""Inoltro best-effort dal ricevitore WhatsApp di PRODUZIONE al webhook del CRM.

NON fa parte dell'applicazione di questo branch: e' il modulo da copiare nel
servizio di produzione (`main`, storia separata) SOLO dopo un'autorizzazione
esplicita, secondo `integrations/whatsapp_relay/ATTIVAZIONE.md`. Dipende solo
dalla libreria standard e da `requests`, gia' presente in produzione.

Regole:
- spento finche' mancano URL, segreto o elenco dei mittenti: nessuna chiamata;
- mai sul percorso della risposta a Meta: parte DOPO la risposta (task in
  background di FastAPI), dopo che il ricevitore ha gia' salvato il messaggio
  nella sua tabella storica; un errore qui non cambia risposta ne' salvataggio;
- inoltra solo i mittenti dell'elenco (`*` = tutti): con un elenco, il corpo
  inoltrato contiene SOLO i loro messaggi e stati; gli altri non escono;
- firma propria (non l'App Secret di Meta): HMAC-SHA256 di `<ts>.<corpo>`;
- tentativi limitati, timeout brevi, nessun tentativo su un rifiuto definitivo
  (4xx diverso da 429): il CRM deduplica sugli identificativi Meta, quindi un
  tentativo ripetuto non crea doppioni;
- i log non contengono numeri, nomi, testi ne' segreti.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

log = logging.getLogger("whatsapp_relay")

URL_ENV = "WHATSAPP_RELAY_URL"
SECRET_ENV = "WHATSAPP_RELAY_SECRET"
SENDERS_ENV = "WHATSAPP_RELAY_SENDERS"
SIGNATURE_HEADER = "X-Stima360-Relay-Signature"
TIMESTAMP_HEADER = "X-Stima360-Relay-Timestamp"
MIN_SECRET_LENGTH = 32
ATTEMPTS = 3
TIMEOUT = (3.0, 5.0)          # connessione, lettura
BACKOFF = (1.0, 3.0)          # attese fra i tentativi
ALL = "*"


@dataclass(frozen=True)
class Config:
    url: str
    secret: str = field(repr=False)
    senders: object            # ALL oppure frozenset di numeri


def _digits(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def config(env=None) -> Config | None:
    """None se l'inoltro e' spento o configurato male (mai un'eccezione)."""
    env = os.environ if env is None else env
    url = (env.get(URL_ENV) or "").strip()
    secret = env.get(SECRET_ENV) or ""
    raw = (env.get(SENDERS_ENV) or "").strip()
    if not url or len(secret) < MIN_SECRET_LENGTH or not raw:
        return None
    parsed = urlparse(url)
    local = parsed.hostname in ("127.0.0.1", "localhost")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
        return None
    if raw == ALL:
        return Config(url, secret, ALL)
    senders = frozenset(d for d in (_digits(x) for x in raw.split(",")) if 6 <= len(d) <= 15)
    return Config(url, secret, senders) if senders else None


def select(raw_body: bytes, senders) -> bytes | None:
    """Il corpo da inoltrare, o None se non c'e' nulla da inoltrare.
    Con `*` il corpo originale; con un elenco, solo messaggi (`from`), stati
    (`recipient_id`) e profili di quei numeri; i cambiamenti diversi da
    `messages` restano in produzione."""
    if senders == ALL:
        return raw_body
    try:
        payload = json.loads(raw_body)
    except ValueError:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("entry"), list):
        return None
    out = copy.deepcopy(payload)
    entries = []
    for entry in out["entry"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("changes"), list):
            continue
        changes = []
        for change in entry["changes"]:
            if not isinstance(change, dict) or not isinstance(change.get("value"), dict):
                continue
            value = change["value"]
            if change.get("field", "messages") != "messages":
                continue
            messages = [m for m in value.get("messages") or [] if isinstance(m, dict) and _digits(m.get("from")) in senders]
            statuses = [s for s in value.get("statuses") or []
                        if isinstance(s, dict) and _digits(s.get("recipient_id")) in senders]
            if not messages and not statuses:
                continue
            value = {k: v for k, v in value.items() if k not in ("messages", "statuses", "contacts", "errors")}
            value["messages"], value["statuses"] = messages, statuses
            value["contacts"] = [c for c in change["value"].get("contacts") or []
                                 if isinstance(c, dict) and _digits(c.get("wa_id")) in senders]
            changes.append({**change, "value": value})
        if changes:
            entries.append({**entry, "changes": changes})
    if not entries:
        return None
    out["entry"] = entries
    return json.dumps(out, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def signature(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode("utf-8"), timestamp.encode("ascii") + b"." + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def deliver(cfg: Config, body: bytes, *, post=None, sleep=time.sleep, now=time.time, timeout=TIMEOUT) -> str:
    """Esito: 'delivered' | 'rejected' | 'gave_up'. Ogni tentativo ha la sua
    firma con l'ora del momento. Non solleva eccezioni."""
    if post is None:
        import requests

        post = requests.post
    for attempt in range(1, ATTEMPTS + 1):
        stamp = str(int(now()))
        headers = {"Content-Type": "application/json", TIMESTAMP_HEADER: stamp,
                   SIGNATURE_HEADER: signature(cfg.secret, stamp, body)}
        try:
            r = post(cfg.url, data=body, headers=headers, timeout=timeout)
            code = getattr(r, "status_code", 0)
        except Exception as exc:  # rete, timeout, DNS: si ritenta
            log.warning("whatsapp_relay attempt=%s error_type=%s", attempt, type(exc).__name__)
            code = None
        if code is not None and 200 <= code < 300:
            log.info("whatsapp_relay delivered attempt=%s", attempt)
            return "delivered"
        if code is not None and 400 <= code < 500 and code != 429:
            log.error("whatsapp_relay rejected status=%s", code)
            return "rejected"
        if code is not None:
            log.warning("whatsapp_relay attempt=%s status=%s", attempt, code)
        if attempt < ATTEMPTS:
            sleep(BACKOFF[min(attempt - 1, len(BACKOFF) - 1)])
    log.error("whatsapp_relay gave_up attempts=%s", ATTEMPTS)
    return "gave_up"


def _deliver_safely(cfg: Config, body: bytes) -> None:
    try:
        deliver(cfg, body)
    except Exception as exc:  # pragma: no cover - deliver non solleva; difesa ulteriore
        log.error("whatsapp_relay failed error_type=%s", type(exc).__name__)


def schedule(background_tasks, raw_body: bytes) -> bool:
    """Da chiamare nel ricevitore DOPO la verifica della firma Meta. Accoda
    l'inoltro come task in background (dopo la risposta). Ritorna True se
    accodato. Non solleva mai: un errore qui non tocca il ricevitore."""
    try:
        cfg = config()
        if cfg is None:
            return False
        body = select(raw_body, cfg.senders)
        if body is None:
            return False
        background_tasks.add_task(_deliver_safely, cfg, body)
        return True
    except Exception as exc:
        log.error("whatsapp_relay schedule_failed error_type=%s", type(exc).__name__)
        return False


BUSINESS_NUMBER = "393295674589"


def self_check(env=None, out=print) -> int:
    """`python whatsapp_relay.py --check` sul servizio di produzione: stato
    dell'inoltro senza stampare segreti ne' numeri. 0 = stato sicuro."""
    env = os.environ if env is None else env
    present = [n for n in (URL_ENV, SECRET_ENV, SENDERS_ENV) if (env.get(n) or "").strip()]
    cfg = config(env)
    if cfg is None:
        if present:
            out("STOP inoltro configurato solo in parte o male: spento (variabili presenti: " + ", ".join(present) + ")")
            return 1
        out("OK   inoltro SPENTO: nessuna variabile WHATSAPP_RELAY_*")
        return 0
    host = urlparse(cfg.url).hostname or ""
    problems = []
    if cfg.senders == ALL and "test" in host:
        problems.append("tutti i mittenti verso un ambiente di TEST")
    if cfg.senders != ALL and BUSINESS_NUMBER in cfg.senders:
        problems.append("il numero business e' tra i mittenti inoltrati")
    if cfg.senders != ALL and len(cfg.senders) != 1:
        problems.append("durante il collaudo serve esattamente un mittente")
    for p in problems:
        out("STOP " + p)
    if problems:
        return 1
    n = "tutti" if cfg.senders == ALL else str(len(cfg.senders))
    out(f"OK   inoltro ACCESO verso {host}{urlparse(cfg.url).path} per {n} mittente/i")
    return 0


if __name__ == "__main__":
    import sys

    if sys.argv[1:] == ["--check"]:
        sys.exit(self_check())
    print("uso: python whatsapp_relay.py --check")
    sys.exit(2)
