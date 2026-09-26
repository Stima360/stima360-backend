"""A30-2 su PostgreSQL reale e via HTTP: service, API, state machine, visibilita',
idempotenza, disponibilita', proiezione (accesa SOLO dentro i test dedicati).

Il router NON e' montato in `main.py` (D1): qui lo si include in
un'applicazione FastAPI creata dal test, e `require_operator` e' sostituito
da un contesto operatore costruito dal test (la stessa classe
`OperatorContext` della produzione).

Opt-in: senza `P29_TEST_DSN` si salta tutto. Database usa-e-getta.
"""
from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

DSN = os.getenv("P29_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare A30-2")

ROOT = Path(__file__).resolve().parents[1]
MIGRAZIONI = ROOT / "migrations"
ROMA = ZoneInfo("Europe/Rome")
#: L'OROLOGIO DEI TEST (gate A30-2, correzione B). Nessuna data fissa: si
#: ancora al NOW() del PostgreSQL di test, letto dal fixture `db`, cosi' la
#: suite non dipende dal giorno in cui gira.
#:  - GIORNO: un giorno di almeno una settimana PRIMA di db_now, lontano dai
#:    cambi d'ora; gli appuntamenti di prova ci stanno sopra, quindi sono nel
#:    passato per il database (completare e' possibile, `completed_at` <= NOW()).
#:  - ORA: il clock di processo INIETTATO nel service (`service._adesso`),
#:    mezzogiorno di GIORNO.
#:  - futuro(): orari su un giorno sicuramente DOPO db_now, per provare le
#:    guardie che leggono il NOW() del database.
#: Tutti e tre sono impostati da `_ancora_orologio`, prima di ogni test.
GIORNO = None
ORA = None
GIORNI_AL_FUTURO = None


def _giorno_senza_cambio_ora(giorno):
    """Vero se `giorno` e i due successivi hanno 24 ore (niente DST)."""
    for i in range(3):
        inizio = datetime.combine(giorno + timedelta(days=i), time(0), tzinfo=ROMA)
        fine = datetime.combine(giorno + timedelta(days=i + 1), time(0), tzinfo=ROMA)
        if fine - inizio != timedelta(hours=24) or inizio.utcoffset() != fine.utcoffset():
            return False
    return True


def _ancora_orologio(db_now):
    global GIORNO, ORA, GIORNI_AL_FUTURO
    oggi = db_now.astimezone(ROMA).date()
    giorno = oggi - timedelta(days=7)
    while not _giorno_senza_cambio_ora(giorno):
        giorno -= timedelta(days=1)
    GIORNO = datetime.combine(giorno, time(0), tzinfo=ROMA)
    ORA = GIORNO.replace(hour=12)
    # due giorni dopo "oggi" del database: sempre nel futuro di db_now
    GIORNI_AL_FUTURO = (oggi - giorno).days + 2


#: Le colonne vere che il servizio legge e che lo schema minimo condiviso non
#: ha (sono nelle migration 001/002/027): aggiunte solo qui.
COLONNE_REALI = """
ALTER TABLE operator_users ADD COLUMN first_name VARCHAR(100), ADD COLUMN last_name VARCHAR(100);
ALTER TABLE contacts ADD COLUMN first_name VARCHAR(100), ADD COLUMN last_name VARCHAR(100),
    ADD COLUMN company_name VARCHAR(200), ADD COLUMN phone VARCHAR(50),
    ADD COLUMN phone_normalized VARCHAR(50);
ALTER TABLE leads ADD COLUMN stage VARCHAR(30) NOT NULL DEFAULT 'new',
    ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'open';
ALTER TABLE properties ADD COLUMN address VARCHAR(250), ADD COLUMN civic_number VARCHAR(30),
    ADD COLUMN city VARCHAR(120);
"""


def ore(h, m=0, giorni=0, s=0, us=0):
    giorno = (GIORNO + timedelta(days=giorni)).date()
    return datetime.combine(giorno, time(h, m, s, us), tzinfo=ROMA)


def futuro(h, m=0):
    """Un orario certamente successivo al NOW() del database."""
    return ore(h, m, giorni=GIORNI_AL_FUTURO)


def chiave():
    return str(uuid.uuid4())


def _dsn_per(nome):
    if "?" in DSN:
        base, query = DSN.split("?", 1)
        return base.rsplit("/", 1)[0] + "/" + nome + "?" + query
    return DSN.rsplit("/", 1)[0] + "/" + nome


@pytest.fixture(scope="module")
def db():
    psycopg2 = pytest.importorskip("psycopg2")
    from tests.test_lmc15_acquisition_bridge_postgres import CATENA, SCHEMA_MINIMO

    nome = f"a30_2_probe_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    servizio = psycopg2.connect(DSN)
    servizio.autocommit = True
    with servizio.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{nome}"')
    dsn = _dsn_per(nome)
    from psycopg2.extras import DictCursor
    conn = psycopg2.connect(dsn, cursor_factory=DictCursor)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT NOW()")
        _ancora_orologio(cur.fetchone()[0])
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_MINIMO)
            cur.execute(COLONNE_REALI)
            for versione in CATENA:
                cur.execute((MIGRAZIONI / f"{versione}.sql").read_text(encoding="utf-8"))
            cur.execute((MIGRAZIONI / "072_a30_1_appointments.sql").read_text(encoding="utf-8"))
        yield {"conn": conn, "dsn": dsn}
    finally:
        conn.close()
        with servizio.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = %s AND pid <> pg_backend_pid()", (nome,))
            cur.execute(f'DROP DATABASE IF EXISTS "{nome}"')
        servizio.close()


def _svuota(cur):
    for trg, tab in (("trg_appointments_refuse_delete", "appointments"),
                     ("trg_appointment_events_append_only", "appointment_events")):
        cur.execute(f"ALTER TABLE {tab} DISABLE TRIGGER {trg}")
    cur.execute("DELETE FROM appointment_events")
    cur.execute("DELETE FROM appointments")
    for trg, tab in (("trg_appointments_refuse_delete", "appointments"),
                     ("trg_appointment_events_append_only", "appointment_events")):
        cur.execute(f"ALTER TABLE {tab} ENABLE TRIGGER {trg}")


@pytest.fixture
def mondo(db, monkeypatch):
    import psycopg2

    from appointments import service
    from core import database as core_database
    from operator_auth.context import OperatorContext

    monkeypatch.setattr(core_database, "get_connection", lambda: psycopg2.connect(db["dsn"]))
    monkeypatch.setattr(service, "_adesso", lambda: ORA)

    conn = db["conn"]
    with conn.cursor() as cur:
        _svuota(cur)
        for t in ("stima_inspections", "stima_acquisitions", "seller_timeline_events",
                  "leads", "stime", "properties", "contacts", "agency_memberships",
                  "operator_users", "agencies"):
            cur.execute(f"DELETE FROM {t}")
        cur.execute("INSERT INTO agencies (slug) VALUES ('a-uno') RETURNING id")
        a = cur.fetchone()[0]
        cur.execute("INSERT INTO agencies (slug) VALUES ('b-due') RETURNING id")
        b = cur.fetchone()[0]

        def operatore(email, nome, agenzia, ruolo):
            cur.execute("INSERT INTO operator_users (email, first_name, last_name) "
                        "VALUES (%s, %s, 'Test') RETURNING id", (email, nome))
            i = cur.fetchone()[0]
            cur.execute("INSERT INTO agency_memberships (agency_id, operator_user_id, role) "
                        "VALUES (%s,%s,%s)", (agenzia, i, ruolo))
            return i

        ids = {"a": a, "b": b,
               "giorgio": operatore("giorgio@example.it", "Giorgio", a, "agency_owner"),
               "luca": operatore("luca@example.it", "Luca", a, "agent"),
               "marta": operatore("marta@example.it", "Marta", a, "agent"),
               "estraneo": operatore("x@example.it", "Estraneo", b, "agent")}
        cur.execute("INSERT INTO contacts (agency_id, display_name, phone, phone_normalized, "
                    "email) VALUES (%s,'Mario Rossi','333 1234567','393331234567',"
                    "'mario@example.it') RETURNING id", (a,))
        ids["mario"] = cur.fetchone()[0]
        cur.execute("INSERT INTO contacts (agency_id, display_name) VALUES (%s,'Bruno') "
                    "RETURNING id", (a,))
        ids["bruno"] = cur.fetchone()[0]
        cur.execute("INSERT INTO contacts (agency_id, display_name) VALUES (%s,'Altro') "
                    "RETURNING id", (b,))
        ids["contatto_b"] = cur.fetchone()[0]
        cur.execute("INSERT INTO leads (contact_id, agency_id, pipeline) VALUES (%s,%s,'sell') "
                    "RETURNING id", (ids["mario"], a))
        ids["lead_mario"] = cur.fetchone()[0]
        cur.execute("INSERT INTO properties (agency_id, title, city) VALUES (%s,'Bilocale',"
                    "'Alba Adriatica') RETURNING id", (a,))
        ids["casa"] = cur.fetchone()[0]
        cur.execute("INSERT INTO stime (agency_id, comune, via, mq) VALUES (%s,'Tortoreto',"
                    "'Via Trieste',95) RETURNING id", (a,))
        ids["stima"] = cur.fetchone()[0]
    conn.commit()

    def ctx(chi, *, agenzia=None, platform=False):
        ruolo = {"giorgio": "agency_owner", "luca": "agent", "marta": "agent",
                 "estraneo": "agent"}.get(chi)
        return OperatorContext(
            user_id=None if chi is None else ids[chi],
            agency_id=agenzia if agenzia is not None else (ids["b"] if chi == "estraneo"
                                                           else ids["a"]),
            role=ruolo, is_platform_admin=platform, session_id=None,
            auth_channel="session")

    def sql(testo, par=None):
        with conn.cursor() as cur:
            cur.execute(testo, par)
            r = cur.fetchall() if cur.description else None
        conn.commit()
        return r

    return {**ids, "ctx": ctx, "sql": sql, "conn": conn, "service": service}


@pytest.fixture
def http(mondo):
    """Il router su un'app FastAPI di TEST. `main.py` non lo monta."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from appointments.router import router
    from operator_auth.dependencies import require_operator

    app = FastAPI()
    app.include_router(router)
    stato = {"ctx": mondo["ctx"]("giorgio")}
    app.dependency_overrides[require_operator] = lambda: stato["ctx"]
    client = TestClient(app)

    def come(chi, **kw):
        stato["ctx"] = mondo["ctx"](chi, **kw)
        return client

    return come


def _nuovo(**kw):
    base = {"appointment_type": "seller_meeting", "assigned_user_id": None,
            "start_at": ore(10).isoformat(), "end_at": ore(11).isoformat(),
            "client_request_id": chiave()}
    base.update(kw)
    return base


def _crea(http, chi="giorgio", **kw):
    r = http(chi).post("/api/appointments", json=_nuovo(**kw))
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# A - CREAZIONE E IDEMPOTENZA (§6)
# ---------------------------------------------------------------------------

def test_01_crea_201_e_registra_l_evento(http, mondo):
    r = _crea(http, assigned_user_id=mondo["luca"], contact_id=mondo["mario"])
    assert r["source"] == "crm_manual" and r["created_by_user_id"] == mondo["giorgio"]
    ev = mondo["sql"]("SELECT event_type, actor_user_id FROM appointment_events "
                      "WHERE appointment_id=%s", (r["id"],))
    assert [tuple(e) for e in ev] == [("created", mondo["giorgio"])]


def test_02_replica_stessa_richiesta_200_senza_nuovi_eventi(http, mondo):
    corpo = _nuovo(assigned_user_id=mondo["luca"])
    primo = http("giorgio").post("/api/appointments", json=corpo)
    secondo = http("giorgio").post("/api/appointments", json=corpo)
    assert primo.status_code == 201 and secondo.status_code == 200
    assert secondo.headers.get("Idempotent-Replay") == "true"
    assert secondo.json()["id"] == primo.json()["id"]
    assert mondo["sql"]("SELECT count(*) FROM appointments")[0][0] == 1
    assert mondo["sql"]("SELECT count(*) FROM appointment_events")[0][0] == 1


def test_03_stessa_chiave_dati_diversi_o_altro_attore_409_senza_dati(http, mondo):
    corpo = _nuovo(assigned_user_id=mondo["luca"])
    http("giorgio").post("/api/appointments", json=corpo)
    diverso = dict(corpo, notes="altro")
    r = http("giorgio").post("/api/appointments", json=diverso)
    assert r.status_code == 409 and r.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    # un'altra agenzia con la stessa chiave: stesso messaggio, nessun dato
    r2 = http("estraneo").post("/api/appointments",
                               json=dict(corpo, assigned_user_id=mondo["estraneo"]))
    assert r2.status_code == 409 and r2.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert set(r2.json()) == {"detail", "code"}
    assert r2.json()["detail"] == r.json()["detail"]


def test_04_chiave_mancante_o_malformata_422(http, mondo):
    corpo = _nuovo(assigned_user_id=mondo["luca"])
    del corpo["client_request_id"]
    r = http("giorgio").post("/api/appointments", json=corpo)
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_ERROR"
    r = http("giorgio").post("/api/appointments",
                             json=_nuovo(assigned_user_id=mondo["luca"], client_request_id="x"))
    assert r.status_code == 422


def test_05_due_richieste_identiche_simultanee_una_sola_riga(http, mondo):
    """Con agente il lock le mette in fila e la seconda rilegge la chiave;
    senza agente (requested) decide l'indice unico."""
    for agente, stato in ((mondo["luca"], "scheduled"), (None, "requested")):
        corpo = _nuovo(assigned_user_id=agente, status=stato,
                       start_at=ore(15).isoformat(), end_at=ore(16).isoformat())
        esiti = []

        def invia():
            esiti.append(http("giorgio").post("/api/appointments", json=corpo).status_code)

        fili = [threading.Thread(target=invia) for _ in range(4)]
        for f in fili:
            f.start()
        for f in fili:
            f.join(timeout=20)
        assert sorted(esiti) == [200, 200, 200, 201], esiti
        n = mondo["sql"]("SELECT count(*) FROM appointments WHERE source_record_id=%s",
                         (corpo["client_request_id"],))[0][0]
        assert n == 1


def test_06_d2_senza_agente_solo_richiesta(http, mondo):
    r = http("giorgio").post("/api/appointments", json=_nuovo(status="scheduled"))
    assert r.status_code == 422 and r.json()["code"] == "AGENT_REQUIRED"
    richiesta = _crea(http, status="requested")
    assert richiesta["status"] == "requested" and richiesta["assigned_user_id"] is None


def test_07_errori_di_forma(http, mondo):
    r = http("giorgio").post("/api/appointments",
                             json=_nuovo(assigned_user_id=mondo["luca"],
                                         start_at="2026-09-14T10:00:00"))
    assert r.status_code == 422 and r.json()["code"] == "TIMEZONE_REQUIRED"
    r = http("giorgio").post("/api/appointments",
                             json=_nuovo(assigned_user_id=mondo["luca"], agency_id=mondo["b"]))
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_ERROR"
    r = http("giorgio").post("/api/appointments",
                             json=_nuovo(assigned_user_id=mondo["estraneo"]))
    assert r.status_code == 422 and r.json()["code"] == "AGENT_NOT_ACTIVE"
    r = http("luca").post("/api/appointments", json=_nuovo(assigned_user_id=mondo["marta"]))
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN_ROLE"
    r = http("giorgio").post("/api/appointments",
                             json=_nuovo(assigned_user_id=mondo["luca"],
                                         contact_id=mondo["contatto_b"]))
    assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND"
    r = http("giorgio").post("/api/appointments",
                             json=_nuovo(assigned_user_id=mondo["luca"],
                                         contact_id=mondo["bruno"],
                                         lead_id=mondo["lead_mario"]))
    assert r.status_code == 422 and r.json()["code"] == "LINK_MISMATCH"


def test_08_conflitto_409_con_conflitti_e_alternative(http, mondo):
    primo = _crea(http, assigned_user_id=mondo["luca"])
    r = http("giorgio").post("/api/appointments",
                             json=_nuovo(assigned_user_id=mondo["luca"],
                                         start_at=ore(10, 30).isoformat(),
                                         end_at=ore(11, 30).isoformat()))
    assert r.status_code == 409
    corpo = r.json()
    assert corpo["code"] == "APPOINTMENT_CONFLICT"
    assert [c["id"] for c in corpo["conflicts"]] == [primo["id"]]
    alt = [datetime.fromisoformat(a["start_at"]).astimezone(ROMA).strftime("%H:%M")
           for a in corpo["alternatives"]]
    assert alt == ["11:00", "11:15", "11:30"]


# ---------------------------------------------------------------------------
# B - TRANSIZIONI
# ---------------------------------------------------------------------------

def test_10_schedule_richiesta_con_agente_esplicito(http, mondo):
    req = _crea(http, status="requested")
    r = http("giorgio").post(f"/api/appointments/{req['id']}/schedule",
                             json={"version": req["version"], "start_at": ore(14).isoformat(),
                                   "end_at": ore(15).isoformat()})
    assert r.status_code == 422 and r.json()["code"] == "AGENT_REQUIRED"
    r = http("giorgio").post(f"/api/appointments/{req['id']}/schedule",
                             json={"version": req["version"], "assigned_user_id": mondo["luca"],
                                   "start_at": ore(14).isoformat(), "end_at": ore(15).isoformat()})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "scheduled" and r.json()["assigned_user_id"] == mondo["luca"]
    assert r.json()["id"] == req["id"]


def test_11_confirm_idempotente_e_version_conflict(http, mondo):
    a = _crea(http, assigned_user_id=mondo["luca"])
    r = http("giorgio").post(f"/api/appointments/{a['id']}/confirm", json={"version": a["version"]})
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    v = r.json()["version"]
    r2 = http("giorgio").post(f"/api/appointments/{a['id']}/confirm", json={"version": v})
    assert r2.status_code == 200 and r2.json()["version"] == v
    vecchia = http("giorgio").post(f"/api/appointments/{a['id']}/confirm",
                                   json={"version": a["version"]})
    assert vecchia.status_code == 409 and vecchia.json()["code"] == "VERSION_CONFLICT"
    assert vecchia.json()["current_version"] == v


def test_12_reassign_stessa_riga_stato_invariato_d5(http, mondo):
    a = _crea(http, assigned_user_id=mondo["luca"])
    c = http("giorgio").post(f"/api/appointments/{a['id']}/confirm",
                             json={"version": a["version"]}).json()
    r = http("giorgio").post(f"/api/appointments/{a['id']}/reassign",
                             json={"version": c["version"], "assigned_user_id": mondo["marta"]})
    assert r.status_code == 200, r.text
    assert r.json()["id"] == a["id"] and r.json()["status"] == "confirmed"
    assert r.json()["assigned_user_id"] == mondo["marta"]
    assert mondo["sql"]("SELECT count(*) FROM appointments")[0][0] == 1
    ev = mondo["sql"]("SELECT event_type, changes FROM appointment_events "
                      "WHERE appointment_id=%s ORDER BY id DESC LIMIT 1", (a["id"],))[0]
    assert ev[0] == "updated" and ev[1]["azione"] == "reassign"
    assert ev[1]["assigned_user_id"] == {"da": mondo["luca"], "a": mondo["marta"]}


def test_13_reassign_controlla_la_disponibilita_ed_e_solo_per_owner_admin(http, mondo):
    a = _crea(http, assigned_user_id=mondo["luca"])
    _crea(http, assigned_user_id=mondo["marta"])     # Marta occupata 10-11
    r = http("giorgio").post(f"/api/appointments/{a['id']}/reassign",
                             json={"version": a["version"], "assigned_user_id": mondo["marta"]})
    assert r.status_code == 409 and r.json()["code"] == "APPOINTMENT_CONFLICT"
    r = http("luca").post(f"/api/appointments/{a['id']}/reassign",
                          json={"version": a["version"], "assigned_user_id": mondo["luca"]})
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN_ROLE"
    # una richiesta si assegna senza controllo di disponibilita' (non occupa)
    req = _crea(http, status="requested")
    r = http("giorgio").post(f"/api/appointments/{req['id']}/reassign",
                             json={"version": req["version"], "assigned_user_id": mondo["marta"]})
    assert r.status_code == 200 and r.json()["status"] == "requested"


def test_14_reschedule_via_http_nuova_riga(http, mondo):
    a = _crea(http, assigned_user_id=mondo["luca"])
    r = http("giorgio").post(f"/api/appointments/{a['id']}/reschedule",
                             json={"version": a["version"], "start_at": ore(16).isoformat(),
                                   "end_at": ore(17).isoformat()})
    assert r.status_code == 201 and r.json()["rescheduled_from_id"] == a["id"]
    r2 = http("giorgio").post(f"/api/appointments/{a['id']}/reschedule",
                              json={"version": a["version"] + 1, "start_at": ore(18).isoformat(),
                                    "end_at": ore(19).isoformat()})
    assert r2.status_code == 409 and r2.json()["code"] == "INVALID_TRANSITION"


def test_15_cancel_motivo_obbligatorio_per_sopralluogo_con_stima(http, mondo):
    req = _crea(http, status="requested", appointment_type="inspection", stima_id=mondo["stima"])
    r = http("giorgio").post(f"/api/appointments/{req['id']}/cancel", json={"version": req["version"]})
    assert r.status_code == 422 and r.json()["code"] == "REASON_REQUIRED"
    r = http("giorgio").post(f"/api/appointments/{req['id']}/cancel",
                             json={"version": req["version"], "reason": "Cliente non raggiungibile"})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    altro = _crea(http, assigned_user_id=mondo["luca"])
    r = http("giorgio").post(f"/api/appointments/{altro['id']}/cancel",
                             json={"version": altro["version"]})
    assert r.status_code == 200 and r.json()["cancelled_reason"] is None


def test_16_complete_solo_da_start_e_completed_at_reale(http, mondo):
    """Gate A30-2, correzione A: la guardia e `completed_at` usano il NOW() del
    DATABASE; un orario dichiarato si conserva esattamente o si rifiuta."""
    non_iniziato = _crea(http, assigned_user_id=mondo["luca"],
                         start_at=futuro(15).isoformat(), end_at=futuro(16).isoformat())
    r = http("giorgio").post(f"/api/appointments/{non_iniziato['id']}/complete",
                             json={"version": non_iniziato["version"]})
    # A30-8 D6: "troppo presto" e' un 422 con codice proprio (era 409).
    assert r.status_code == 422 and r.json()["code"] == "COMPLETE_TOO_EARLY"
    passato = _crea(http, assigned_user_id=mondo["luca"])      # GIORNO 10-11, < db_now
    db_now = mondo["sql"]("SELECT NOW()")[0][0]
    for sbagliato in (ore(9, 59), db_now + timedelta(seconds=1), futuro(9)):
        r = http("giorgio").post(f"/api/appointments/{passato['id']}/complete",
                                 json={"version": passato["version"],
                                       "completed_at": sbagliato.isoformat()})
        assert r.status_code == 422 and r.json()["code"] == "COMPLETED_AT_INVALID", sbagliato
    # dichiarato: conservato ESATTAMENTE, secondi e microsecondi compresi
    dichiarato = ore(10, 50, s=17, us=123456)
    r = http("giorgio").post(f"/api/appointments/{passato['id']}/complete",
                             json={"version": passato["version"],
                                   "completed_at": dichiarato.isoformat()})
    assert r.status_code == 200
    assert datetime.fromisoformat(r.json()["completed_at"]) == dichiarato
    salvato = mondo["sql"]("SELECT completed_at FROM appointments WHERE id=%s",
                           (passato["id"],))[0][0]
    assert salvato == dichiarato
    r = http("giorgio").post(f"/api/appointments/{passato['id']}/cancel",
                             json={"version": r.json()["version"]})
    assert r.status_code == 409 and r.json()["code"] == "INVALID_TRANSITION"


def test_16b_senza_completed_at_vale_il_now_del_database(http, mondo):
    a = _crea(http, assigned_user_id=mondo["marta"])
    prima = mondo["sql"]("SELECT NOW()")[0][0]
    r = http("giorgio").post(f"/api/appointments/{a['id']}/complete",
                             json={"version": a["version"]})
    dopo = mondo["sql"]("SELECT NOW()")[0][0]
    assert r.status_code == 200, r.text
    completato = datetime.fromisoformat(r.json()["completed_at"])
    # e' il NOW() della transazione della mutazione, non l'orologio iniettato
    assert prima <= completato <= dopo
    assert completato != ORA


def test_17_no_show_solo_da_end(http, mondo):
    # SENTINELLA AGGIORNATA DA A30-2P F9: la guardia dell'assenza usa il NOW()
    # del DATABASE (stesso orologio di no_show_at), quindi "in corso" si
    # costruisce attorno al NOW() del PostgreSQL di test, non all'ORA iniettata.
    db_now = mondo["sql"]("SELECT NOW()")[0][0]
    in_corso = _crea(http, assigned_user_id=mondo["luca"],
                     start_at=(db_now - timedelta(minutes=30)).isoformat(),
                     end_at=(db_now + timedelta(minutes=30)).isoformat())
    r = http("giorgio").post(f"/api/appointments/{in_corso['id']}/no-show",
                             json={"version": in_corso["version"]})
    # A30-8 D6: "troppo presto" e' un 422 con codice proprio (era 409).
    assert r.status_code == 422 and r.json()["code"] == "NO_SHOW_TOO_EARLY"
    finito = _crea(http, assigned_user_id=mondo["marta"])
    r = http("giorgio").post(f"/api/appointments/{finito['id']}/no-show",
                             json={"version": finito["version"]})
    assert r.status_code == 200 and r.json()["status"] == "no_show"


def test_18_patch_solo_campi_non_temporali(http, mondo):
    a = _crea(http, assigned_user_id=mondo["luca"])
    r = http("giorgio").patch(f"/api/appointments/{a['id']}",
                              json={"version": a["version"], "notes": "Citofono Rossi",
                                    "contact_id": mondo["mario"]})
    assert r.status_code == 200 and r.json()["notes"] == "Citofono Rossi"
    v = r.json()["version"]
    r = http("giorgio").patch(f"/api/appointments/{a['id']}",
                              json={"version": v, "start_at": ore(15).isoformat()})
    assert r.status_code == 422
    r = http("giorgio").patch(f"/api/appointments/{a['id']}",
                              json={"version": v, "lead_id": mondo["lead_mario"],
                                    "contact_id": mondo["bruno"]})
    assert r.status_code == 422 and r.json()["code"] == "LINK_MISMATCH"
    r = http("giorgio").patch(f"/api/appointments/{a['id']}",
                              json={"version": v, "property_id": 999999})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# C - VISIBILITA' E LETTURE
# ---------------------------------------------------------------------------

def test_20_un_agent_non_vede_gli_appuntamenti_altrui_ne_le_richieste_libere(http, mondo):
    di_marta = _crea(http, assigned_user_id=mondo["marta"], contact_id=mondo["mario"])
    libera = _crea(http, status="requested")
    for id_ in (di_marta["id"], libera["id"]):
        assert http("luca").get(f"/api/appointments/{id_}").status_code == 404
        r = http("luca").post(f"/api/appointments/{id_}/cancel", json={"version": 1})
        assert r.status_code == 404
    assert http("estraneo").get(f"/api/appointments/{di_marta['id']}").status_code == 404
    assert http("giorgio").get(f"/api/appointments/{di_marta['id']}").status_code == 200


def test_21_calendario_agent_propri_dettagli_colleghi_solo_occupato(http, mondo):
    _crea(http, assigned_user_id=mondo["luca"], contact_id=mondo["mario"])
    _crea(http, assigned_user_id=mondo["marta"], contact_id=mondo["mario"],
          start_at=ore(14).isoformat(), end_at=ore(15).isoformat(), notes="riservato")
    _crea(http, status="requested", start_at=ore(16).isoformat(), end_at=ore(17).isoformat())
    r = http("luca").get("/api/appointments/calendar",
                         params={"from": ore(0).isoformat(), "to": ore(0, giorni=1).isoformat()})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    propri = [i for i in items if i["kind"] == "appointment"]
    occupati = [i for i in items if i["kind"] == "busy"]
    assert len(propri) == 1 and propri[0]["contact_name"] == "Mario Rossi"
    assert len(occupati) == 1
    assert set(occupati[0]) == {"kind", "agent_id", "agent_name", "start_at", "end_at",
                                "label", "readonly"}
    assert occupati[0]["agent_name"] == "Marta Test" and occupati[0]["label"] == "Occupato"
    # owner: tutto, richiesta compresa, e nessun "busy"
    r = http("giorgio").get("/api/appointments/calendar",
                            params={"from": ore(0).isoformat(), "to": ore(0, giorni=1).isoformat()})
    tipi = [(i["kind"], i.get("is_request")) for i in r.json()["items"]]
    assert tipi == [("appointment", False), ("appointment", False), ("appointment", True)]


def test_22_calendario_limiti_e_filtri(http, mondo):
    r = http("giorgio").get("/api/appointments/calendar",
                            params={"from": ore(0).isoformat(),
                                    "to": ore(0, giorni=43).isoformat()})
    assert r.status_code == 422 and r.json()["code"] == "RANGE_TOO_LARGE"
    r = http("giorgio").get("/api/appointments/calendar",
                            params={"from": ore(0).isoformat(), "to": ore(0, giorni=1).isoformat(),
                                    "statuses": "booked"})
    assert r.status_code == 422
    r = http("giorgio").get("/api/appointments/calendar",
                            params={"from": "2026-09-14T00:00:00", "to": ore(0, giorni=1).isoformat()})
    assert r.status_code == 422 and r.json()["code"] == "TIMEZONE_REQUIRED"
    a = _crea(http, assigned_user_id=mondo["luca"])
    http("giorgio").post(f"/api/appointments/{a['id']}/cancel", json={"version": a["version"]})
    r = http("giorgio").get("/api/appointments/calendar",
                            params={"from": ore(0).isoformat(), "to": ore(0, giorni=1).isoformat()})
    assert r.json()["items"] == []          # gli annullati sono nascosti di default
    r = http("giorgio").get("/api/appointments/calendar",
                            params={"from": ore(0).isoformat(), "to": ore(0, giorni=1).isoformat(),
                                    "statuses": "cancelled"})
    assert len(r.json()["items"]) == 1


def test_23_dettaglio_riepiloghi_azioni_e_link_d9(http, mondo):
    a = _crea(http, assigned_user_id=mondo["luca"], contact_id=mondo["mario"],
              lead_id=mondo["lead_mario"], property_id=mondo["casa"], stima_id=mondo["stima"])
    d = http("giorgio").get(f"/api/appointments/{a['id']}").json()
    assert d["contact"]["phone_normalized"] == "393331234567"
    assert d["stima"]["comune"] == "Tortoreto" and d["property"]["city"] == "Alba Adriatica"
    assert d["agent"]["name"] == "Luca Test" and d["agent"]["active"] is True
    assert d["allowed_links"] == ["contact", "property"]          # mai stima o lead (D9)
    assert "reassign" in d["allowed_actions"] and "complete" in d["allowed_actions"]
    agente = http("luca").get(f"/api/appointments/{a['id']}").json()
    assert "reassign" not in agente["allowed_actions"]
    ev = http("luca").get(f"/api/appointments/{a['id']}/events").json()["items"]
    assert [e["event_type"] for e in ev] == ["created"]


def test_24_agents_e_liste(http, mondo):
    r = http("luca").get("/api/appointments/agents").json()["items"]
    assert {a["name"] for a in r} == {"Giorgio Test", "Luca Test", "Marta Test"}
    assert [a["is_me"] for a in r if a["name"] == "Luca Test"] == [True]
    _crea(http, assigned_user_id=mondo["luca"])
    _crea(http, status="requested", start_at=ore(15).isoformat(), end_at=ore(16).isoformat())
    richieste = http("giorgio").get("/api/appointments", params={"statuses": "requested"})
    assert len(richieste.json()["items"]) == 1
    assert http("luca").get("/api/appointments", params={"statuses": "requested"}).json()[
        "items"] == []


# ---------------------------------------------------------------------------
# D - DISPONIBILITA'
# ---------------------------------------------------------------------------

def test_30_availability_slot_e_finestra(http, mondo):
    _crea(http, assigned_user_id=mondo["luca"])
    r = http("giorgio").get("/api/appointments/availability",
                            params={"user_id": mondo["luca"], "from": ore(9).isoformat(),
                                    "to": ore(12).isoformat(), "duration": 60, "step": 60})
    assert r.status_code == 200, r.text
    assert [s["available"] for s in r.json()["slots"]] == [True, False, True]
    r = http("giorgio").get("/api/appointments/availability",
                            params={"user_id": mondo["luca"], "from": ore(0).isoformat(),
                                    "to": ore(0, giorni=8).isoformat()})
    assert r.status_code == 422 and r.json()["code"] == "RANGE_TOO_LARGE"
    r = http("giorgio").get("/api/appointments/availability",
                            params={"user_id": mondo["estraneo"], "from": ore(9).isoformat(),
                                    "to": ore(12).isoformat()})
    assert r.status_code == 422 and r.json()["code"] == "AGENT_NOT_ACTIVE"


def test_31_check_redige_i_colleghi_per_un_agent(http, mondo):
    _crea(http, assigned_user_id=mondo["marta"], contact_id=mondo["mario"])
    corpo = {"assigned_user_id": mondo["marta"], "start_at": ore(10, 30).isoformat(),
             "end_at": ore(11, 30).isoformat()}
    capo = http("giorgio").post("/api/appointments/availability/check", json=corpo).json()
    assert capo["available"] is False and "id" in capo["conflicts"][0]
    agente = http("luca").post("/api/appointments/availability/check", json=corpo).json()
    assert agente["available"] is False
    assert set(agente["conflicts"][0]) == {"start_at", "end_at", "label"}
    assert len(agente["alternatives"]) == 3


# ---------------------------------------------------------------------------
# E - SESSIONE E SCOPE
# ---------------------------------------------------------------------------

def test_40_senza_sessione_e_platform_admin_non_in_acting(http, mondo):
    r = http(None).get("/api/appointments/agents")
    assert r.status_code == 403 and r.json()["code"] == "SESSION_REQUIRED"
    # platform admin senza agenzia scelta: require_agency() rifiuta con 403.
    from operator_auth.context import OperatorContext
    ctx = OperatorContext(user_id=mondo["giorgio"], agency_id=None, role=None,
                          is_platform_admin=True, session_id=None, auth_channel="session")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from appointments.router import router
    from operator_auth.dependencies import require_operator
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_operator] = lambda: ctx
    r = TestClient(app).get("/api/appointments/agents")
    assert r.status_code == 403 and r.json()["code"] == "PLATFORM_ADMIN_AGENCY_REQUIRED"


# ---------------------------------------------------------------------------
# F - PROIEZIONE LMC-15. ACCESA da A30-2P; l'arresto d'emergenza (spenta) si
#     prova spegnendola SOLO dentro il test.
# ---------------------------------------------------------------------------

def test_50_spenta_il_sopralluogo_con_stima_resta_richiesta(http, mondo, proiezione_spenta):
    r = http("giorgio").post("/api/appointments",
                             json=_nuovo(appointment_type="inspection", stima_id=mondo["stima"],
                                         assigned_user_id=mondo["luca"]))
    assert r.status_code == 422 and r.json()["code"] == "INSPECTION_PROJECTION_NOT_ACTIVE"
    req = _crea(http, status="requested", appointment_type="inspection", stima_id=mondo["stima"])
    r = http("giorgio").post(f"/api/appointments/{req['id']}/schedule",
                             json={"version": req["version"], "assigned_user_id": mondo["luca"],
                                   "start_at": ore(14).isoformat(), "end_at": ore(15).isoformat()})
    assert r.status_code == 422 and r.json()["code"] == "INSPECTION_PROJECTION_NOT_ACTIVE"
    d = http("giorgio").get(f"/api/appointments/{req['id']}").json()
    assert "schedule" not in d["allowed_actions"]
    assert mondo["sql"]("SELECT count(*) FROM stima_inspections")[0][0] == 0


@pytest.fixture
def proiezione_spenta(monkeypatch):
    """L'arresto d'emergenza: interruttore a False solo dentro il test."""
    from appointments import projection
    monkeypatch.setattr(projection, "PROJECTION_ENABLED", False)
    return projection


@pytest.fixture
def proiezione_accesa(monkeypatch):
    from appointments import projection
    monkeypatch.setattr(projection, "PROJECTION_ENABLED", True)
    return projection


def _ispezione(mondo, id_):
    return mondo["sql"]("SELECT status, scheduled_for, completed_at, cancelled_reason "
                        "FROM stima_inspections WHERE id=%s", (id_,))[0]


def test_51_accesa_schedule_reschedule_complete(http, mondo, proiezione_accesa):
    req = _crea(http, status="requested", appointment_type="inspection", stima_id=mondo["stima"])
    # SENTINELLA AGGIORNATA DA A30-7 (D4): `schedule` rifiuta un inizio gia'
    # passato per l'orologio del service (ORA = mezzogiorno di GIORNO): la
    # richiesta si fissa alle 13, lo spostamento e il completamento restano.
    r = http("giorgio").post(f"/api/appointments/{req['id']}/schedule",
                             json={"version": req["version"], "assigned_user_id": mondo["luca"],
                                   "start_at": ore(13).isoformat(), "end_at": ore(14).isoformat()})
    assert r.status_code == 200, r.text
    fissato = r.json()
    ins = fissato["stima_inspection_id"]
    assert ins is not None
    assert _ispezione(mondo, ins)[0] == "scheduled" and _ispezione(mondo, ins)[1] == ore(13)
    timeline = mondo["sql"]("SELECT event_type FROM seller_timeline_events ORDER BY id")
    assert [t[0] for t in timeline] == ["inspection_scheduled"]

    # SENTINELLA AGGIORNATA DA A30-8 (D6): anche `reschedule` rifiuta un
    # inizio gia' passato per l'orologio del service (ORA = 12): lo
    # spostamento va alle 15, ancora nel passato del database, quindi il
    # completamento resta possibile.
    r = http("giorgio").post(f"/api/appointments/{fissato['id']}/reschedule",
                             json={"version": fissato["version"], "start_at": ore(15).isoformat(),
                                   "end_at": ore(16).isoformat()})
    assert r.status_code == 201, r.text
    nuova = r.json()
    assert nuova["stima_inspection_id"] == ins
    vecchia = mondo["sql"]("SELECT stima_inspection_id FROM appointments WHERE id=%s",
                           (fissato["id"],))[0][0]
    assert vecchia is None
    assert _ispezione(mondo, ins)[1] == ore(15)

    r = http("giorgio").post(f"/api/appointments/{nuova['id']}/complete",
                             json={"version": nuova["version"],
                                   "completed_at": ore(15, 55).isoformat()})
    assert r.status_code == 200, r.text
    stato, _, completato, _ = _ispezione(mondo, ins)
    assert stato == "completed" and completato == ore(15, 55)
    timeline = mondo["sql"]("SELECT event_type FROM seller_timeline_events ORDER BY id")
    assert [t[0] for t in timeline] == ["inspection_scheduled", "inspection_completed"]


def test_51b_accesa_completed_at_mai_dopo_il_recorded_at_di_lmc15(http, mondo,
                                                                  proiezione_accesa):
    """Correzione A: senza `completed_at` vale il NOW() della transazione che
    scrive ANCHE `stima_inspections`; LMC-15 registra `completed_recorded_at`
    con lo stesso NOW(), quindi il suo vincolo d'ordine regge per costruzione."""
    a = _crea(http, appointment_type="inspection", stima_id=mondo["stima"],
              assigned_user_id=mondo["luca"])
    a = http("giorgio").get(f"/api/appointments/{a['id']}").json()["appointment"]
    r = http("giorgio").post(f"/api/appointments/{a['id']}/complete",
                             json={"version": a["version"]})
    assert r.status_code == 200, r.text
    completato, registrato = mondo["sql"](
        "SELECT completed_at, completed_recorded_at FROM stima_inspections WHERE id=%s",
        (a["stima_inspection_id"],))[0]
    assert completato == datetime.fromisoformat(r.json()["completed_at"])
    assert completato == registrato


def test_52_accesa_cancel_e_no_show(http, mondo, proiezione_accesa):
    a = _crea(http, appointment_type="inspection", stima_id=mondo["stima"],
              assigned_user_id=mondo["luca"])
    # la creazione proiettata scrive il collegamento nella stessa transazione:
    # la risposta porta gia' la version corrente
    corrente = http("giorgio").get(f"/api/appointments/{a['id']}").json()["appointment"]
    assert corrente["version"] == a["version"] and corrente["stima_inspection_id"] is not None
    r = http("giorgio").post(f"/api/appointments/{a['id']}/cancel",
                             json={"version": corrente["version"]})
    assert r.status_code == 422 and r.json()["code"] == "REASON_REQUIRED"
    r = http("giorgio").post(f"/api/appointments/{a['id']}/cancel",
                             json={"version": corrente["version"],
                                   "reason": "Rinviato dal cliente"})
    assert r.status_code == 200, r.text
    assert _ispezione(mondo, corrente["stima_inspection_id"])[0] == "cancelled"
    assert _ispezione(mondo, corrente["stima_inspection_id"])[3] == "Rinviato dal cliente"

    b = _crea(http, appointment_type="inspection", stima_id=mondo["stima"],
              assigned_user_id=mondo["marta"])
    b = http("giorgio").get(f"/api/appointments/{b['id']}").json()["appointment"]
    r = http("giorgio").post(f"/api/appointments/{b['id']}/no-show", json={"version": b["version"]})
    assert r.status_code == 200, r.text
    assert _ispezione(mondo, b["stima_inspection_id"])[0] == "cancelled"
    assert _ispezione(mondo, b["stima_inspection_id"])[3] == "no_show"


def test_53_accesa_un_errore_della_proiezione_non_lascia_niente_a_meta(http, mondo,
                                                                       proiezione_accesa):
    a = _crea(http, appointment_type="inspection", stima_id=mondo["stima"],
              assigned_user_id=mondo["luca"])
    a = http("giorgio").get(f"/api/appointments/{a['id']}").json()["appointment"]
    # qualcuno chiude la riga LMC-15 dal percorso legacy
    mondo["sql"]("UPDATE stima_inspections SET status='cancelled', cancelled_at=NOW(), "
                 "cancelled_recorded_at=NOW(), cancelled_by_operator_user_id=%s, "
                 "cancelled_reason='altrove' WHERE id=%s",
                 (mondo["giorgio"], a["stima_inspection_id"]))
    r = http("giorgio").post(f"/api/appointments/{a['id']}/complete",
                             json={"version": a["version"]})
    assert r.status_code == 409 and r.json()["code"] == "PROJECTION_CONFLICT"
    stato = mondo["sql"]("SELECT status, version FROM appointments WHERE id=%s", (a["id"],))[0]
    assert tuple(stato) == ("scheduled", a["version"])
