"""A30-9A - il CONTRATTO del provider di calendario, neutro rispetto a Google.

Il dominio (riconciliatore) parla solo con questa interfaccia:

    ensure_event(auth, calendar_id, payload) -> EnsureResult
        "fai si' che l'evento `payload.event_id` ESISTA su quel calendario con
        QUESTO contenuto". Non e' un INSERT cieco: un'implementazione deve
        essere idempotente per `event_id`:
          * evento assente            -> crearlo con QUELL'id;
          * gia' presente (anche dopo un 409 "duplicato" su insert, per
            esempio perche' il processo e' morto dopo un successo remoto)
                                       -> aggiornarlo, oppure lasciarlo se
                                          uguale;
          * cancellato o sparito sul remoto mentre il CRM lo vuole attivo
            (404/410)                  -> RICREARLO con lo stesso id: il CRM e'
                                          la fonte autorevole.
    delete_event(auth, calendar_id, event_id) -> DeleteResult
        "fai si' che l'evento NON esista". 404/410 = gia' assente = successo.

Gli errori escono SOLO come `CalendarProviderError(kind, ...)`: nessun
dettaglio HTTP del fornitore (corpo, header, token) arriva al dominio.
`classify(errore)` decide retry / ri-autorizzazione / fallimento.

Nessuna rete qui: il provider vero (REST verso Google) e' A30-9B.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from . import constants as k

# ---------------------------------------------------------------------------
# DATI
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EventPayload:
    """L'evento remoto desiderato, neutro. Nessun dato personale (D-privacy):
    niente luogo, niente partecipanti, niente cliente."""
    event_id: str
    summary: str
    start_at: datetime
    end_at: datetime
    timezone: str
    description: str
    private_properties: dict


@dataclass(frozen=True)
class ProviderAuth:
    """Le credenziali di UNA connessione per UNA chiamata. Il refresh token non
    compare mai in `repr` (e quindi nei log di un'eccezione)."""
    connection_id: int
    refresh_token: str = field(repr=False)


@dataclass(frozen=True)
class EnsureResult:
    outcome: str                      # created | updated | unchanged
    etag: str | None = None
    remote_updated_at: datetime | None = None


@dataclass(frozen=True)
class DeleteResult:
    outcome: str                      # deleted | absent


class CalendarProvider(Protocol):
    name: str

    def ensure_event(self, auth: ProviderAuth, calendar_id: str,
                     payload: EventPayload) -> EnsureResult: ...

    def delete_event(self, auth: ProviderAuth, calendar_id: str,
                     event_id: str) -> DeleteResult: ...


# ---------------------------------------------------------------------------
# ERRORI
# ---------------------------------------------------------------------------

#: I tipi d'errore del contratto.
RATE_LIMITED = "rate_limited"        # 429, o 403 con reason di rate limit
SERVER_ERROR = "server_error"        # 5xx
TIMEOUT = "timeout"
NETWORK = "network"
UNAUTHORIZED = "unauthorized"        # 401 (anche dopo il refresh)
INVALID_GRANT = "invalid_grant"      # refresh token revocato/scaduto
FORBIDDEN = "forbidden"              # 403 non di rate limit
NOT_FOUND = "not_found"              # 404
GONE = "gone"                        # 410
CONFLICT = "conflict"                # 409 non risolto dall'implementazione
BAD_REQUEST = "bad_request"          # 400
ERROR_KINDS = (RATE_LIMITED, SERVER_ERROR, TIMEOUT, NETWORK, UNAUTHORIZED,
               INVALID_GRANT, FORBIDDEN, NOT_FOUND, GONE, CONFLICT, BAD_REQUEST)

#: Le `reason` di un 403 che sono in realta' limiti di frequenza (Google
#: Calendar API: usageLimits).
RATE_LIMIT_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded",
                                "quotaExceeded"})


class CalendarProviderError(Exception):
    """L'unico errore che un provider solleva. Il messaggio e' il solo `kind`
    (piu' lo status HTTP): mai corpo della risposta, mai token."""

    def __init__(self, kind: str, *, http_status: int | None = None,
                 reason: str | None = None, operation: str | None = None):
        if kind not in ERROR_KINDS:
            raise ValueError(f"tipo d'errore sconosciuto: {kind}")
        self.kind = kind
        self.http_status = http_status
        self.reason = reason
        self.operation = operation
        super().__init__(kind if http_status is None else f"{kind} ({http_status})")


# ---------------------------------------------------------------------------
# CLASSIFICAZIONE E BACKOFF (funzioni pure)
# ---------------------------------------------------------------------------

RETRY = "retry"
NEEDS_REAUTH = "needs_reauth"
FAILED = "failed"
SUCCESS = "success"


@dataclass(frozen=True)
class Classification:
    action: str       # retry | needs_reauth | failed | success
    code: str         # codice stabile per `last_error_code` ([a-z0-9_])


def classify(error: CalendarProviderError, *, operation: str | None = None) -> Classification:
    """La politica d'errore (vincolante, gate A30-9A §10).

    * 429, 5xx, timeout, rete, 409 non risolto   -> retry con backoff;
    * 401 / invalid_grant                       -> needs_reauth;
    * 403: rate limit (reason nota)             -> retry; qualunque altro 403
      (permessi revocati, scope insufficiente, reason ignota) -> needs_reauth:
      senza certezza non si martella il fornitore, si chiede all'operatore di
      ricollegarsi (e il resync riprende da li');
    * 404/410 su DELETE                         -> successo idempotente;
    * 404/410 su ensure (non dovrebbe uscire: il contratto ricrea) -> retry;
    * 400                                       -> failed (errore nostro).
    """
    operation = operation or error.operation
    kind = error.kind
    if kind in (NOT_FOUND, GONE) and operation == "delete":
        return Classification(SUCCESS, "remote_absent")
    if kind in (RATE_LIMITED, SERVER_ERROR, TIMEOUT, NETWORK, CONFLICT, NOT_FOUND, GONE):
        return Classification(RETRY, kind)
    if kind == FORBIDDEN:
        if error.reason in RATE_LIMIT_REASONS:
            return Classification(RETRY, RATE_LIMITED)
        return Classification(NEEDS_REAUTH, FORBIDDEN)
    if kind in (UNAUTHORIZED, INVALID_GRANT):
        return Classification(NEEDS_REAUTH, kind)
    return Classification(FAILED, kind)       # BAD_REQUEST


def backoff_seconds(attempt: int) -> int:
    """Attesa prima del tentativo successivo, dopo `attempt` fallimenti (>= 1).
    Deterministica (nessun jitter): BASE * FACTOR**(attempt-1), max CAP."""
    if attempt < 1:
        raise ValueError("attempt parte da 1")
    return min(k.BACKOFF_BASE_SECONDS * k.BACKOFF_FACTOR ** (attempt - 1),
               k.BACKOFF_CAP_SECONDS)


def exhausted(attempt: int) -> bool:
    """Vero quando `attempt` fallimenti chiudono la riga in `failed`."""
    return attempt >= k.MAX_ATTEMPTS
