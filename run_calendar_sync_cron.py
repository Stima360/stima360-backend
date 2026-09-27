"""A30-9B/A30-10B - il runner CRON della sincronizzazione Google Calendar, nei
due sensi.

Niente HTTP, niente Basic, niente sessione browser (§23): a differenza di
`run_communication_dispatch_cron.py` questo script gira IN PROCESSO, con le
stesse funzioni del dominio (`calendar_sync.service.run_once` outbound A30-9A,
`calendar_sync.inbound.run_once` inbound A30-10B) - non chiama nessuna rotta
di questa applicazione, e non ne apre una connessione propria (P26 H11): usa
`core.database` come tutto il resto.

ORDINE (A30-10A/B, dimostrato non arbitrario): PRIMA l'inbound, POI
l'outbound. Un reschedule/cancel applicato dall'inbound passa dal dominio
autorevole del dominio Agenda, che chiama GIA' `on_appointment_mutation`
nella sua stessa transazione: la riga di sync e' quindi GIA' `pending`/dirty
quando l'outbound parte, nello STESSO giro - zero giri persi a riconfermare su
Google una modifica appena arrivata da Google. L'ordine opposto non avrebbe
alcun vantaggio e ritarderebbe di un giro intero quella conferma.

Ogni riga presa (nei due sensi) e' lavorata in transazioni BREVI dal
riconciliatore/worker stesso (§23: mai una transazione PostgreSQL aperta
durante una chiamata Google); questo runner si limita ad aprire i due giri e a
leggerne l'esito.

Un giro, due esiti, un codice di uscita:
  0  i due giri sono avvenuti e nessuna riga (inbound o outbound) e' rimasta
     `failed`/`internal_error` (una coda vuota e' 0: niente da fare non e' un
     guasto);
  1  guasto TECNICO prima di cominciare: Google disabilitato/non configurato
     per questo ambiente, o chiave di cifratura assente - il cron non deve
     girare a vuoto silenziosamente su un ambiente sbagliato;
  2  i giri sono avvenuti ma almeno una riga (di uno dei due) e' finita
     `failed` o `internal_error` (§24: le altre righe del lotto sono state
     comunque lavorate - un errore non blocca l'intero batch, ne' l'altro
     verso).
"""
from __future__ import annotations

import logging
import os

from calendar_sync import config as gcal_config
from calendar_sync import crypto
from calendar_sync import inbound
from calendar_sync import service
from calendar_sync.google_provider import GoogleCalendarProvider

log = logging.getLogger("calendar_sync.cron")

#: §24: gli esiti di riga che rendono il giro un insuccesso applicativo.
#: Vale per entrambe le code: nessun esito inbound usa questi due nomi con un
#: significato diverso da quello outbound.
GUASTO = ("failed", "internal_error")


def _batch_size() -> int:
    raw = os.getenv("GOOGLE_CALENDAR_WORKER_BATCH_SIZE", "25")
    try:
        valore = int(raw)
    except (TypeError, ValueError):
        valore = 25
    return max(1, min(valore, 200))


def _inbound_batch_size() -> int:
    """A30-10B: coda propria, env proprio - mai `GOOGLE_CALENDAR_WORKER_BATCH_SIZE`
    (quello resta il lotto OUTBOUND)."""
    raw = os.getenv("GOOGLE_CALENDAR_INBOUND_BATCH_SIZE", "25")
    try:
        valore = int(raw)
    except (TypeError, ValueError):
        valore = 25
    return max(1, min(valore, 200))


def run_once() -> tuple[list, list]:
    """UN giro nei due sensi: config e provider vero condivisi (nessun access
    token esce da qui: vive nel provider, per chiamata), inbound PRIMA
    dell'outbound (vedi motivazione sopra)."""
    cfg = gcal_config.require_config()
    keyring = crypto.require_keyring()
    provider = GoogleCalendarProvider(client_id=cfg.client_id, client_secret=cfg.client_secret)
    esiti_inbound = inbound.run_once(provider=provider, keyring=keyring,
                                     limit=_inbound_batch_size())
    esiti_outbound = service.run_once(provider=provider, keyring=keyring, limit=_batch_size())
    return esiti_inbound, esiti_outbound


def main() -> int:
    if not gcal_config.is_enabled():
        log.info("calendar_sync: Google Calendar disabilitato per questo ambiente, nessun giro")
        return 1
    try:
        esiti_inbound, esiti_outbound = run_once()
    except gcal_config.GoogleCalendarConfigError:
        log.warning("calendar_sync: configurazione Google incompleta, nessun giro")
        return 1
    except crypto.CalendarCryptoNotConfigured:
        log.warning("calendar_sync: chiave di cifratura (GOOGLE_TOKEN_FERNET_KEYS) assente, "
                    "nessun giro")
        return 1
    for sync_id, esito in esiti_inbound:
        print(f"inbound sync_id={sync_id} esito={esito}", flush=True)
    for sync_id, esito in esiti_outbound:
        print(f"outbound sync_id={sync_id} esito={esito}", flush=True)
    if any(esito in GUASTO for _, esito in esiti_inbound + esiti_outbound):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
