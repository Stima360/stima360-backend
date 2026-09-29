"""A30-13B.1 - chi puo' creare un appuntamento nell'agenda di chi, PROVATO sul server.

Il quick booking dell'Agenda (click su uno slot vuoto) non ha un endpoint
suo: usa `POST /api/appointments`, lo stesso del pulsante "+ Nuovo
appuntamento". Il frontend mostra a un `agent` solo "Io", ma quella e'
presentazione: l'autorita' e' il server. Questo modulo prova, contro il router
VERO montato su un'app FastAPI di test e un PostgreSQL usa-e-getta, che il
backend GIA' esistente (nessuna riga di `appointments/` toccata da A30-13B.1)
fa rispettare la matrice anche a un payload manipolato:

  * agent -> per se' 201; per un collega 403 FORBIDDEN_ROLE, niente scritto;
  * owner e admin -> per un agente ATTIVO della propria agenzia 201; per un
    agente di un'altra agenzia o revocato 422 AGENT_NOT_ACTIVE;
  * Supreme (platform admin) -> solo nell'agenzia in cui e' "acting": 201 per
    un agente di quell'agenzia, 422 per uno di un'altra, 403 senza acting;
  * un conflitto reale resta 409, senza appuntamento fantasma;
  * gli orari di lavoro restano SOFT per il CRM (A30-11): fuori fascia si crea;
  * la CREATE fissata segna la riga di sync Google (dirty) nella stessa
    transazione, una volta sola, senza alcuna chiamata di rete; una richiesta,
    un rifiuto o una replica idempotente non ne aggiungono.

I contesti sono `OperatorContext` VERI, con i ruoli veri: la permission passa
da `operator_auth.permissions.may_assign_records`, non da un booleano finto.

Le fixture di schema/mondo si riusano da `test_a30_2_appointments_postgres`
(non si ricopiano); qui si aggiunge alla catena 073-076 perche' orari di
lavoro e sync Google devono esistere davvero.

Opt-in: senza `P29_TEST_DSN` si salta tutto. Database usa-e-getta.
"""
from __future__ import annotations

import os
import uuid

import pytest

from tests import test_a30_2_appointments_postgres as a30_2
# Fixture riusate da A30-2, NON ricopiate: il suo `db` (database usa-e-getta,
# orologio ancorato, catena LMC-15 + 072) e il suo `mondo`. Questo modulo non
# apre connessioni proprie: aggiunge soltanto 073-076 sullo stesso database.
from tests.test_a30_2_appointments_postgres import db, mondo  # noqa: F401

DSN = os.getenv("P29_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare A30-13B.1")

MIGRAZIONI = a30_2.MIGRAZIONI
# Oltre alla 072 di A30-2: facciata LMC-15, sync Google (outbound e inbound)
# e orari di lavoro, perche' qui devono esistere davvero.
CATENA_AGENDA = ("073_a30_2p_lmc15_facade", "074_a30_9a_calendar_sync",
                 "075_a30_10_calendar_inbound", "076_a30_11_working_hours")
NAMESPACE = "a3013b-test"


@pytest.fixture(scope="module")
def agenda_completa(db):  # noqa: F811
    with db["conn"].cursor() as cur:
        for versione in CATENA_AGENDA:
            cur.execute((MIGRAZIONI / f"{versione}.sql").read_text(encoding="utf-8"))
    db["conn"].commit()
    return db


@pytest.fixture
def w(mondo, agenda_completa, monkeypatch):  # noqa: F811
    """Il mondo di A30-2 piu' un admin, un agente revocato e un Supreme senza
    membership; il sync Google ACCESO (solo il namespace: nessuna rete)."""
    from calendar_sync import config as gcal_config

    monkeypatch.setattr(gcal_config, "hook_deployment_namespace", lambda environ=None: NAMESPACE)
    sql = mondo["sql"]
    sql("DELETE FROM appointment_calendar_sync")
    sql("DELETE FROM agent_working_hours")

    def utente(email, nome, *, platform=False):
        return sql("INSERT INTO operator_users (email, first_name, last_name, is_platform_admin) "
                   "VALUES (%s,%s,'Test',%s) RETURNING id", (email, nome, platform))[0][0]

    anna = utente("anna@example.it", "Anna")
    sql("INSERT INTO agency_memberships (agency_id, operator_user_id, role) VALUES (%s,%s,"
        "'agency_admin')", (mondo["a"], anna))
    revocato = utente("revocato@example.it", "Revocato")
    sql("INSERT INTO agency_memberships (agency_id, operator_user_id, role, status) "
        "VALUES (%s,%s,'agent','revoked')", (mondo["a"], revocato))
    supremo = utente("supremo@example.it", "Supremo", platform=True)
    yield {**mondo, "anna": anna, "revocato": revocato, "supremo": supremo}
    # Le tabelle 074/076 non le conosce la pulizia di A30-2 (il suo schema non
    # le ha): le righe di questo modulo si tolgono qui, prima del prossimo test.
    mondo["conn"].rollback()
    sql("DELETE FROM appointment_calendar_sync")
    sql("DELETE FROM agent_working_hours")


def _ctx(user_id, agency_id, role, *, platform=False):
    from operator_auth.context import OperatorContext
    return OperatorContext(user_id=user_id, agency_id=agency_id, role=role,
                           is_platform_admin=platform, session_id=None,
                           auth_channel="operator_session")


@pytest.fixture
def api(w):
    """Il router VERO; `require_operator` restituisce il contesto scelto."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from appointments.router import router
    from operator_auth.dependencies import require_operator

    app = FastAPI()
    app.include_router(router)
    stato = {"ctx": None}
    app.dependency_overrides[require_operator] = lambda: stato["ctx"]
    client = TestClient(app)
    ruoli = {
        "luca": (w["luca"], w["a"], "agent"),
        "marta": (w["marta"], w["a"], "agent"),
        "giorgio": (w["giorgio"], w["a"], "agency_owner"),
        "anna": (w["anna"], w["a"], "agency_admin"),
        "estraneo": (w["estraneo"], w["b"], "agent"),
    }

    def come(chi):
        if chi == "supremo_in_a":          # acting nell'agenzia A: ruolo None
            stato["ctx"] = _ctx(w["supremo"], w["a"], None, platform=True)
        elif chi == "supremo_senza_acting":
            stato["ctx"] = _ctx(w["supremo"], None, None, platform=True)
        else:
            stato["ctx"] = _ctx(*ruoli[chi])
        return client

    return come


def _corpo(assigned_user_id, *, h=10, h_fine=None, status=None, chiave=None):
    corpo = {"appointment_type": "seller_meeting",
             "start_at": a30_2.ore(h).isoformat(),
             "end_at": a30_2.ore(h_fine if h_fine is not None else h + 1).isoformat(),
             "client_request_id": chiave or str(uuid.uuid4())}
    if assigned_user_id is not None:
        corpo["assigned_user_id"] = assigned_user_id
    corpo["status"] = status or ("scheduled" if assigned_user_id is not None else "requested")
    return corpo


def _conta(w):
    return {t: w["sql"](f"SELECT count(*) FROM {t}")[0][0]
            for t in ("appointments", "appointment_events", "appointment_calendar_sync")}


def _sync(w, appointment_id):
    return w["sql"]("SELECT status, dirty_generation, provider, agency_id "
                    "FROM appointment_calendar_sync WHERE current_appointment_id=%s",
                    (appointment_id,))


def _rifiutato(w, risposta, stato, codice, prima):
    assert risposta.status_code == stato, risposta.text
    assert risposta.json()["code"] == codice, risposta.text
    assert _conta(w) == prima, "un rifiuto non deve scrivere niente"


# ---------------------------------------------------------------------------
# A - AGENT
# ---------------------------------------------------------------------------

def test_01_agent_crea_per_se_stesso(api, w):
    r = api("luca").post("/api/appointments", json=_corpo(w["luca"]))
    assert r.status_code == 201, r.text
    riga = r.json()
    assert riga["assigned_user_id"] == w["luca"] and riga["status"] == "scheduled"
    creato_da = w["sql"]("SELECT created_by_user_id, agency_id FROM appointments WHERE id=%s",
                         (riga["id"],))[0]
    assert (creato_da[0], creato_da[1]) == (w["luca"], w["a"])


def test_02_agent_payload_manipolato_verso_un_collega_403_senza_scrivere(api, w):
    prima = _conta(w)
    r = api("luca").post("/api/appointments", json=_corpo(w["marta"]))
    _rifiutato(w, r, 403, "FORBIDDEN_ROLE", prima)
    # anche verso il titolare o l'admin: nessun collega, di nessun ruolo
    for altro in (w["giorgio"], w["anna"]):
        _rifiutato(w, api("luca").post("/api/appointments", json=_corpo(altro)),
                   403, "FORBIDDEN_ROLE", prima)


def test_03_agent_non_raggiunge_un_agente_di_un_altra_agenzia(api, w):
    prima = _conta(w)
    # agent di A verso un agente di B: prima ancora della membership, e' un collega
    _rifiutato(w, api("luca").post("/api/appointments", json=_corpo(w["estraneo"])),
               403, "FORBIDDEN_ROLE", prima)
    # agent di B verso un agente di A
    _rifiutato(w, api("estraneo").post("/api/appointments", json=_corpo(w["luca"])),
               403, "FORBIDDEN_ROLE", prima)


def test_04_agent_senza_agente_non_viene_mai_assegnato_a_un_altro(api, w):
    # D2 (A30-2): l'agente non si inferisce. "Fissato" senza agente -> 422;
    # "Richiesta" senza agente -> resta senza agente: nessuna agenda occupata.
    prima = _conta(w)
    _rifiutato(w, api("luca").post("/api/appointments",
                                   json=_corpo(None, status="scheduled")),
               422, "AGENT_REQUIRED", prima)
    r = api("luca").post("/api/appointments", json=_corpo(None))
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "requested" and r.json()["assigned_user_id"] is None


# ---------------------------------------------------------------------------
# B - OWNER / ADMIN
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("chi", ["giorgio", "anna"])
def test_05_owner_e_admin_creano_per_un_agente_attivo_della_propria_agenzia(api, w, chi):
    r = api(chi).post("/api/appointments", json=_corpo(w["marta"]))
    assert r.status_code == 201, r.text
    assert r.json()["assigned_user_id"] == w["marta"]
    assert w["sql"]("SELECT created_by_user_id FROM appointments WHERE id=%s",
                    (r.json()["id"],))[0][0] == w[chi]


@pytest.mark.parametrize("chi", ["giorgio", "anna"])
def test_06_owner_e_admin_non_assegnano_un_agente_di_un_altra_agenzia(api, w, chi):
    prima = _conta(w)
    _rifiutato(w, api(chi).post("/api/appointments", json=_corpo(w["estraneo"])),
               422, "AGENT_NOT_ACTIVE", prima)
    assert w["sql"]("SELECT count(*) FROM appointments WHERE agency_id=%s", (w["b"],))[0][0] == 0


@pytest.mark.parametrize("chi", ["giorgio", "anna"])
def test_07_agente_revocato_rifiutato(api, w, chi):
    prima = _conta(w)
    _rifiutato(w, api(chi).post("/api/appointments", json=_corpo(w["revocato"])),
               422, "AGENT_NOT_ACTIVE", prima)


def test_08_elenco_agenti_solo_attivi_della_propria_agenzia_con_is_me(api, w):
    # E' la sorgente del selettore del quick booking: attivi, stessa agenzia.
    attesi = {w["giorgio"], w["luca"], w["marta"], w["anna"]}
    for chi, io in (("luca", w["luca"]), ("anna", w["anna"]), ("supremo_in_a", None)):
        r = api(chi).get("/api/appointments/agents")
        assert r.status_code == 200, r.text
        voci = r.json()["items"]
        assert {v["id"] for v in voci} == attesi
        assert [v["id"] for v in voci if v["is_me"]] == ([io] if io else [])


# ---------------------------------------------------------------------------
# C - SUPREME (platform admin): solo dentro l'agenzia in cui e' "acting"
# ---------------------------------------------------------------------------

def test_09_supreme_in_acting_crea_per_un_agente_di_quell_agenzia(api, w):
    r = api("supremo_in_a").post("/api/appointments", json=_corpo(w["luca"]))
    assert r.status_code == 201, r.text
    riga = w["sql"]("SELECT agency_id, assigned_user_id, created_by_user_id FROM appointments "
                    "WHERE id=%s", (r.json()["id"],))[0]
    assert tuple(riga) == (w["a"], w["luca"], w["supremo"])


def test_10_supreme_non_raggiunge_un_altra_agenzia_senza_cambiare_contesto(api, w):
    prima = _conta(w)
    _rifiutato(w, api("supremo_in_a").post("/api/appointments", json=_corpo(w["estraneo"])),
               422, "AGENT_NOT_ACTIVE", prima)
    assert w["sql"]("SELECT count(*) FROM appointments WHERE agency_id=%s", (w["b"],))[0][0] == 0
    # senza acting non c'e' agenzia: nessun bypass globale
    _rifiutato(w, api("supremo_senza_acting").post("/api/appointments",
                                                  json=_corpo(w["luca"])),
               403, "PLATFORM_ADMIN_AGENCY_REQUIRED", prima)


# ---------------------------------------------------------------------------
# D - INTEGRITA' DEL DOMINIO lungo lo stesso percorso
# ---------------------------------------------------------------------------

def test_11_conflitto_reale_409_senza_appuntamento_fantasma(api, w):
    primo = api("luca").post("/api/appointments", json=_corpo(w["luca"], h=10))
    assert primo.status_code == 201, primo.text
    prima = _conta(w)
    for chi in ("luca", "giorgio", "supremo_in_a"):
        corpo = _corpo(w["luca"], h=10)
        corpo["start_at"] = a30_2.ore(10, 30).isoformat()
        corpo["end_at"] = a30_2.ore(11, 30).isoformat()
        r = api(chi).post("/api/appointments", json=corpo)
        _rifiutato(w, r, 409, "APPOINTMENT_CONFLICT", prima)
        assert [c["id"] for c in r.json()["conflicts"]] == [primo.json()["id"]]


def test_12_orari_di_lavoro_crm_restano_soft(api, w):
    giorno_iso = a30_2.ore(10).isoweekday()
    # 09:00-18:00 per Luca in quel giorno: la fascia esiste davvero
    w["sql"]("INSERT INTO agent_working_hours (agency_id, user_id, day_of_week, "
             "start_minute, end_minute) VALUES (%s,%s,%s,540,1080)",
             (w["a"], w["luca"], giorno_iso))
    assert w["sql"]("SELECT count(*) FROM agent_working_hours")[0][0] == 1
    for chi, h in (("luca", 20), ("giorgio", 6)):   # fuori fascia, senza conflitto
        r = api(chi).post("/api/appointments", json=_corpo(w["luca"], h=h))
        assert r.status_code == 201, r.text


def test_13_create_fissata_segna_il_sync_google_una_volta_sola(api, w):
    corpo = _corpo(w["luca"], h=15)
    r = api("luca").post("/api/appointments", json=corpo)
    assert r.status_code == 201, r.text
    sync = _sync(w, r.json()["id"])
    assert len(sync) == 1
    stato, generazione, provider, agenzia = sync[0]
    assert (stato, provider, agenzia) == ("pending", "google", w["a"])
    assert generazione >= 1
    # la stessa richiesta ripetuta (idempotenza): 200, nessuna nuova generazione
    replica = api("luca").post("/api/appointments", json=corpo)
    assert replica.status_code == 200 and replica.json()["id"] == r.json()["id"]
    assert _sync(w, r.json()["id"])[0][1] == generazione
    assert w["sql"]("SELECT count(*) FROM appointment_calendar_sync")[0][0] == 1


def test_14_richiesta_e_rifiuti_non_toccano_il_sync_google(api, w):
    r = api("luca").post("/api/appointments", json=_corpo(None, h=16))
    assert r.status_code == 201 and r.json()["status"] == "requested"
    assert _sync(w, r.json()["id"]) == []
    api("luca").post("/api/appointments", json=_corpo(w["marta"], h=17))        # 403
    api("giorgio").post("/api/appointments", json=_corpo(w["estraneo"], h=17))  # 422
    assert w["sql"]("SELECT count(*) FROM appointment_calendar_sync")[0][0] == 0


def test_15_nessuna_rete_durante_la_create(api, w, monkeypatch):
    # Il frontend non chiama Google e nemmeno la CREATE: il sync e' una riga
    # "pending" per il worker. Ogni socket Python aperto qui fallirebbe il test
    # (psycopg2 usa libpq, TestClient e' in-process).
    import socket

    def vietato(*a, **k):
        raise AssertionError("connessione di rete durante la CREATE")

    monkeypatch.setattr(socket.socket, "connect", vietato)
    monkeypatch.setattr(socket, "create_connection", vietato)
    r = api("giorgio").post("/api/appointments", json=_corpo(w["luca"], h=18))
    assert r.status_code == 201, r.text
    assert _sync(w, r.json()["id"])[0][0] == "pending"
