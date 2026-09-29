"""Filtri dell'Agenda: il SERVER resta l'autorita', anche con parametri manipolati.

La barra dei filtri (piano A30-4 congelato, §3/§7) manda al server parametri
che esistevano gia' (`agents`, `types`, `statuses`, `show_colleagues`). Il
frontend offre il filtro agente solo a chi assegna, ma un parametro si puo'
scrivere a mano: qui si prova, contro il router VERO e un PostgreSQL
usa-e-getta, che nessuna combinazione allarga cio' che il chiamante vede.

  * un `agent` che chiede `agents=<collega>` non riceve nessun appuntamento
    del collega: al massimo il suo "Occupato", senza dati;
  * un `agent` con `show_colleagues=false` vede solo i propri;
  * un owner che chiede `agents=<agente di un'altra agenzia>` riceve NIENTE;
  * `types` e `statuses` filtrano davvero, "tutti gli stati" include annullati;
  * la Lista filtra per tipo e stato, e un agent vi vede solo i propri;
  * nessuna di queste letture scrive (GET senza effetti).

Fixture e router di prova riusati da `test_a30_2_appointments_postgres`:
nessuna connessione propria. Opt-in: senza `P29_TEST_DSN` si salta tutto.
"""
from __future__ import annotations

import json
import os

import pytest

from tests.test_a30_2_appointments_postgres import (  # noqa: F401
    _crea, db, http, mondo, ore)

DSN = os.getenv("P29_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare i filtri")

TUTTI_GLI_STATI = "requested,scheduled,confirmed,completed,cancelled,no_show,rescheduled"


def _cal(http, chi, **parametri):
    p = {"from": ore(0).isoformat(), "to": ore(0, giorni=1).isoformat(), **parametri}
    r = http(chi).get("/api/appointments/calendar", params=p)
    assert r.status_code == 200, r.text
    return r.json()["items"]


def _conta(mondo):
    return [mondo["sql"](f"SELECT count(*) FROM {t}")[0][0]
            for t in ("appointments", "appointment_events")]


@pytest.fixture
def agenda(http, mondo):
    """Luca 10-11 (proprio), Marta 14-15 con note riservate, una richiesta
    libera, e in un'ALTRA agenzia un appuntamento dell'estraneo."""
    luca = _crea(http, assigned_user_id=mondo["luca"], contact_id=mondo["mario"])
    marta = _crea(http, assigned_user_id=mondo["marta"], contact_id=mondo["mario"],
                  appointment_type="call", start_at=ore(14).isoformat(),
                  end_at=ore(15).isoformat(), notes="riservato-marta")
    richiesta = _crea(http, status="requested", start_at=ore(16).isoformat(),
                      end_at=ore(17).isoformat())
    altra = _crea(http, chi="estraneo", assigned_user_id=mondo["estraneo"],
                  notes="riservato-altra-agenzia")
    return {"luca": luca, "marta": marta, "richiesta": richiesta, "altra": altra}


def test_01_agent_che_chiede_un_collega_riceve_solo_occupato(http, mondo, agenda):
    items = _cal(http, "luca", agents=str(mondo["marta"]))
    assert [i for i in items if i["kind"] == "appointment"] == []
    occupati = [i for i in items if i["kind"] == "busy"]
    assert len(occupati) == 1
    assert set(occupati[0]) == {"kind", "agent_id", "agent_name", "start_at", "end_at",
                                "label", "readonly"}
    testo = json.dumps(items)
    assert "riservato-marta" not in testo and "Mario Rossi" not in testo
    assert str(agenda["marta"]["id"]) not in {str(i.get("id")) for i in items}


def test_02_agent_senza_colleghi_vede_solo_i_propri(http, mondo, agenda):
    items = _cal(http, "luca", show_colleagues="false")
    assert [(i["kind"], i["id"]) for i in items] == [("appointment", agenda["luca"]["id"])]


def test_03_owner_non_raggiunge_un_altra_agenzia_con_il_filtro_agente(http, mondo, agenda):
    assert _cal(http, "giorgio", agents=str(mondo["estraneo"])) == []
    testo = json.dumps(_cal(http, "giorgio", agents=f"{mondo['estraneo']},{mondo['luca']}"))
    assert "riservato-altra-agenzia" not in testo
    solo_luca = _cal(http, "giorgio", agents=str(mondo["luca"]))
    assert [i["id"] for i in solo_luca] == [agenda["luca"]["id"]]


def test_04_tipo_e_stato_filtrano_e_tutti_include_gli_annullati(http, mondo, agenda):
    telefonate = _cal(http, "giorgio", types="call")
    assert [i["id"] for i in telefonate] == [agenda["marta"]["id"]]
    richieste = _cal(http, "giorgio", statuses="requested")
    assert [i["id"] for i in richieste] == [agenda["richiesta"]["id"]]
    r = http("giorgio").post(f"/api/appointments/{agenda['luca']['id']}/cancel",
                             json={"version": agenda["luca"]["version"]})
    assert r.status_code == 200, r.text
    assert agenda["luca"]["id"] not in [i["id"] for i in _cal(http, "giorgio")]
    tutti = _cal(http, "giorgio", statuses=TUTTI_GLI_STATI)
    assert agenda["luca"]["id"] in [i["id"] for i in tutti]
    # valori inventati: rifiutati, non ignorati
    p = {"from": ore(0).isoformat(), "to": ore(0, giorni=1).isoformat()}
    assert http("giorgio").get("/api/appointments/calendar",
                               params={**p, "types": "buyer"}).status_code == 422
    assert http("giorgio").get("/api/appointments/calendar",
                               params={**p, "agents": "tutti"}).status_code == 422


def test_05_lista_filtra_per_tipo_e_stato_e_un_agent_vede_solo_i_propri(http, mondo, agenda):
    p = {"from": ore(0).isoformat(), "to": ore(0, giorni=1).isoformat(), "limit": 200}
    r = http("giorgio").get("/api/appointments", params={**p, "types": "call"})
    assert r.status_code == 200 and [i["id"] for i in r.json()["items"]] == [agenda["marta"]["id"]]
    r = http("luca").get("/api/appointments", params={**p, "statuses": TUTTI_GLI_STATI})
    assert [i["id"] for i in r.json()["items"]] == [agenda["luca"]["id"]]


def test_06_le_letture_filtrate_non_scrivono(http, mondo, agenda):
    prima = _conta(mondo)
    _cal(http, "luca", agents=str(mondo["marta"]), show_colleagues="false")
    _cal(http, "giorgio", agents=str(mondo["estraneo"]), types="call", statuses=TUTTI_GLI_STATI)
    http("luca").get("/api/appointments", params={"types": "call", "statuses": "requested"})
    assert _conta(mondo) == prima
