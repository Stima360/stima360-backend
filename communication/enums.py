"""Gli insiemi chiusi del dominio COMMUNICATION.

Ogni insieme qui corrisponde a un CHECK di `migrations/064_p29_communication_foundation.sql`,
e la corrispondenza e' verificata da un test invece che promessa da un commento:
un valore aggiunto da una parte sola produrrebbe un rifiuto del database al
momento peggiore - cioe' in produzione, su un messaggio che qualcuno aspettava -
invece che un test rosso.

Stessa forma e stessa ragione di consent/enums.py.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Canale. Due, e nessun terzo.
#
# `sms` e `push` si aggiungeranno quando esistera' un adapter che li sa mandare:
# un insieme che contiene valori che nessun provider implementa e' una promessa
# che il codice non mantiene.
# --------------------------------------------------------------------------
CHANNEL_EMAIL = "email"
CHANNEL_WHATSAPP = "whatsapp"
CHANNELS = frozenset({CHANNEL_EMAIL, CHANNEL_WHATSAPP})

# --------------------------------------------------------------------------
# Direzione.
#
# `inbound` e' ammesso dal database da subito perche' l'inbound di domani entri
# in QUESTO ledger senza una migration su un CHECK. In P29-2 pero' NESSUN
# percorso lo scrive, e il service lo rifiuta: vedi WRITABLE_DIRECTIONS.
# --------------------------------------------------------------------------
DIRECTION_OUTBOUND = "outbound"
DIRECTION_INBOUND = "inbound"
DIRECTIONS = frozenset({DIRECTION_OUTBOUND, DIRECTION_INBOUND})

#: Cio' che P29-2 puo' scrivere. Un sottoinsieme, non l'insieme: la differenza
#: fra "il database lo accetterebbe" e "questo modulo lo produce".
WRITABLE_DIRECTIONS = frozenset({DIRECTION_OUTBOUND})

# --------------------------------------------------------------------------
# Tipo. Due, e governano il consent gate di P29-2.4.
#
# `service` esegue una richiesta dell'interessato, `marketing` richiede il
# consenso. Non c'e' una terza categoria: `transactional` sarebbe un sinonimo di
# service, e `notification` descriveva l'alert all'amministratore, che il design
# ha messo fuori dal perimetro del dominio.
# --------------------------------------------------------------------------
TYPE_SERVICE = "service"
TYPE_MARKETING = "marketing"
COMMUNICATION_TYPES = frozenset({TYPE_SERVICE, TYPE_MARKETING})

# --------------------------------------------------------------------------
# Modo. Risponde a una domanda sola: CHI HA PREMUTO INVIA.
#
#   manual     l'operatore sceglie il contenuto e manda
#   assisted   il sistema propone, l'OPERATORE conferma
#   automatic  il sistema manda senza conferma
#
# Ortogonale al canale e al tipo: un WhatsApp puo' essere manuale, un marketing
# puo' essere assistito - e resta marketing.
# --------------------------------------------------------------------------
MODE_MANUAL = "manual"
MODE_ASSISTED = "assisted"
MODE_AUTOMATIC = "automatic"
MODES = frozenset({MODE_MANUAL, MODE_ASSISTED, MODE_AUTOMATIC})

#: I modi in cui una persona ha premuto invia. Da qui discende `actor_type`, che
#: NON e' un parametro: vedi service._attore.
OPERATOR_MODES = frozenset({MODE_MANUAL, MODE_ASSISTED})

# --------------------------------------------------------------------------
# Stato. Sette, e `indeterminate` e' quello che di solito manca.
#
# Chiamiamo il provider e la connessione cade prima della risposta: il messaggio
# PUO' essere partito. Non e' `failed`, e trattarlo come tale produce un doppione
# al primo retry o una perdita silenziosa.
#
# `scheduled`, `delivered` e `draft` NON ci sono, con una ragione ciascuno: il
# primo sarebbe indistinguibile da `queued` con `scheduled_at` futuro, il secondo
# arriva con il webhook di stato che oggi non esiste, il terzo appartiene alla UI.
# --------------------------------------------------------------------------
STATUS_QUEUED = "queued"
STATUS_SENDING = "sending"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"
STATUS_INDETERMINATE = "indeterminate"
STATUS_SUPPRESSED = "suppressed"
STATUS_CANCELLED = "cancelled"
STATUSES = frozenset({
    STATUS_QUEUED, STATUS_SENDING, STATUS_SENT, STATUS_FAILED,
    STATUS_INDETERMINATE, STATUS_SUPPRESSED, STATUS_CANCELLED,
})

#: L'unico stato con cui un messaggio nasce. NON e' un parametro di `enqueue`:
#: un chiamante che potesse accodare un messaggio gia' `sent` scriverebbe nel
#: registro una comunicazione mai avvenuta.
INITIAL_STATUS = STATUS_QUEUED

#: L'unico stato da cui P29-2.2 puo' annullare. Dopo il claim il messaggio
#: appartiene a un dispatcher, e annullarlo da qui vorrebbe dire strapparglielo
#: di mano mentre sta parlando con un provider.
CANCELLABLE_STATUSES = frozenset({STATUS_QUEUED})

# --------------------------------------------------------------------------
# Classe di fallimento. Registra un FATTO osservato - sappiamo o non sappiamo
# cosa ha fatto il provider - e non una conclusione.
#
# Nessun percorso di P29-2.2 la scrive: un messaggio in coda non ha ancora
# fallito. Sta qui perche' l'insieme e' del dominio, non della fase.
# --------------------------------------------------------------------------
FAILURE_DEFINITE = "definite"
FAILURE_INDETERMINATE = "indeterminate"
FAILURE_CLASSES = frozenset({FAILURE_DEFINITE, FAILURE_INDETERMINATE})

# --------------------------------------------------------------------------
# Attore. Due, e NON c'e' `subject`: il soggetto non manda comunicazioni, le
# riceve e semmai risponde. In `consent/` `subject` esiste perche' li' il
# soggetto DECIDE.
# --------------------------------------------------------------------------
ACTOR_SYSTEM = "system"
ACTOR_OPERATOR = "operator"
ACTOR_TYPES = frozenset({ACTOR_SYSTEM, ACTOR_OPERATOR})

# --------------------------------------------------------------------------
# Motivo dell'invio. Insieme chiuso.
#
# `admin_lead_alert` NON c'e': l'alert all'amministratore e' fuori dal perimetro
# del dominio e continua a passare da `invia_mail` senza toccare questo ledger.
# --------------------------------------------------------------------------
REASON_STIMA_PDF = "stima_pdf"
REASON_OPERATOR_MANUAL = "operator_manual"
REASON_OPERATOR_REPLY = "operator_reply"
REASON_M1 = "m1"
REASON_M2 = "m2"
REASON_M3 = "m3"
REASON_M4 = "m4"
REASON_M5 = "m5"
# LMC-1B: il magic link con cui il proprietario entra in "La Mia Casa". E' di
# SERVIZIO - risponde a una richiesta esplicita del destinatario - e il suo
# valore nel CHECK arriva dalla migration 067, non dalla 064.
REASON_OWNER_LOGIN_LINK = "owner_login_link"
REASON_CODES = frozenset({
    REASON_STIMA_PDF, REASON_OPERATOR_MANUAL, REASON_OPERATOR_REPLY,
    REASON_M1, REASON_M2, REASON_M3, REASON_M4, REASON_M5,
    REASON_OWNER_LOGIN_LINK,
})

# --------------------------------------------------------------------------
# Codici di errore NOSTRI, non del provider: `error_detail` porta il testo
# grezzo, questo campo porta il nome stabile che resta vero anche quando il
# provider cambia le sue stringhe.
#
# Nessun percorso di P29-2.2 li scrive.
# --------------------------------------------------------------------------
ERROR_PROVIDER_UNAVAILABLE = "provider_unavailable"
ERROR_PROVIDER_REJECTED = "provider_rejected"
ERROR_INVALID_DESTINATION = "invalid_destination"
ERROR_MISSING_CREDENTIALS = "missing_credentials"
ERROR_RENDERING_FAILED = "rendering_failed"
ERROR_TIMEOUT = "timeout"
ERROR_OUTCOME_UNKNOWN = "outcome_unknown"
ERROR_UNKNOWN = "unknown"
ERROR_CODES = frozenset({
    ERROR_PROVIDER_UNAVAILABLE, ERROR_PROVIDER_REJECTED, ERROR_INVALID_DESTINATION,
    ERROR_MISSING_CREDENTIALS, ERROR_RENDERING_FAILED, ERROR_TIMEOUT,
    ERROR_OUTCOME_UNKNOWN, ERROR_UNKNOWN,
})

# --------------------------------------------------------------------------
# Esito di un TENTATIVO di dispatch. Quattro, e non sono gli stati del
# messaggio: un tentativo racconta una chiamata, un messaggio racconta una
# comunicazione.
#
#   in_progress   reclamato, la chiamata non e' ancora finita
#   accepted      il provider l'ha preso in carico
#   rejected      il provider ha rifiutato, o l'invio non e' avvenuto per una
#                 ragione che conosciamo con certezza
#   indeterminate non sappiamo cosa abbia fatto il provider
# --------------------------------------------------------------------------
OUTCOME_IN_PROGRESS = "in_progress"
OUTCOME_ACCEPTED = "accepted"
OUTCOME_REJECTED = "rejected"
OUTCOME_INDETERMINATE = "indeterminate"
ATTEMPT_OUTCOMES = frozenset({
    OUTCOME_IN_PROGRESS, OUTCOME_ACCEPTED, OUTCOME_REJECTED, OUTCOME_INDETERMINATE,
})

#: L'unico esito con cui un tentativo nasce, al claim.
INITIAL_OUTCOME = OUTCOME_IN_PROGRESS

#: Gli stati in cui un messaggio e' di qualcuno. Solo da qui si finalizza, e
#: solo con il token di quel claim.
CLAIMED_STATUS = STATUS_SENDING

#: Dopo quanto un claim senza esito e' considerato orfano. Largamente superiore
#: a qualunque timeout di provider - 10-30 secondi - perche' un falso stale
#: dichiarerebbe ignoto un invio che stava solo andando piano.
DEFAULT_STALE_AFTER_SECONDS = 15 * 60

#: Il canale per cui l'oggetto esiste. WhatsApp non ha un subject, e il CHECK
#: `communication_messages_subject_chk` lo impone: qui si dichiara una volta
#: sola, cosi' il service non ripete il letterale.
CHANNELS_WITH_SUBJECT = frozenset({CHANNEL_EMAIL})
