"""VENDITORI-SOSPESI-1 - un'opportunita' Venditore sospesa blocca il Cestino
dell'immobile (regola INVARIATA del backend) e resta raggiungibile e
chiudibile, sul database VERO con le rotte reali.

Il caso reale da cui nasce (TEST, IMM-113 / contatto #182 / lead #85,
`paused`): «Nessun venditore attivo» in Venditori, ma «Opportunità Venditore
aperta» nel foglio Elimina dell'immobile. Qui lo si riproduce con dati propri
(mai i dati reali) e si fissa:

  s01  la regola: open e paused bloccano, closed (non vende piu' / per errore)
       no; il blocco porta lo stato di ogni lead (serve all'interfaccia);
  s02  COERENZA: ogni lead che blocca il Cestino e' raggiungibile in
       Venditori (filtro Attivi o Sospesi) da chi gestisce l'agenzia;
  s03  chiusura DIRETTA da sospesa («Non vende più»): nessun evento artificiale
       di riattivazione nello storico, un solo evento di chiusura, lead e
       collegamenti conservati; il Cestino resta bloccato fino alla chiusura
       effettiva, poi si sblocca (e prima il tentativo e' 409 senza effetti);
  s04  chiusura diretta da sospesa come «Inserito per errore»;
  s05  permessi tra agenti: un altro agente non vede la sospesa e non la chiude
       (404, nessuna modifica, nessun dettaglio); l'agente assegnato e il
       titolare si';
  s06  la regola backend open/paused e' nel codice esattamente come prima.

Riusa fixture e helper di tests/test_venditori_1_postgres.py. Senza
P29_TEST_DSN il modulo e' SKIP.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_censimento_3_backend_postgres import DSN, _q, completo  # noqa: F401
from tests.test_venditori_1_postgres import (  # noqa: F401  (fixture riusate)
    _attiva, _contatto, _coppie, _disattiva, _immobile, _proprietario, _worklist, base, m,
)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL per VENDITORI-SOSPESI-1")

ROOT = Path(__file__).resolve().parents[1]
BLOCCO = "SELLER_OPPORTUNITY_OPEN"


def _controllo(m, pid, chi="owner_a"):
    r = m["api"](chi).get(f"/api/property/properties/{pid}/deletion-check")
    assert r.status_code == 200, r.text
    return r.json()


def _blocco_venditore(m, pid, chi="owner_a"):
    return next((b for b in _controllo(m, pid, chi)["blockers"] if b["code"] == BLOCCO), None)


def _eventi(m, lead):
    """Gli eventi «Vende» dello storico del lead, in ordine."""
    return [r[0] for r in _q(m, "SELECT metadata->>'event' FROM activities WHERE lead_id = %s "
                                "AND metadata ? 'event' ORDER BY id", (lead,))]


def _sospesa(m, nome, chi="owner_a", agente=None):
    p = _immobile(m)
    c = _contatto(m, nome, agente=agente)
    _proprietario(m, p["id"], c)
    r = _attiva(m, p["id"], c, chi=chi)
    assert r.status_code in (200, 201), r.text
    lead = r.json()["lead_id"]
    r = _disattiva(m, p["id"], c, "paused", chi=chi)
    assert r.status_code == 200 and r.json()["status"] == "paused", r.text
    return p, c, lead


def _stato(m, lead):
    return _q(m, "SELECT status, stage, closed_at IS NOT NULL, lost_reason FROM leads WHERE id = %s", (lead,))[0]


# ---------------------------------------------------------------------------

def test_s01_open_e_paused_bloccano_closed_no(m):
    p, c, lead = _sospesa(m, "Sara Sospesa")
    b = _blocco_venditore(m, p["id"])
    assert b is not None and [(i["id"], i["status"]) for i in b["items"]] == [(lead, "paused")]
    assert _controllo(m, p["id"])["can_trash"] is False
    # riaperta: blocca ancora (open)
    assert _attiva(m, p["id"], c).status_code == 200
    assert [(i["id"], i["status"]) for i in _blocco_venditore(m, p["id"])["items"]] == [(lead, "open")]
    # chiusa: non blocca piu'
    assert _disattiva(m, p["id"], c, "not_selling").status_code == 200
    assert _blocco_venditore(m, p["id"]) is None


def test_s02_ogni_lead_che_blocca_e_raggiungibile_in_venditori(m):
    """La sentinella dell'incoerenza: «Nessun venditore attivo» + blocco
    «Venditore» e' ammesso solo se il lead si trova nel filtro Sospesi."""
    p1, c1, l1 = _sospesa(m, "Paolo Sospeso")
    p2 = _immobile(m)
    c2 = _contatto(m, "Olga Aperta")
    _proprietario(m, p2["id"], c2)
    l2 = _attiva(m, p2["id"], c2).json()["lead_id"]
    attivi = {x["lead_id"] for x in _worklist(m)["items"]}
    sospesi = {x["lead_id"] for x in _worklist(m, status="paused")["items"]}
    assert l1 not in attivi and l1 in sospesi                     # il caso reale: non fra gli Attivi
    for pid in (p1["id"], p2["id"]):
        for voce in (_blocco_venditore(m, pid) or {"items": []})["items"]:
            assert voce["id"] in attivi | sospesi, (pid, voce)
            assert (voce["id"] in sospesi) == (voce["status"] == "paused")
    assert l2 in attivi


def test_s03_chiusura_diretta_da_sospesa_storico_e_cestino(m):
    p, c, lead = _sospesa(m, "Marco Chiude")
    # finche' e' sospesa: il Cestino rifiuta, nulla cambia
    prima = _q(m, "SELECT deleted_at FROM properties WHERE id = %s", (p["id"],))[0][0]
    r = m["api"]().post(f"/api/property/properties/{p['id']}/trash", json={"reason_code": "duplicate"})
    assert r.status_code == 409, r.text
    assert BLOCCO in {b["code"] for b in r.json().get("blockers", [])}, r.json()
    assert _q(m, "SELECT deleted_at FROM properties WHERE id = %s", (p["id"],))[0][0] == prima is None
    assert _stato(m, lead)[0] == "paused"                          # nessuna chiusura automatica
    eventi_prima = _eventi(m, lead)
    assert eventi_prima[-1] == "paused"
    conteggi = {t: _q(m, f"SELECT count(*) FROM {t}")[0][0] for t in ("leads", "property_leads", "property_contacts")}

    # «Smetti…» -> «Non vende più», DIRETTAMENTE da sospesa
    r = _disattiva(m, p["id"], c, "not_selling", note="ha deciso di affittare")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "closed" and r.json()["stage"] == "lost"
    stato, fase, chiusa, motivo = _stato(m, lead)
    assert (stato, fase, chiusa) == ("closed", "lost", True) and motivo.startswith("Non vende più")
    # storico: UN evento in piu', di chiusura; nessuna riattivazione artificiale
    nuovi = _eventi(m, lead)[len(eventi_prima):]
    assert nuovi == ["not_selling"], nuovi
    assert "reopened" not in _eventi(m, lead) and _eventi(m, lead).count("activated") == 1
    # niente si cancella: lead, collegamento venditore, proprietario
    assert {t: _q(m, f"SELECT count(*) FROM {t}")[0][0] for t in conteggi} == conteggi
    assert _q(m, "SELECT relation_type FROM property_leads WHERE lead_id = %s", (lead,)) == [["seller"]]

    # solo ADESSO il Cestino si sblocca
    assert _blocco_venditore(m, p["id"]) is None and _controllo(m, p["id"])["can_trash"] is True
    r = m["api"]().post(f"/api/property/properties/{p['id']}/trash", json={"reason_code": "duplicate"})
    assert r.status_code == 200, r.text
    assert _q(m, "SELECT deleted_at IS NOT NULL FROM properties WHERE id = %s", (p["id"],))[0][0] is True
    assert _stato(m, lead)[0] == "closed"                          # il Cestino non tocca il lead
    r = m["api"]().post(f"/api/property/properties/{p['id']}/restore")
    assert r.status_code == 200, r.text


def test_s04_inserito_per_errore_da_sospesa(m):
    p, c, lead = _sospesa(m, "Elsa Errore")
    eventi_prima = _eventi(m, lead)
    r = _disattiva(m, p["id"], c, "mistake")
    assert r.status_code == 200 and r.json()["status"] == "closed", r.text
    assert _eventi(m, lead)[len(eventi_prima):] == ["mistake"]
    assert "reopened" not in _eventi(m, lead)
    assert _blocco_venditore(m, p["id"]) is None


def test_s05_permessi_tra_agenti(m):
    p, c, lead = _sospesa(m, "Gino Agente", chi="agent_a", agente=m["ids"]["agent_a"])
    assert _q(m, "SELECT assigned_agent_id FROM leads WHERE id = %s", (lead,))[0][0] == m["ids"]["agent_a"]
    # un altro agente: non la vede fra le sospese e non la chiude (404, nessun dettaglio)
    assert lead not in {x["lead_id"] for x in _worklist(m, "agent_a2", status="paused")["items"]}
    r = _disattiva(m, p["id"], c, "not_selling", chi="agent_a2")
    assert r.status_code == 404, r.text
    assert "Gino" not in r.text and str(lead) not in r.text
    r = _disattiva(m, p["id"], c, "mistake", chi="agent_a2")
    assert r.status_code == 404, r.text
    assert _stato(m, lead)[0] == "paused"                          # nulla e' cambiato
    # un'altra agenzia: 404
    assert _disattiva(m, p["id"], c, "not_selling", chi="owner_b").status_code == 404
    assert _stato(m, lead)[0] == "paused"
    # l'agente assegnato la vede e la chiude direttamente
    assert lead in {x["lead_id"] for x in _worklist(m, "agent_a", status="paused")["items"]}
    assert _disattiva(m, p["id"], c, "not_selling", chi="agent_a").status_code == 200
    assert _stato(m, lead)[0] == "closed" and "reopened" not in _eventi(m, lead)
    # e il titolare vede sempre le sospese dell'agenzia (lista)
    p2, c2, lead2 = _sospesa(m, "Tina Titolare", chi="agent_a", agente=m["ids"]["agent_a"])
    assert lead2 in {x["lead_id"] for x in _worklist(m, "owner_a", status="paused")["items"]}
    assert _disattiva(m, p2["id"], c2, "not_selling", chi="owner_a").status_code == 200


def test_s06_regola_backend_open_paused_invariata():
    testo = (ROOT / "property" / "lifecycle.py").read_text(encoding="utf-8")
    assert testo.count("AND pl.relation_type = 'seller' AND l.status IN ('open', 'paused')") == 2
    assert 'SELLER_OPPORTUNITY_OPEN = "SELLER_OPPORTUNITY_OPEN"' in testo
