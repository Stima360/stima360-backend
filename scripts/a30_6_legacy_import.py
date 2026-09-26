#!/usr/bin/env python3
"""A30-6 - census e import dei sopralluoghi legacy `stime_dettagliate` -> Agenda.

Modalita', in ordine di rischio:

  --census            (predefinito) SOLA LETTURA: sessione READ ONLY, conta,
                      classifica e mostra il piano e la deriva.
  --dry-run           esegue parsing, mapping e import in una transazione e fa
                      SEMPRE rollback: prova che gli INSERT passano i vincoli
                      senza lasciare nulla.
  --apply             esegue l'import e fa commit. Solo su un TEST certificato,
                      con --confirm-database.
  --rollback-dry-run  mostra quali appuntamenti importati e MAI lavorati
                      verrebbero annullati (rollback sempre).
  --rollback          li annulla davvero (status cancelled, nessuna DELETE),
                      con --confirm-database. Una chiave annullata resta
                      occupata: quel record legacy non si reimporta.

Guardie (fallisce chiuso, allowlist, stesso schema di `a30_2p_backfill.py`):
  1. `DB_NAME` deve superare la guardia del runner P26 ed essere ESATTAMENTE
     un TEST certificato (`CERTIFIED_TEST_DATABASES`, la stessa lista di
     `a30_is_certified_test_database()` della 072). PROD non e' ammessa in
     nessun modo, nemmeno per il census: su PROD si esegue prima, a mano,
     `roadmap/A30-6_census_readonly.sql` con una review separata (D1).
  2. nel database: `current_database()` deve coincidere con `DB_NAME` e
     `a30_is_certified_test_database()` deve essere vera.
  3. `--apply` e `--rollback` pretendono `--confirm-database <nome>`
     identico a `DB_NAME`.

Nessuna credenziale nel codice: la connessione usa le variabili DB_* del
runner P26. L'output e' JSON di soli conteggi e id numerici: nessun dato
personale.

Uso::

    DB_NAME=stima360_db_test python scripts/a30_6_legacy_import.py --census
    DB_NAME=stima360_db_test python scripts/a30_6_legacy_import.py --dry-run
    DB_NAME=stima360_db_test python scripts/a30_6_legacy_import.py --apply \\
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

MODES = ("census", "dry-run", "apply", "rollback-dry-run", "rollback")
COMMITTING_MODES = ("apply", "rollback")


def assert_database_identity(cursor, database_name: str) -> None:
    cursor.execute("SELECT current_database() AS db, a30_is_certified_test_database() AS ok")
    riga = cursor.fetchone()
    if riga["db"] != database_name or not riga["ok"]:
        raise GuardFailure(
            f"BLOCKED: connected to {riga['db']!r}, certified={riga['ok']}: "
            f"expected the certified TEST database {database_name!r}.")


def run(connection, database_name: str, *, mode: str) -> dict:
    """Esegue una modalita' sulla connessione. Il commit avviene SOLO per
    `apply` e `rollback`; ogni altra modalita' termina con ROLLBACK."""
    from psycopg2.extras import RealDictCursor

    from appointments_legacy import stime_dettagliate_import as legacy

    if mode not in MODES:
        raise GuardFailure(f"BLOCKED: unknown mode {mode!r}.")
    if mode == "census":
        # il server stesso rifiuta qualunque scrittura
        connection.set_session(readonly=True)
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            assert_database_identity(cursor, database_name)
            if mode == "census":
                return {"mode": mode, "database": database_name,
                        "census": legacy.census(cursor)}
            if mode in ("rollback-dry-run", "rollback"):
                esito = legacy.rollback_untouched(cursor, apply=True)
                risultato = {"mode": mode, "database": database_name,
                             "rollback": esito, "committed": mode == "rollback"}
            else:
                prima = legacy.census(cursor)
                esito = legacy.run_import(cursor, apply=True)
                dopo = legacy.census(cursor)
                risultato = {"mode": mode, "database": database_name,
                             "census_before": prima, "import": esito,
                             "census_after": dopo, "committed": mode == "apply"}
        if mode in COMMITTING_MODES:
            connection.commit()
        else:
            connection.rollback()
        return risultato
    except Exception:
        connection.rollback()
        raise
    finally:
        if mode == "census":
            connection.rollback()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    gruppo = parser.add_mutually_exclusive_group()
    gruppo.add_argument("--census", action="store_true", help="solo lettura (predefinito)")
    gruppo.add_argument("--dry-run", action="store_true", help="import e rollback")
    gruppo.add_argument("--apply", action="store_true", help="import e commit")
    gruppo.add_argument("--rollback-dry-run", action="store_true",
                        help="mostra gli annullamenti, poi rollback")
    gruppo.add_argument("--rollback", action="store_true",
                        help="annulla gli import mai lavorati e fa commit")
    parser.add_argument("--confirm-database", default=None)
    args = parser.parse_args(argv)
    mode = ("apply" if args.apply else "dry-run" if args.dry_run
            else "rollback" if args.rollback
            else "rollback-dry-run" if args.rollback_dry_run else "census")
    try:
        database_name = assert_certified_test_database(os.getenv("DB_NAME"))
        if mode in COMMITTING_MODES and args.confirm_database != database_name:
            raise GuardFailure(
                f"BLOCKED: --{mode} requires --confirm-database equal to DB_NAME.")
    except GuardFailure as exc:
        print(exc, file=sys.stderr)
        return 2
    connection = connect(database_name)
    try:
        esito = run(connection, database_name, mode=mode)
    except GuardFailure as exc:
        print(exc, file=sys.stderr)
        return 2
    finally:
        connection.close()
    print(json.dumps(esito, default=str, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
