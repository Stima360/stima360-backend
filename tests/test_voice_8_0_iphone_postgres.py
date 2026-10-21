"""STIMA VOICE - collaudo iPhone del 21/10 sul server vero (PostgreSQL, fornitore finto).

  r1  «Francesco Ruvo»: il contatto Francesco RUVOLO e' un CANDIDATO da confermare,
      mai scelto da solo; nessun contatto inventato
  r2  «Censori Giorgio» (cognome prima del nome) trova Giorgio Censori, da confermare
  r3  un nome che non somiglia a nessuno: nessun candidato, nessun contatto creato
  r4  la frase dettata su iPhone, con l'output sbagliato del modello (il venditore
      come cliente): Visita immobile, cliente Giorgio Censori, incarico di Francesco
      Ruvolo (l'altro suo immobile, senza incarico, non e' proposto), domani alle 10:30;
      solo dopo la conferma l'appuntamento in Agenda (crm_manual)
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tests.test_voice_2_resolver_postgres import DSN, ctx, db  # noqa: F401
from tests.test_voice_4_executor_postgres import _voce_accesa, conferma_tutto, q  # noqa: F401
from tests.test_voice_5_api_postgres import api, detta  # noqa: F401
from tests.test_voice_8_0_conversations_postgres import _persone  # noqa: F401
from tests.test_voice_8_2_visits_postgres import _incarichi  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL")

DETTATO = "Ehi Crea un appuntamento rivendita Domani alle ore 10:30 Compratore Giorgio censori venditore Francesco Ruvo"
QUANDO = {"date_text": "domani", "time_text": "alle 10"}


def _telefonata(api, testo, contatto):  # noqa: F811
    vista = detta(api, "agent_a1", testo, [{"intent": "create_appointment", "quote": testo, "appointment_type": "call",
                                            "when": QUANDO, "contact": contatto}])
    assert vista.status_code == 200, vista.text
    return vista.json()["steps"][0]


def test_r1_nome_a_meta_e_un_candidato(api, db):  # noqa: F811
    contatti = q(db, "SELECT count(*) FROM contacts")[0][0]
    p = _telefonata(api, "Telefonata con Francesco Ruvo domani alle 10", {"first_name": "Francesco", "last_name": "Ruvo"})
    assert [c["id"] for c in p["candidates"]["contact"]] == [db["ids"]["ruvolo"]]
    assert p["needs"] in ("needs_confirmation", "needs_answer") and p["state"] == "pending"
    assert q(db, "SELECT count(*) FROM contacts")[0][0] == contatti                # nessun contatto inventato


def test_r2_cognome_prima_del_nome(api, db):  # noqa: F811
    p = _telefonata(api, "Telefonata con Censori Giorgio domani alle 10", {"first_name": "Censori", "last_name": "Giorgio"})
    assert [c["id"] for c in p["candidates"]["contact"]] == [db["ids"]["giorgio"]]
    assert p["state"] == "pending"


def test_r3_nessuna_somiglianza_nessun_candidato(api, db):  # noqa: F811
    contatti = q(db, "SELECT count(*) FROM contacts")[0][0]
    p = _telefonata(api, "Telefonata con Zeno Quarantotto domani alle 10", {"first_name": "Zeno", "last_name": "Quarantotto"})
    assert not p["candidates"].get("contact") and p["state"] == "pending"
    assert q(db, "SELECT count(*) FROM contacts")[0][0] == contatti


def test_r4_dalla_frase_dettata_all_agenda(api, db):  # noqa: F811
    from voice.dates import ROMA
    prima = q(db, "SELECT count(*) FROM appointments")[0][0]
    sbagliato = [{"intent": "create_appointment", "quote": DETTATO, "appointment_type": "sale_appointment",
                  "when": {"date_text": "Domani", "time_text": "alle ore 10:30"},
                  "contact": {"first_name": "Francesco", "last_name": "Ruvo"}}]
    vista = detta(api, "agent_a1", DETTATO, sbagliato).json()
    p = vista["steps"][0]
    s = p["summary"]
    assert s["type"] == "buyer_visit" and s["contact"] == "Giorgio Censori", s
    assert s["property"].startswith("Via Roma 10") and "Francesco Ruvolo" in s["property"], s
    assert db["ids"]["roma12"] not in [c["id"] for c in p["candidates"].get("property", [])]
    domani = (datetime.now(ROMA) + timedelta(days=1)).date()
    inizio = datetime.fromisoformat(s["start_at"]).astimezone(ROMA)
    assert (inizio.date(), inizio.hour, inizio.minute) == (domani, 10, 30)
    assert p["needs"] == "needs_confirmation" and p["state"] == "pending"           # mai senza conferma
    assert q(db, "SELECT count(*) FROM appointments")[0][0] == prima
    conferma_tutto(db, "agent_a1", vista["command_id"])
    fatto = api("agent_a1").post(f"/api/voice/commands/{vista['command_id']}/execute").json()
    assert fatto["status"] == "completed", fatto
    riga = q(db, "SELECT appointment_type, contact_id, property_id, source FROM appointments WHERE id = %s",
             (fatto["steps"][0]["result"]["id"],))[0]
    assert list(riga) == ["buyer_visit", db["ids"]["giorgio"], db["ids"]["roma10"], "crm_manual"]
    assert q(db, "SELECT count(*) FROM appointments")[0][0] == prima + 1
