#!/usr/bin/env python3
"""V3 - verifica IN SOLA LETTURA della coda dei messaggi, prima di attivare.

Da eseguire nella Shell Render del servizio TEST (usa le variabili DB_* del
servizio). Non scrive niente: sessione `readonly`, transazione annullata alla
fine. Non stampa indirizzi, nomi o testi: solo conteggi e date.

    python scripts/v3_verifica_coda.py

Risponde a: quanti messaggi aspettano in coda (per tipo e motivo), da quando,
quanti partirebbero al primo giro del dispatcher, quante sequenze esistono e
in che stato. I messaggi vecchi in coda partirebbero al primo giro: questo
script serve a vederli PRIMA, non a toccarli.
"""
from __future__ import annotations

import os
import sys

QUERY_CODA = """
    SELECT m.agency_id, m.communication_type, m.reason_code, m.status, count(*) AS n,
           min(m.scheduled_at) AS piu_vecchio, max(m.scheduled_at) AS piu_recente,
           count(*) FILTER (WHERE m.status = 'queued' AND m.scheduled_at <= NOW()) AS dovuti_ora
      FROM communication_messages m
     WHERE m.status IN ('queued', 'sending', 'indeterminate')
     GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4"""

QUERY_SEQUENZE = """
    SELECT j.agency_id, j.journey_key, j.version, j.status, j.name,
           (SELECT count(*) FROM communication_enrollments e
             WHERE e.journey_id = j.id AND e.status IN ('active', 'paused')) AS iscrizioni_aperte
      FROM communication_journeys j ORDER BY 1, 2, 3"""


def rapporto(cur) -> dict:
    """I due quadri, in sola lettura. `cur` restituisce righe come tuple."""
    cur.execute("SELECT current_database()")
    database = cur.fetchone()[0]
    cur.execute(QUERY_CODA)
    coda = cur.fetchall()
    cur.execute("SELECT to_regclass('communication_journeys') IS NOT NULL")
    sequenze = []
    if cur.fetchone()[0]:
        cur.execute(QUERY_SEQUENZE)
        sequenze = cur.fetchall()
    return {"database": database, "coda": coda, "sequenze": sequenze}


def main() -> int:
    import psycopg2
    conn = psycopg2.connect(host=os.environ["DB_HOST"], port=os.environ.get("DB_PORT", "5432"),
                            dbname=os.environ["DB_NAME"], user=os.environ["DB_USER"],
                            password=os.environ["DB_PASSWORD"])
    conn.set_session(readonly=True)
    try:
        with conn.cursor() as cur:
            r = rapporto(cur)
    finally:
        conn.rollback()
        conn.close()
    print("database:", r["database"])
    print("limite destinatari marketing (COMMUNICATION_MARKETING_ALLOWED_RECIPIENTS):",
          "ATTIVO" if os.getenv("COMMUNICATION_MARKETING_ALLOWED_RECIPIENTS") is not None else "non attivo")
    print("\n== CODA (queued / sending / indeterminate) ==")
    if not r["coda"]:
        print("vuota")
    for agenzia, tipo, motivo, stato, n, vecchio, recente, dovuti in r["coda"]:
        print(f"agenzia {agenzia} | {tipo:9} | {motivo:22} | {stato:13} | {n:5} | dal {vecchio} al {recente} "
              f"| dovuti adesso: {dovuti}")
    print("\n== SEQUENZE ==")
    if not r["sequenze"]:
        print("nessuna")
    for agenzia, chiave, versione, stato, nome, aperte in r["sequenze"]:
        print(f"agenzia {agenzia} | {chiave} v{versione} | {stato:8} | {nome} | iscrizioni aperte: {aperte}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
