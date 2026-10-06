"""CREAZIONE-GUIDATA-1 - backend della procedura guidata su PostgreSQL VERO.

La procedura usa SOLO rotte esistenti; l'unica estensione e' `record_kind`
(+ `assigned_agent_id`) su `POST /api/property/census/units`, per le unita'
COMMERCIALI in palazzina (ingresso dall'elenco Commerciale). Si prova:

  01  unita' commerciale in palazzina: nasce `crm`/`draft`, indirizzo
      ereditato, nell'elenco Commerciale e nei contatori dell'edificio;
  02  assegnazione con le regole di POST /properties: l'agente a se stesso
      (mai dal payload), il titolare a un agente attivo, mai fuori agenzia;
  03  censimento invariato: default `census`, nessuna assegnazione ammessa;
  04  idempotenza e duplicati anche per la commerciale (stessa chiave = stessa
      riga; identita' catastale completa bloccata, mai inventata);
  05  edificio esistente riusato senza modifiche; edificio vuoto ammesso;
  06  separazione fra agenzie (edificio altrui = 404, nessuna riga);
  07  percorsi esistenti: un'acquisizione nasce sulla commerciale senza
      «Prendi in carico» (sulla census resta 409 PROPERTY_IN_CENSUS).
"""
from __future__ import annotations

import uuid

import pytest

from tests.test_censimento_3_backend_postgres import (  # noqa: F401  (fixture riusate)
    DSN, _edificio, _q, _unita, completo, mondo,
)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata")
UNITS = "/api/property/census/units"


def test_01_unita_commerciale_in_palazzina(mondo):
    m = mondo
    e = _edificio(m, units_declared=4)
    u = _unita(m, building_id=e["id"], record_kind="crm", floor="1", internal_number="1")
    assert (u["record_kind"], u["commercial_status"], u["address_inherited"]) == ("crm", "draft", True)
    assert (u["city"], u["address"], u["civic_number"]) == ("Fermo", "Via Roma", "10")
    assert u["code"].startswith("IMM-")
    assert _q(m, "SELECT note FROM property_status_history WHERE property_id = %s", (u["id"],))[0][0] == "initial status"
    commerciali = m["api"]().get("/api/property/properties", params={"limit": 200}).json()["items"]
    assert u["id"] in {p["id"] for p in commerciali}
    s = m["api"]().get(f"/api/property/buildings/{e['id']}").json()["census_summary"]
    assert (s["units_counted"], s["units_to_complete"], s["units_in_census"]) == (1, 3, 0)


def test_02_assegnazione_con_le_regole_esistenti(mondo):
    m = mondo
    e = _edificio(m)
    ids = m["ids"]
    # l'agente: sempre a se stesso, anche se il corpo dice altro
    agente = m["api"]("agent_a").post(UNITS, json={"building_id": e["id"], "record_kind": "crm", "floor": "1",
                                                   "assigned_agent_id": ids["owner_a"]})
    assert agente.status_code == 201, agente.text
    assert agente.json()["assigned_agent_id"] == ids["agent_a"] and agente.json()["assigned_to"]
    # il titolare sceglie un agente attivo della stessa agenzia
    titolare = m["api"]().post(UNITS, json={"building_id": e["id"], "record_kind": "crm", "floor": "2",
                                             "assigned_agent_id": ids["agent_a"]})
    assert titolare.status_code == 201 and titolare.json()["assigned_agent_id"] == ids["agent_a"]
    # ... mai un operatore di un'altra agenzia (400, nessuna riga)
    prima = _q(m, "SELECT count(*) FROM properties WHERE building_id = %s", (e["id"],))[0][0]
    fuori = m["api"]().post(UNITS, json={"building_id": e["id"], "record_kind": "crm", "floor": "3",
                                          "assigned_agent_id": ids["owner_b"]})
    assert fuori.status_code == 400, fuori.text
    assert _q(m, "SELECT count(*) FROM properties WHERE building_id = %s", (e["id"],))[0][0] == prima
    # senza scelta: nessuna assegnazione inventata
    libera = _unita(m, building_id=e["id"], record_kind="crm", floor="4")
    assert libera["assigned_agent_id"] is None


def test_03_censimento_invariato(mondo):
    m = mondo
    e = _edificio(m)
    u = _unita(m, building_id=e["id"], floor="1")
    assert u["record_kind"] == "census" and u["assigned_agent_id"] is None
    r = m["api"]().post(UNITS, json={"building_id": e["id"], "assigned_agent_id": m["ids"]["agent_a"]})
    assert r.status_code == 422
    r = m["api"]().post(UNITS, json={"building_id": e["id"], "record_kind": "listing"})
    assert r.status_code == 422
    # agente: una census non si auto-assegna (regola di prima)
    a = m["api"]("agent_a").post(UNITS, json={"building_id": e["id"], "floor": "2"})
    assert a.status_code == 201 and a.json()["assigned_agent_id"] is None


def test_04_idempotenza_e_duplicati_anche_per_la_commerciale(mondo):
    m = mondo
    e = _edificio(m)
    chiave = str(uuid.uuid4())
    corpo = {"building_id": e["id"], "record_kind": "crm", "floor": "1", "client_request_id": chiave}
    primo = m["api"]().post(UNITS, json=corpo)
    secondo = m["api"]().post(UNITS, json=corpo)
    assert primo.status_code == 201 and secondo.json()["replica"] is True and secondo.json()["id"] == primo.json()["id"]
    assert _q(m, "SELECT count(*) FROM properties WHERE client_request_id = %s", (chiave,))[0][0] == 1
    # stessa chiave, corpo diverso (anche solo il tipo di scheda): rifiutata
    diverso = m["api"]().post(UNITS, json={**corpo, "record_kind": "census"})
    assert diverso.status_code == 409 and diverso.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    catasto = {"cadastral_municipality_code": "D542", "cadastral_section": "", "cadastral_sheet": "12",
               "cadastral_parcel": "345", "cadastral_subunit": "4"}
    _unita(m, building_id=e["id"], record_kind="crm", floor="2", **catasto)
    doppio = m["api"]().post(UNITS, json={"building_id": e["id"], "record_kind": "crm", "floor": "3", **catasto})
    assert doppio.status_code == 409 and doppio.json()["code"] == "CADASTRAL_DUPLICATE"
    # stessa posizione (piano/interno): avviso, mai blocco silenzioso
    _unita(m, building_id=e["id"], record_kind="crm", floor="5", internal_number="9")
    simile = m["api"]().post(UNITS, json={"building_id": e["id"], "record_kind": "crm", "floor": "5", "internal_number": "9"})
    assert simile.status_code == 409 and simile.json()["code"] == "SIMILAR_FOUND"


def test_05_edificio_esistente_riusato_senza_modifiche_ed_edificio_vuoto(mondo):
    m = mondo
    e = _edificio(m, units_declared=6, name="Residenza Gabbiano")
    prima = _q(m, "SELECT name, address, civic_number, units_declared, updated_at FROM buildings WHERE id = %s", (e["id"],))[0]
    _unita(m, building_id=e["id"], record_kind="crm", floor="1")
    _unita(m, building_id=e["id"], floor="2")
    dopo = _q(m, "SELECT name, address, civic_number, units_declared, updated_at FROM buildings WHERE id = %s", (e["id"],))[0]
    assert tuple(dopo) == tuple(prima)
    vuoto = _edificio(m, address="Via Vuota", civic_number="1", name=None, units_declared=None, units_declared_source=None)
    s = m["api"]().get(f"/api/property/buildings/{vuoto['id']}").json()
    assert s["units"] == [] and s["census_summary"]["units_declared"] is None
    # i candidati della procedura: stessa via nel Comune
    trovati = m["api"]().get("/api/property/buildings", params={"city": "Fermo", "search": "Roma", "sort": "address"}).json()
    assert e["id"] in {b["id"] for b in trovati["items"]} and vuoto["id"] not in {b["id"] for b in trovati["items"]}


def test_06_separazione_fra_agenzie(mondo):
    m = mondo
    e = _edificio(m)
    r = m["api"]("owner_b").post(UNITS, json={"building_id": e["id"], "record_kind": "crm"})
    assert r.status_code == 404
    assert _q(m, "SELECT count(*) FROM properties WHERE building_id = %s", (e["id"],))[0][0] == 0


def test_07_percorsi_esistenti_acquisizione_sulla_commerciale(mondo):
    m = mondo
    e = _edificio(m)
    api = m["api"]()

    def corpo(pid):
        return {"property_id": pid, "owner_contact_id": m["mario"],
                "appointment": {"start_at": "2027-03-01T10:00:00+01:00", "assigned_user_id": m["ids"]["owner_a"],
                                "client_request_id": str(uuid.uuid4())}}
    commerciale = _unita(m, building_id=e["id"], record_kind="crm", floor="1")
    censita = _unita(m, building_id=e["id"], floor="2")
    for u in (commerciale, censita):
        assert api.post(f"/api/property/properties/{u['id']}/contacts", json={"contact_id": m["mario"], "role": "owner"}).status_code in (200, 201)
    r = api.post("/api/acquisitions", json=corpo(censita["id"]))
    assert r.status_code == 409 and r.json()["code"] == "PROPERTY_IN_CENSUS"
    r = api.post("/api/acquisitions", json=corpo(commerciale["id"]))
    assert r.status_code == 201, r.text
