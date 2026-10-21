"""STIMA VOICE - collaudo reale del 21/10 sul server vero (PostgreSQL, fornitore finto).

  01  un 429 del fornitore su una RISPOSTA: 429 VOICE_PROVIDER_BUSY con `retry_after`
      e l'intestazione Retry-After; il comando a cui si rispondeva NON e' annullato
  02  durante la pausa nessuna chiamata al fornitore (risposta immediata, stessi secondi);
      dopo la pausa la stessa risposta, con la stessa chiave, va a buon fine e solo
      allora il comando precedente si chiude
  03  la frase reale con l'output SBAGLIATO del modello: visita immobile, domanda
      «chi visita?», l'incarico proposto e' quello di Francesco Ruvolo; nulla si salva
  04  con l'output corretto: dopo la conferma, visita immobile di Giorgio Censori
      sull'incarico di Francesco Ruvolo; l'immobile di Ruvolo SENZA incarico non e'
      proposto (nessun incarico inventato)
"""
from __future__ import annotations

import uuid

import pytest

from tests.test_voice_2_resolver_postgres import DSN, ctx, db  # noqa: F401
from tests.test_voice_4_executor_postgres import _voce_accesa, conferma_tutto, q  # noqa: F401
from tests.test_voice_5_api_postgres import api, detta  # noqa: F401
from tests.test_voice_8_2_visits_postgres import _incarichi  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL")

FRASE = "Crea un appuntamento di vendita Giorgio Censori, che va a vedere la casa di Francesco Ruvolo domani alle 10"
QUANDO = {"date_text": "domani", "time_text": "alle 10"}


@pytest.fixture(scope="module", autouse=True)
def _persone(db, _incarichi):  # noqa: F811
    def contatto(nome, cognome):
        return q(db, "INSERT INTO contacts (agency_id, contact_type, display_name, first_name, last_name, assigned_agent_id, status) "
                     "VALUES (1, 'person', %s, %s, %s, %s, 'active') RETURNING id",
                 (f"{nome} {cognome}", nome, cognome, db["ids"]["agent_a1"]))[0][0]
    db["ids"]["giorgio"] = contatto("Giorgio", "Censori")
    db["ids"]["ruvolo"] = contatto("Francesco", "Ruvolo")
    # Ruvolo e' proprietario di roma10 (con incarico, dal V8-2) e di roma12 (senza incarico)
    for pid in (db["ids"]["roma10"], db["ids"]["roma12"]):
        q(db, "INSERT INTO property_contacts (property_id, contact_id, role) VALUES (%s, %s, 'owner') ON CONFLICT DO NOTHING",
          (pid, db["ids"]["ruvolo"]))
    yield


SBAGLIATO = [{"intent": "create_appointment", "quote": FRASE, "appointment_type": "sale_appointment", "when": QUANDO,
              "contact": {"first_name": "Francesco", "last_name": "Ruvolo"}}]
GIUSTO = [{"intent": "create_appointment", "quote": FRASE, "appointment_type": "buyer_visit", "when": QUANDO,
           "contact": {"first_name": "Giorgio", "last_name": "Censori", "role": "buyer"},
           "property": {"owner": {"first_name": "Francesco", "last_name": "Ruvolo"}}}]


def _passo(vista):
    return vista["steps"][0]


def test_01_02_un_429_non_rompe_la_conversazione(api, db):  # noqa: F811
    from voice import service
    primo = detta(api, "agent_a1", FRASE, SBAGLIATO)
    assert primo.status_code == 200, primo.text
    cid = primo.json()["command_id"]
    risposta = "Il cliente è Giorgio Censori"
    testo_risposta = f"{FRASE}\n{service.REPLY_PREFIX}{risposta}"
    api.finto.register(testo_risposta, {"commands": GIUSTO, "clarifications": []})
    chiave = str(uuid.uuid4())
    api.finto.busy_next = 37
    r = api("agent_a1").post(f"/api/voice/commands/{cid}/reply", json={"client_command_id": chiave, "text": risposta})
    assert r.status_code == 429 and r.json()["code"] == "VOICE_PROVIDER_BUSY" and r.json()["retry_after"] == 37
    assert r.headers["Retry-After"] == "37"
    assert q(db, "SELECT status FROM voice_commands WHERE id = %s", (cid,))[0][0] != "cancelled"
    chiamate = len(api.finto.calls)
    r = api("agent_a1").post(f"/api/voice/commands/{cid}/reply", json={"client_command_id": chiave, "text": risposta})
    assert r.status_code == 429 and 1 <= r.json()["retry_after"] <= 37
    assert len(api.finto.calls) == chiamate                                     # nessuna chiamata durante la pausa
    nuovo = api("agent_a1").post("/api/voice/commands", json={"client_command_id": str(uuid.uuid4()), "text": "altro"})
    assert nuovo.status_code == 429 and len(api.finto.calls) == chiamate        # vale per tutti: il limite e' del fornitore
    service._fornitore_occupato_fino[0] = None                                   # la pausa e' finita
    r = api("agent_a1").post(f"/api/voice/commands/{cid}/reply", json={"client_command_id": chiave, "text": risposta})
    assert r.status_code == 200, r.text
    assert q(db, "SELECT status FROM voice_commands WHERE id = %s", (cid,))[0][0] == "cancelled"
    assert _passo(r.json())["summary"]["contact"] == "Giorgio Censori"


def test_03_output_sbagliato(api, db):  # noqa: F811
    prima = q(db, "SELECT count(*) FROM appointments")[0][0]
    vista = detta(api, "agent_a1", FRASE, SBAGLIATO).json()
    p = _passo(vista)
    assert p["summary"]["type"] == "buyer_visit" and p["summary"]["type_label"] == "Visita immobile"
    assert "visit_client_is_owner" in [m["code"] for m in p["reasons"]] and p["needs"] == "needs_answer"
    assert [c["id"] for c in p["candidates"]["property"]] == [db["ids"]["roma10"]]
    assert "proprietario Francesco Ruvolo" in p["candidates"]["property"][0]["label"]
    assert q(db, "SELECT count(*) FROM appointments")[0][0] == prima


def test_04_output_corretto(api, db):  # noqa: F811
    vista = detta(api, "agent_a1", FRASE, GIUSTO).json()
    p = _passo(vista)
    s = p["summary"]
    assert s["type"] == "buyer_visit" and s["contact"] == "Giorgio Censori"
    assert s["property"].startswith("Via Roma 10") and "Francesco Ruvolo" in s["property"]
    assert db["ids"]["roma12"] not in [c["id"] for c in p["candidates"].get("property", [])]
    assert p["needs"] == "needs_confirmation"                                    # mai senza conferma
    conferma_tutto(db, "agent_a1", vista["command_id"])
    fatto = api("agent_a1").post(f"/api/voice/commands/{vista['command_id']}/execute").json()
    assert fatto["status"] == "completed", fatto
    riga = q(db, "SELECT appointment_type, contact_id, property_id, source FROM appointments WHERE id = %s",
             (_passo(fatto)["result"]["id"],))[0]
    assert list(riga) == ["buyer_visit", db["ids"]["giorgio"], db["ids"]["roma10"], "crm_manual"]
