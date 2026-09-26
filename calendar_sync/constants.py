"""A30-9A - i valori chiusi. Specchio dei CHECK della migration 074: un test
confronta queste tuple con il testo della migration."""
from __future__ import annotations

PROVIDER_GOOGLE = "google"

CONNECTION_STATUSES = ("connected", "needs_reauth", "disconnected")

SYNC_STATUSES = (
    "pending",             # da riconciliare (anche appena nata)
    "syncing",             # presa da un worker (claim_token + claimed_at)
    "synced",              # remoto allineato alla generazione corrente
    "retrying",            # errore transitorio, riprova a next_attempt_at
    "failed",              # errore definitivo o tentativi esauriti
    "needs_reauth",        # la connessione va ri-autorizzata dall'operatore
    "waiting_connection",  # l'agente attuale non ha una connessione
    "detached",            # la connessione remota e' stata scollegata (D14)
)

#: Gli stati che un worker puo' prendere (oltre ai claim con lease scaduto).
CLAIMABLE_STATUSES = ("pending", "retrying")

#: D11: in A30-9 solo il calendario principale dell'account.
DEFAULT_CALENDAR_ID = "primary"

#: Stati dell'appuntamento per cui l'evento remoto DEVE esistere (con una
#: connessione valida), deve essere ASSENTE, o non si tocca (D8).
APPOINTMENT_REMOTE_PRESENT = ("scheduled", "confirmed")
APPOINTMENT_REMOTE_ABSENT = ("cancelled",)
APPOINTMENT_REMOTE_UNTOUCHED = ("completed", "no_show", "requested")

#: Lease di un claim: oltre questo tempo un claim orfano torna disponibile.
DEFAULT_LEASE_SECONDS = 300

#: Retry (D-retry): backoff esponenziale con tetto, deterministico.
#: Tentativo n (1-based, dopo n fallimenti): BASE * FACTOR**(n-1), max CAP.
#: 60s, 3m, 9m, 27m, 81m, ~4h, 6h -> all'8o fallimento la riga e' `failed`
#: (circa 12 ore di tentativi). Un "Riprova sincronizzazione" la riporta
#: `pending` e azzera i tentativi.
BACKOFF_BASE_SECONDS = 60
BACKOFF_FACTOR = 3
BACKOFF_CAP_SECONDS = 6 * 3600
MAX_ATTEMPTS = 8

#: Namespace dell'id evento deterministico: cambiarlo cambia TUTTI gli id.
EVENT_ID_NAMESPACE = "stima360:calendar-event:v1"
EVENT_ID_PREFIX = "s360"

#: Il marcatore d'origine nelle proprieta' private dell'evento.
ORIGIN_MARKER = "stima360_crm"

#: TTL di un intento OAuth (<= 1 ora, CHECK della 074).
OAUTH_STATE_TTL_SECONDS = 600
