"""A30-9B - il provider REALE (REST verso Google Calendar).

Stesso contratto di `provider.py` / `fake_provider.py` (A30-9A): nessuna
logica Google fuori da questo modulo, nessun dettaglio HTTP che arriva al
dominio (solo `CalendarProviderError(kind, ...)`, mai un corpo di risposta o
un token).

NESSUNA libreria client Google (`google-api-python-client` e' VIETATA, §2):
solo `google-auth` (refresh del token, via `AuthorizedSession`) e `requests`
(gia' nello stack) per le chiamate REST dirette. Ogni chiamata ha un timeout
ESPLICITO (§33: nessuna richiesta senza timeout). L'access token vive SOLO
nella `AuthorizedSession` di UNA chiamata: non e' mai salvato nel database,
non e' mai loggato (§12).

`ensure_event` e `delete_event` non passano MAI `attendees` e chiedono
`sendUpdates=none` (§14): nessun invito al cliente, nessun destinatario.
"""
from __future__ import annotations

from datetime import datetime

import requests
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials

from .provider import (
    BAD_REQUEST,
    CONFLICT,
    FORBIDDEN,
    GONE,
    INVALID_GRANT,
    NETWORK,
    NOT_FOUND,
    PRIVATE_PROPERTIES_WHITELIST,
    RATE_LIMITED,
    SERVER_ERROR,
    TIMEOUT,
    UNAUTHORIZED,
    CalendarProviderError,
    DeleteResult,
    EnsureResult,
    EventPayload,
    ProviderAuth,
    RemoteEvent,
)

TOKEN_URI = "https://oauth2.googleapis.com/token"
API_BASE = "https://www.googleapis.com/calendar/v3"
#: (connect, read) in secondi. Nessuna chiamata Google senza timeout (§33).
HTTP_TIMEOUT = (5, 20)
#: Nessun destinatario, nessun invito: nessun attendee su questi eventi (§14).
SEND_UPDATES_NONE = {"sendUpdates": "none"}


def _reason(body) -> str | None:
    if not isinstance(body, dict):
        return None
    errore = body.get("error")
    if not isinstance(errore, dict):
        return None
    dettagli = errore.get("errors")
    if isinstance(dettagli, list) and dettagli and isinstance(dettagli[0], dict):
        return dettagli[0].get("reason")
    return None


def _classify_status(status: int, body, *, operation: str) -> CalendarProviderError:
    reason = _reason(body)
    if status == 429:
        kind = RATE_LIMITED
    elif status >= 500:
        kind = SERVER_ERROR
    elif status == 401:
        kind = UNAUTHORIZED
    elif status == 403:
        kind = FORBIDDEN
    elif status == 404:
        kind = NOT_FOUND
    elif status == 410:
        kind = GONE
    elif status == 409:
        kind = CONFLICT
    else:
        kind = BAD_REQUEST
    return CalendarProviderError(kind, http_status=status, reason=reason, operation=operation)


def _istante(valore) -> datetime | None:
    if not valore:
        return None
    try:
        return datetime.fromisoformat(str(valore).replace("Z", "+00:00"))
    except ValueError:
        return None


def _updated_at(body):
    return _istante(body.get("updated") if isinstance(body, dict) else None)


def _remote_event(body: dict) -> RemoteEvent:
    """A30-10: SOLO i campi ammessi da `RemoteEvent` - mai `summary`,
    `description`, `location`, `attendees`, `organizer`, dati di conferenza:
    quei campi restano nel `dict` grezzo di Google, che non esce da questa
    funzione. Un evento `cancelled` (tombstone HTTP 200) non porta orari ne'
    proprieta' private: e' un fatto ("questo evento non c'e' piu'"), non un
    contenuto."""
    stato = body.get("status") or "confirmed"
    if stato == "cancelled":
        return RemoteEvent(status=stato, start_at=None, end_at=None, timezone=None,
                           etag=body.get("etag"), updated_at=_updated_at(body),
                           private_properties={})
    inizio = body.get("start") or {}
    fine = body.get("end") or {}
    private_grezze = ((body.get("extendedProperties") or {}).get("private") or {})
    private = {chiave: valore for chiave, valore in private_grezze.items()
              if chiave in PRIVATE_PROPERTIES_WHITELIST}
    return RemoteEvent(
        status=stato,
        start_at=_istante(inizio.get("dateTime")),
        end_at=_istante(fine.get("dateTime")),
        timezone=inizio.get("timeZone"),
        etag=body.get("etag"),
        updated_at=_updated_at(body),
        private_properties=private,
    )


def _event_body(payload: EventPayload) -> dict:
    """SOLO i campi ammessi da §15: etichetta neutra, orari, descrizione
    fissa, proprieta' private tecniche. Mai un attendee."""
    return {
        "summary": payload.summary,
        "description": payload.description,
        "start": {"dateTime": payload.start_at.isoformat(), "timeZone": payload.timezone},
        "end": {"dateTime": payload.end_at.isoformat(), "timeZone": payload.timezone},
        "extendedProperties": {"private": {str(a): str(b) for a, b in
                                           payload.private_properties.items()}},
    }


class GoogleCalendarProvider:
    """Il provider vero. `client_id`/`client_secret` servono SOLO a rinfrescare
    l'access token dal refresh token della connessione; non compaiono mai in
    `repr`, in un log o in un `CalendarProviderError`."""

    name = "google"

    def __init__(self, *, client_id: str, client_secret: str):
        self._client_id = client_id
        self._client_secret = client_secret

    def __repr__(self) -> str:  # mai client_id/secret
        return "GoogleCalendarProvider()"

    def _session(self, auth: ProviderAuth) -> AuthorizedSession:
        credenziali = Credentials(
            token=None,
            refresh_token=auth.refresh_token,
            token_uri=TOKEN_URI,
            client_id=self._client_id,
            client_secret=self._client_secret,
        )
        return AuthorizedSession(credenziali)

    def _request(self, auth: ProviderAuth, metodo: str, url: str, *, operation: str,
                 json=None, params=None):
        sessione = self._session(auth)
        try:
            return sessione.request(metodo, url, json=json, params=params,
                                    timeout=HTTP_TIMEOUT)
        except RefreshError:
            # Il refresh token e' stato revocato/e' scaduto: needs_reauth,
            # mai il dettaglio della libreria (potrebbe citare il token).
            raise CalendarProviderError(INVALID_GRANT, operation=operation) from None
        except requests.exceptions.Timeout:
            raise CalendarProviderError(TIMEOUT, operation=operation) from None
        except requests.exceptions.RequestException:
            raise CalendarProviderError(NETWORK, operation=operation) from None

    @staticmethod
    def _corpo(risposta):
        try:
            return risposta.json()
        except ValueError:
            return None

    def get_event(self, auth: ProviderAuth, calendar_id: str,
                  event_id: str) -> RemoteEvent | None:
        """A30-10: lettura puntuale di un evento GIA' mappato. 404/410 =
        assente = `None`, mai un errore da ritentare (§ contratto). Nessuna
        `attendees`/`summary`/`description` arbitraria esce da qui: solo
        quanto `_remote_event` porta nel `RemoteEvent` neutro."""
        url = f"{API_BASE}/calendars/{calendar_id}/events/{event_id}"
        risposta = self._request(auth, "GET", url, operation="get")
        if risposta.status_code in (404, 410):
            return None
        if not risposta.ok:
            raise _classify_status(risposta.status_code, self._corpo(risposta), operation="get")
        return _remote_event(self._corpo(risposta) or {})

    def ensure_event(self, auth: ProviderAuth, calendar_id: str,
                     payload: EventPayload) -> EnsureResult:
        """§13: update/patch dell'id deterministico; 404 -> crea con lo
        STESSO id; CREATE 409 -> l'evento esiste, si aggiorna. Un solo
        risultato finale, nessun id casuale."""
        base = f"{API_BASE}/calendars/{calendar_id}/events"
        corpo = _event_body(payload)
        risposta = self._request(auth, "PATCH", f"{base}/{payload.event_id}",
                                 operation="ensure", json=corpo, params=SEND_UPDATES_NONE)
        if risposta.status_code == 404:
            creazione = dict(corpo)
            creazione["id"] = payload.event_id
            risposta_create = self._request(auth, "POST", base, operation="ensure",
                                            json=creazione, params=SEND_UPDATES_NONE)
            if risposta_create.status_code == 409:
                risposta_upd = self._request(auth, "PATCH", f"{base}/{payload.event_id}",
                                             operation="ensure", json=corpo,
                                             params=SEND_UPDATES_NONE)
                if not risposta_upd.ok:
                    raise _classify_status(risposta_upd.status_code, self._corpo(risposta_upd),
                                           operation="ensure")
                dati = self._corpo(risposta_upd) or {}
                return EnsureResult("updated", dati.get("etag"), _updated_at(dati))
            if not risposta_create.ok:
                raise _classify_status(risposta_create.status_code, self._corpo(risposta_create),
                                       operation="ensure")
            dati = self._corpo(risposta_create) or {}
            return EnsureResult("created", dati.get("etag"), _updated_at(dati))
        if not risposta.ok:
            raise _classify_status(risposta.status_code, self._corpo(risposta), operation="ensure")
        dati = self._corpo(risposta) or {}
        return EnsureResult("updated", dati.get("etag"), _updated_at(dati))

    def delete_event(self, auth: ProviderAuth, calendar_id: str, event_id: str) -> DeleteResult:
        """404/410 = gia' assente = successo idempotente (§14)."""
        url = f"{API_BASE}/calendars/{calendar_id}/events/{event_id}"
        risposta = self._request(auth, "DELETE", url, operation="delete",
                                 params=SEND_UPDATES_NONE)
        if risposta.status_code in (404, 410):
            return DeleteResult("absent")
        if not risposta.ok:
            raise _classify_status(risposta.status_code, self._corpo(risposta), operation="delete")
        return DeleteResult("deleted")
