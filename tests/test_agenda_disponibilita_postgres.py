"""Disponibilita' (UI delle API A30-11): il contratto HTTP che la UI usa, PROVATO.

La UI "Disponibilita'" dell'Agenda non ha endpoint suoi: chiama le 8 rotte
A30-11 gia' esistenti (`appointments/router.py`). I test di A30-11 provano la
logica pura e i vincoli del database; qui si prova, contro il router VERO e un
PostgreSQL usa-e-getta, cio' che la UI da' per scontato:

  A  un agent legge e scrive solo i propri orari/eccezioni (403 sugli altri);
  B  owner, admin e Supreme "acting" gestiscono gli agenti attivi della
     propria agenzia;
  C  cross-agenzia, agente revocato e Supreme senza acting: rifiutati;
  D-F orari: carica/salva, giorno chiuso, piu' fasce nello stesso giorno,
     fasce sovrapposte rifiutate SENZA perdere l'orario precedente;
  G-H eccezioni positive e negative; I chiusure (le legge anche un agent,
     le scrive solo owner/admin); J cancellazioni, anche cross-agenzia;
  K  errori di validazione leggibili;
  L  nessuna lettura scrive;
  M  il CRM resta SOFT: fuori orario, in un'eccezione negativa e in una
     chiusura un appuntamento manuale si crea comunque.

(N - le prenotazioni pubbliche HARD - e' gia' certificato da
`tests/test_a30_12_public_booking_postgres.py` 14-17 e 26.)

Contesti `OperatorContext` VERI e fixture riusate da A30-13B.1 / A30-2:
nessuna connessione propria. Opt-in: senza `P29_TEST_DSN` si salta tutto.
"""
from __future__ import annotations

import os

import pytest

from tests import test_a30_2_appointments_postgres as a30_2
from tests.test_a30_13b_create_permissions_postgres import (  # noqa: F401
    _corpo, agenda_completa, api, db, mondo, w)

DSN = os.getenv("P29_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare la Disponibilita'")

BASE = "/api/appointments"
LUN_9_13 = {"day_of_week": 1, "start_minute": 540, "end_minute": 780}
LUN_15_19 = {"day_of_week": 1, "start_minute": 900, "end_minute": 1140}
MER_9_18 = {"day_of_week": 3, "start_minute": 540, "end_minute": 1080}


@pytest.fixture
def ww(w):  # noqa: F811
    """Il mondo di A30-13B.1; in uscita toglie ANCHE eccezioni e chiusure (le
    tabelle 076 che la pulizia di A30-2 non conosce), prima della sua."""
    yield w
    w["conn"].rollback()
    for tabella in ("agent_availability_exceptions", "agency_closures", "agent_working_hours"):
        w["sql"](f"DELETE FROM {tabella}")


def _orari(api, chi, user_id):
    return api(chi).get(f"{BASE}/agents/{user_id}/working-hours")


def _salva(api, chi, user_id, slots):
    return api(chi).put(f"{BASE}/agents/{user_id}/working-hours", json={"slots": slots})


def _senza_id(items):
    return [{k: v for k, v in r.items() if k != "id"} for r in items]


def _conta(ww):
    return {t: ww["sql"](f"SELECT count(*) FROM {t}")[0][0]
            for t in ("agent_working_hours", "agent_availability_exceptions",
                      "agency_closures", "appointments")}


FINESTRA = {"from": "2026-10-01", "to": "2026-12-31"}


# ---------------------------------------------------------------------------
# A-C  PERMESSI
# ---------------------------------------------------------------------------

def test_a_agent_legge_e_scrive_solo_i_propri(api, ww):
    r = _salva(api, "luca", ww["luca"], [LUN_9_13])
    assert r.status_code == 200, r.text
    assert _orari(api, "luca", ww["luca"]).status_code == 200
    prima = _conta(ww)
    for chiamata in (lambda: _orari(api, "luca", ww["marta"]),
                     lambda: _salva(api, "luca", ww["marta"], [LUN_9_13]),
                     lambda: api("luca").get(f"{BASE}/agents/{ww['marta']}/availability-exceptions",
                                             params=FINESTRA),
                     lambda: api("luca").post(f"{BASE}/agents/{ww['marta']}/availability-exceptions",
                                              json={"exception_date": "2026-10-02",
                                                    "is_available": False})):
        r = chiamata()
        assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN_ROLE", r.text
    assert _conta(ww) == prima


@pytest.mark.parametrize("chi", ["giorgio", "anna", "supremo_in_a"])
def test_b_owner_admin_supreme_gestiscono_gli_agenti_della_propria_agenzia(api, ww, chi):
    r = _salva(api, chi, ww["marta"], [MER_9_18])
    assert r.status_code == 200, r.text
    assert _senza_id(_orari(api, chi, ww["marta"]).json()["items"]) == [MER_9_18]
    r = api(chi).post(f"{BASE}/agents/{ww['luca']}/availability-exceptions",
                      json={"exception_date": "2026-10-02", "is_available": False})
    assert r.status_code == 201, r.text


@pytest.mark.parametrize("chi", ["giorgio", "anna", "supremo_in_a"])
def test_c_nessun_accesso_fuori_agenzia_o_a_un_revocato(api, ww, chi):
    prima = _conta(ww)
    for target in (ww["estraneo"], ww["revocato"]):
        for r in (_orari(api, chi, target), _salva(api, chi, target, [LUN_9_13]),
                  api(chi).get(f"{BASE}/agents/{target}/availability-exceptions", params=FINESTRA)):
            assert r.status_code == 422 and r.json()["code"] == "AGENT_NOT_ACTIVE", r.text
    assert _conta(ww) == prima


def test_c2_supreme_senza_acting_non_ha_agenzia(api, ww):
    for r in (_orari(api, "supremo_senza_acting", ww["luca"]),
              api("supremo_senza_acting").get(f"{BASE}/closures", params=FINESTRA)):
        assert r.status_code == 403 and r.json()["code"] == "PLATFORM_ADMIN_AGENCY_REQUIRED"


# ---------------------------------------------------------------------------
# D-F  ORARI SETTIMANALI
# ---------------------------------------------------------------------------

def test_d_e_f_carica_salva_giorno_chiuso_e_piu_fasce(api, ww):
    assert _orari(api, "luca", ww["luca"]).json()["items"] == []        # mai configurato
    r = _salva(api, "luca", ww["luca"], [LUN_15_19, MER_9_18, LUN_9_13])
    assert r.status_code == 200, r.text
    # due fasce il lunedi', una il mercoledi', ordinate; martedi' (chiuso) assente
    assert _senza_id(r.json()["items"]) == [LUN_9_13, LUN_15_19, MER_9_18]
    assert _senza_id(_orari(api, "luca", ww["luca"]).json()["items"]) == [LUN_9_13, LUN_15_19, MER_9_18]
    # il PUT sostituisce l'intero set: ora solo il mercoledi'
    assert _senza_id(_salva(api, "luca", ww["luca"], [MER_9_18]).json()["items"]) == [MER_9_18]
    # e un set vuoto toglie tutto (il pannello chiede conferma prima)
    assert _salva(api, "luca", ww["luca"], []).json()["items"] == []


def test_f2_fasce_sovrapposte_rifiutate_senza_perdere_l_orario(api, ww):
    _salva(api, "luca", ww["luca"], [LUN_9_13])
    r = _salva(api, "luca", ww["luca"],
               [LUN_9_13, {"day_of_week": 1, "start_minute": 720, "end_minute": 900}])
    assert r.status_code == 422, r.text
    assert "sovrappone" in r.json()["detail"]
    assert _senza_id(_orari(api, "luca", ww["luca"]).json()["items"]) == [LUN_9_13]


# ---------------------------------------------------------------------------
# G-J  ECCEZIONI, CHIUSURE, CANCELLAZIONI
# ---------------------------------------------------------------------------

def test_g_h_eccezioni_positive_e_negative(api, ww):
    url = f"{BASE}/agents/{ww['luca']}/availability-exceptions"
    neg = api("luca").post(url, json={"exception_date": "2026-10-02", "start_minute": 900,
                                      "end_minute": 1080, "is_available": False,
                                      "reason_code": "Visita medica"})
    pos = api("luca").post(url, json={"exception_date": "2026-10-04", "start_minute": 600,
                                      "end_minute": 720, "is_available": True})
    assert (neg.status_code, pos.status_code) == (201, 201), (neg.text, pos.text)
    items = api("luca").get(url, params=FINESTRA).json()["items"]
    assert _senza_id(items) == [
        {"exception_date": "2026-10-02", "start_minute": 900, "end_minute": 1080,
         "is_available": False, "reason_code": "Visita medica"},
        {"exception_date": "2026-10-04", "start_minute": 600, "end_minute": 720,
         "is_available": True, "reason_code": None}]
    # tutto il giorno: i valori di partenza dello schema
    tutto = api("luca").post(url, json={"exception_date": "2026-10-05", "is_available": False})
    assert (tutto.json()["start_minute"], tutto.json()["end_minute"]) == (0, 1440)


def test_i_chiusure_le_legge_anche_un_agent_le_scrive_solo_owner_admin(api, ww):
    corpo = {"closure_date": "2026-12-24", "reason_code": "Vigilia"}
    r = api("luca").post(f"{BASE}/closures", json=corpo)
    assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN_ROLE"
    creata = api("anna").post(f"{BASE}/closures", json=corpo)
    assert creata.status_code == 201, creata.text
    visti = api("luca").get(f"{BASE}/closures", params=FINESTRA).json()["items"]
    assert _senza_id(visti) == [{"closure_date": "2026-12-24", "start_minute": 0,
                                 "end_minute": 1440, "reason_code": "Vigilia"}]
    r = api("luca").delete(f"{BASE}/closures/{creata.json()['id']}")
    assert r.status_code == 403
    # l'altra agenzia non la vede
    assert api("estraneo").get(f"{BASE}/closures", params=FINESTRA).json()["items"] == []


def test_j_cancellazioni_e_cross_agenzia(api, ww):
    ecc = api("luca").post(f"{BASE}/agents/{ww['luca']}/availability-exceptions",
                           json={"exception_date": "2026-10-02", "is_available": False}).json()
    chi = api("giorgio").post(f"{BASE}/closures", json={"closure_date": "2026-12-25"}).json()
    # un'altra agenzia non cancella nulla di questa: stessa risposta di "non esiste"
    r = api("estraneo").delete(f"{BASE}/agents/{ww['estraneo']}/availability-exceptions/{ecc['id']}")
    assert r.status_code == 404
    assert api("estraneo").delete(f"{BASE}/closures/{chi['id']}").status_code == 403
    # chi puo', cancella; una seconda volta e' 404
    url = f"{BASE}/agents/{ww['luca']}/availability-exceptions/{ecc['id']}"
    assert api("luca").delete(url).json() == {"deleted": True}
    assert api("luca").delete(url).status_code == 404
    assert api("giorgio").delete(f"{BASE}/closures/{chi['id']}").json() == {"deleted": True}
    assert api("giorgio").delete(f"{BASE}/closures/{chi['id']}").status_code == 404


# ---------------------------------------------------------------------------
# K-L  ERRORI E LETTURE
# ---------------------------------------------------------------------------

def test_k_errori_di_validazione_leggibili(api, ww):
    casi = [
        _salva(api, "luca", ww["luca"], [{"day_of_week": 1, "start_minute": 1320, "end_minute": 120}]),
        _salva(api, "luca", ww["luca"], [{"day_of_week": 8, "start_minute": 540, "end_minute": 600}]),
        api("luca").post(f"{BASE}/agents/{ww['luca']}/availability-exceptions",
                         json={"exception_date": "2026-10-02", "is_available": False,
                               "start_minute": 600, "end_minute": 600}),
        api("luca").get(f"{BASE}/agents/{ww['luca']}/availability-exceptions",
                        params={"from": "02/10/2026", "to": "2026-12-31"}),
        api("giorgio").post(f"{BASE}/closures", json={"closure_date": "2026-12-24",
                                                     "reason_code": "x" * 41}),
    ]
    for r in casi:
        assert r.status_code == 422, r.text
        assert r.json()["code"] == "VALIDATION_ERROR" and r.json()["detail"]


def test_l_nessuna_lettura_scrive(api, ww):
    _salva(api, "luca", ww["luca"], [LUN_9_13])
    prima = _conta(ww)
    for chi, target in (("luca", ww["luca"]), ("giorgio", ww["marta"]), ("supremo_in_a", ww["luca"])):
        _orari(api, chi, target)
        api(chi).get(f"{BASE}/agents/{target}/availability-exceptions", params=FINESTRA)
        api(chi).get(f"{BASE}/closures", params=FINESTRA)
    assert _conta(ww) == prima


# ---------------------------------------------------------------------------
# M  IL CRM RESTA SOFT
# ---------------------------------------------------------------------------

def test_m_crm_manuale_resta_soft_fuori_orario_eccezione_e_chiusura(api, ww):
    giorno = a30_2.ore(10)
    dow = giorno.isoweekday()
    data = giorno.date().isoformat()
    # l'agente lavora SOLO 09-10 quel giorno, ha un'assenza 12-14, e l'agenzia chiude 16-18
    assert _salva(api, "luca", ww["luca"],
                  [{"day_of_week": dow, "start_minute": 540, "end_minute": 600}]).status_code == 200
    assert api("luca").post(f"{BASE}/agents/{ww['luca']}/availability-exceptions",
                            json={"exception_date": data, "start_minute": 720, "end_minute": 840,
                                  "is_available": False}).status_code == 201
    assert api("giorgio").post(f"{BASE}/closures",
                               json={"closure_date": data, "start_minute": 960,
                                     "end_minute": 1080}).status_code == 201
    for chi, h in (("luca", 20), ("luca", 12), ("giorgio", 16)):   # fuori orario / assente / chiuso
        r = api(chi).post(f"{BASE}", json=_corpo(ww["luca"], h=h))
        assert r.status_code == 201, (h, r.text)
