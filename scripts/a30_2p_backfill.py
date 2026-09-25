#!/usr/bin/env python3
"""A30-2P - census e backfill di `stima_inspections` verso `appointments`.

Tre modi, in ordine di rischio:

  --census     (predefinito) SOLO LETTURA: conta e mostra il piano.
  --dry-run    esegue il backfill in una transazione e fa SEMPRE rollback:
               prova che gli INSERT passano i vincoli, senza lasciare nulla.
  --apply      esegue e fa commit. Solo su un TEST certificato.

Guardie (fallisce chiuso, allowlist):
  1. `DB_NAME` deve superare la guardia del runner P26 ed essere ESATTAMENTE
     un TEST certificato (`CERTIFIED_TEST_DATABASES`, stessa lista di
     `a30_is_certified_test_database()` della 072). In A30-2P la PROD non e'
     ammessa in nessun modo, nemmeno per il census.
  2. nel database: `current_database()` deve coincidere con `DB_NAME` e
     `a30_is_certified_test_database()` deve essere vera.
  3. `--apply` pretende `--confirm-database <nome>` identico a `DB_NAME`.

Il backfill non tocca `stima_inspections` (solo lettura) e non legge
`stime_dettagliate`, `property_visits` o Google.

Uso::

    DB_NAME=stima360_db_test python scripts/a30_2p_backfill.py --census
    DB_NAME=stima360_db_test python scripts/a30_2p_backfill.py --dry-run
    DB_NAME=stima360_db_test python scripts/a30_2p_backfill.py --apply \\
        --confirm-database stima360_db_test
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.a30_test_cleanup import assert_certified_test_database  # noqa: E402
from scripts.p26_migrate import GuardFailure, connect  # noqa: E402


def assert_database_identity(cursor, database_name: str) -> None:
    cursor.execute("SELECT current_database() AS db, a30_is_certified_test_database() AS ok")
    riga = cursor.fetchone()
    if riga["db"] != database_name or not riga["ok"]:
        raise GuardFailure(
            f"BLOCKED: connected to {riga['db']!r}, certified={riga['ok']}: "
            f"expected the certified TEST database {database_name!r}.")


def run(connection, database_name: str, *, mode: str) -> dict:
    from psycopg2.extras import RealDictCursor

    from appointments import backfill

    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        assert_database_identity(cursor, database_name)
        prima = backfill.census(cursor)
        if mode == "census":
            connection.rollback()
            return {"mode": mode, "database": database_name, "census": prima}
        esito = backfill.run_backfill(cursor, apply=True)
        dopo = backfill.census(cursor)
        if mode == "apply":
            connection.commit()
        else:
            connection.rollback()
    return {"mode": mode, "database": database_name, "census_before": prima,
            "backfill": esito, "census_after": dopo,
            "committed": mode == "apply"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    gruppo = parser.add_mutually_exclusive_group()
    gruppo.add_argument("--census", action="store_true", help="solo lettura (predefinito)")
    gruppo.add_argument("--dry-run", action="store_true", help="esegue e fa rollback")
    gruppo.add_argument("--apply", action="store_true", help="esegue e fa commit")
    parser.add_argument("--confirm-database", default=None)
    args = parser.parse_args(argv)
    mode = "apply" if args.apply else "dry-run" if args.dry_run else "census"
    try:
        database_name = assert_certified_test_database(os.getenv("DB_NAME"))
        if mode == "apply" and args.confirm_database != database_name:
            raise GuardFailure(
                "BLOCKED: --apply requires --confirm-database equal to DB_NAME.")
    except GuardFailure as exc:
        print(exc, file=sys.stderr)
        return 2
    connection = connect(database_name)
    try:
        esito = run(connection, database_name, mode=mode)
    except GuardFailure as exc:
        connection.rollback()
        print(exc, file=sys.stderr)
        return 2
    finally:
        connection.close()
    print(json.dumps(esito, default=str, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
