"""A30-12 su PostgreSQL reale e via HTTP: migration 077, il servizio del
booking pubblico, l'entrypoint autorevole dell'Agenda, e i due lati del
gate - concorrenza e privacy - che un doppio non puo' provare.

Schema: riusa `CATENA`/`SCHEMA_MINIMO` di `test_lmc15_acquisition_bridge_postgres`
(stessa disciplina di `test_a30_2_appointments_postgres`), poi applica per
intero 072 (Agenda), 073 (facade LMC-15 - non necessaria qui ma parte della
catena reale del Mac) e 076 (orari di lavoro), infine 077 (booking pubblico).
074/075 (calendar_sync) NON si applicano: senza `PUBLIC_BOOKING_IP_PEPPER`
irrilevante e senza `hook_deployment_namespace()` configurato il mark-dirty
verso Google e' un NO-OP per costruzione (`calendar_sync.integration`,
FAIL-OPEN) - nessuna tabella Google e' mai toccata da questa suite.

L'API operatore (`/api/appointments/booking-links...`) e quella pubblica
(`/api/public/booking/...`) sono montate su un'app FastAPI di TEST, come
`test_a30_2_appointments_postgres`: `main.py` non viene importato.

Opt-in: senza `P29_TEST_DSN` si salta tutto. Database usa-e-getta.
"""
from __future__ import annotations

import hashlib
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

DSN = os.getenv("P29_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare A30-12")

ROOT = Path(__file__).resolve().parents[1]
MIGRAZIONI = ROOT / "migrations"
VERSIONE = "077_a30_12_public_booking"

IP_PEPPER = "test-pepper-a30-12-non-usare-in-produzione"

#: Colonne vere che `SCHEMA_MINIMO` (condiviso, minimo) non ha e che il
#: booking pubblico legge/scrive davvero.
COLONNE_REALI = """
ALTER TABLE agencies ADD COLUMN name VARCHAR(200);
ALTER TABLE operator_users ADD COLUMN first_name VARCHAR(100), ADD COLUMN last_name VARCHAR(100);
ALTER TABLE contacts
    ADD COLUMN contact_type VARCHAR(20) NOT NULL DEFAULT 'person',
    ADD COLUMN first_name VARCHAR(100), ADD COLUMN last_name VARCHAR(100),
    ADD COLUMN company_name VARCHAR(200),
    ADD COLUMN phone VARCHAR(50), ADD COLUMN phone_normalized VARCHAR(50),
    ADD COLUMN secondary_phone VARCHAR(50), ADD COLUMN source VARCHAR(100),
    ADD COLUMN notes TEXT, ADD COLUMN created_by_user_id BIGINT;
"""


def _dsn_per(nome: str) -> str:
    if "?" in DSN:
        base, query = DSN.split("?", 1)
        return base.rsplit("/", 1)[0] + "/" + nome + "?" + query
    return DSN.rsplit("/", 1)[0] + "/" + nome


@pytest.fixture(scope="module")
def db():
    psycopg2 = pytest.importorskip("psycopg2")
    from tests.test_lmc15_acquisition_bridge_postgres import CATENA, SCHEMA_MINIMO

    nome = f"a30_12_probe_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    servizio = psycopg2.connect(DSN)
    servizio.autocommit = True
    with servizio.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{nome}"')
    dsn = _dsn_per(nome)
    from psycopg2.extras import DictCursor
    conn = psycopg2.connect(dsn, cursor_factory=DictCursor)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_MINIMO)
            cur.execute(COLONNE_REALI)
            for versione in CATENA:
                cur.execute((MIGRAZIONI / f"{versione}.sql").read_text(encoding="utf-8"))
            cur.execute((MIGRAZIONI / "072_a30_1_appointments.sql").read_text(encoding="utf-8"))
            cur.execute((MIGRAZIONI / "073_a30_2p_lmc15_facade.sql").read_text(encoding="utf-8"))
            cur.execute((MIGRAZIONI / "076_a30_11_working_hours.sql").read_text(encoding="utf-8"))
            cur.execute((MIGRAZIONI / f"{VERSIONE}.sql").read_text(encoding="utf-8"))
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
    for t in ("public_booking_submissions", "public_booking_rate_limits",
              "public_booking_links", "appointment_events", "appointments",
              "agent_working_hours", "agent_availability_exceptions", "agency_closures",
              "contacts", "agency_memberships", "operator_users", "agencies"):
        cur.execute(f"DELETE FROM {t}")
    for trg, tab in (("trg_appointments_refuse_delete", "appointments"),
                     ("trg_appointment_events_append_only", "appointment_events")):
        cur.execute(f"ALTER TABLE {tab} ENABLE TRIGGER {trg}")


@pytest.fixture
def mondo(db, monkeypatch):
    import psycopg2

    from appointments import service as appt_service
    from core import database as core_database
    from operator_auth.context import OperatorContext

    monkeypatch.setattr(core_database, "get_connection", lambda: psycopg2.connect(db["dsn"]))
    monkeypatch.setenv("PUBLIC_BOOKING_IP_PEPPER", IP_PEPPER)

    conn = db["conn"]
    with conn.cursor() as cur:
        _svuota(cur)
        cur.execute("INSERT INTO agencies (slug, name) VALUES ('a-uno','Agenzia Uno') "
                    "RETURNING id")
        a = cur.fetchone()[0]
        cur.execute("INSERT INTO agencies (slug, name) VALUES ('b-due','Agenzia Due') "
                    "RETURNING id")
        b = cur.fetchone()[0]

        def operatore(email, nome, cognome, agenzia, ruolo, stato="active"):
            cur.execute("INSERT INTO operator_users (email, first_name, last_name) "
                        "VALUES (%s,%s,%s) RETURNING id", (email, nome, cognome))
            i = cur.fetchone()[0]
            if agenzia is not None:
                cur.execute(
                    "INSERT INTO agency_memberships (agency_id, operator_user_id, role, status) "
                    "VALUES (%s,%s,%s,%s)", (agenzia, i, ruolo, stato))
            return i

        ids = {
            "a": a, "b": b,
            "giorgio": operatore("giorgio@example.it", "Giorgio", "Owner", a, "agency_owner"),
            "luca": operatore("luca@example.it", "Luca", "Agente", a, "agent"),
            "marta": operatore("marta@example.it", "Marta", "Agente", a, "agent"),
            "revocato": operatore("revocato@example.it", "Rev", "Ocato", a, "agent",
                                  stato="revoked"),
            "estraneo": operatore("x@example.it", "Estraneo", "Altrove", b, "agent"),
        }
    conn.commit()

    def ctx(chi, *, agenzia=None, platform=False):
        ruolo = {"giorgio": "agency_owner", "luca": "agent", "marta": "agent",
                 "estraneo": "agent"}.get(chi)
        return OperatorContext(
            user_id=None if chi is None else ids[chi],
            agency_id=agenzia if agenzia is not None else (ids["b"] if chi == "estraneo"
                                                           else ids["a"]),
            role=ruolo, is_platform_admin=platform, session_id=None, auth_channel="session")

    def sql(testo, par=None):
        with conn.cursor() as cur:
            cur.execute(testo, par)
            r = cur.fetchall() if cur.description else None
        conn.commit()
        return r

    def orario_settimanale(agente, agenzia=None, day_of_week=None, start=540, end=1080):
        """Copre TUTTI i 7 giorni (default) cosi' i test non dipendono dal
        giorno della settimana in cui girano, salvo quando serve un giorno
        specifico (D10 - eccezioni/chiusure)."""
        agenzia = agenzia if agenzia is not None else a
        giorni = [day_of_week] if day_of_week is not None else list(range(1, 8))
        with conn.cursor() as cur:
            for g in giorni:
                cur.execute(
                    "INSERT INTO agent_working_hours (agency_id, user_id, day_of_week, "
                    "start_minute, end_minute) VALUES (%s,%s,%s,%s,%s)",
                    (agenzia, agente, g, start, end))
        conn.commit()

    return {**ids, "ctx": ctx, "sql": sql, "conn": conn, "service": appt_service,
           "orario_settimanale": orario_settimanale}


@pytest.fixture
def http(mondo):
    """Le due rotte di questo gate su un'app FastAPI di TEST: l'operatore
    (`appointments.router`, con `require_operator` sostituito) e la pubblica
    (`public_booking.public_router`, senza dipendenze - come in produzione).
    `main.py` non viene importato."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from appointments.router import router as appointments_router
    from operator_auth.dependencies import require_operator
    from public_booking.public_router import router as public_router

    app = FastAPI()
    app.include_router(appointments_router)
    app.include_router(public_router)
    stato = {"ctx": mondo["ctx"]("giorgio")}
    app.dependency_overrides[require_operator] = lambda: stato["ctx"]
    client = TestClient(app)

    def come(chi=None, **kw):
        if chi is not None:
            stato["ctx"] = mondo["ctx"](chi, **kw)
        return client

    return come


def _lunedi_prossimo(oggi=None):
    """Un lunedi' futuro, lontano da qualunque now() reale - usato per gli
    slot/le eccezioni cosi' i test non dipendono dal giorno in cui girano."""
    oggi = oggi or datetime.now(timezone.utc).date()
    giorni = (7 - oggi.weekday()) % 7 or 7
    return oggi + timedelta(days=giorni + 14)  # due settimane oltre, margine ampio


# ---------------------------------------------------------------------------
# A - LA MIGRATION
# ---------------------------------------------------------------------------

def test_01_migration_077_up_down_up(db):
    conn = db["conn"]
    conn.autocommit = True
    giu = (MIGRAZIONI / f"{VERSIONE}_down.sql").read_text(encoding="utf-8")
    su = (MIGRAZIONI / f"{VERSIONE}.sql").read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(giu)
        cur.execute(
            "SELECT to_regclass('public.public_booking_links'), "
            "       to_regclass('public.public_booking_submissions'), "
            "       to_regclass('public.public_booking_rate_limits')")
        assert list(cur.fetchone()) == [None, None, None]
        cur.execute(su)
        cur.execute(
            "SELECT to_regclass('public.public_booking_links'), "
            "       to_regclass('public.public_booking_submissions'), "
            "       to_regclass('public.public_booking_rate_limits')")
        riga = cur.fetchone()
        assert all(v is not None for v in riga)
    conn.autocommit = False


def test_02_down_rifiuta_se_ci_sono_righe(mondo):
    conn = mondo["conn"]
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public_booking_links (agency_id, assigned_user_id, token_hash, "
            "appointment_type, duration_minutes) VALUES (%s,%s,%s,'call',30)",
            (mondo["a"], mondo["luca"], "x" * 64))
    conn.commit()
    giu = (MIGRAZIONI / f"{VERSIONE}_down.sql").read_text(encoding="utf-8")
    with pytest.raises(Exception):
        with conn.cursor() as cur:
            cur.execute(giu)
    conn.rollback()


# ---------------------------------------------------------------------------
# B - CRUD OPERATORE DEI LINK (D2, D6)
# ---------------------------------------------------------------------------

def _crea_link(http, chi="giorgio", **kw):
    corpo = {"assigned_user_id": kw.pop("assigned_user_id"), "appointment_type": "call",
            "duration_minutes": 30}
    corpo.update(kw)
    r = http(chi).post("/api/appointments/booking-links", json=corpo)
    return r


def test_03_owner_crea_un_link_per_un_agente_qualunque(http, mondo):
    r = _crea_link(http, "giorgio", assigned_user_id=mondo["luca"])
    assert r.status_code == 201, r.text
    corpo = r.json()
    assert corpo["assigned_user_id"] == mondo["luca"]
    assert corpo["status"] == "active"
    assert "token" in corpo and len(corpo["token"]) >= 32
    assert "token_hash" not in corpo


def test_04_il_token_grezzo_non_e_mai_persistito_nel_db(http, mondo):
    r = _crea_link(http, "giorgio", assigned_user_id=mondo["luca"])
    token = r.json()["token"]
    riga = mondo["sql"]("SELECT token_hash FROM public_booking_links WHERE id=%s",
                        (r.json()["id"],))[0]
    assert riga[0] != token
    assert riga[0] == hashlib.sha256(token.encode()).hexdigest()
    # e non compare in nessuna colonna testuale della riga:
    tutta = mondo["sql"]("SELECT * FROM public_booking_links WHERE id=%s", (r.json()["id"],))[0]
    assert token not in [str(v) for v in tutta]


def test_05_agente_non_puo_creare_un_link_per_un_collega(http, mondo):
    r = _crea_link(http, "luca", assigned_user_id=mondo["marta"])
    assert r.status_code == 403, r.text


def test_06_agente_puo_creare_gestire_e_disabilitare_il_proprio_link(http, mondo):
    r = _crea_link(http, "luca", assigned_user_id=mondo["luca"])
    assert r.status_code == 201, r.text
    link_id = r.json()["id"]
    r2 = http("luca").patch(f"/api/appointments/booking-links/{link_id}",
                            json={"label": "Il mio link"})
    assert r2.status_code == 200 and r2.json()["label"] == "Il mio link"
    r3 = http("luca").post(f"/api/appointments/booking-links/{link_id}/disable")
    assert r3.status_code == 200 and r3.json()["status"] == "disabled"


def test_07_agente_non_puo_gestire_il_link_di_un_collega(http, mondo):
    r = _crea_link(http, "luca", assigned_user_id=mondo["luca"])
    link_id = r.json()["id"]
    for verbo, path in ((http("marta").patch, f"/api/appointments/booking-links/{link_id}"),):
        rr = verbo(path, json={"label": "intruso"})
        assert rr.status_code == 404, rr.text  # non rivela nemmeno che esiste
    rr = http("marta").post(f"/api/appointments/booking-links/{link_id}/rotate")
    assert rr.status_code == 404
    rr = http("marta").post(f"/api/appointments/booking-links/{link_id}/disable")
    assert rr.status_code == 404


def test_08_owner_admin_vede_tutti_i_link_agente_solo_i_propri(http, mondo):
    _crea_link(http, "giorgio", assigned_user_id=mondo["luca"])
    _crea_link(http, "giorgio", assigned_user_id=mondo["marta"])
    tutti = http("giorgio").get("/api/appointments/booking-links").json()["items"]
    assert len(tutti) == 2
    solo_luca = http("luca").get("/api/appointments/booking-links").json()["items"]
    assert len(solo_luca) == 1 and solo_luca[0]["assigned_user_id"] == mondo["luca"]


def test_09_tenant_isolation_un_link_di_unaltra_agenzia_e_invisibile(http, mondo):
    r = _crea_link(http, "giorgio", assigned_user_id=mondo["luca"])
    link_id = r.json()["id"]
    rr = http("estraneo").patch(f"/api/appointments/booking-links/{link_id}", json={"label": "x"})
    assert rr.status_code == 404


def test_10_rotate_invalida_il_vecchio_token(http, mondo):
    r = _crea_link(http, "giorgio", assigned_user_id=mondo["luca"])
    link_id = r.json()["id"]
    vecchio_token = r.json()["token"]
    r2 = http("giorgio").post(f"/api/appointments/booking-links/{link_id}/rotate")
    nuovo_token = r2.json()["token"]
    assert nuovo_token != vecchio_token
    # il vecchio token non risolve piu' nulla di prenotabile:
    rr = http(None).get(f"/api/public/booking/{vecchio_token}")
    assert rr.status_code == 404
    rr = http(None).get(f"/api/public/booking/{nuovo_token}")
    assert rr.status_code == 200


def test_11_get_non_scrive_mai_nulla_su_public_booking_links(http, mondo):
    r = _crea_link(http, "giorgio", assigned_user_id=mondo["luca"])
    token = r.json()["token"]
    prima = mondo["sql"]("SELECT updated_at FROM public_booking_links WHERE id=%s",
                         (r.json()["id"],))[0][0]
    http(None).get(f"/api/public/booking/{token}")
    http(None).get(f"/api/public/booking/{token}/slots",
                   params={"from": "2027-01-01T00:00:00+00:00", "to": "2027-01-02T00:00:00+00:00"})
    dopo = mondo["sql"]("SELECT updated_at FROM public_booking_links WHERE id=%s",
                        (r.json()["id"],))[0][0]
    assert prima == dopo
    assert mondo["sql"]("SELECT count(*) FROM public_booking_submissions")[0][0] == 0


# ---------------------------------------------------------------------------
# C - RISOLUZIONE PUBBLICA DEL TOKEN E PRIVACY (D privacy)
# ---------------------------------------------------------------------------

def test_12_token_inesistente_scaduto_revocato_disabilitato_stessa_risposta(http, mondo):
    r = _crea_link(http, "giorgio", assigned_user_id=mondo["luca"])
    link_id, token = r.json()["id"], r.json()["token"]

    inesistente = http(None).get("/api/public/booking/token-che-non-esiste-affatto")
    assert inesistente.status_code == 404
    forma = inesistente.json()

    http("giorgio").post(f"/api/appointments/booking-links/{link_id}/disable")
    disabilitato = http(None).get(f"/api/public/booking/{token}")
    assert disabilitato.status_code == 404
    assert disabilitato.json() == forma

    r2 = _crea_link(http, "giorgio", assigned_user_id=mondo["luca"],
                    expires_at="2020-01-01T00:00:00+00:00")
    scaduto = http(None).get(f"/api/public/booking/{r2.json()['token']}")
    assert scaduto.status_code == 404
    assert scaduto.json() == forma


def test_13_metadata_pubblica_non_espone_id_interni_ne_dati_di_agenzia_sensibili(http, mondo):
    r = _crea_link(http, "giorgio", assigned_user_id=mondo["luca"])
    dati = http(None).get(f"/api/public/booking/{r.json()['token']}").json()
    assert set(dati) == {"agency_name", "agent_name", "appointment_type",
                         "appointment_type_label", "duration_minutes", "submission_token"}
    assert dati["agency_name"] == "Agenzia Uno"
    assert dati["agent_name"] == "Luca Agente"


# ---------------------------------------------------------------------------
# D - D10: DISPONIBILITA' PUBBLICA HARD (orari, eccezioni, chiusure, buffer)
# ---------------------------------------------------------------------------

def test_14_agente_senza_orari_zero_slot_pubblici(http, mondo):
    r = _crea_link(http, "giorgio", assigned_user_id=mondo["luca"])
    token = r.json()["token"]
    inizio = _lunedi_prossimo()
    rr = http(None).get(f"/api/public/booking/{token}/slots",
                        params={"from": f"{inizio}T00:00:00+00:00",
                               "to": f"{inizio + timedelta(days=1)}T00:00:00+00:00"})
    assert rr.status_code == 200
    assert rr.json()["slots"] == []


def test_15_orario_settimanale_configurato_produce_slot(http, mondo):
    mondo["orario_settimanale"](mondo["luca"])
    r = _crea_link(http, "giorgio", assigned_user_id=mondo["luca"])
    token = r.json()["token"]
    inizio = _lunedi_prossimo()
    rr = http(None).get(f"/api/public/booking/{token}/slots",
                        params={"from": f"{inizio}T00:00:00+00:00",
                               "to": f"{inizio + timedelta(days=1)}T00:00:00+00:00"})
    assert rr.status_code == 200
    assert len(rr.json()["slots"]) > 0


def test_16_eccezione_negativa_rimuove_gli_slot_di_quel_giorno(http, mondo):
    mondo["orario_settimanale"](mondo["luca"])
    lunedi = _lunedi_prossimo()
    mondo["sql"](
        "INSERT INTO agent_availability_exceptions (agency_id, user_id, exception_date, "
        "start_minute, end_minute, is_available) VALUES (%s,%s,%s,0,1440,false)",
        (mondo["a"], mondo["luca"], lunedi))
    r = _crea_link(http, "giorgio", assigned_user_id=mondo["luca"])
    rr = http(None).get(f"/api/public/booking/{r.json()['token']}/slots",
                        params={"from": f"{lunedi}T00:00:00+00:00",
                               "to": f"{lunedi + timedelta(days=1)}T00:00:00+00:00"})
    assert rr.json()["slots"] == []


def test_17_chiusura_agenzia_rimuove_gli_slot(http, mondo):
    mondo["orario_settimanale"](mondo["luca"])
    lunedi = _lunedi_prossimo()
    mondo["sql"](
        "INSERT INTO agency_closures (agency_id, closure_date, start_minute, end_minute) "
        "VALUES (%s,%s,0,1440)", (mondo["a"], lunedi))
    r = _crea_link(http, "giorgio", assigned_user_id=mondo["luca"])
    rr = http(None).get(f"/api/public/booking/{r.json()['token']}/slots",
                        params={"from": f"{lunedi}T00:00:00+00:00",
                               "to": f"{lunedi + timedelta(days=1)}T00:00:00+00:00"})
    assert rr.json()["slots"] == []


def test_18_buffer_del_link_riduce_gli_slot_intorno_a_un_impegno(http, mondo):
    mondo["orario_settimanale"](mondo["luca"])
    lunedi = _lunedi_prossimo()
    inizio_giorno = datetime.combine(lunedi, datetime.min.time(), tzinfo=timezone.utc)
    r = _crea_link(http, "giorgio", assigned_user_id=mondo["luca"],
                   buffer_before_minutes=60, buffer_after_minutes=60)
    token, link_id = r.json()["token"], r.json()["id"]
    # occupa 10:00-10:30 direttamente nel DB (un impegno gia' esistente):
    mondo["sql"](
        "INSERT INTO appointments (agency_id, assigned_user_id, appointment_type, status, "
        "start_at, end_at, buffer_before_minutes, buffer_after_minutes, source) "
        "VALUES (%s,%s,'call','scheduled',%s,%s,0,0,'crm_manual')",
        (mondo["a"], mondo["luca"],
         inizio_giorno + timedelta(hours=10), inizio_giorno + timedelta(hours=10, minutes=30)))
    rr = http(None).get(f"/api/public/booking/{token}/slots",
                        params={"from": (inizio_giorno + timedelta(hours=9)).isoformat(),
                               "to": (inizio_giorno + timedelta(hours=12)).isoformat()})
    slots = rr.json()["slots"]
    # nessuno slot deve iniziare nella finestra [9:00, 11:30) - un'ora di
    # buffer PRIMA e DOPO l'impegno 10:00-10:30.
    for s in slots:
        inizio_slot = datetime.fromisoformat(s["start_at"].replace("Z", "+00:00"))
        assert not (inizio_giorno + timedelta(hours=9) <= inizio_slot
                   < inizio_giorno + timedelta(hours=11, minutes=30))


def test_19_client_non_puo_scegliere_durata_agente_o_agenzia(http, mondo):
    """D3: la rotta slot accetta SOLO `from`/`to`. Qualunque altro parametro
    e' semplicemente ignorato da FastAPI (non dichiarato nella firma), quindi
    non ha ALCUN effetto sulla durata usata (quella del link)."""
    mondo["orario_settimanale"](mondo["luca"])
    r = _crea_link(http, "giorgio", assigned_user_id=mondo["luca"], duration_minutes=45)
    inizio = _lunedi_prossimo()
    rr = http(None).get(f"/api/public/booking/{r.json()['token']}/slots",
                        params={"from": f"{inizio}T00:00:00+00:00",
                               "to": f"{inizio + timedelta(days=1)}T00:00:00+00:00",
                               "duration": "5", "user_id": "999", "agency_id": "999"})
    assert rr.status_code == 200
    slot = rr.json()["slots"][0]
    inizio_s = datetime.fromisoformat(slot["start_at"].replace("Z", "+00:00"))
    fine_s = datetime.fromisoformat(slot["end_at"].replace("Z", "+00:00"))
    assert (fine_s - inizio_s) == timedelta(minutes=45)


# ---------------------------------------------------------------------------
# E - SUBMIT: creazione, idempotenza, TOCTOU, concorrenza (D1, D7, D10)
# ---------------------------------------------------------------------------

def _submit(http, token, submission_token, start_at, name="Mario Rossi", phone="333 1234567",
           email=None):
    return http(None).post(
        f"/api/public/booking/{token}/submit",
        json={"submission_token": submission_token, "start_at": start_at,
              "name": name, "phone": phone, "email": email})


def test_20_submit_crea_un_appuntamento_scheduled_subito(http, mondo):
    mondo["orario_settimanale"](mondo["luca"])
    r = _crea_link(http, "giorgio", assigned_user_id=mondo["luca"])
    token = r.json()["token"]
    meta = http(None).get(f"/api/public/booking/{token}").json()
    lunedi = _lunedi_prossimo()
    inizio = datetime.combine(lunedi, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=10)
    rr = _submit(http, token, meta["submission_token"], inizio.isoformat())
    assert rr.status_code == 201, rr.text
    corpo = rr.json()
    assert corpo["status"] == "scheduled"
    assert set(corpo) == {"status", "start_at", "end_at", "appointment_type"}
    riga = mondo["sql"](
        "SELECT source, source_record_id, created_by_user_id, status, contact_id "
        "FROM appointments WHERE agency_id=%s", (mondo["a"],))[0]
    assert riga[0] == "booking_link"
    assert riga[2] is None  # created_by_user_id
    assert riga[3] == "scheduled"
    evento = mondo["sql"]("SELECT actor_user_id FROM appointment_events")[0]
    assert evento[0] is None


def test_21_contatto_nuovo_creato_quando_nessuno_corrisponde(http, mondo):
    mondo["orario_settimanale"](mondo["luca"])
    r = _crea_link(http, "giorgio", assigned_user_id=mondo["luca"])
    token = r.json()["token"]
    meta = http(None).get(f"/api/public/booking/{token}").json()
    inizio = datetime.combine(_lunedi_prossimo(), datetime.min.time(),
                              tzinfo=timezone.utc) + timedelta(hours=10)
    _submit(http, token, meta["submission_token"], inizio.isoformat(),
           name="Contatto Nuovissimo", phone="333 0000000")
    assert mondo["sql"]("SELECT count(*) FROM contacts WHERE display_name=%s",
                        ("Contatto Nuovissimo",))[0][0] == 1


def test_22_contatto_esistente_collegato_silenziosamente_senza_duplicati(http, mondo):
    mondo["orario_settimanale"](mondo["luca"])
    mondo["sql"](
        "INSERT INTO contacts (agency_id, display_name, phone, phone_normalized, email, "
        "email_normalized) VALUES (%s,'Mario Rossi','333 1234567','393331234567',NULL,NULL)",
        (mondo["a"],))
    n_prima = mondo["sql"]("SELECT count(*) FROM contacts")[0][0]
    r = _crea_link(http, "giorgio", assigned_user_id=mondo["luca"])
    token = r.json()["token"]
    meta = http(None).get(f"/api/public/booking/{token}").json()
    inizio = datetime.combine(_lunedi_prossimo(), datetime.min.time(),
                              tzinfo=timezone.utc) + timedelta(hours=10)
    _submit(http, token, meta["submission_token"], inizio.isoformat(),
           name="Mario Rossi", phone="333 1234567")
    assert mondo["sql"]("SELECT count(*) FROM contacts")[0][0] == n_prima
    riga = mondo["sql"]("SELECT contact_id FROM appointments WHERE agency_id=%s",
                        (mondo["a"],))[0]
    contatto = mondo["sql"]("SELECT id FROM contacts WHERE display_name='Mario Rossi'")[0]
    assert riga[0] == contatto[0]


def test_23_niente_rivela_se_il_contatto_esisteva_gia(http, mondo):
    """D5: la risposta pubblica e' identica indipendentemente dal fatto che
    il contatto sia stato trovato o creato - lo si verifica per struttura,
    non per un campo che lo direbbe (non ce n'e' nessuno, per costruzione:
    vedi `_prenotazione_pubblica`)."""
    from public_booking.service import _prenotazione_pubblica
    import inspect as _inspect
    assert "contact" not in _inspect.getsource(_prenotazione_pubblica).lower()


def test_24_retry_della_stessa_submission_e_idempotente(http, mondo):
    mondo["orario_settimanale"](mondo["luca"])
    r = _crea_link(http, "giorgio", assigned_user_id=mondo["luca"])
    token = r.json()["token"]
    meta = http(None).get(f"/api/public/booking/{token}").json()
    inizio = datetime.combine(_lunedi_prossimo(), datetime.min.time(),
                              tzinfo=timezone.utc) + timedelta(hours=10)
    prima = _submit(http, token, meta["submission_token"], inizio.isoformat())
    seconda = _submit(http, token, meta["submission_token"], inizio.isoformat())
    assert prima.status_code == 201 and seconda.status_code == 201
    assert prima.json() == seconda.json()
    assert mondo["sql"]("SELECT count(*) FROM appointments")[0][0] == 1
    assert mondo["sql"]("SELECT count(*) FROM appointment_events")[0][0] == 1


def test_25_stesso_submission_token_payload_diverso_e_rifiutato(http, mondo):
    mondo["orario_settimanale"](mondo["luca"])
    r = _crea_link(http, "giorgio", assigned_user_id=mondo["luca"])
    token = r.json()["token"]
    meta = http(None).get(f"/api/public/booking/{token}").json()
    inizio = datetime.combine(_lunedi_prossimo(), datetime.min.time(),
                              tzinfo=timezone.utc) + timedelta(hours=10)
    _submit(http, token, meta["submission_token"], inizio.isoformat(), name="Mario Rossi")
    rr = _submit(http, token, meta["submission_token"], inizio.isoformat(), name="Nome Diverso")
    assert rr.status_code == 409
    assert mondo["sql"]("SELECT count(*) FROM appointments")[0][0] == 1


def test_26_toctou_una_chiusura_inserita_dopo_il_get_rifiuta_il_submit_tardivo(http, mondo):
    """GET mostra uno slot -> nel frattempo arriva una chiusura -> il
    submit per quello slot, ormai stantio, deve essere rifiutato (D10, il
    ri-controllo HARD avviene DOPO il lock, non sui dati del GET)."""
    mondo["orario_settimanale"](mondo["luca"])
    r = _crea_link(http, "giorgio", assigned_user_id=mondo["luca"])
    token = r.json()["token"]
    lunedi = _lunedi_prossimo()
    inizio = datetime.combine(lunedi, datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=10)
    meta = http(None).get(f"/api/public/booking/{token}").json()
    rr = http(None).get(f"/api/public/booking/{token}/slots",
                        params={"from": f"{lunedi}T00:00:00+00:00",
                               "to": f"{lunedi + timedelta(days=1)}T00:00:00+00:00"})
    assert any(True for _ in rr.json()["slots"])  # lo slot era disponibile al GET

    # nel frattempo: una chiusura agenzia copre tutta la giornata.
    mondo["sql"]("INSERT INTO agency_closures (agency_id, closure_date, start_minute, "
                "end_minute) VALUES (%s,%s,0,1440)", (mondo["a"], lunedi))

    esito = _submit(http, token, meta["submission_token"], inizio.isoformat())
    assert esito.status_code == 409, esito.text
    assert mondo["sql"]("SELECT count(*) FROM appointments")[0][0] == 0


def test_27_due_submit_concorrenti_sullo_stesso_slot_uno_solo_vince(http, mondo):
    mondo["orario_settimanale"](mondo["luca"])
    r = _crea_link(http, "giorgio", assigned_user_id=mondo["luca"])
    token = r.json()["token"]
    inizio = datetime.combine(_lunedi_prossimo(), datetime.min.time(),
                              tzinfo=timezone.utc) + timedelta(hours=10)
    esiti = []

    def invia(nome):
        meta = http(None).get(f"/api/public/booking/{token}").json()
        r = _submit(http, token, meta["submission_token"], inizio.isoformat(), name=nome)
        esiti.append(r.status_code)

    fili = [threading.Thread(target=invia, args=(f"Cliente {i}",)) for i in range(4)]
    for f in fili:
        f.start()
    for f in fili:
        f.join(timeout=20)
    assert esiti.count(201) == 1, esiti
    assert esiti.count(409) == 3, esiti
    assert mondo["sql"]("SELECT count(*) FROM appointments WHERE agency_id=%s",
                        (mondo["a"],))[0][0] == 1


# ---------------------------------------------------------------------------
# F - RATE LIMITING (D9) - budget GET e POST separati
# ---------------------------------------------------------------------------

def test_28_rate_limit_get_per_link_applicato(http, mondo, monkeypatch):
    monkeypatch.setenv("PUBLIC_BOOKING_RATE_LIMIT_GET_LINK", "2")
    r = _crea_link(http, "giorgio", assigned_user_id=mondo["luca"])
    token = r.json()["token"]
    esiti = [http(None).get(f"/api/public/booking/{token}").status_code for _ in range(4)]
    assert esiti.count(200) == 2
    assert esiti.count(404) == 2  # risposta neutra, non un 429 distinguibile


def test_29_rate_limit_post_per_ip_applicato(http, mondo, monkeypatch):
    monkeypatch.setenv("PUBLIC_BOOKING_RATE_LIMIT_POST_IP", "1")
    mondo["orario_settimanale"](mondo["luca"])
    r = _crea_link(http, "giorgio", assigned_user_id=mondo["luca"])
    token = r.json()["token"]
    inizio = datetime.combine(_lunedi_prossimo(), datetime.min.time(),
                              tzinfo=timezone.utc) + timedelta(hours=10)
    meta1 = http(None).get(f"/api/public/booking/{token}").json()
    prima = _submit(http, token, meta1["submission_token"], inizio.isoformat())
    assert prima.status_code == 201
    meta2 = http(None).get(f"/api/public/booking/{token}").json()
    seconda = _submit(http, token, meta2["submission_token"],
                      (inizio + timedelta(hours=1)).isoformat())
    assert seconda.status_code == 404  # rifiutata dal rate limit, risposta neutra


# ---------------------------------------------------------------------------
# G - IP: SOLO l'HMAC, mai l'IP grezzo (D8)
# ---------------------------------------------------------------------------

def test_30_client_ip_hash_e_sempre_un_hmac_mai_lip_grezzo(http, mondo):
    mondo["orario_settimanale"](mondo["luca"])
    r = _crea_link(http, "giorgio", assigned_user_id=mondo["luca"])
    token = r.json()["token"]
    meta = http(None).get(f"/api/public/booking/{token}").json()
    inizio = datetime.combine(_lunedi_prossimo(), datetime.min.time(),
                              tzinfo=timezone.utc) + timedelta(hours=10)
    _submit(http, token, meta["submission_token"], inizio.isoformat())
    riga = mondo["sql"]("SELECT client_ip_hash FROM public_booking_submissions")[0]
    assert len(riga[0]) == 64
    assert riga[0] != "testclient"  # l'host di default del TestClient FastAPI
    import hmac as _hmac
    atteso = _hmac.new(IP_PEPPER.encode(), b"testclient", hashlib.sha256).hexdigest()
    assert riga[0] == atteso


# ---------------------------------------------------------------------------
# H - REGRESSIONE: A30-11 CRM SOFT resta invariato
# ---------------------------------------------------------------------------

def test_31_crm_manuale_resta_soft_senza_orari_configurati(mondo):
    """A30-11 (D2): senza orari configurati, il CRM manuale resta
    PERMISSIVO - l'esatto opposto del pubblico HARD provato sopra
    (test_14). Un solo controllo, sul motore vero, basta a dimostrare che
    A30-12 non l'ha toccato."""
    from appointments import working_hours
    effettiva = working_hours.effective_windows(
        datetime(2027, 1, 4, 0, 0, tzinfo=timezone.utc),
        datetime(2027, 1, 5, 0, 0, tzinfo=timezone.utc),
        has_weekly_config=False, weekly_rows=[], exception_rows=[], closure_rows=[])
    assert working_hours.is_within(
        datetime(2027, 1, 4, 10, 0, tzinfo=timezone.utc),
        datetime(2027, 1, 4, 10, 30, tzinfo=timezone.utc), effettiva) is True
