"""STIMA360 ZONA-6 - ultimo contatto, prossima azione e «Da ricontattare» per la mappa.

GET /api/crm/sellers/zone sul database VERO, con le rotte reali di core,
property, crm (stesse fixture di tests/test_venditori_1_postgres.py):

  z01  per edificio: ultimo contatto (interazione piu' recente sulle unita' o
       sui loro venditori), prossima azione (attivita' aperta piu' vicina,
       altrimenti `leads.next_action_at`), «Da ricontattare» = la stessa vista
       «Da richiamare» della worklist Venditori; immobile senza edificio a parte;
  z02  venditori fuori dalla worklist (lead chiuso, inserito per errore) non
       contano; un'attivita' futura non e' «da ricontattare»;
  z03  scope: un'altra agenzia non vede nulla, un agente vede solo i propri
       venditori e le interazioni che puo' leggere; nessuna scrittura.
Senza P29_TEST_DSN il modulo e' SKIP.
"""
from __future__ import annotations

import pytest

from tests.test_censimento_3_backend_postgres import DSN, _q  # noqa: F401
from tests.test_venditori_1_postgres import (  # noqa: F401  (fixture riusate)
    _attiva, _contatto, _immobile, _proprietario, _worklist, base, completo, m,
)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL per ZONA-6")


def _zona(m, chi="owner_a"):
    r = m["api"](chi).get("/api/crm/sellers/zone")
    assert r.status_code == 200, r.text
    return r.json()


def _edificio(m, **kw):
    r = m["api"]().post("/api/property/buildings", json={"city": "Fermo", "address": "Via Zona", "civic_number": "6",
                                                         "units_declared": 4, "units_declared_source": "survey", **kw})
    assert r.status_code == 201, r.text
    return r.json()


def _unita_presa(m, building_id, interno):
    r = m["api"]().post("/api/property/census/units", json={"building_id": building_id, "floor": "1", "internal_number": interno})
    assert r.status_code == 201, r.text
    t = m["api"]().post(f"/api/property/properties/{r.json()['id']}/take-in-charge", json={"include_pertinenze": True})
    assert t.status_code == 200, t.text
    return t.json()


def _venditore(m, property_id, nome, chi="owner_a", agente=None):
    c = _contatto(m, nome, agente=agente)
    _proprietario(m, property_id, c, chi=chi)
    r = _attiva(m, property_id, c, chi=chi)
    assert r.status_code == 201, r.text
    return c, r.json()["lead_id"]


def _task(m, lead, contatto, titolo, quando, chi="owner_a"):
    r = m["api"](chi).post("/api/core/tasks", json={"lead_id": lead, "contact_id": contatto, "title": titolo, "due_at": quando})
    assert r.status_code in (200, 201), r.text


def _interazione(m, property_id, tipo, contatto=None, lead=None, chi="owner_a"):
    corpo = {"interaction_type": tipo, "note": f"{tipo} di prova"}
    if contatto:
        corpo.update(contact_id=contatto)
    if lead:
        corpo.update(lead_id=lead, context="seller")
    r = m["api"](chi).post(f"/api/property/properties/{property_id}/interactions", json=corpo)
    assert r.status_code == 201, r.text
    return r.json()


def _per_edificio(esito, bid):
    return next((x for x in esito["buildings"] if x["building_id"] == bid), None)


def test_z01_edificio_ultimo_contatto_prossima_azione_e_da_ricontattare(m):
    e = _edificio(m)
    u1 = _unita_presa(m, e["id"], "1")
    u2 = _unita_presa(m, e["id"], "2")
    mario, l1 = _venditore(m, u1["id"], "Mario Zona")
    anna, l2 = _venditore(m, u2["id"], "Anna Zona")
    _task(m, l1, mario, "Richiamare Mario", "2020-01-01T09:00:00+01:00")      # scaduta
    _task(m, l2, anna, "Visita Anna", "2031-01-01T09:00:00+01:00")
    _interazione(m, u1["id"], "call", mario, l1)
    ultima = _interazione(m, u2["id"], "meeting", anna, l2)
    villa = _immobile(m, address="Via Villa", civic_number="1")              # nessun edificio
    luca, l3 = _venditore(m, villa["id"], "Luca Villa")
    _q(m, "UPDATE leads SET next_action_at = '2031-03-01T10:00:00+01:00' WHERE id = %s", (l3,))
    prima = _q(m, "SELECT count(*) FROM activities")[0][0]

    esito = _zona(m)
    b = _per_edificio(esito, e["id"])
    assert b["to_recontact"] is True
    assert b["next_action"]["title"] == "Richiamare Mario" and b["next_action"]["overdue"] is True
    assert b["next_action"]["source"] == "task" and b["next_action"]["due_at"].startswith("2020-01-01")
    assert b["last_contact"]["type"] == "meeting"
    assert b["last_contact"]["occurred_at"][:19] == str(_q(m, "SELECT occurred_at AT TIME ZONE 'UTC' FROM activities WHERE id = %s",
                                                            (ultima["id"],))[0][0]).replace(" ", "T")[:19]
    v = next(x for x in esito["properties"] if x["property_id"] == villa["id"])
    assert v["to_recontact"] is False and v["next_action"]["source"] == "lead" and v["next_action"]["overdue"] is False
    assert v["next_action"]["title"] == "Prossima azione"
    # «Da ricontattare» e' la vista «Da richiamare» della worklist: stessi lead
    assert {x["lead_id"] for x in _worklist(m, view="overdue")["items"]} == {l1}
    # sola lettura
    assert _q(m, "SELECT count(*) FROM activities")[0][0] == prima


def test_z02_lead_chiuso_o_per_errore_non_conta_e_attivita_futura_non_e_scaduta(m):
    e = _edificio(m, civic_number="8")
    u = _unita_presa(m, e["id"], "1")
    mario, lead = _venditore(m, u["id"], "Mario Futuro")
    _task(m, lead, mario, "Dopo", "2031-05-01T09:00:00+01:00")
    b = _per_edificio(_zona(m), e["id"])
    assert b["to_recontact"] is False and b["next_action"]["overdue"] is False and b["last_contact"] is None
    _task(m, lead, mario, "Scaduta", "2020-02-01T09:00:00+01:00")
    assert _per_edificio(_zona(m), e["id"])["to_recontact"] is True
    _q(m, "UPDATE leads SET status = 'closed' WHERE id = %s", (lead,))
    assert _per_edificio(_zona(m), e["id"]) is None                          # fuori dalla worklist: nulla
    _q(m, "UPDATE leads SET status = 'open', lost_reason = 'created_by_mistake' WHERE id = %s", (lead,))
    assert _per_edificio(_zona(m), e["id"]) is None


def test_z03_altra_agenzia_e_agente_vedono_solo_il_proprio(m):
    e = _edificio(m, civic_number="12")
    u1 = _unita_presa(m, e["id"], "1")
    u2 = _unita_presa(m, e["id"], "2")
    mario, l1 = _venditore(m, u1["id"], "Mario Agente", chi="agent_a", agente=m["ids"]["agent_a"])
    anna, l2 = _venditore(m, u2["id"], "Anna Altro", chi="agent_a2", agente=m["ids"]["agent_a2"])
    _task(m, l2, anna, "Richiamare Anna", "2020-03-01T09:00:00+01:00", chi="agent_a2")
    _interazione(m, u2["id"], "call", anna, l2, chi="agent_a2")
    assert _zona(m, "owner_b") == {"buildings": [], "properties": []}
    tutto = _per_edificio(_zona(m, "owner_a"), e["id"])
    assert tutto["to_recontact"] is True and tutto["last_contact"]["type"] == "call"
    # agent_a: il venditore di Anna e la sua telefonata non sono suoi
    suo = _per_edificio(_zona(m, "agent_a"), e["id"])
    assert suo is not None and suo["to_recontact"] is False and suo["next_action"] is None and suo["last_contact"] is None
    assert _per_edificio(_zona(m, "agent_a2"), e["id"])["to_recontact"] is True
    assert {x["lead_id"] for x in _worklist(m, "agent_a", view="overdue")["items"]} == set()
    assert mario and l1
