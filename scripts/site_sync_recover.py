#!/usr/bin/env python3
"""CATALOGO-CANONICO-1 - recupero degli invii del sito non trasferiti nella scheda.

Riprende SOLO le righe di `site_submissions` (migration 088) rimaste
`pending` o `failed`, e le stime rapide saltate per mancanza di lead quando nel
frattempo il lead c'e'. Usa i valori conservati AL MOMENTO DELL'INVIO: non
ricostruisce nulla da `stime` (che contiene i default del backend e i metri
quadri interi) e non e' un backfill dello storico. Le stime precedenti alla
088 non hanno l'invio originale: il census le conta, nessuna modalita' le tocca.

Modalita':

  --census    (predefinito) solo lettura: invii per tipo, stato e motivo; stime
              senza invio conservato.
  --dry-run   l'elenco degli invii che il recupero riprenderebbe (nessuna
              scrittura).
  --apply     li riprende: prima le stime rapide, poi le dettagliate, una
              transazione per invio. Idempotente: ripeterlo non duplica nulla.
              Solo su un TEST certificato, con --confirm-database.

Guardie (fallisce chiuso, stessa allowlist di `a30_6_legacy_import.py`):
`DB_NAME` deve essere un TEST certificato; `--apply` pretende
`--confirm-database` identico a `DB_NAME`. PROD esclusa.

Output: JSON con conteggi e id numerici, nessun dato personale.

Uso (Render Shell del servizio TEST)::

    python scripts/site_sync_recover.py --census
    python scripts/site_sync_recover.py --dry-run
    python scripts/site_sync_recover.py --apply --confirm-database "$DB_NAME"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.a30_test_cleanup import assert_certified_test_database  # noqa: E402
from scripts.p26_migrate import GuardFailure  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    gruppo = parser.add_mutually_exclusive_group()
    gruppo.add_argument("--census", action="store_true", help="solo lettura (predefinito)")
    gruppo.add_argument("--dry-run", action="store_true", help="elenco di cio' che verrebbe ripreso")
    gruppo.add_argument("--apply", action="store_true", help="riprende gli invii")
    parser.add_argument("--confirm-database", default=None)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--agency-id", type=int, default=None)
    parser.add_argument("--min-age-seconds", type=int, default=120,
                        help="ignora gli invii piu' recenti (potrebbero essere ancora in corso)")
    args = parser.parse_args(argv)
    try:
        database_name = assert_certified_test_database(os.getenv("DB_NAME"))
        if args.apply and args.confirm_database != database_name:
            raise GuardFailure("BLOCKED: --apply requires --confirm-database equal to DB_NAME.")
    except GuardFailure as failure:
        print(str(failure), file=sys.stderr)
        return 2

    from property import site_sync

    if args.apply or args.dry_run:
        esito = site_sync.recover(apply=args.apply, limit=args.limit, agency_id=args.agency_id,
                                  min_age_seconds=args.min_age_seconds)
    else:
        esito = site_sync.census()
    print(json.dumps({"database": database_name, "mode": "apply" if args.apply else "dry-run" if args.dry_run else "census",
                      **esito}, default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
