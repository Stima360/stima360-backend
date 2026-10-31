"""BLOCCO-4B - trasporto verso la WhatsApp Cloud API di Meta.

DISATTIVATO per impostazione predefinita: `current()` restituisce un
trasporto solo con `WHATSAPP_OUTBOUND_MODE=meta`. I test usano un trasporto
simulato con la stessa interfaccia (`send(phone_number_id, to, text)`).

L'esito e' sempre uno di tre, mai un'eccezione:

    accepted       Meta ha accettato e restituito l'id del messaggio (wamid)
    rejected       rifiuto CERTO (errore Meta, connessione mai aperta): non partito
    indeterminate  esito INCERTO (timeout di risposta, errore 5xx, risposta
                   illeggibile): il messaggio potrebbe essere partito, e non
                   viene mai ritentato da solo
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

import requests

from .enums import (
    GRAPH_VERSION_ENV,
    GRAPH_VERSION_PATTERN,
    META_WINDOW_ERROR,
    MODE_ENV,
    MODE_META,
    REQUEST_TIMEOUT_SECONDS,
    TOKEN_ENV,
)


@dataclass
class SendResult:
    outcome: str                         # accepted | rejected | indeterminate
    provider_message_id: str | None = None
    error_code: str | None = None        # uno degli ERROR_CODES del registro comunicazioni
    error_detail: str | None = None      # codice tecnico, mai dati personali


def enabled() -> bool:
    return (os.getenv(MODE_ENV) or "").strip().lower() == MODE_META


def credentials_present() -> bool:
    return bool((os.getenv(TOKEN_ENV) or "").strip())


def graph_version() -> str | None:
    """La versione dell'API, solo se dichiarata esplicitamente e ben formata (es. `v21.0`)."""
    value = (os.getenv(GRAPH_VERSION_ENV) or "").strip()
    return value if re.fullmatch(GRAPH_VERSION_PATTERN, value) else None


class MetaCloudTransport:
    """Un messaggio di testo con `POST /{version}/{phone_number_id}/messages`."""

    def __init__(self, token: str, version: str, session=None):
        if not re.fullmatch(GRAPH_VERSION_PATTERN, version or ""):
            raise ValueError("WhatsApp Graph API version missing or malformed")
        self._token = token
        self._version = version
        self._session = session or requests

    def send(self, phone_number_id: str, to: str, text: str) -> SendResult:
        url = f"https://graph.facebook.com/{self._version}/{phone_number_id}/messages"
        payload = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to,
                   "type": "text", "text": {"preview_url": False, "body": text}}
        try:
            response = self._session.post(url, json=payload, timeout=REQUEST_TIMEOUT_SECONDS,
                                          headers={"Authorization": f"Bearer {self._token}"})
        except requests.exceptions.ConnectTimeout:
            # La connessione non si e' mai aperta: il messaggio non e' partito.
            return SendResult("rejected", error_code="provider_unavailable", error_detail="connect_timeout")
        except requests.exceptions.Timeout:
            # La richiesta e' partita e la risposta non e' arrivata: esito incerto.
            return SendResult("indeterminate", error_code="timeout", error_detail="read_timeout")
        except requests.exceptions.RequestException as exc:
            # Connessione caduta o altro errore: non si sa se Meta l'abbia ricevuta.
            return SendResult("indeterminate", error_code="outcome_unknown", error_detail=type(exc).__name__)
        return classify(response.status_code, _json(response))


def _json(response):
    try:
        return response.json()
    except ValueError:
        return None


def classify(status_code: int, body) -> SendResult:
    """Dalla risposta HTTP di Meta all'esito. Separata per i test."""
    if 200 <= status_code < 300:
        messages = (body or {}).get("messages") if isinstance(body, dict) else None
        wamid = messages[0].get("id") if isinstance(messages, list) and messages and isinstance(messages[0], dict) else None
        if wamid:
            return SendResult("accepted", provider_message_id=str(wamid))
        return SendResult("indeterminate", error_code="outcome_unknown", error_detail="accepted_without_id")
    if status_code >= 500:
        return SendResult("indeterminate", error_code="outcome_unknown", error_detail=f"http_{status_code}")
    error = (body or {}).get("error") if isinstance(body, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    if status_code in (401, 403) or code == 190:
        return SendResult("rejected", error_code="missing_credentials", error_detail=f"meta_{code or status_code}")
    if code == META_WINDOW_ERROR:
        return SendResult("rejected", error_code="provider_rejected", error_detail=f"meta_{code}_window_closed")
    if code in (131026, 131021, 131030, 100):
        return SendResult("rejected", error_code="invalid_destination", error_detail=f"meta_{code}")
    if status_code == 429 or code in (130429, 131048, 131056, 80007):
        return SendResult("rejected", error_code="provider_unavailable", error_detail=f"meta_{code or status_code}")
    return SendResult("rejected", error_code="provider_rejected", error_detail=f"meta_{code or status_code}")


def current():
    """Il trasporto attivo, o None se l'invio e' disattivato o mancano credenziali o versione."""
    version = graph_version()
    if not enabled() or not credentials_present() or version is None:
        return None
    return MetaCloudTransport(os.getenv(TOKEN_ENV).strip(), version)
