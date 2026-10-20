"""APPUNTAMENTI DI VENDITA - la tipologia `sale_appointment` (migration 106) su
PostgreSQL vero.

Banco: quello di DELETE-ARCH Fase 0 (`tests/test_delete_arch_0_postgres.py`:
schema COMPLETO dal runner, 106 compresa, incarichi generati dal percorso
reale dell'acquisizione), piu' il router VERO dell'Agenda sullo stesso
contesto per ruolo. Gira solo con P29_TEST_DSN su un cluster locale.

  01  la 106: tredici tipologie, vincolo incarico/cliente/agente, 077 invariata,
      down protetta
  02  creazione su un incarico IN CORSO: 201, nessuna visita acquirente
      proiettata, «Apri incarico» fra i collegamenti del dettaglio (solo qui)
  03  incarico SCADUTO: ammesso
  03b incarichi STORICI e firmati nel ponte LMC-15: ammessi; senza scheda in
      #/incarichi, nel dettaglio resta «Apri immobile» e non «Apri incarico»
  03c la ricerca degli incarichi (`/lookups/mandates`): ogni origine, in corso o
      scaduti, mai conclusi o d'altre agenzie; `mandate_page` solo per le
      acquisizioni
  04  incarico concluso (venduto), ritirato o su immobile archiviato, immobile
      senza incarico: 422 SALE_MANDATE_REQUIRED; immobile di un'altra agenzia: 404
  05  cliente e agente obbligatori, senza richiesta d'acquisto registrata
  06  il database rifiuta da solo un appuntamento di vendita senza cliente
  07  modifica: cliente e incarico non si tolgono; le note si cambiano anche se
      nel frattempo l'incarico e' stato venduto
  08  non si prenota da un link pubblico; le altre tipologie restano invariate
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from tests.test_delete_arch_0_postgres import (  # noqa: F401  (fixture riusate)
    DSN, _collega, _contatto, _futuro, _immobile, _q, completo, mondo, operatori,
)
from tests.test_fix_mandate_1_postgres import _incarico_vero, _ponte, _sql_senza_guardia

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
VERSIONE = "106_sale_appointments"


@pytest.fixture
def vm(mondo):
    """`mondo` + il router dell'Agenda, con lo stesso contesto per ruolo."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from appointments.router import router as agenda
    from operator_auth.dependencies import legacy_basic_agency_context, require_operator
    from property.router import router as immobili

    app = FastAPI()
    app.include_router(agenda)
    app.include_router(immobili)
    app.dependency_overrides[legacy_basic_agency_context] = mondo["ctx"]
    app.dependency_overrides[require_operator] = mondo["ctx"]
    client = TestClient(app)

    class _Come:
        def __init__(self, chi):
            self.chi = chi

        def __getattr__(self, metodo):
            def invia(*a, **k):
                mondo["stato"]["chi"] = self.chi
                return getattr(client, metodo)(*a, **k)
            return invia

    return {**mondo, "agenda": _Come}


def _incarico(m):
    """Un immobile con un incarico reale, nato dall'acquisizione."""
    p = _immobile(m)
    proprietario = _contatto(m, "Olga Proprietaria")
    _collega(m, p["id"], proprietario, "owner", True)
    _incarico_vero(m, p["id"], proprietario)
    return p["id"]


def _vendita(m, property_id, contact_id, chi="owner_a", giorni=12, ora=10, **kw):
    corpo = {"appointment_type": "sale_appointment", "property_id": property_id,
             "contact_id": contact_id, "assigned_user_id": m["ids"]["agent_a"],
             "start_at": _futuro(giorni, ora).isoformat(),
             "end_at": _futuro(giorni, ora + 1).isoformat(),
             "client_request_id": str(uuid.uuid4())}
    corpo.update(kw)
    return m["agenda"](chi).post("/api/appointments", json=corpo)


def _conta(m, tabella, where="TRUE", par=None):
    return _q(m, f"SELECT count(*) FROM {tabella} WHERE {where}", par)[0][0]


def test_01_la_106(vm):
    m = vm
    definizione = _q(m, "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                        "WHERE conname = 'appointments_type_chk'")[0][0]
    assert "'sale_appointment'" in definizione and "'buyer_visit'" in definizione
    vincolo = _q(m, "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname = 'appointments_sale_links_chk'")[0][0]
    for colonna in ("property_id", "contact_id", "assigned_user_id"):
        assert colonna in vincolo, vincolo
    assert "'sale_appointment'" not in _q(
        m, "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
           "WHERE conname = 'public_booking_links_appointment_type_chk'")[0][0]

    assert _vendita(m, _incarico(m), _contatto(m, "Carla Acquirente")).status_code == 201
    giu = (ROOT / "migrations" / f"{VERSIONE}_down.sql").read_text(encoding="utf-8")
    giu = giu.replace("BEGIN;", "").replace("COMMIT;", "")
    conn = m["conn"]
    conn.autocommit = False
    try:
        with pytest.raises(Exception, match="106 down: esistono appuntamenti di vendita"):
            with conn.cursor() as cur:
                cur.execute(giu)
    finally:
        conn.rollback()
        conn.autocommit = True
    assert _conta(m, "pg_constraint", "conname = 'appointments_sale_links_chk'") == 1


def test_02_su_un_incarico_in_corso(vm):
    m = vm
    casa, cliente = _incarico(m), _contatto(m, "Carla Acquirente")
    r = _vendita(m, casa, cliente)
    assert r.status_code == 201, r.text
    a = r.json()
    assert (a["appointment_type"], a["property_id"], a["contact_id"], a["assigned_user_id"],
            a["status"]) == ("sale_appointment", casa, cliente, m["ids"]["agent_a"], "scheduled")
    # distinta dalla visita acquirente: nessuna proiezione in property_visits
    assert _conta(m, "property_visits", "appointment_id = %s", (a["id"],)) == 0
    d = m["agenda"]("owner_a").get(f"/api/appointments/{a['id']}")
    assert d.status_code == 200, d.text
    assert d.json()["allowed_links"] == ["contact", "property", "mandate"]
    assert d.json()["property"]["id"] == casa
    # la scheda dell'incarico a cui porta «Apri incarico» esiste
    assert m["api"]().get(f"/api/property/mandates/{casa}").status_code == 200
    # un'altra tipologia con lo stesso immobile non ha «Apri incarico»
    altro = m["agenda"]("owner_a").post("/api/appointments", json={
        "appointment_type": "seller_meeting", "property_id": casa,
        "assigned_user_id": m["ids"]["agent_a"],
        "start_at": _futuro(12, 14).isoformat(), "end_at": _futuro(12, 15).isoformat(),
        "client_request_id": str(uuid.uuid4())})
    assert altro.status_code == 201, altro.text
    assert "mandate" not in m["agenda"]("owner_a").get(
        f"/api/appointments/{altro.json()['id']}").json()["allowed_links"]


def test_03_su_un_incarico_scaduto(vm):
    m = vm
    casa = _incarico(m)
    _q(m, "UPDATE properties SET mandate_end = %s WHERE id = %s",
       (date.today() - timedelta(days=3), casa))
    r = _vendita(m, casa, _contatto(m, "Carla Acquirente"))
    assert r.status_code == 201, r.text


def _storico(m, fine=None):
    """Un incarico STORICO (pre-081): tipo e inizio, senza acquisizione."""
    pid = _immobile(m)["id"]
    _sql_senza_guardia(m, "UPDATE properties SET mandate_type = 'Esclusiva', mandate_start = %s, "
                          "mandate_end = %s, commercial_status = 'mandate' WHERE id = %s",
                       (date.today() - timedelta(days=200), fine, pid))
    return pid


def _firmato(m):
    """Un incarico firmato nel ponte LMC-15 (percorso reale), senza dati sulla scheda."""
    post = _ponte(m)
    pid = _immobile(m)["id"]
    stima = _q(m, "INSERT INTO stime (agency_id, nome, cognome, email, telefono, comune) "
                  "VALUES (1, 'Lia', 'Firma', 'av@x.test', '333', 'Giulianova') RETURNING id")[0][0]
    r = post(f"/api/acquisition/stime/{stima}/links", {"property_id": pid})
    assert r.status_code == 201, r.text
    r = post(f"/api/acquisition/links/{r.json()['id']}/mandate",
             {"mandate_signed_at": "2025-03-01T10:00:00+01:00", "mandate_reference": "INC-AV"})
    assert r.status_code == 200, r.text
    return pid


def test_03b_incarichi_storici_e_lmc15(vm):
    m = vm
    storico, scaduto = _storico(m), _storico(m, fine=date.today() - timedelta(days=5))
    firmato = _firmato(m)
    try:
        for i, pid in enumerate((storico, scaduto, firmato)):
            r = _vendita(m, pid, _contatto(m, f"Carla Acquirente {i}"), ora=9 + 2 * i)
            assert r.status_code == 201, (pid, r.text)
            d = m["agenda"]("owner_a").get(f"/api/appointments/{r.json()['id']}").json()
            # nessuna scheda in #/incarichi: resta la scheda immobile
            assert d["allowed_links"] == ["contact", "property"], d["allowed_links"]
            assert m["api"]().get(f"/api/property/mandates/{pid}").status_code == 404
        # uno storico venduto resta escluso, come gli altri
        _sql_senza_guardia(m, "UPDATE properties SET commercial_status = 'sold' WHERE id = %s", (storico,))
        r = _vendita(m, storico, _contatto(m, "Dina Acquirente"), ora=17)
        assert r.status_code == 422 and r.json()["code"] == "SALE_MANDATE_REQUIRED", r.text
    finally:
        # gli appuntamenti li toglie la pulizia di `mondo`; il ponte no (come test_08 di FIX-MANDATE-1)
        _q(m, "DELETE FROM stima_acquisitions WHERE property_id = %s", (firmato,))


def test_03c_ricerca_degli_incarichi(vm):
    m = vm
    marca = uuid.uuid4().hex[:6]
    da_acquisizione = _incarico(m)
    storico = _storico(m)
    venduto = _storico(m)
    senza = _immobile(m)["id"]
    altrui = _immobile(m, "owner_b")["id"]
    for pid in (da_acquisizione, storico, venduto, senza, altrui):
        _q(m, "UPDATE properties SET title = %s WHERE id = %s", (f"Casa {marca}", pid))
    _sql_senza_guardia(m, "UPDATE properties SET commercial_status = 'sold' WHERE id = %s", (venduto,))
    r = m["agenda"]("owner_a").get("/api/appointments/lookups/mandates", params={"search": marca})
    assert r.status_code == 200, r.text
    voci = {v["id"]: v for v in r.json()["items"]}
    assert set(voci) == {da_acquisizione, storico}, voci
    assert (voci[da_acquisizione]["mandate_origin"], voci[da_acquisizione]["mandate_page"]) == (
        "acquisition", True)
    assert (voci[storico]["mandate_origin"], voci[storico]["mandate_page"]) == ("historical", False)
    assert {v["mandate_state"] for v in voci.values()} == {"active"}
    # senza testo non si elenca l'archivio
    assert m["agenda"]("owner_a").get("/api/appointments/lookups/mandates").json()["items"] == []


@pytest.mark.parametrize("modifica", [
    "UPDATE properties SET commercial_status = 'sold' WHERE id = %s",
    "UPDATE properties SET commercial_status = 'withdrawn' WHERE id = %s",
    "UPDATE properties SET archived_at = NOW() WHERE id = %s",
])
def test_04_incarico_concluso_ritirato_o_archiviato(vm, modifica):
    m = vm
    casa = _incarico(m)
    _sql_senza_guardia(m, modifica, (casa,))
    prima = _conta(m, "appointments")
    r = _vendita(m, casa, _contatto(m, "Carla Acquirente"))
    assert r.status_code == 422 and r.json()["code"] == "SALE_MANDATE_REQUIRED", r.text
    assert _conta(m, "appointments") == prima


def test_04b_immobile_senza_incarico_o_di_un_altra_agenzia(vm):
    m = vm
    cliente = _contatto(m, "Carla Acquirente")
    senza = _immobile(m)["id"]
    r = _vendita(m, senza, cliente)
    assert r.status_code == 422 and r.json()["code"] == "SALE_MANDATE_REQUIRED", r.text
    altrui = _immobile(m, "owner_b")["id"]
    r = _vendita(m, altrui, cliente)
    assert r.status_code == 404, r.text


def test_05_cliente_e_agente_obbligatori(vm):
    m = vm
    casa = _incarico(m)
    r = _vendita(m, casa, None)
    assert r.status_code == 422 and r.json()["code"] == "SALE_CLIENT_REQUIRED", r.text
    r = _vendita(m, casa, _contatto(m, "Dario Acquirente"), assigned_user_id=None,
                 status="requested")
    assert r.status_code == 422 and r.json()["code"] == "AGENT_REQUIRED", r.text
    r = _vendita(m, None, _contatto(m, "Elio Acquirente"))
    assert r.status_code == 422 and r.json()["code"] == "SALE_MANDATE_REQUIRED", r.text
    # nessuna richiesta d'acquisto serve: il cliente e' un contatto dell'agenzia
    cliente = _contatto(m, "Franca Acquirente")
    assert _conta(m, "buy_requests", "contact_id = %s", (cliente,)) == 0
    assert _vendita(m, casa, cliente).status_code == 201


def test_06_il_database_rifiuta_da_solo_una_vendita_senza_cliente(vm):
    from psycopg2 import errors
    m = vm
    casa = _incarico(m)
    with pytest.raises(errors.CheckViolation, match="appointments_sale_links_chk"):
        _q(m, "INSERT INTO appointments (agency_id, assigned_user_id, appointment_type, status, "
              "start_at, end_at, property_id) VALUES (1, %s, 'sale_appointment', 'scheduled', "
              "%s, %s, %s)", (m["ids"]["agent_a"], _futuro(12, 10), _futuro(12, 11), casa))


def test_07_modifica(vm):
    m = vm
    casa, cliente = _incarico(m), _contatto(m, "Carla Acquirente")
    a = _vendita(m, casa, cliente).json()
    http = m["agenda"]("owner_a")
    r = http.patch(f"/api/appointments/{a['id']}", json={"version": a["version"], "contact_id": None})
    assert r.status_code == 422 and r.json()["code"] == "SALE_CLIENT_REQUIRED", r.text
    senza = _immobile(m)["id"]
    r = http.patch(f"/api/appointments/{a['id']}", json={"version": a["version"], "property_id": senza})
    assert r.status_code == 422 and r.json()["code"] == "SALE_MANDATE_REQUIRED", r.text
    # l'incarico gia' collegato resta valido anche se poi viene venduto
    _sql_senza_guardia(m, "UPDATE properties SET commercial_status = 'sold' WHERE id = %s", (casa,))
    r = http.patch(f"/api/appointments/{a['id']}",
                   json={"version": a["version"], "notes": "Portare la planimetria"})
    assert r.status_code == 200, r.text
    assert r.json()["notes"] == "Portare la planimetria" and r.json()["property_id"] == casa


def test_08_nessuna_prenotazione_pubblica_e_altre_tipologie_invariate(vm):
    m = vm
    prima = _conta(m, "public_booking_links")
    r = m["agenda"]("owner_a").post("/api/appointments/booking-links", json={
        "assigned_user_id": m["ids"]["agent_a"], "appointment_type": "sale_appointment",
        "duration_minutes": 60})
    assert r.status_code == 422, r.text
    assert _conta(m, "public_booking_links") == prima
    casa = _immobile(m)["id"]
    r = m["agenda"]("owner_a").post("/api/appointments", json={
        "appointment_type": "call", "property_id": casa, "assigned_user_id": m["ids"]["agent_a"],
        "start_at": _futuro(12, 16).isoformat(), "end_at": _futuro(12, 17).isoformat(),
        "client_request_id": str(uuid.uuid4())})
    assert r.status_code == 201, r.text
