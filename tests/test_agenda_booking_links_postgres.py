"""Link prenotazione (UI delle API operatore A30-12): cio' che la UI da' per scontato, PROVATO.

`tests/test_a30_12_public_booking_postgres.py` certifica gia' il CRUD dei link
(owner/agent, isolamento, rotate che invalida, GET senza scritture, token mai
nel database). Qui si provano, contro il router VERO e un PostgreSQL
usa-e-getta, le assunzioni SPECIFICHE del pannello "Link prenotazione":

  * il token grezzo compare SOLO nelle risposte di create e rotate - mai in
    elenco, modifica o disattivazione - e l'hash non compare MAI;
  * Supreme "acting" e admin vedono e gestiscono i link dell'agenzia; Supreme
    senza acting non ha agenzia; un agent vede solo i propri, e quelli dei
    colleghi o di un'altra agenzia sono "non trovati";
  * l'agente di un link non si cambia (PATCH lo rifiuta) e un PATCH con
    `null` non svuota nulla: la UI non offre ne' l'una ne' l'altra cosa;
  * un link disattivato resta non prenotabile anche dopo una rotazione (per
    questo la UI non offre "Ruota" su un link disattivato);
  * l'indirizzo pubblico mostrato dalla UI funziona davvero (N), con
    `Cache-Control: no-store` invariato; le letture non scrivono (M).

Fixture riusate da A30-13B.1 / A30-2 piu' la 077: nessuna connessione
propria. Opt-in: senza `P29_TEST_DSN` si salta tutto.
"""
from __future__ import annotations

import json
import os

import pytest

from tests import test_a30_2_appointments_postgres as a30_2
from tests.test_a30_13b_create_permissions_postgres import (  # noqa: F401
    _ctx, agenda_completa, api, db, mondo, w)

DSN = os.getenv("P29_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare i link")

BASE = "/api/appointments/booking-links"
PUBBLICA = "/api/public/booking/"


@pytest.fixture(scope="module")
def con_077(agenda_completa):  # noqa: F811
    with agenda_completa["conn"].cursor() as cur:
        # La colonna reale che la metadata pubblica legge e che lo schema
        # minimo di A30-2 non ha (stessa definizione di `test_a30_12_*`).
        cur.execute("ALTER TABLE agencies ADD COLUMN IF NOT EXISTS name VARCHAR(200)")
        cur.execute((a30_2.MIGRAZIONI / "077_a30_12_public_booking.sql").read_text(encoding="utf-8"))
    agenda_completa["conn"].commit()
    return agenda_completa


@pytest.fixture
def ww(w, con_077, monkeypatch):  # noqa: F811
    monkeypatch.setenv("PUBLIC_BOOKING_IP_PEPPER", "pepe-di-test")
    yield w
    w["conn"].rollback()
    for tabella in ("public_booking_submissions", "public_booking_rate_limits",
                    "public_booking_links"):
        w["sql"](f"DELETE FROM {tabella}")


@pytest.fixture
def pubblico(ww):
    """La rotta PUBBLICA, senza dipendenze: come in produzione."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from public_booking.public_router import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _corpo(user_id, **extra):
    return {"assigned_user_id": user_id, "appointment_type": "seller_meeting",
            "duration_minutes": 60, **extra}


def _conta(ww):
    return {t: ww["sql"](f"SELECT count(*) FROM {t}")[0][0]
            for t in ("public_booking_links", "public_booking_submissions",
                      "public_booking_rate_limits", "appointments")}


def test_01_token_solo_in_create_e_rotate_hash_mai(api, ww):
    creato = api("giorgio").post(BASE, json=_corpo(ww["luca"], label="Sito"))
    assert creato.status_code == 201, creato.text
    corpo = creato.json()
    assert isinstance(corpo["token"], str) and len(corpo["token"]) >= 32
    link_id = corpo["id"]
    ruotato = api("giorgio").post(f"{BASE}/{link_id}/rotate").json()
    assert ruotato["token"] and ruotato["token"] != corpo["token"]
    senza = [api("giorgio").get(BASE).json()["items"][0],
             api("giorgio").patch(f"{BASE}/{link_id}", json={"duration_minutes": 45}).json(),
             api("giorgio").post(f"{BASE}/{link_id}/disable").json()]
    for r in senza:
        assert "token" not in r, r
    tutto = json.dumps([corpo, ruotato, *senza])
    hash_db = ww["sql"]("SELECT token_hash FROM public_booking_links WHERE id=%s", (link_id,))[0][0]
    assert "token_hash" not in tutto and hash_db not in tutto


@pytest.mark.parametrize("chi", ["anna", "supremo_in_a"])
def test_02_admin_e_supreme_acting_vedono_e_gestiscono_i_link_dell_agenzia(api, ww, chi):
    di_luca = api("giorgio").post(BASE, json=_corpo(ww["luca"])).json()
    di_marta = api(chi).post(BASE, json=_corpo(ww["marta"]))
    assert di_marta.status_code == 201, di_marta.text
    visti = {r["id"] for r in api(chi).get(BASE).json()["items"]}
    assert visti == {di_luca["id"], di_marta.json()["id"]}
    assert api(chi).post(f"{BASE}/{di_luca['id']}/disable").json()["status"] == "disabled"


def test_03_supreme_senza_acting_nessuna_agenzia(api, ww):
    for r in (api("supremo_senza_acting").get(BASE),
              api("supremo_senza_acting").post(BASE, json=_corpo(ww["luca"]))):
        assert r.status_code == 403 and r.json()["code"] == "PLATFORM_ADMIN_AGENCY_REQUIRED"


def test_04_agent_solo_i_propri_e_i_link_altrui_sono_non_trovati(api, ww):
    proprio = api("luca").post(BASE, json=_corpo(ww["luca"]))
    assert proprio.status_code == 201, proprio.text
    di_marta = api("giorgio").post(BASE, json=_corpo(ww["marta"])).json()
    assert [r["id"] for r in api("luca").get(BASE).json()["items"]] == [proprio.json()["id"]]
    r = api("luca").post(BASE, json=_corpo(ww["marta"]))
    assert r.status_code == 403, r.text
    prima = _conta(ww)
    for r in (api("luca").patch(f"{BASE}/{di_marta['id']}", json={"duration_minutes": 30}),
              api("luca").post(f"{BASE}/{di_marta['id']}/rotate"),
              api("luca").post(f"{BASE}/{di_marta['id']}/disable")):
        assert r.status_code == 404, r.text
    assert _conta(ww) == prima


def test_05_altra_agenzia_mai_raggiungibile(api, ww):
    di_a = api("giorgio").post(BASE, json=_corpo(ww["luca"])).json()
    assert api("estraneo").get(BASE).json()["items"] == []
    for r in (api("estraneo").patch(f"{BASE}/{di_a['id']}", json={"duration_minutes": 30}),
              api("estraneo").post(f"{BASE}/{di_a['id']}/rotate"),
              api("estraneo").post(f"{BASE}/{di_a['id']}/disable")):
        assert r.status_code == 404, r.text
    r = api("giorgio").post(BASE, json=_corpo(ww["estraneo"]))
    assert r.status_code == 422 and r.json()["code"] == "AGENT_NOT_ACTIVE"


def test_06_agente_non_modificabile_e_patch_null_non_svuota(api, ww):
    link = api("giorgio").post(BASE, json=_corpo(ww["luca"], label="Vetrina")).json()
    r = api("giorgio").patch(f"{BASE}/{link['id']}", json={"assigned_user_id": ww["marta"]})
    assert r.status_code == 422, r.text
    r = api("giorgio").patch(f"{BASE}/{link['id']}", json={"label": None})
    assert r.status_code == 200 and r.json()["label"] == "Vetrina"
    r = api("giorgio").patch(f"{BASE}/{link['id']}", json={
        "appointment_type": "call", "duration_minutes": 15,
        "buffer_before_minutes": 5, "buffer_after_minutes": 10, "label": "Telefonate"})
    assert r.status_code == 200, r.text
    assert {k: r.json()[k] for k in ("appointment_type", "duration_minutes", "buffer_before_minutes",
                                     "buffer_after_minutes", "label", "assigned_user_id")} == {
        "appointment_type": "call", "duration_minutes": 15, "buffer_before_minutes": 5,
        "buffer_after_minutes": 10, "label": "Telefonate", "assigned_user_id": ww["luca"]}
    for sbagliato in ({"duration_minutes": 0}, {"buffer_after_minutes": 1441},
                      {"appointment_type": "cena"}):
        assert api("giorgio").patch(f"{BASE}/{link['id']}", json=sbagliato).status_code == 422


def test_07_indirizzo_pubblico_funziona_e_resta_no_store(api, ww, pubblico):
    token = api("giorgio").post(BASE, json=_corpo(ww["luca"])).json()["token"]
    r = pubblico.get(f"{PUBBLICA}{token}")
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    assert "agency_id" not in r.json() and "token_hash" not in r.text
    assert token not in r.text                        # il token non torna indietro


def test_08_disattivato_resta_non_prenotabile_anche_se_ruotato(api, ww, pubblico):
    link = api("giorgio").post(BASE, json=_corpo(ww["luca"])).json()
    api("giorgio").post(f"{BASE}/{link['id']}/disable")
    nuovo = api("giorgio").post(f"{BASE}/{link['id']}/rotate").json()
    assert nuovo["status"] == "disabled"
    for token in (link["token"], nuovo["token"]):
        r = pubblico.get(f"{PUBBLICA}{token}")
        assert r.status_code == 404 and r.json() == {"available": False}
        assert r.headers["cache-control"] == "no-store"


def test_09_le_letture_operatore_non_scrivono(api, ww):
    api("giorgio").post(BASE, json=_corpo(ww["luca"]))
    api("luca").post(BASE, json=_corpo(ww["luca"]))
    prima = _conta(ww)
    for chi in ("giorgio", "anna", "luca", "supremo_in_a", "estraneo"):
        assert api(chi).get(BASE).status_code == 200
    assert _conta(ww) == prima
