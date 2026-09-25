#!/usr/bin/env python3
"""A30 - la pulizia ESPLICITA dei dati di prova dell'Agenda (Q7 del GATE A30-1).

Cancella soltanto le righe `appointments` con `source = 'a30_test'` e il
`test_run_id` indicato, e i loro `appointment_events`. Lo fa chiamando
`a30_test_purge(run_id)` della migration 072, che e' l'unico percorso che
sblocca la DELETE.

Due guardie indipendenti, entrambe in ALLOWLIST (fallisce chiuso):

  1. qui, prima di connettersi: `DB_NAME` deve superare la guardia del
     runner P26 (`assert_test_database_name`) ED essere esattamente uno dei
     nomi TEST certificati (`CERTIFIED_TEST_DATABASES`);
  2. nel database: `a30_test_purge` rifiuta ogni `current_database()` che non
     sia un TEST certificato (`a30_is_certified_test_database()` della 072).

Senza `--apply` conta e non cancella nulla.

Uso::

    DB_NAME=stima360_db_test python scripts/a30_test_cleanup.py --run-id <run>
    DB_NAME=stima360_db_test python scripts/a30_test_cleanup.py --run-id <run> --apply
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.p26_migrate import GuardFailure, assert_test_database_name, connect  # noqa: E402

RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")

#: I soli database su cui la purge e' ammessa. Stesso elenco della funzione
#: `a30_is_certified_test_database()` della 072 (un test lo verifica).
CERTIFIED_TEST_DATABASES = frozenset({"stima360_db_test"})


def assert_certified_test_database(database_name: str | None) -> str:
    """Allowlist: il nome deve superare la guardia del runner ed essere
    ESATTAMENTE un TEST certificato. Qualunque altro nome e' rifiutato."""
    nome = assert_test_database_name(database_name)
    if nome not in CERTIFIED_TEST_DATABASES:
        raise GuardFailure(
            f"BLOCKED: {nome!r} is not a certified A30 TEST database "
            f"(allowed: {sorted(CERTIFIED_TEST_DATABASES)}).")
    return nome


def assert_run_id(run_id: str | None) -> str:
    valore = (run_id or "").strip()
    if not RUN_ID_RE.match(valore):
        raise GuardFailure(f"BLOCKED: {run_id!r} is not a valid A30 test run id.")
    return valore


def count_run(cursor, run_id: str) -> tuple[int, int]:
    cursor.execute(
        "SELECT count(*) FROM appointments WHERE source = 'a30_test' AND test_run_id = %s",
        (run_id,),
    )
    appuntamenti = cursor.fetchone()[0]
    cursor.execute(
        "SELECT count(*) FROM appointment_events e JOIN appointments a "
        "ON a.id = e.appointment_id "
        "WHERE a.source = 'a30_test' AND a.test_run_id = %s",
        (run_id,),
    )
    return appuntamenti, cursor.fetchone()[0]


def purge_run(connection, run_id: str, *, apply: bool) -> dict:
    """Conta sempre; cancella solo con `apply`. Una transazione."""
    run_id = assert_run_id(run_id)
    with connection.cursor() as cursor:
        appuntamenti, eventi = count_run(cursor, run_id)
        esito = {"run_id": run_id, "appointments": appuntamenti, "events": eventi,
                 "applied": False}
        if apply:
            cursor.execute("SELECT appointments_deleted, events_deleted "
                           "FROM a30_test_purge(%s)", (run_id,))
            cancellati, eventi_cancellati = cursor.fetchone()
            esito.update(applied=True, appointments_deleted=cancellati,
                         events_deleted=eventi_cancellati)
            connection.commit()
        else:
            connection.rollback()
    return esito


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--apply", action="store_true",
                        help="cancella davvero; senza, conta soltanto")
    args = parser.parse_args(argv)
    try:
        database_name = assert_certified_test_database(os.getenv("DB_NAME"))
        run_id = assert_run_id(args.run_id)
    except GuardFailure as exc:
        print(exc, file=sys.stderr)
        return 2
    connection = connect(database_name)
    try:
        esito = purge_run(connection, run_id, apply=args.apply)
    finally:
        connection.close()
    print(esito)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
