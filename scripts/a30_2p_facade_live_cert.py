#!/usr/bin/env python3
"""A30-2P - certificazione LIVE della facade LMC-15 su `stima360_db_test`.

UNA SOLA TRANSAZIONE FISICA, CHIUSA SEMPRE CON ROLLBACK. Nulla di cio' che la
certificazione scrive sopravvive: ne' righe `appointments` (`lmc15_facade`),
ne' righe `stima_inspections`, ne' eventi di timeline. Le sequence degli id
avanzano comunque (PostgreSQL non annulla `nextval`): i buchi negli id sono
attesi e accettati; nessun `setval`.

Il percorso e' quello VERO, in-process: il router LMC-15 (`acquisition.router`)
su un'app FastAPI isolata, con `require_operator` sostituito dall'operatore
indicato, poi service -> facade -> SQL sullo schema TEST. Ogni `commit()`
interno e' neutralizzato: la connessione passata al codice e' un involucro
della transazione esterna; ogni blocco `core_cursor` vive in un SAVEPOINT
proprio, che `commit()` rilascia (senza confermare nulla) e `rollback()`
annulla, come una transazione reale. Qualunque altra connessione al database e' vietata
durante la certificazione (fallisce chiusa).

Guardie, PRIMA di qualunque scrittura (fallisce chiuso):
  1. `DB_NAME` e' esattamente un TEST certificato (allowlist) e coincide con
     `current_database()`; `a30_is_certified_test_database()` e' vera;
  2. 072 e 073 applicate (e non ritirate) nel ledger; il CHECK
     `appointments_lmc15_facade_chk` esiste;
  3. l'operatore e' ATTIVO e ha una membership ATTIVA nell'agenzia (il suo
     ruolo reale entra nel contesto); la stima e' dell'agenzia;
  4. nessuna riga `source='lmc15_facade'`; Q9 = 0/0; Q10 tutto 0;
  5. `pg_try_advisory_xact_lock` sulla chiave della certificazione (una sola
     corsa alla volta), `lock_timeout` e `statement_timeout`.

Scenari (ognuno nel suo SAVEPOINT): create scheduled; complete (anche prima
dell'inizio); create + cancel senza motivo; create completed a posteriori;
F2 su entrambe le rotte; rollback atomico dopo la scrittura LMC-15; Q10 = 0
dentro la transazione. Poi ROLLBACK incondizionato e, da una connessione NUOVA
in sola lettura: nessuno degli id creati esiste piu', `lmc15_facade` = 0, Q5
invariata, Q7 = 0, Q7b = 0, Q9 = 0/0, Q10 = 0. Gli hash globali delle tabelle
sono riportati ma NON sono un gate (attivita' concorrente sul TEST).

Uso (NON eseguire senza approvazione del gate)::

    DB_NAME=stima360_db_test python scripts/a30_2p_facade_live_cert.py \\
        --agency-id <id> --operator-user-id <id> --stima-id <id>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.a30_test_cleanup import assert_certified_test_database  # noqa: E402
from scripts.p26_migrate import GuardFailure, connect  # noqa: E402

LOCK_KEY = "a30_2p:facade_live_cert"
LOCK_TIMEOUT = "5s"
STATEMENT_TIMEOUT = "60s"
SAVEPOINT = "a30_2p_cert_scenario"
SAVEPOINT_CHIAMATA = "a30_2p_cert_call"
MIGRAZIONI = ("072_a30_1_appointments", "073_a30_2p_lmc15_facade")
COLONNE = ["id", "stima_id", "status", "scheduled_for", "completed_at", "cancelled_at",
           "cancelled_reason"]

# --- le query del census A30-2P, copiate alla lettera (un test lo verifica) --
Q5 = """
SELECT count(*) AS n
  FROM stima_inspections i
  JOIN stime s ON s.id = i.stima_id
 WHERE s.agency_id IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM appointments a WHERE a.stima_inspection_id = i.id)
   AND NOT EXISTS (SELECT 1 FROM appointments a
                    WHERE a.source = 'stima_inspections_backfill'
                      AND a.source_record_id = 'stima_inspections:' || i.id)
"""

Q7 = """
SELECT
  (SELECT count(*) FROM appointments a
    WHERE a.source = 'stima_inspections_backfill'
      AND (a.stima_inspection_id IS NULL
           OR a.source_record_id <> 'stima_inspections:' || a.stima_inspection_id))
      AS chiave_e_collegamento_incoerenti,
  (SELECT count(*) FROM (SELECT stima_inspection_id FROM appointments
                          WHERE stima_inspection_id IS NOT NULL
                          GROUP BY 1 HAVING count(*) > 1) d) AS collegamenti_doppi,
  (SELECT count(*) FROM stima_inspections i JOIN stime s ON s.id = i.stima_id
    WHERE i.status = 'completed' AND i.completed_at IS NULL) AS completed_senza_istante
"""

Q7B = """
WITH candidati AS (
    SELECT i.*
      FROM stima_inspections i
      JOIN stime s ON s.id = i.stima_id
     WHERE s.agency_id IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM appointments a WHERE a.stima_inspection_id = i.id)
       AND NOT EXISTS (SELECT 1 FROM appointments a
                        WHERE a.source = 'stima_inspections_backfill'
                          AND a.source_record_id = 'stima_inspections:' || i.id))
SELECT count(*) FILTER (WHERE COALESCE(scheduled_for, completed_at) IS NULL)
           AS senza_istante_iniziale,
       count(*) FILTER (WHERE status = 'scheduled' AND scheduled_for IS NULL)
           AS scheduled_senza_scheduled_for,
       count(*) FILTER (WHERE status = 'cancelled' AND scheduled_for IS NULL)
           AS cancelled_senza_scheduled_for,
       count(*) FILTER (WHERE status = 'cancelled' AND cancelled_at IS NULL)
           AS cancelled_senza_cancelled_at
  FROM candidati
"""

Q9 = """
SELECT count(*) FILTER (WHERE i.status <> 'scheduled') AS chiuse_dopo,
       count(*) FILTER (WHERE i.status = 'scheduled'
                          AND i.scheduled_for IS DISTINCT FROM a.start_at) AS spostate_dopo
  FROM appointments a JOIN stima_inspections i ON i.id = a.stima_inspection_id
 WHERE a.source = 'stima_inspections_backfill' AND a.status = 'requested' AND a.version = 1
"""

Q10 = """
WITH coppie AS (
    SELECT a.id, a.agency_id, a.status AS a_status, a.source, a.stima_id AS a_stima,
           a.start_at, a.completed_at AS a_completed_at, a.cancelled_at AS a_cancelled_at,
           a.no_show_at, a.stima_inspection_id,
           i.id AS i_id, i.status AS i_status, i.stima_id AS i_stima,
           i.scheduled_for, i.completed_at AS i_completed_at,
           i.cancelled_at AS i_cancelled_at, i.cancelled_reason,
           s.agency_id AS i_agency
      FROM appointments a
      LEFT JOIN stima_inspections i ON i.id = a.stima_inspection_id
      LEFT JOIN stime s ON s.id = i.stima_id
     WHERE a.stima_inspection_id IS NOT NULL
)
SELECT
  count(*) FILTER (WHERE i_id IS NULL)
      AS collegamento_senza_riga_lmc15,
  count(*) FILTER (WHERE i_id IS NOT NULL AND NOT (
         (a_status IN ('scheduled', 'confirmed') AND i_status = 'scheduled')
      OR (a_status = 'requested' AND source = 'stima_inspections_backfill'
          AND i_status = 'scheduled')
      OR (a_status = 'completed' AND i_status = 'completed')
      OR (a_status = 'cancelled' AND i_status = 'cancelled')
      OR (a_status = 'no_show'   AND i_status = 'cancelled'
          AND cancelled_reason = 'no_show')))
      AS stato_incompatibile,
  count(*) FILTER (WHERE a_status IN ('requested', 'scheduled', 'confirmed')
                     AND i_status = 'scheduled'
                     AND scheduled_for IS DISTINCT FROM start_at)
      AS scheduled_for_diverso_da_start_at,
  count(*) FILTER (WHERE a_status = 'completed' AND i_status = 'completed'
                     AND i_completed_at IS DISTINCT FROM a_completed_at)
      AS completed_at_diverso,
  count(*) FILTER (WHERE a_status = 'cancelled' AND i_status = 'cancelled'
                     AND i_cancelled_at IS DISTINCT FROM a_cancelled_at)
      AS cancelled_at_diverso,
  count(*) FILTER (WHERE a_status = 'no_show' AND i_status = 'cancelled'
                     AND i_cancelled_at IS DISTINCT FROM no_show_at)
      AS no_show_at_diverso,
  count(*) FILTER (WHERE i_id IS NOT NULL
                     AND (i_stima IS DISTINCT FROM a_stima
                          OR i_agency IS DISTINCT FROM agency_id))
      AS stima_o_agenzia_diversa,
  (SELECT count(*) FROM appointments WHERE status = 'rescheduled'
                                      AND stima_inspection_id IS NOT NULL)
      AS rescheduled_ancora_collegato,
  -- gli orfani senza stima (Q2) non sono importabili e restano fuori
  (SELECT count(*) FROM stima_inspections i2
    WHERE i2.stima_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM appointments a2
                       WHERE a2.stima_inspection_id = i2.id))
      AS lmc15_con_stima_non_rappresentati
  FROM coppie
"""

TABELLE = ("appointments", "appointment_events", "stima_inspections", "seller_timeline_events")


class CertificationFailure(Exception):
    """Una verifica della certificazione non e' passata."""


def _verifica(condizione, messaggio):
    if not condizione:
        raise CertificationFailure(messaggio)


def _uno(cur, sql, par=None):
    cur.execute(sql, par)
    return cur.fetchone()


def _tutti_zero(riga) -> bool:
    return all(v == 0 for v in dict(riga).values())


def _hash_tabelle(cur) -> dict:
    """Informativo, MAI un gate: il TEST puo' avere attivita' concorrente."""
    esito = {}
    for t in TABELLE:
        riga = _uno(cur, f"SELECT count(*) AS n, md5(coalesce(string_agg(x::text, '|' "
                         f"ORDER BY x.id), '')) AS h FROM {t} x")
        esito[t] = {"rows": riga["n"], "md5": riga["h"]}
    return esito


# ---------------------------------------------------------------------------
# GUARDIE (sola lettura, prima di qualunque scrittura)
# ---------------------------------------------------------------------------

def check_preconditions(cur, database_name: str, *, agency_id: int, operator_user_id: int,
                        stima_id: int) -> dict:
    riga = _uno(cur, "SELECT current_database() AS db, "
                     "a30_is_certified_test_database() AS certificato")
    if riga["db"] != database_name or not riga["certificato"]:
        raise GuardFailure(f"BLOCKED: connected to {riga['db']!r}, "
                           f"certified={riga['certificato']}: expected {database_name!r}.")
    applicate = _uno(cur, "SELECT count(*) AS n FROM schema_migrations "
                          "WHERE version = ANY(%s) AND rolled_back_at IS NULL",
                     (list(MIGRAZIONI),))["n"]
    if applicate != len(MIGRAZIONI):
        raise GuardFailure(f"BLOCKED: {MIGRAZIONI} not both applied in the ledger.")
    if not _uno(cur, "SELECT count(*) AS n FROM pg_constraint WHERE conrelid = "
                     "'appointments'::regclass AND conname = 'appointments_lmc15_facade_chk'")["n"]:
        raise GuardFailure("BLOCKED: appointments_lmc15_facade_chk is missing.")
    membro = _uno(cur, "SELECT m.role FROM agency_memberships m "
                       "JOIN operator_users u ON u.id = m.operator_user_id "
                       "WHERE m.agency_id = %s AND m.operator_user_id = %s "
                       "AND m.status = 'active' AND u.status = 'active'",
                  (agency_id, operator_user_id))
    if membro is None:
        raise GuardFailure("BLOCKED: the operator is not ACTIVE with an ACTIVE membership "
                           "in the agency.")
    if not _uno(cur, "SELECT count(*) AS n FROM stime WHERE id = %s AND agency_id = %s",
                (stima_id, agency_id))["n"]:
        raise GuardFailure("BLOCKED: the estimate does not belong to the agency.")
    facade = _uno(cur, "SELECT count(*) AS n FROM appointments "
                       "WHERE source = 'lmc15_facade'")["n"]
    if facade:
        raise GuardFailure(f"BLOCKED: {facade} lmc15_facade appointment(s) already exist.")
    q9 = dict(_uno(cur, Q9))
    if not _tutti_zero(q9):
        raise GuardFailure(f"BLOCKED: Q9 is not 0/0: {q9}")
    q10 = dict(_uno(cur, Q10))
    if not _tutti_zero(q10):
        raise GuardFailure(f"BLOCKED: Q10 is not all 0: {q10}")
    return {"operator_role": membro["role"], "q5": _uno(cur, Q5)["n"], "q7": dict(_uno(cur, Q7)), "q7b": dict(_uno(cur, Q7B)),
            "q9": q9, "q10": q10}


# ---------------------------------------------------------------------------
# LA TRANSAZIONE ESTERNA E IL SUO INVOLUCRO
# ---------------------------------------------------------------------------

class _ConnessioneCondivisa:
    """Cio' che il codice riceve da `core.database.get_connection()`: la
    STESSA connessione della transazione esterna, dentro un SAVEPOINT proprio
    (uno per blocco `core_cursor`, come una transazione reale). `commit()`
    rilascia il SAVEPOINT - nulla viene confermato: la transazione esterna
    resta aperta; `rollback()` torna al SAVEPOINT (annulla solo quel blocco,
    come farebbe il ROLLBACK reale); `close()` senza commit annulla il blocco,
    e non chiude la connessione."""

    def __init__(self, conn):
        self._conn = conn
        self._aperto = True
        self._esegui(f"SAVEPOINT {SAVEPOINT_CHIAMATA}")

    def _esegui(self, sql):
        with self._conn.cursor() as cur:
            cur.execute(sql)

    def cursor(self, *a, **k):
        return self._conn.cursor(*a, **k)

    def commit(self):
        if self._aperto:
            self._esegui(f"RELEASE SAVEPOINT {SAVEPOINT_CHIAMATA}")
            self._aperto = False

    def rollback(self):
        if self._aperto:
            self._esegui(f"ROLLBACK TO SAVEPOINT {SAVEPOINT_CHIAMATA}")
            self._esegui(f"RELEASE SAVEPOINT {SAVEPOINT_CHIAMATA}")
            self._aperto = False

    def close(self):
        self.rollback()


def _vietata(*_a, **_k):
    raise RuntimeError("A30-2P live cert: an extra database connection was attempted")


def _app(ctx):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from acquisition.router import router
    from operator_auth.dependencies import require_operator

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_operator] = lambda: ctx
    return TestClient(app, raise_server_exceptions=False)


def _scenario(conn, nome, funzione, registro):
    """Un SAVEPOINT per scenario: rilasciato se lo scenario passa."""
    with conn.cursor() as cur:
        cur.execute(f"SAVEPOINT {SAVEPOINT}")
    registro["current_scenario"] = nome
    funzione()
    registro["current_scenario"] = None
    with conn.cursor() as cur:
        cur.execute(f"RELEASE SAVEPOINT {SAVEPOINT}")
    registro["scenarios"].append(nome)


def _registra(cur, registro, inspection_id):
    """Registra ogni id generato per un sopralluogo, in tutte e 4 le tabelle."""
    registro["ids"]["stima_inspections"].add(inspection_id)
    cur.execute("SELECT id FROM appointments WHERE stima_inspection_id = %s", (inspection_id,))
    appuntamenti = [r["id"] for r in cur.fetchall()]
    registro["ids"]["appointments"].update(appuntamenti)
    if appuntamenti:
        cur.execute("SELECT id FROM appointment_events WHERE appointment_id = ANY(%s)",
                    (appuntamenti,))
        registro["ids"]["appointment_events"].update(r["id"] for r in cur.fetchall())
    cur.execute("SELECT id FROM seller_timeline_events WHERE idempotency_key LIKE %s",
                (f"lmc15:v1:%:insp:{inspection_id}",))
    registro["ids"]["seller_timeline_events"].update(r["id"] for r in cur.fetchall())


def run_scenarios(conn, *, agency_id, operator_user_id, operator_role, stima_id,
                  registro) -> None:
    from psycopg2.extras import RealDictCursor

    from operator_auth.context import OperatorContext

    ctx = OperatorContext(user_id=operator_user_id, agency_id=agency_id, role=operator_role,
                          is_platform_admin=False, session_id=None, auth_channel="session")
    client = _app(ctx)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    ora = _uno(cur, "SELECT NOW() AS t")["t"]
    base = f"/api/acquisition/stime/{stima_id}/inspections"

    def conta_stima():
        return _uno(cur, "SELECT (SELECT count(*) FROM stima_inspections WHERE stima_id = %s) "
                         "+ (SELECT count(*) FROM seller_timeline_events WHERE stima_id = %s) "
                         "AS n", (stima_id, stima_id))["n"]

    def crea(quando):
        r = client.post(base, json={"scheduled_for": quando.isoformat()})
        _verifica(r.status_code == 201, f"create scheduled: {r.status_code} {r.text}")
        corpo = r.json()
        _verifica(list(corpo) == COLONNE, f"create scheduled: columns {list(corpo)}")
        _registra(cur, registro, corpo["id"])
        return corpo

    def appuntamento(inspection_id):
        return _uno(cur, "SELECT * FROM appointments WHERE stima_inspection_id = %s",
                    (inspection_id,))

    def s1_create_scheduled():
        isp = crea(ora + timedelta(days=7))
        a = appuntamento(isp["id"])
        _verifica(a is not None and a["source"] == "lmc15_facade"
                  and a["status"] == "scheduled" and a["assigned_user_id"] is None
                  and a["stima_id"] == stima_id
                  and a["source_record_id"] == f"stima_inspections:{isp['id']}"
                  and a["end_at"] - a["start_at"] == timedelta(minutes=60),
                  f"create scheduled: appointment {a}")
        registro["s1"] = isp["id"]

    def s2_complete():
        fatto = ora - timedelta(hours=1)                 # prima dell'inizio: ammesso
        r = client.post(f"/api/acquisition/inspections/{registro['s1']}/complete",
                        json={"completed_at": fatto.isoformat()})
        _verifica(r.status_code == 200 and r.json()["status"] == "completed",
                  f"complete: {r.status_code} {r.text}")
        a = appuntamento(registro["s1"])
        i = _uno(cur, "SELECT completed_at FROM stima_inspections WHERE id = %s",
                 (registro["s1"],))
        _verifica(a["status"] == "completed" and a["completed_at"] == i["completed_at"] == fatto,
                  "complete: completed_at not identical")
        _registra(cur, registro, registro["s1"])

    def s3_create_cancel():
        isp = crea(ora + timedelta(days=8))
        r = client.post(f"/api/acquisition/inspections/{isp['id']}/cancel", json={})
        _verifica(r.status_code == 200 and r.json()["cancelled_reason"] is None,
                  f"cancel: {r.status_code} {r.text}")
        a = appuntamento(isp["id"])
        i = _uno(cur, "SELECT cancelled_at FROM stima_inspections WHERE id = %s", (isp["id"],))
        _verifica(a["status"] == "cancelled" and a["cancelled_reason"] is None
                  and a["cancelled_at"] == i["cancelled_at"], "cancel: cancelled_at not identical")
        _registra(cur, registro, isp["id"])

    def s4_posthoc():
        fatto = ora - timedelta(days=2)
        r = client.post(f"{base}/completed", json={"completed_at": fatto.isoformat()})
        _verifica(r.status_code == 201 and r.json()["status"] == "completed",
                  f"posthoc: {r.status_code} {r.text}")
        _registra(cur, registro, r.json()["id"])
        a = appuntamento(r.json()["id"])
        _verifica(a["status"] == "completed" and a["start_at"] == fatto == a["completed_at"],
                  "posthoc: start_at/completed_at")

    def s5_f2():
        isp = crea(ora + timedelta(days=9))
        prima = conta_stima()
        futuro_ = (ora + timedelta(days=1)).isoformat()
        for url in (f"{base}/completed", f"/api/acquisition/inspections/{isp['id']}/complete"):
            r = client.post(url, json={"completed_at": futuro_})
            _verifica(r.status_code == 422 and set(r.json()) == {"detail"}
                      and r.json()["detail"].startswith("COMPLETED_AT_INVALID"),
                      f"F2 {url}: {r.status_code} {r.text}")
        _verifica(conta_stima() == prima, "F2: something was written")
        _verifica(appuntamento(isp["id"])["status"] == "scheduled", "F2: appointment changed")

    def s6_rollback_atomico():
        from appointments import repository

        prima = conta_stima()
        originale = repository.insert_appointment

        def guasto(*_a, **_k):
            raise RuntimeError("A30-2P live cert: simulated failure after the LMC-15 write")

        repository.insert_appointment = guasto
        try:
            r = client.post(base, json={"scheduled_for": (ora + timedelta(days=10)).isoformat()})
        finally:
            repository.insert_appointment = originale
        _verifica(r.status_code == 500, f"rollback: expected 500, got {r.status_code}")
        _verifica(conta_stima() == prima, "rollback: the LMC-15 write survived")

    def s7_q10():
        q10 = dict(_uno(cur, Q10))
        registro["q10_in_transaction"] = q10
        _verifica(_tutti_zero(q10), f"Q10 inside the transaction: {q10}")

    for nome, funzione in (("create_scheduled", s1_create_scheduled), ("complete", s2_complete),
                           ("create_cancel_no_reason", s3_create_cancel),
                           ("create_completed_posthoc", s4_posthoc), ("f2_both_routes", s5_f2),
                           ("atomic_rollback", s6_rollback_atomico), ("q10_zero", s7_q10)):
        _scenario(conn, nome, funzione, registro)


# ---------------------------------------------------------------------------
# VERIFICA DOPO IL ROLLBACK (connessione nuova, sola lettura)
# ---------------------------------------------------------------------------

def verify_after_rollback(cur, registro, prima: dict) -> dict:
    cur.execute("SET TRANSACTION READ ONLY")
    sopravvissuti = {}
    for tabella, ids in registro["ids"].items():
        if ids:
            cur.execute(f"SELECT id FROM {tabella} WHERE id = ANY(%s)", (sorted(ids),))
            trovati = [r["id"] for r in cur.fetchall()]
            if trovati:
                sopravvissuti[tabella] = trovati
    esito = {
        "surviving_ids": sopravvissuti,
        "lmc15_facade": _uno(cur, "SELECT count(*) AS n FROM appointments "
                                  "WHERE source = 'lmc15_facade'")["n"],
        "q5": _uno(cur, Q5)["n"], "q7": dict(_uno(cur, Q7)), "q7b": dict(_uno(cur, Q7B)),
        "q9": dict(_uno(cur, Q9)), "q10": dict(_uno(cur, Q10)),
    }
    esito["passed"] = (not sopravvissuti and esito["lmc15_facade"] == 0
                       and esito["q5"] == prima["q5"] and _tutti_zero(esito["q7"])
                       and _tutti_zero(esito["q7b"]) and _tutti_zero(esito["q9"])
                       and _tutti_zero(esito["q10"]))
    return esito


# ---------------------------------------------------------------------------
# ORCHESTRAZIONE
# ---------------------------------------------------------------------------

def certify(apri, database_name: str, *, agency_id: int, operator_user_id: int,
            stima_id: int) -> dict:
    """`apri()` restituisce una connessione NUOVA al database (autocommit off)."""
    from psycopg2.extras import RealDictCursor

    import database as radice
    from core import database as core_database

    registro = {"scenarios": [], "ids": {t: set() for t in TABELLE}}
    report = {"database": database_name, "agency_id": agency_id,
              "operator_user_id": operator_user_id, "stima_id": stima_id}
    conn = apri()
    originali = (core_database.get_connection, getattr(radice, "get_connection", None))
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
            cur.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
            if not _uno(cur, "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0)) AS ok",
                        (LOCK_KEY,))["ok"]:
                raise GuardFailure("BLOCKED: another facade live certification is running.")
            prima = check_preconditions(cur, database_name, agency_id=agency_id,
                                        operator_user_id=operator_user_id, stima_id=stima_id)
            report["before"] = prima
            report["hashes_before_informational"] = _hash_tabelle(cur)
        core_database.get_connection = lambda: _ConnessioneCondivisa(conn)
        if originali[1] is not None:
            radice.get_connection = _vietata
        try:
            run_scenarios(conn, agency_id=agency_id, operator_user_id=operator_user_id,
                          operator_role=prima["operator_role"], stima_id=stima_id,
                          registro=registro)
            report["in_transaction"] = "passed"
        except Exception as exc:                        # noqa: BLE001 - riportato, mai confermato
            report["in_transaction"] = f"FAILED: {type(exc).__name__}: {exc}"
    finally:
        core_database.get_connection = originali[0]
        if originali[1] is not None:
            radice.get_connection = originali[1]
        conn.rollback()                                 # SEMPRE: nulla sopravvive
        conn.close()
    report["scenarios"] = registro["scenarios"]
    report["failed_scenario"] = registro.get("current_scenario")
    report["q10_in_transaction"] = registro.get("q10_in_transaction")
    report["created_ids"] = {t: sorted(v) for t, v in registro["ids"].items()}

    verifica = apri()
    try:
        with verifica.cursor(cursor_factory=RealDictCursor) as cur:
            report["after_rollback"] = verify_after_rollback(cur, registro, report["before"])
            report["hashes_after_informational"] = _hash_tabelle(cur)
    finally:
        verifica.rollback()
        verifica.close()
    report["passed"] = (report["in_transaction"] == "passed"
                        and report["after_rollback"]["passed"])
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--agency-id", type=int, required=True)
    parser.add_argument("--operator-user-id", type=int, required=True)
    parser.add_argument("--stima-id", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        database_name = assert_certified_test_database(os.getenv("DB_NAME"))
    except GuardFailure as exc:
        print(exc, file=sys.stderr)
        return 2
    try:
        report = certify(lambda: connect(database_name), database_name,
                         agency_id=args.agency_id, operator_user_id=args.operator_user_id,
                         stima_id=args.stima_id)
    except GuardFailure as exc:
        print(exc, file=sys.stderr)
        return 2
    print(json.dumps(report, default=str, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
