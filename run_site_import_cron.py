"""SITE-IMPORT-1 - il cron che porta nel CRM le nuove richieste del sito.

    python3 run_site_import_cron.py              # un giro (batch SITE_IMPORT_BATCH)
    python3 run_site_import_cron.py --dry-run    # conta soltanto, non scrive nulla
    python3 run_site_import_cron.py --initialize-baseline # solo una volta
    python3 run_site_import_cron.py --limit 2000 # lotto piu' lungo

Gira sul database del CRM (variabili DB_* come il servizio web) e LEGGE il
database del sito da SITE_DB_URL, in sola lettura. Non manda email, WhatsApp o
altro; non attiva sequenze; non tocca il sito.

Un giro, un esito, un codice di uscita:
    0  completato (o saltato perche' un altro giro era in corso)
    2  completato con record falliti/parziali (si riprendono al giro dopo)
    1  configurazione o guardia: non e' stato importato nulla
Nessuna credenziale, nessun dato personale nel log.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import site estimations into the CRM")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--initialize-baseline", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    from site_import.config import Config, ConfigurationError
    from site_import.service import ImportRefused, run_once

    inizio = time.monotonic()
    try:
        config = Config.from_env()
        esito = run_once(config, limit=args.limit, dry_run=args.dry_run,
                         initialize_baseline=args.initialize_baseline)
    except (ConfigurationError, ImportRefused) as exc:
        print(f"site_import status=refused reason={exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 - un guasto del giro, non dei record
        print(f"site_import status=error error_type={type(exc).__name__}")
        return 1
    durata = int((time.monotonic() - inizio) * 1000)
    campi = " ".join(f"{k}={v}" for k, v in esito.items())
    print(f"site_import {campi} duration_ms={durata}")
    if esito.get("failed") or esito.get("partial"):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
