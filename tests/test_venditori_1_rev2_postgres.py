"""VENDITORI-1 REV 2 - R1 e R2 sul database VERO, con le rotte reali.

R1  la worklist considera «acquisizione in corso» SOLO un'acquisizione non
    terminale, sia quando coincide il lead sia quando coincide l'immobile: una
    vecchia acquisizione `lost` sul lead non nasconde piu' «Avvia
    acquisizione», e se sullo stesso immobile c'e' un'acquisizione davvero
    aperta (anche per un comproprietario) e' quella che si vede.
R2  un'acquisizione con `source='seller_lead'` e un `lead_id` passa solo se il
    lead e' l'opportunita' Venditore indicata: stessa agenzia, visibile, SELL,
    dello stesso proprietario, collegato `seller` a QUELL'immobile. Altrimenti
    rifiutata senza scrivere acquisition, appointment o eventi.

Riusa fixture e helper di tests/test_venditori_1_postgres.py. Senza
P29_TEST_DSN il modulo e' SKIP.
"""
from __future__ import annotations

import uuid

import pytest

from tests.test_censimento_3_backend_postgres import DSN, _q, completo  # noqa: F401
from tests.test_venditori_1_postgres import (  # noqa: F401  (fixture riusate)
    _attiva, _contatto, _immobile, _proprietario, _worklist, base, m,
)

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL per VENDITORI-1 REV 2")

_GIORNO = iter(range(1, 28))


def _acquisizione(m, property_id, contact_id, lead_id=None, source="seller_lead"):
    giorno = next(_GIORNO)
    corpo = {"property_id": property_id, "owner_contact_id": contact_id,
             "appointment": {"start_at": f"2031-04-{giorno:02d}T10:00:00+02:00",
                             "end_at": f"2031-04-{giorno:02d}T11:00:00+02:00",
                             "assigned_user_id": m["ids"]["owner_a"], "client_request_id": str(uuid.uuid4())}}
    if lead_id is not None:
        corpo["lead_id"] = lead_id
    if source is not None:
        corpo["source"] = source
    return m["api"]().post("/api/acquisitions", json=corpo)


def _persa(m, acq):
    r = m["api"]().post(f"/api/acquisitions/{acq['id']}/lost",
                        json={"version": acq["version"], "lost_reason": "owner_no_longer_selling"})
    assert r.status_code == 200, r.text
    assert _q(m, "SELECT status FROM acquisitions WHERE id = %s", (acq["id"],))[0][0] == "lost"


def _voce(m, lead):
    return next(x for x in _worklist(m, status="all")["items"] if x["lead_id"] == lead)


def _conteggi(m):
    return tuple(_q(m, f"SELECT count(*) FROM {t}")[0][0]
                 for t in ("acquisitions", "appointments", "acquisition_events", "appointment_events"))


# ---------------------------------------------------------------------------
# R1 - acquisizione terminale
# ---------------------------------------------------------------------------

def test_r1a_acquisizione_persa_sul_lead_non_blocca_piu_avvia_acquisizione(m):
    p = _immobile(m)
    mario = _contatto(m, "Mario Persa")
    _proprietario(m, p["id"], mario)
    lead = _attiva(m, p["id"], mario).json()["lead_id"]
    r = _acquisizione(m, p["id"], mario, lead)
    assert r.status_code == 201, r.text
    prima = r.json()
    assert _voce(m, lead)["acquisition"]["id"] == prima["id"]           # aperta: si vede
    _persa(m, prima)
    # Mario torna a vendere: l'opportunita' e' (ri)attiva
    assert _attiva(m, p["id"], mario).status_code in (200, 201)
    voce = _voce(m, lead)
    assert voce["status"] == "open"
    assert voce["acquisition"] is None, voce["acquisition"]              # la persa non conta
    # di nuovo possibile: il modulo esistente accetta una nuova acquisizione con lo stesso lead
    r2 = _acquisizione(m, p["id"], mario, lead)
    assert r2.status_code == 201, r2.text
    seconda = r2.json()
    assert _voce(m, lead)["acquisition"]["id"] == seconda["id"]
    # e quella davvero aperta continua a bloccarne una terza
    r3 = _acquisizione(m, p["id"], mario, lead)
    assert r3.status_code == 409 and r3.json()["code"] == "OPEN_ACQUISITION_EXISTS"
    assert _q(m, "SELECT count(*) FROM acquisitions WHERE property_id = %s", (p["id"],))[0][0] == 2


def test_r1b_vecchia_persa_sul_lead_e_nuova_aperta_del_comproprietario(m):
    p = _immobile(m)
    mario = _contatto(m, "Mario Vecchia")
    anna = _contatto(m, "Anna Nuova")
    _proprietario(m, p["id"], mario)
    _proprietario(m, p["id"], anna)
    lead_m = _attiva(m, p["id"], mario).json()["lead_id"]
    lead_a = _attiva(m, p["id"], anna).json()["lead_id"]
    r = _acquisizione(m, p["id"], mario, lead_m)
    assert r.status_code == 201, r.text
    _persa(m, r.json())
    r = _acquisizione(m, p["id"], anna, lead_a)
    assert r.status_code == 201, r.text
    aperta = r.json()["id"]
    # entrambe le card vedono l'acquisizione APERTA dell'immobile, non la vecchia persa
    assert _voce(m, lead_m)["acquisition"]["id"] == aperta
    assert _voce(m, lead_a)["acquisition"]["id"] == aperta
    assert _voce(m, lead_m)["acquisition"]["status"] not in ("lost", "acquired")


def test_r1c_comproprietari_senza_acquisizioni_invariati(m):
    p = _immobile(m)
    mario = _contatto(m, "Mario Solo")
    anna = _contatto(m, "Anna Sola")
    _proprietario(m, p["id"], mario)
    _proprietario(m, p["id"], anna)
    lead_m = _attiva(m, p["id"], mario).json()["lead_id"]
    assert _voce(m, lead_m)["acquisition"] is None
    assert {x["contact"]["id"] for x in _worklist(m)["items"]} == {mario}   # Anna non e' venditrice


# ---------------------------------------------------------------------------
# R2 - integrita' di lead_id nel passaggio Venditore -> Acquisizione
# ---------------------------------------------------------------------------

@pytest.fixture
def scena(m):
    a = _immobile(m)
    b = _immobile(m, address="Via Bari", civic_number="2")
    mario = _contatto(m, "Mario Acq")
    anna = _contatto(m, "Anna Acq")
    for pid, cid in ((a["id"], mario), (a["id"], anna), (b["id"], mario)):
        _proprietario(m, pid, cid)
    return {"a": a["id"], "b": b["id"], "mario": mario, "anna": anna}


def _rifiutata(m, r, prima):
    assert r.status_code in (404, 409, 422), (r.status_code, r.text)
    assert _conteggi(m) == prima, "scritture residue dopo il rifiuto"


def test_r2a_seller_corretto_passa(m, scena):
    lead = _attiva(m, scena["a"], scena["mario"]).json()["lead_id"]
    r = _acquisizione(m, scena["a"], scena["mario"], lead)
    assert r.status_code == 201, r.text
    assert list(_q(m, "SELECT property_id, owner_contact_id, lead_id, source FROM acquisitions WHERE id = %s",
                   (r.json()["id"],))[0]) == [scena["a"], scena["mario"], lead, "seller_lead"]


def test_r2b_lead_di_un_altro_proprietario_rifiutato(m, scena):
    lead_anna = _attiva(m, scena["a"], scena["anna"]).json()["lead_id"]
    prima = _conteggi(m)
    _rifiutata(m, _acquisizione(m, scena["a"], scena["mario"], lead_anna), prima)


def test_r2c_lead_sell_di_mario_per_un_altro_immobile_rifiutato(m, scena):
    lead_b = _attiva(m, scena["b"], scena["mario"]).json()["lead_id"]
    prima = _conteggi(m)
    _rifiutata(m, _acquisizione(m, scena["a"], scena["mario"], lead_b), prima)


@pytest.mark.parametrize("pipeline", ["buy", "general"])
def test_r2d_lead_buy_o_general_visibile_rifiutato(m, scena, pipeline):
    r = m["api"]().post("/api/core/leads", json={"contact_id": scena["mario"], "pipeline": pipeline})
    assert r.status_code == 201, r.text
    lead = r.json()["id"]
    # anche se qualcuno lo collegasse come seller all'immobile, resta un lead non SELL
    _q(m, "INSERT INTO property_leads (property_id, lead_id, relation_type) VALUES (%s, %s, 'seller')",
       (scena["a"], lead))
    prima = _conteggi(m)
    _rifiutata(m, _acquisizione(m, scena["a"], scena["mario"], lead), prima)


def test_r2e_lead_sell_di_mario_non_collegato_seller_all_immobile_rifiutato(m, scena):
    r = m["api"]().post("/api/core/leads", json={"contact_id": scena["mario"], "pipeline": "sell"})
    assert r.status_code == 201, r.text
    lead = r.json()["id"]
    prima = _conteggi(m)
    _rifiutata(m, _acquisizione(m, scena["a"], scena["mario"], lead), prima)
    # collegato all'immobile ma con un'altra relazione: ancora no
    _q(m, "INSERT INTO property_leads (property_id, lead_id, relation_type) VALUES (%s, %s, 'related')",
       (scena["a"], lead))
    _rifiutata(m, _acquisizione(m, scena["a"], scena["mario"], lead), prima)


def test_r2f_il_rifiuto_ha_un_codice_e_non_tocca_i_vecchi_flussi(m, scena):
    lead_anna = _attiva(m, scena["a"], scena["anna"]).json()["lead_id"]
    prima = _conteggi(m)
    r = _acquisizione(m, scena["a"], scena["mario"], lead_anna)
    _rifiutata(m, r, prima)
    assert r.json()["code"] == "SELLER_LEAD_MISMATCH"
    # flusso generico (source diversa da seller_lead): il controllo resta solo la visibilita'
    r = m["api"]().post("/api/core/leads", json={"contact_id": scena["mario"], "pipeline": "general"})
    lead_generico = r.json()["id"]
    r = _acquisizione(m, scena["a"], scena["mario"], lead_generico, source="referral")
    assert r.status_code == 201, r.text


def test_r2g_modifica_di_un_acquisizione_seller_lead_con_un_lead_sbagliato_rifiutata(m, scena):
    lead_m = _attiva(m, scena["a"], scena["mario"]).json()["lead_id"]
    lead_anna = _attiva(m, scena["a"], scena["anna"]).json()["lead_id"]
    acq = _acquisizione(m, scena["a"], scena["mario"], lead_m).json()
    eventi = _q(m, "SELECT count(*) FROM acquisition_events WHERE acquisition_id = %s", (acq["id"],))[0][0]
    r = m["api"]().patch(f"/api/acquisitions/{acq['id']}", json={"version": acq["version"], "lead_id": lead_anna})
    assert r.status_code == 422 and r.json()["code"] == "SELLER_LEAD_MISMATCH", r.text
    assert _q(m, "SELECT lead_id FROM acquisitions WHERE id = %s", (acq["id"],))[0][0] == lead_m
    assert _q(m, "SELECT count(*) FROM acquisition_events WHERE acquisition_id = %s", (acq["id"],))[0][0] == eventi


# ---------------------------------------------------------------------------
# R3 - venditore non piu' collegato come proprietario (lato server)
# ---------------------------------------------------------------------------

def test_r3a_scollegato_resta_in_lista_e_si_puo_sospendere_e_chiudere_senza_ricollegarlo(m):
    p = _immobile(m)
    mario = _contatto(m, "Mario Scollegato")
    _proprietario(m, p["id"], mario)
    lead = _attiva(m, p["id"], mario).json()["lead_id"]
    # SENTINELLA AGGIORNATA DA DELETE-ARCH FASE 0 (brief §7, contratto REV 2
    # 5.5): con l'opportunita' Vende aperta la rimozione del proprietario
    # dall'API e' rifiutata (409 SELLER_OPPORTUNITY_OPEN, si usa prima
    # «Smetti…»), e l'opportunita' NON viene chiusa. Lo stato "scollegato con
    # opportunita' viva" puo' pero' esistere ancora (dati storici, D8): qui lo
    # si produce in SQL, e la tolleranza di R3 resta provata come prima.
    r = m["api"]().delete(f"/api/property/properties/{p['id']}/contacts/{mario}/owner")
    assert r.status_code == 409 and r.json()["code"] == "SELLER_OPPORTUNITY_OPEN", r.text
    assert _q(m, "SELECT status FROM leads WHERE id = %s", (lead,))[0][0] == "open"
    _q(m, "DELETE FROM property_contacts WHERE property_id = %s AND contact_id = %s", (p["id"], mario))
    voce = _voce(m, lead)
    assert voce["still_owner"] is False and voce["status"] == "open"
    # le azioni che richiedono il collegamento restano rifiutate dal backend...
    r = m["api"]().post(f"/api/property/properties/{p['id']}/interactions",
                        json={"interaction_type": "call", "note": "x", "contact_id": mario, "lead_id": lead,
                              "context": "seller"})
    assert r.status_code >= 400
    assert _attiva(m, p["id"], mario).status_code == 409                    # SELLER_NOT_OWNER
    r = _acquisizione(m, p["id"], mario, lead)
    assert r.status_code in (409, 422)
    # ...ma sospendere e chiudere si puo': lo storico va su lead e contatto
    for esito, stato in (("paused", "paused"), ("not_selling", "closed")):
        r = m["api"]().post("/api/crm/sellers/deactivate",
                            json={"property_id": p["id"], "contact_id": mario, "outcome": esito})
        assert r.status_code == 200, r.text
        assert _q(m, "SELECT status FROM leads WHERE id = %s", (lead,))[0][0] == stato
    eventi = _q(m, "SELECT property_id, contact_id, metadata->>'event', (metadata->>'property_id')::int "
                   "FROM activities WHERE lead_id = %s AND metadata->>'event' IN ('paused', 'not_selling') ORDER BY id",
                (lead,))
    assert [tuple(e) for e in eventi] == [(None, mario, "paused", p["id"]), (None, mario, "not_selling", p["id"])]
    # nessun collegamento ricreato, owner non trasformato
    assert _q(m, "SELECT count(*) FROM property_contacts WHERE property_id = %s AND contact_id = %s",
              (p["id"], mario))[0][0] == 0


# ---------------------------------------------------------------------------
# GATE FINALE R2 - PATCH: si valida lo stato FINALE risultante, non il payload
# ---------------------------------------------------------------------------

def _riga(m, acq_id):
    return _q(m, "SELECT to_jsonb(a) FROM acquisitions a WHERE id = %s", (acq_id,))[0][0]


def _eventi(m, acq_id):
    return _q(m, "SELECT count(*) FROM acquisition_events WHERE acquisition_id = %s", (acq_id,))[0][0]


def _patch(m, acq_id, **campi):
    versione = _riga(m, acq_id)["version"]
    return m["api"]().patch(f"/api/acquisitions/{acq_id}", json={"version": versione, **campi})


def _invariata(m, acq_id, r, prima, eventi):
    assert r.status_code == 422 and r.json()["code"] == "SELLER_LEAD_MISMATCH", (r.status_code, r.text)
    assert _riga(m, acq_id) == prima, "modifica parziale dopo il rifiuto"
    assert _eventi(m, acq_id) == eventi


@pytest.fixture
def coerente(m, scena):
    lead_m = _attiva(m, scena["a"], scena["mario"]).json()["lead_id"]
    r = _acquisizione(m, scena["a"], scena["mario"], lead_m)
    assert r.status_code == 201, r.text
    return {**scena, "lead_m": lead_m, "acq": r.json()["id"]}


def test_gate_a_patch_solo_proprietario_anna_con_lead_mario_rifiutata(m, coerente):
    acq = coerente["acq"]
    prima, eventi = _riga(m, acq), _eventi(m, acq)
    _invariata(m, acq, _patch(m, acq, owner_contact_id=coerente["anna"]), prima, eventi)
    assert prima["owner_contact_id"] == coerente["mario"] and prima["lead_id"] == coerente["lead_m"]


@pytest.mark.parametrize("quale", ["anna", "mario_su_b", "buy", "general"])
def test_gate_b_patch_solo_lead_sbagliato_rifiutata(m, coerente, quale):
    if quale == "anna":
        lead = _attiva(m, coerente["a"], coerente["anna"]).json()["lead_id"]
    elif quale == "mario_su_b":
        lead = _attiva(m, coerente["b"], coerente["mario"]).json()["lead_id"]
    else:
        r = m["api"]().post("/api/core/leads", json={"contact_id": coerente["mario"], "pipeline": quale})
        assert r.status_code == 201, r.text
        lead = r.json()["id"]
    acq = coerente["acq"]
    prima, eventi = _riga(m, acq), _eventi(m, acq)
    _invariata(m, acq, _patch(m, acq, lead_id=lead), prima, eventi)


@pytest.mark.parametrize("caso", ["lead_general", "lead_sell_related", "lead_sell_di_anna"])
def test_gate_c_patch_solo_source_seller_lead_su_combinazione_non_valida_rifiutata(m, scena, caso):
    if caso == "lead_general":
        lead = m["api"]().post("/api/core/leads", json={"contact_id": scena["mario"], "pipeline": "general"}).json()["id"]
    elif caso == "lead_sell_related":
        lead = m["api"]().post("/api/core/leads", json={"contact_id": scena["mario"], "pipeline": "sell"}).json()["id"]
        _q(m, "INSERT INTO property_leads (property_id, lead_id, relation_type) VALUES (%s, %s, 'related')",
           (scena["a"], lead))
    else:
        lead = _attiva(m, scena["a"], scena["anna"]).json()["lead_id"]
    # acquisizione generica: il vecchio flusso la accetta (solo visibilita')
    r = _acquisizione(m, scena["a"], scena["mario"], lead, source="referral")
    assert r.status_code == 201, r.text
    acq = r.json()["id"]
    prima, eventi = _riga(m, acq), _eventi(m, acq)
    _invariata(m, acq, _patch(m, acq, source="seller_lead"), prima, eventi)
    assert _riga(m, acq)["source"] == "referral"


def test_gate_c2_patch_source_seller_lead_su_combinazione_valida_passa(m, scena):
    lead = _attiva(m, scena["a"], scena["mario"]).json()["lead_id"]
    acq = _acquisizione(m, scena["a"], scena["mario"], lead, source="referral").json()["id"]
    r = _patch(m, acq, source="seller_lead")
    assert r.status_code == 200, r.text
    assert _riga(m, acq)["source"] == "seller_lead"


def test_gate_d_patch_innocua_su_seller_lead_coerente_funziona(m, coerente):
    acq = coerente["acq"]
    eventi = _eventi(m, acq)
    r = _patch(m, acq, notes="Richiamare dopo le 18", asking_price="210000.00", sale_timing="within_6_months")
    assert r.status_code == 200, r.text
    riga = _riga(m, acq)
    assert (riga["notes"], riga["sale_timing"], riga["lead_id"], riga["owner_contact_id"], riga["source"]) == (
        "Richiamare dopo le 18", "within_6_months", coerente["lead_m"], coerente["mario"], "seller_lead")
    assert _eventi(m, acq) == eventi + 1
    # anche riscrivere gli stessi valori (nessun cambio effettivo) resta innocuo
    r = _patch(m, acq, lead_id=coerente["lead_m"], owner_contact_id=coerente["mario"], source="seller_lead")
    assert r.status_code == 200, r.text
