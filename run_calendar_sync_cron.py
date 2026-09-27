"""A30-9B - il runner CRON del worker di sincronizzazione Google Calendar.

Niente HTTP, niente Basic, niente sessione browser (§23): a differenza di
`run_communication_dispatch_cron.py` questo script gira IN PROCESSO, con le
stesse funzioni del dominio (`calendar_sync.service.run_once`, A30-9A) - non
chiama nessuna rotta di questa applicazione, e non ne apre una connessione
propria (P26 H11): usa `core.database` come tutto il resto.

Ogni riga presa e' lavorata in transazioni BREVI dal riconciliatore stesso
(§23: mai una transazione PostgreSQL aperta durante una chiamata Google);
questo runner si limita ad aprire il giro e a leggerne l'esito.

Un giro, un esito, un codice di uscita:
  0  il giro e' avvenuto e nessuna riga e' rimasta `failed`/`internal_error`
     (una coda vuota e' 0: niente da fare non e' un guasto);
  1  guasto TECNICO prima di cominciare: Google disabilitato/non configurato
     per questo ambiente, o chiave di cifratura assente - il cron non deve
     girare a vuoto silenziosamente su un ambiente sbagliato;
  2  il giro e' avvenuto ma almeno una riga e' finita `failed` o
     `internal_error` (§24: le altre righe del lotto sono state comunque
     lavorate - un errore non blocca l'intero batch).
"""
from __future__ import annotations

import logging
import os

from calendar_sync import config as gcal_config
from calendar_sync import crypto
from calendar_sync import service
from calendar_sync.google_provider import GoogleCalendarProvider

log = logging.getLogger("calendar_sync.cron")

#: §24: gli esiti di riga che rendono il giro un insuccesso applicativo.
GUASTO = ("failed", "internal_error")


def _batch_size() -> int:
    raw = os.getenv("GOOGLE_CALENDAR_WORKER_BATCH_SIZE", "25")
    try:
        valore = int(raw)
    except (TypeError, ValueError):
        valore = 25
    return max(1, min(valore, 200))


def run_once() -> list:
    """UN giro del worker: config, provider vero, un lotto riconciliato.
    Nessun access token esce da qui (vive nel provider, per chiamata)."""
    cfg = gcal_config.require_config()
    keyring = crypto.require_keyring()
    provider = GoogleCalendarProvider(client_id=cfg.client_id, client_secret=cfg.client_secret)
    return service.run_once(provider=provider, keyring=keyring, limit=_batch_size())


def main() -> int:
    if not gcal_config.is_enabled():
        log.info("calendar_sync: Google Calendar disabilitato per questo ambiente, nessun giro")
        return 1
    try:
        esiti = run_once()
    except gcal_config.GoogleCalendarConfigError:
        log.warning("calendar_sync: configurazione Google incompleta, nessun giro")
        return 1
    except crypto.CalendarCryptoNotConfigured:
        log.warning("calendar_sync: chiave di cifratura (GOOGLE_TOKEN_FERNET_KEYS) assente, "
                    "nessun giro")
        return 1
    for sync_id, esito in esiti:
        print(f"sync_id={sync_id} esito={esito}", flush=True)
    if any(esito in GUASTO for _, esito in esiti):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
