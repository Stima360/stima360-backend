"""VENDITORI-1 - «questo proprietario vende questo specifico immobile».

Sul database VERO (schema completo con 030/033/081/082/083) attraverso le
rotte reali di core, property, crm e acquisitions:

  Opportunita' Venditore = lead `pipeline='sell'` del contatto
                           + `property_leads(relation_type='seller')` verso
                             QUELL'immobile.
  Nessuna tabella nuova: lo storico e' `activities`, la prossima azione sono i
  `tasks`, l'agente e' `leads.assigned_agent_id`, l'acquisizione e' il modulo
  esistente.

Copre i casi A-L del brief (attivazione idempotente, specificita' per
immobile, comproprietari, lead preesistente, censimento, storico unico,
disattivazione senza cancellazioni, agenzie/ruoli, worklist senza N+1,
passaggio all'acquisizione) piu' la parita' del punteggio seller intent.
Senza P29_TEST_DSN il modulo e' SKIP.
"""
from __future__ import annotations

import threading
import uuid

import pytest

from tests.test_censimento_3_backend_postgres import (  # noqa: F401  (fixture riusate)
    DSN, _q, completo,
)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL per VENDITORI-1")


# ---------------------------------------------------------------------------
# Fixture: app con i router veri, contesti per ruolo, dati e pulizia
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def base(completo):
    """Operatori in piu' (un secondo agente nell'agenzia 1, un agente nella 2)."""
    with completo["conn"].cursor() as cur:
        def operatore(email, nome, agenzia, ruolo):
            cur.execute("INSERT INTO operator_users (email, email_normalized, password_hash, first_name) "
                        "VALUES (%s, %s, 'pbkdf2_sha256$1$x$y', %s) RETURNING id", (email, email, nome))
            uid = cur.fetchone()[0]
            cur.execute("INSERT INTO agency_memberships (agency_id, operator_user_id, role, status) "
                        "VALUES (%s, %s, %s, 'active')", (agenzia, uid, ruolo))
            return uid
        completo["ids"]["agent_a2"] = operatore("a2.a@x.test", "Aldo", 1, "agent")
        completo["ids"]["admin_a"] = operatore("ad.a@x.test", "Ada", 1, "agency_admin")
    return completo


@pytest.fixture
def m(base):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from acquisitions.router import router as acquisizioni
    from core.router import router as core
    from crm.router import router as crm
    from operator_auth.context import OperatorContext
    from operator_auth.dependencies import legacy_basic_agency_context, require_operator
    from property.router import router as immobili

    ids = base["ids"]
    ruoli = {"owner_a": (ids["owner_a"], 1, "agency_owner"), "admin_a": (ids["admin_a"], 1, "agency_admin"),
             "agent_a": (ids["agent_a"], 1, "agent"), "agent_a2": (ids["agent_a2"], 1, "agent"),
             "owner_b": (ids["owner_b"], 2, "agency_owner")}
    stato = {"chi": "owner_a"}

    def contesto():
        uid, agenzia, ruolo = ruoli[stato["chi"]]
        return OperatorContext(user_id=uid, agency_id=agenzia, role=ruolo, is_platform_admin=False,
                               session_id=None, auth_channel="operator_session")

    app = FastAPI()
    for r in (immobili, acquisizioni, core, crm):
        app.include_router(r)
    app.dependency_overrides[legacy_basic_agency_context] = contesto
    app.dependency_overrides[require_operator] = contesto
    client = TestClient(app)

    class _Come:
        def __init__(self, chi):
            self.chi = chi

        def __getattr__(self, metodo):
            def invia(*a, **k):
                stato["chi"] = self.chi
                return getattr(client, metodo)(*a, **k)
            return invia

    def pulisci():
        c = base
        for tabella, trigger in (("activities", "trg_activities_property_history"),
                                 ("acquisition_events", "trg_acquisition_events_append_only"),
                                 ("acquisitions", "trg_acquisitions_refuse_delete"),
                                 ("appointment_events", "trg_appointment_events_append_only"),
                                 ("appointments", "trg_appointments_refuse_delete")):
            _q(c, f"ALTER TABLE {tabella} DISABLE TRIGGER {trigger}")
        _q(c, "ALTER TABLE properties DISABLE TRIGGER trg_properties_mandate_origin")
        _q(c, "UPDATE properties SET acquisition_id = NULL WHERE id > 25")
        _q(c, "ALTER TABLE properties ENABLE TRIGGER trg_properties_mandate_origin")
        _q(c, "DELETE FROM acquisition_events")
        _q(c, "DELETE FROM acquisitions")
        _q(c, "DELETE FROM appointment_events")
        _q(c, "DELETE FROM appointments")
        _q(c, "DELETE FROM activities")
        for tabella, trigger in (("activities", "trg_activities_property_history"),
                                 ("acquisition_events", "trg_acquisition_events_append_only"),
                                 ("acquisitions", "trg_acquisitions_refuse_delete"),
                                 ("appointment_events", "trg_appointment_events_append_only"),
                                 ("appointments", "trg_appointments_refuse_delete")):
            _q(c, f"ALTER TABLE {tabella} ENABLE TRIGGER {trigger}")
        _q(c, "DELETE FROM tasks")
        _q(c, "DELETE FROM property_leads")
        _q(c, "DELETE FROM lead_stime")
        _q(c, "DELETE FROM property_contacts")
        _q(c, "DELETE FROM leads")
        _q(c, "DELETE FROM property_status_history WHERE property_id > 25")
        _q(c, "DELETE FROM property_price_history WHERE property_id > 25")
        _q(c, "DELETE FROM property_accessories")
        _q(c, "UPDATE properties SET parent_property_id = NULL WHERE id > 25")
        _q(c, "DELETE FROM properties WHERE id > 25")
        _q(c, "DELETE FROM buildings")
        _q(c, "DELETE FROM contacts WHERE id <> %s", (c["mario"],))
        _q(c, "UPDATE contacts SET assigned_agent_id = NULL WHERE id = %s", (c["mario"],))
    pulisci()
    yield {**base, "api": lambda chi="owner_a": _Come(chi), "ctx": contesto, "stato": stato, "ruoli": ruoli}
    pulisci()


def _immobile(m, chi="owner_a", **kw):
    corpo = {"title": "Trilocale Via Roma 10", "property_type": "apartment", "city": "Fermo",
             "address": "Via Roma", "civic_number": "10", **kw}
    r = m["api"](chi).post("/api/property/properties", json=corpo)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _contatto(m, nome, agenzia=1, agente=None):
    return _q(m, "INSERT INTO contacts (agency_id, display_name, phone, email, assigned_agent_id) "
                 "VALUES (%s, %s, %s, %s, %s) RETURNING id",
              (agenzia, nome, "+39 333 0000000", f"{nome.split()[0].lower()}@x.test", agente))[0][0]


def _proprietario(m, property_id, contact_id, chi="owner_a", role="owner"):
    r = m["api"](chi).post(f"/api/property/properties/{property_id}/contacts", json={"contact_id": contact_id, "role": role})
    assert r.status_code in (200, 201), r.text


def _attiva(m, property_id, contact_id, chi="owner_a", **kw):
    return m["api"](chi).post("/api/crm/sellers", json={"property_id": property_id, "contact_id": contact_id, **kw})


def _disattiva(m, property_id, contact_id, outcome, chi="owner_a", **kw):
    return m["api"](chi).post("/api/crm/sellers/deactivate",
                              json={"property_id": property_id, "contact_id": contact_id, "outcome": outcome, **kw})


def _worklist(m, chi="owner_a", **params):
    r = m["api"](chi).get("/api/crm/sellers", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _coppie(lista):
    return {(x["contact"]["id"], x["property"]["id"]) for x in lista["items"]}


def _lead_sell(m, contact_id):
    return [r[0] for r in _q(m, "SELECT id FROM leads WHERE contact_id = %s AND pipeline = 'sell' ORDER BY id", (contact_id,))]


def _link_seller(m, property_id):
    return _q(m, "SELECT lead_id FROM property_leads WHERE property_id = %s AND relation_type = 'seller' ORDER BY id", (property_id,))


# ---------------------------------------------------------------------------
# A. Attivazione idempotente
# ---------------------------------------------------------------------------

def test_a01_vende_crea_l_opportunita_sell_e_conserva_il_proprietario(m):
    p = _immobile(m)
    mario = _contatto(m, "Mario Bianchi")
    _proprietario(m, p["id"], mario)
    r = _attiva(m, p["id"], mario)
    assert r.status_code == 201, r.text
    corpo = r.json()
    assert corpo["created"] is True and corpo["property_id"] == p["id"] and corpo["contact_id"] == mario
    lead = _q(m, "SELECT id, pipeline, stage, status, agency_id, contact_id FROM leads WHERE id = %s", (corpo["lead_id"],))[0]
    assert list(lead[1:]) == ["sell", "new", "open", 1, mario]
    assert _link_seller(m, p["id"]) == [[corpo["lead_id"]]]
    # il proprietario resta proprietario: nessuna riga property_contacts toccata o aggiunta
    assert _q(m, "SELECT role FROM property_contacts WHERE property_id = %s AND contact_id = %s", (p["id"], mario)) == [["owner"]]
    # la transizione e' nello storico dell'immobile (una riga di activities, non una tabella nuova)
    storico = m["api"]().get(f"/api/property/properties/{p['id']}/interactions").json()["items"]
    assert [(s["interaction_type"], s["contact_id"]) for s in storico] == [("status_change", mario)]
    assert "Vende" in storico[0]["note"]


def test_a02_retry_secondo_click_e_concorrenza_nessun_duplicato(m):
    p = _immobile(m)
    mario = _contatto(m, "Mario Verdi")
    _proprietario(m, p["id"], mario)
    primo = _attiva(m, p["id"], mario)
    secondo = _attiva(m, p["id"], mario)
    assert primo.status_code == 201 and secondo.status_code == 200, (primo.text, secondo.text)
    assert secondo.json()["lead_id"] == primo.json()["lead_id"] and secondo.json()["created"] is False
    # due richieste simultanee su un'altra coppia: una sola opportunita'
    p2 = _immobile(m, address="Via Po", civic_number="2")
    _proprietario(m, p2["id"], mario)
    esiti = [None, None]

    def corri(i):
        esiti[i] = _attiva(m, p2["id"], mario)
    fili = [threading.Thread(target=corri, args=(i,)) for i in range(2)]
    for f in fili:
        f.start()
    for f in fili:
        f.join(30)
    assert sorted(e.status_code for e in esiti) == [200, 201], [e.text for e in esiti]
    assert len({e.json()["lead_id"] for e in esiti}) == 1
    assert len(_lead_sell(m, mario)) == 2                      # uno per immobile, non di piu'
    assert len(_link_seller(m, p["id"])) == 1 and len(_link_seller(m, p2["id"])) == 1


def test_a03_solo_un_proprietario_reale_dell_immobile_puo_vendere(m):
    p = _immobile(m)
    estraneo = _contatto(m, "Ugo Estraneo")
    r = _attiva(m, p["id"], estraneo)
    assert r.status_code == 409 and r.json()["code"] == "SELLER_NOT_OWNER", r.text
    inquilino = _contatto(m, "Ivo Inquilino")
    _proprietario(m, p["id"], inquilino, role="tenant")
    r = _attiva(m, p["id"], inquilino)
    assert r.status_code == 409 and r.json()["code"] == "SELLER_NOT_OWNER"
    assert _lead_sell(m, estraneo) == [] and _lead_sell(m, inquilino) == []


# ---------------------------------------------------------------------------
# B / C. Specificita' per immobile, comproprietari
# ---------------------------------------------------------------------------

def test_b01_mario_vende_solo_l_immobile_a(m):
    a = _immobile(m)
    b = _immobile(m, address="Viale Mare", civic_number="5")
    mario = _contatto(m, "Mario Neri")
    _proprietario(m, a["id"], mario)
    _proprietario(m, b["id"], mario)
    assert _attiva(m, a["id"], mario).status_code == 201
    assert _coppie(_worklist(m)) == {(mario, a["id"])}


def test_c01_comproprietari_attivati_separatamente(m):
    a = _immobile(m)
    mario, anna = _contatto(m, "Mario Gialli"), _contatto(m, "Anna Gialli")
    _proprietario(m, a["id"], mario)
    _proprietario(m, a["id"], anna)
    assert _attiva(m, a["id"], mario).status_code == 201
    assert _coppie(_worklist(m)) == {(mario, a["id"])}
    dettaglio = m["api"]().get(f"/api/property/properties/{a['id']}").json()
    venditori = {l["contact_id"] for l in dettaglio["leads"] if l["relation_type"] == "seller" and l["pipeline"] == "sell"}
    assert venditori == {mario}                                # Anna: non ancora confermata
    assert _attiva(m, a["id"], anna).status_code == 201
    assert _coppie(_worklist(m)) == {(mario, a["id"]), (anna, a["id"])}
    assert len({x["lead_id"] for x in _worklist(m)["items"]}) == 2


# ---------------------------------------------------------------------------
# D. Lead SELL gia' esistente (stima pubblica, altri immobili, chiusi, ambigui)
# ---------------------------------------------------------------------------

def _lead_esistente(m, contact_id, *, status="open", agente=None, source="public_stima"):
    return _q(m, "INSERT INTO leads (contact_id, source, pipeline, stage, status, agency_id, assigned_agent_id) "
                 "VALUES (%s, %s, 'sell', 'new', %s, 1, %s) RETURNING id", (contact_id, source, status, agente))[0][0]


def test_d01_lead_sell_univoco_senza_immobile_viene_riusato(m):
    p = _immobile(m)
    mario = _contatto(m, "Mario Stima")
    stima = _lead_esistente(m, mario)                         # come lo crea il bridge della stima pubblica
    _proprietario(m, p["id"], mario)
    r = _attiva(m, p["id"], mario)
    assert r.status_code == 200 and r.json()["lead_id"] == stima and r.json()["reused"] is True, r.text
    assert _lead_sell(m, mario) == [stima] and _link_seller(m, p["id"]) == [[stima]]


def test_d02_lead_ambigui_nessuna_associazione_poi_scelta_esplicita(m):
    p = _immobile(m)
    mario = _contatto(m, "Mario Doppio")
    l1, l2 = _lead_esistente(m, mario), _lead_esistente(m, mario)
    _proprietario(m, p["id"], mario)
    r = _attiva(m, p["id"], mario)
    assert r.status_code == 409 and r.json()["code"] == "SELLER_LEAD_AMBIGUOUS", r.text
    assert sorted(c["id"] for c in r.json()["candidates"]) == [l1, l2]
    assert _link_seller(m, p["id"]) == [] and _lead_sell(m, mario) == [l1, l2]     # nulla scritto
    r = _attiva(m, p["id"], mario, lead_id=l2)
    assert r.status_code == 200 and r.json()["lead_id"] == l2
    assert _link_seller(m, p["id"]) == [[l2]] and _lead_sell(m, mario) == [l1, l2]
    # «crea un lead nuovo» resta una scelta esplicita
    q = _immobile(m, address="Via Nuova", civic_number="1")
    _proprietario(m, q["id"], mario)
    assert _attiva(m, q["id"], mario).status_code == 200                       # resta solo l1 libero: univoco
    r = _immobile(m, address="Via Terza", civic_number="3")
    _proprietario(m, r["id"], mario)
    nuovo = _attiva(m, r["id"], mario, new_lead=True)
    assert nuovo.status_code == 201 and nuovo.json()["lead_id"] not in (l1, l2)


def test_d03_lead_di_altro_immobile_o_chiuso_non_si_riusa(m):
    p, altro = _immobile(m), _immobile(m, address="Via Altra", civic_number="9")
    mario = _contatto(m, "Mario Altro")
    l_altro = _lead_esistente(m, mario)
    _q(m, "INSERT INTO property_leads (property_id, lead_id, relation_type) VALUES (%s, %s, 'seller')", (altro["id"], l_altro))
    l_chiuso = _lead_esistente(m, mario, status="closed")
    _proprietario(m, p["id"], mario)
    r = _attiva(m, p["id"], mario)
    assert r.status_code == 201 and r.json()["lead_id"] not in (l_altro, l_chiuso), r.text
    # lead esplicito che appartiene a un altro immobile: rifiutato
    q = _immobile(m, address="Via Quarta", civic_number="4")
    _proprietario(m, q["id"], mario)
    r = _attiva(m, q["id"], mario, lead_id=l_altro)
    assert r.status_code == 409 and r.json()["code"] == "SELLER_LEAD_OTHER_PROPERTY"


def test_d04_lead_non_visibile_all_agente_nessun_duplicato(m):
    p = _immobile(m)
    mario = _contatto(m, "Mario Agente", agente=m["ids"]["agent_a"])
    _lead_esistente(m, mario, agente=None)                   # lead della stima: non assegnato
    _proprietario(m, p["id"], mario)
    r = _attiva(m, p["id"], mario, chi="agent_a")
    assert r.status_code == 409 and r.json()["code"] == "SELLER_LEAD_NOT_VISIBLE", r.text
    assert len(_lead_sell(m, mario)) == 1


# ---------------------------------------------------------------------------
# E. Censimento: niente scorciatoie, si passa da «Prendi in carico»
# ---------------------------------------------------------------------------

def test_e01_census_rifiutato_poi_presa_in_carico_poi_venditore(m):
    unita = m["api"]().post("/api/property/census/units", json={"city": "Fermo", "address": "Via Censita", "civic_number": "1"})
    assert unita.status_code == 201, unita.text
    u = unita.json()
    mario = _contatto(m, "Mario Censito")
    _proprietario(m, u["id"], mario)                          # il collegamento proprietario puo' esistere
    r = _attiva(m, u["id"], mario)
    assert r.status_code == 409 and r.json()["code"] == "PROPERTY_IN_CENSUS", r.text
    assert _lead_sell(m, mario) == []
    assert _q(m, "SELECT record_kind FROM properties WHERE id = %s", (u["id"],))[0][0] == "census"
    t = m["api"]().post(f"/api/property/properties/{u['id']}/take-in-charge", json={"include_pertinenze": True})
    assert t.status_code == 200 and t.json()["id"] == u["id"] and t.json()["code"] == u["code"]
    assert t.json()["record_kind"] == "crm"
    r = _attiva(m, u["id"], mario)
    assert r.status_code == 201 and _coppie(_worklist(m)) == {(mario, u["id"])}


# ---------------------------------------------------------------------------
# F. Storico: una sola riga, visibile ovunque
# ---------------------------------------------------------------------------

def test_f01_interazione_da_venditori_e_la_stessa_riga_ovunque(m):
    p = _immobile(m)
    mario = _contatto(m, "Mario Storico")
    _proprietario(m, p["id"], mario)
    lead = _attiva(m, p["id"], mario).json()["lead_id"]
    nota = "Chiamato Mario, vuole 220.000 EUR, lo richiamo venerdi"
    r = m["api"]().post(f"/api/property/properties/{p['id']}/interactions",
                        json={"interaction_type": "call", "note": nota, "contact_id": mario, "lead_id": lead, "context": "seller"})
    assert r.status_code == 201, r.text
    aid = r.json()["id"]
    assert r.json()["lead_id"] == lead and r.json()["context"] == "seller"
    assert _q(m, "SELECT count(*) FROM activities WHERE description = %s", (nota,))[0][0] == 1   # nessuna copia
    sull_immobile = [x["id"] for x in m["api"]().get(f"/api/property/properties/{p['id']}/interactions").json()["items"]]
    del_contatto = [x["id"] for x in m["api"]().get("/api/core/activities", params={"contact_id": mario}).json()["items"]]
    del_lead = [x["id"] for x in m["api"]().get("/api/core/activities", params={"lead_id": lead}).json()["items"]]
    assert aid in sull_immobile and aid in del_contatto and aid in del_lead
    riga = next(x for x in _worklist(m)["items"] if x["lead_id"] == lead)
    assert riga["last_interaction"]["id"] == aid and riga["last_interaction"]["interaction_type"] == "call"
    # un lead che NON e' il venditore di questo immobile: rifiutato, nulla scritto
    altro = _lead_esistente(m, mario)
    r = m["api"]().post(f"/api/property/properties/{p['id']}/interactions",
                        json={"interaction_type": "call", "note": "x", "contact_id": mario, "lead_id": altro, "context": "seller"})
    assert r.status_code == 400
    assert _q(m, "SELECT count(*) FROM activities WHERE lead_id = %s", (altro,))[0][0] == 0


# ---------------------------------------------------------------------------
# G. Disattivazione e riattivazione senza cancellare nulla
# ---------------------------------------------------------------------------

def test_g01_disattivare_conserva_tutto_e_la_riattivazione_riapre_lo_stesso_lead(m):
    p = _immobile(m)
    mario = _contatto(m, "Mario Pausa")
    _proprietario(m, p["id"], mario)
    lead = _attiva(m, p["id"], mario).json()["lead_id"]
    m["api"]().post(f"/api/property/properties/{p['id']}/interactions",
                    json={"interaction_type": "call", "note": "prima telefonata", "contact_id": mario, "lead_id": lead, "context": "seller"})
    t = m["api"]().post("/api/core/tasks", json={"lead_id": lead, "contact_id": mario, "title": "Richiamare Mario", "due_at": "2030-01-10T09:00:00+01:00"})
    assert t.status_code in (200, 201), t.text
    prima = {tab: _q(m, f"SELECT count(*) FROM {tab}")[0][0] for tab in ("activities", "tasks", "property_leads", "leads")}

    r = _disattiva(m, p["id"], mario, "paused")
    assert r.status_code == 200 and r.json()["status"] == "paused"
    assert _coppie(_worklist(m)) == set() and _coppie(_worklist(m, status="paused")) == {(mario, p["id"])}

    r = _disattiva(m, p["id"], mario, "not_selling", note="Ha deciso di affittare")
    assert r.status_code == 200 and r.json()["status"] == "closed" and r.json()["stage"] == "lost"
    assert r.json()["lost_reason"].startswith("Non vende più")
    assert _disattiva(m, p["id"], mario, "not_selling").status_code == 200        # idempotente
    assert _coppie(_worklist(m)) == set() and _coppie(_worklist(m, status="closed")) == {(mario, p["id"])}
    dopo = {tab: _q(m, f"SELECT count(*) FROM {tab}")[0][0] for tab in ("activities", "tasks", "property_leads", "leads")}
    assert dopo["tasks"] == prima["tasks"] and dopo["property_leads"] == prima["property_leads"] and dopo["leads"] == prima["leads"]
    assert dopo["activities"] == prima["activities"] + 2                       # solo le due transizioni, append-only

    r = _attiva(m, p["id"], mario)
    assert r.status_code == 200 and r.json()["lead_id"] == lead and r.json()["reopened"] is True
    assert _q(m, "SELECT status, stage, lost_reason, closed_at FROM leads WHERE id = %s", (lead,))[0] == ["open", "new", None, None]
    assert _lead_sell(m, mario) == [lead] and _coppie(_worklist(m)) == {(mario, p["id"])}

    r = _disattiva(m, p["id"], mario, "mistake")
    assert r.status_code == 200 and r.json()["lost_reason"].startswith("Inserito per errore")
    assert _q(m, "SELECT count(*) FROM property_leads WHERE lead_id = %s", (lead,))[0][0] == 1


# ---------------------------------------------------------------------------
# H. Agenzie e ruoli
# ---------------------------------------------------------------------------

def test_h01_nessun_leak_tra_agenzie_e_visibilita_per_ruolo(m):
    p = _immobile(m)
    mario = _contatto(m, "Mario Agenzia", agente=m["ids"]["agent_a"])
    _proprietario(m, p["id"], mario)
    # l'agente lavora i propri contatti: il lead nasce assegnato a lui
    r = _attiva(m, p["id"], mario, chi="agent_a")
    assert r.status_code == 201 and r.json()["assigned_agent_id"] == m["ids"]["agent_a"], r.text
    assert _coppie(_worklist(m, "agent_a")) == {(mario, p["id"])}
    assert _coppie(_worklist(m, "agent_a2")) == set()                          # un altro agente non lo vede
    assert _coppie(_worklist(m, "owner_a")) == {(mario, p["id"])}
    # un'altra agenzia: ne' lista, ne' attivazione, ne' disattivazione
    assert _coppie(_worklist(m, "owner_b")) == set()
    assert _attiva(m, p["id"], mario, chi="owner_b").status_code == 404
    assert _disattiva(m, p["id"], mario, "not_selling", chi="owner_b").status_code == 404
    # agente su un proprietario non suo: rifiutato in chiaro, nessun lead
    anna = _contatto(m, "Anna Altrui", agente=m["ids"]["agent_a2"])
    _proprietario(m, p["id"], anna)
    r = _attiva(m, p["id"], anna, chi="agent_a")
    assert r.status_code == 409 and r.json()["code"] == "SELLER_CONTACT_NOT_ASSIGNED", r.text
    assert _lead_sell(m, anna) == []
    # l'admin crea (lead non assegnato), poi assegna con la rotta di sempre
    r = _attiva(m, p["id"], anna, chi="admin_a")
    assert r.status_code == 201 and r.json()["assigned_agent_id"] is None
    assert _coppie(_worklist(m, "agent_a2")) == set()
    a = m["api"]("admin_a").patch(f"/api/core/leads/{r.json()['lead_id']}/assignment", json={"assigned_agent_id": m["ids"]["agent_a2"]})
    assert a.status_code == 200, a.text
    assert _coppie(_worklist(m, "agent_a2")) == {(anna, p["id"])}
    # un agente non filtra per un altro agente
    assert _worklist(m, "agent_a", agent_id=m["ids"]["agent_a2"])["items"] == []


# ---------------------------------------------------------------------------
# I. Worklist: filtri, paginazione, ultima interazione, prossima azione, N+1
# ---------------------------------------------------------------------------

def _scenario_lista(m, n):
    righe = []
    for i in range(n):
        p = _immobile(m, address=f"Via Lista {i}", civic_number=str(i), city="Fermo" if i % 2 else "Porto")
        c = _contatto(m, f"Venditore{i} Lista")
        _proprietario(m, p["id"], c)
        righe.append((c, p["id"], _attiva(m, p["id"], c).json()["lead_id"]))
    return righe


def test_i01_filtri_paginazione_ultima_interazione_e_prossima_azione(m):
    righe = _scenario_lista(m, 5)
    (c0, p0, l0), (c1, p1, l1), (c2, p2, l2) = righe[:3]
    m["api"]().patch(f"/api/core/leads/{l1}", json={"stage": "contacted"})
    m["api"]().patch(f"/api/core/leads/{l2}", json={"stage": "appointment"})
    m["api"]().post("/api/core/tasks", json={"lead_id": l0, "contact_id": c0, "title": "Richiamare", "due_at": "2020-01-01T09:00:00+01:00"})
    m["api"]().post("/api/core/tasks", json={"lead_id": l0, "contact_id": c0, "title": "Dopo", "due_at": "2031-01-01T09:00:00+01:00"})
    m["api"]().post("/api/core/tasks", json={"lead_id": l1, "contact_id": c1, "title": "Visita", "due_at": "2031-02-01T09:00:00+01:00"})
    m["api"]().post(f"/api/property/properties/{p1}/interactions",
                    json={"interaction_type": "call", "note": "vecchia", "contact_id": c1, "lead_id": l1, "context": "seller"})
    nuova = m["api"]().post(f"/api/property/properties/{p1}/interactions",
                            json={"interaction_type": "meeting", "note": "nuova", "contact_id": c1, "lead_id": l1, "context": "seller"}).json()

    tutti = _worklist(m)
    assert len(tutti["items"]) == 5 and tutti["has_more"] is False
    riga0 = next(x for x in tutti["items"] if x["lead_id"] == l0)
    assert riga0["next_action"]["title"] == "Richiamare" and riga0["next_action"]["overdue"] is True
    riga1 = next(x for x in tutti["items"] if x["lead_id"] == l1)
    assert riga1["last_interaction"]["id"] == nuova["id"] and riga1["next_action"]["overdue"] is False
    assert {x["lead_id"] for x in _worklist(m, view="new")["items"]} == {l0, righe[3][2], righe[4][2]}
    assert [x["lead_id"] for x in _worklist(m, view="contacted")["items"]] == [l1]
    assert [x["lead_id"] for x in _worklist(m, view="ready")["items"]] == [l2]
    assert [x["lead_id"] for x in _worklist(m, view="overdue")["items"]] == [l0]
    assert {x["lead_id"] for x in _worklist(m, view="no_action")["items"]} == {l2, righe[3][2], righe[4][2]}
    assert {x["property"]["city"] for x in _worklist(m, city="Porto")["items"]} == {"Porto"}
    assert [x["lead_id"] for x in _worklist(m, search="Venditore3")["items"]] == [righe[3][2]]
    pag1, pag2 = _worklist(m, limit=2, offset=0), _worklist(m, limit=2, offset=2)
    assert len(pag1["items"]) == 2 and pag1["has_more"] is True and len(pag2["items"]) == 2
    assert not ({x["lead_id"] for x in pag1["items"]} & {x["lead_id"] for x in pag2["items"]})
    assert _worklist(m, limit=2, offset=4)["has_more"] is False
    assert m["api"]().get("/api/crm/sellers", params={"view": "boh"}).status_code == 422


def test_i02_nessuna_query_per_riga(m, monkeypatch):
    """La worklist esegue lo stesso numero di statement con 2 o con 8 righe."""
    from core import database as core_database
    conta = {"n": 0}
    originale = core_database.get_connection

    def contata():
        conn = originale()

        class Cur:
            def __init__(self, cur):
                self._c = cur

            def execute(self, *a, **k):
                conta["n"] += 1
                return self._c.execute(*a, **k)

            def __getattr__(self, n):
                return getattr(self._c, n)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return self._c.__exit__(*a)

        class Conn:
            def cursor(self, *a, **k):
                return Cur(conn.cursor(*a, **k))

            def __getattr__(self, n):
                return getattr(conn, n)
        return Conn()

    _scenario_lista(m, 2)
    monkeypatch.setattr(core_database, "get_connection", contata)
    _worklist(m)
    con_due = conta["n"]
    monkeypatch.setattr(core_database, "get_connection", originale)
    _scenario_lista(m, 6)
    monkeypatch.setattr(core_database, "get_connection", contata)
    conta["n"] = 0
    assert len(_worklist(m)["items"]) == 8
    assert conta["n"] == con_due, (con_due, conta["n"])


def test_i03_punteggio_seller_intent_in_blocco_uguale_a_quello_singolo(m, monkeypatch):
    from core import database as core_database
    from seller_intent import database as intent_db
    righe = _scenario_lista(m, 3)
    # una delle tre con un follow-up automatico P18 scaduto: il punteggio cambia
    c, _, lead = righe[0]
    _q(m, "INSERT INTO tasks (agency_id, lead_id, contact_id, title, task_type, status, due_at, metadata) "
          "VALUES (1, %s, %s, 'Contattare proprietario', 'automated_followup', 'open', NOW() - INTERVAL '3 days', "
          "'{\"source\": \"followup\", \"rule_code\": \"FOLLOWUP_STIMA_RICHIESTA\"}')", (lead, c))
    lista = {x["lead_id"]: x["intent"] for x in _worklist(m)["items"]}
    # il percorso singolo legge con la connessione legacy di seller_intent:
    # qui la stessa del banco (quella del core gia' puntata dal fixture),
    # nessun sito di connessione nuovo (P26 h11)
    monkeypatch.setattr(intent_db, "get_connection", lambda: core_database.get_connection())
    from seller_intent.service import get_seller_intent_score_scoped
    m["stato"]["chi"] = "owner_a"
    for _, _, lead in righe:
        singolo = get_seller_intent_score_scoped(m["ctx"](), lead_id=lead)
        assert lista[lead] == {"score": singolo["score"], "band": singolo["band"], "state": singolo["state"]}


# ---------------------------------------------------------------------------
# L. Dall'opportunita' all'acquisizione esistente
# ---------------------------------------------------------------------------

def test_l01_avvia_acquisizione_stesso_immobile_proprietario_e_lead(m):
    p = _immobile(m)
    mario = _contatto(m, "Mario Acquisito")
    _proprietario(m, p["id"], mario)
    lead = _attiva(m, p["id"], mario).json()["lead_id"]
    # il proprietario collegato e' riconosciuto dall'acquisizione (mai «senza proprietari»)
    dettaglio = m["api"]().get(f"/api/property/properties/{p['id']}").json()
    assert [c["contact_id"] for c in dettaglio["contacts"] if c["role"] in ("owner", "seller")] == [mario]
    r = m["api"]().post("/api/acquisitions", json={
        "property_id": p["id"], "owner_contact_id": mario, "lead_id": lead, "source": "seller_lead",
        "appointment": {"start_at": "2031-03-01T10:00:00+01:00", "end_at": "2031-03-01T11:00:00+01:00",
                        "assigned_user_id": m["ids"]["owner_a"], "client_request_id": str(uuid.uuid4())}})
    assert r.status_code == 201, r.text
    acq = r.json()
    riga = _q(m, "SELECT property_id, owner_contact_id, lead_id FROM acquisitions WHERE id = %s", (acq["id"],))[0]
    assert list(riga) == [p["id"], mario, lead]
    assert _lead_sell(m, mario) == [lead]                                  # nessun lead nuovo
    w = next(x for x in _worklist(m)["items"] if x["lead_id"] == lead)
    assert w["acquisition"]["id"] == acq["id"] and w["acquisition"]["visible"] is True
    # una seconda acquisizione sullo stesso immobile resta rifiutata dal modulo esistente
    r2 = m["api"]().post("/api/acquisitions", json={
        "property_id": p["id"], "owner_contact_id": mario, "lead_id": lead,
        "appointment": {"start_at": "2031-03-02T10:00:00+01:00", "end_at": "2031-03-02T11:00:00+01:00",
                        "assigned_user_id": m["ids"]["owner_a"], "client_request_id": str(uuid.uuid4())}})
    assert r2.status_code == 409
    assert _q(m, "SELECT count(*) FROM acquisitions WHERE property_id = %s", (p["id"],))[0][0] == 1
