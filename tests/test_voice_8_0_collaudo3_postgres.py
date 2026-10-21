"""STIMA VOICE - collaudo 3 (21/10) sul server vero (PostgreSQL, fornitore finto).

Contatti (cognomi pronunciati male, nessun alias):
  c1  Francesco Ruvolo salvato SOLO con il nome completo (`display_name`, come
      molti contatti del CRM): «Francesco Ruvo», «Francesco Rivo», «Rivo
      Francesco» lo propongono come candidato da confermare; nessun contatto creato
  c2  visibilita': un agente non vede il Ruvolo assegnato a un collega (nessun
      candidato); il titolare si'
  c3  nessun falso positivo: «Mario Bianchi», e un contatto con il solo nome
      «Francesco» non si propone quando c'e' chi corrisponde a nome e cognome
  c4  visita con «venditore Francesco Rivo» senza immobili collegati: la domanda
      dice chi e' stato trovato fra i contatti e chiede l'immobile

Chiarimenti senza il modello (conteggio delle chiamate al fornitore):
  l1  STIMA chiede il giorno -> «domani»: nuovo piano, nessuna chiamata; il
      comando precedente e' annullato, il nuovo ha la data
  l2  STIMA chiede l'ora -> «10 e 30»; «alle tre» -> «pomeriggio»: nessuna chiamata
  l3  la stessa risposta ripetuta con la stessa chiave: un solo comando
  l4  durante la pausa dopo un 429 la risposta «domani» funziona (nessuna chiamata);
      una correzione che non e' una data («il cliente e' Mario Rossi») resta
      ferma con VOICE_PROVIDER_BUSY
  l5  una risposta non deterministica va al modello: UNA chiamata

Ripresa e sovrapposizioni:
  r1  il comando in corso si rilegge dal registro (vista e conversazione) senza
      chiamare il modello; dopo una risposta e' quello nuovo; annullato, nessuno
  r2  un comando nuovo chiude quello lasciato a meta' (mai eseguito); un comando
      gia' concluso resta com'era
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest

from tests.test_voice_2_resolver_postgres import DSN, ctx, db  # noqa: F401
from tests.test_voice_4_executor_postgres import _voce_accesa, conferma_tutto, q  # noqa: F401
from tests.test_voice_5_api_postgres import api, detta  # noqa: F401
from tests.test_voice_8_2_visits_postgres import _incarichi  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL")

QUANDO = {"date_text": "domani", "time_text": "alle 10"}


@pytest.fixture(scope="module", autouse=True)
def _contatti(db):  # noqa: F811
    def contatto(display, nome, cognome, agente):
        return q(db, "INSERT INTO contacts (agency_id, contact_type, display_name, first_name, last_name, assigned_agent_id, "
                     "status) VALUES (1, 'person', %s, %s, %s, %s, 'active') RETURNING id",
                 (display, nome, cognome, db["ids"][agente] if agente else None))[0][0]
    # come nel CRM: solo il nome completo, nome e cognome vuoti
    db["ids"]["ruvolo"] = contatto("Francesco Ruvolo", None, None, "agent_a1")
    db["ids"]["solo_francesco"] = contatto("Francesco", "Francesco", None, "agent_a1")
    db["ids"]["ruvolo_collega"] = contatto("Ernesto Ruvolo", "Ernesto", "Ruvolo", "agent_a2")
    db["ids"]["censori"] = contatto("Giorgio Censori", "Giorgio", "Censori", "agent_a1")
    yield


def _chiudi_aperti(db):  # noqa: F811
    from voice import ledger
    for cid, attore in q(db, "SELECT id, actor_user_id FROM voice_commands WHERE status = ANY(%s)",
                         (["planned", "awaiting_answers", "partial"],)):
        chi = next(k for k in ("owner_a", "agent_a1", "agent_a2") if db["ids"][k] == attore)
        ledger.cancel(ctx(db, chi), cid)


@pytest.fixture(autouse=True)
def _pulito(db):  # noqa: F811
    from voice import service
    _chiudi_aperti(db)
    service._fornitore_occupato_fino[0] = None
    yield
    service._fornitore_occupato_fino[0] = None


def _telefonata(api, chi, testo, contatto):  # noqa: F811
    r = detta(api, chi, testo, [{"intent": "create_appointment", "quote": testo, "appointment_type": "call",
                                 "when": QUANDO, "contact": contatto}])
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Contatti
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("nome, cognome", [("Francesco", "Ruvo"), ("Francesco", "Rivo"), ("Rivo", "Francesco"),
                                           ("Francesco", "Ruvolo")])
def test_c1_nome_completo_e_cognome_storpiato(api, db, nome, cognome):  # noqa: F811
    contatti = q(db, "SELECT count(*) FROM contacts")[0][0]
    p = _telefonata(api, "agent_a1", f"Telefonata con {nome} {cognome} domani alle 10",
                    {"first_name": nome, "last_name": cognome})["steps"][0]
    assert [c["id"] for c in p["candidates"]["contact"]] == [db["ids"]["ruvolo"]], p["candidates"]
    assert p["state"] == "pending" and p["needs"] in ("needs_confirmation", "needs_answer")   # mai da solo
    assert q(db, "SELECT count(*) FROM contacts")[0][0] == contatti                        # nessun contatto inventato


def test_c2_la_visibilita_del_crm_vale_anche_qui(api, db):  # noqa: F811
    testo = "Telefonata con Ernesto Rivo domani alle 10"
    agente = _telefonata(api, "agent_a1", testo, {"first_name": "Ernesto", "last_name": "Rivo"})["steps"][0]
    assert not agente["candidates"].get("contact")                       # assegnato a un collega: invisibile
    titolare = _telefonata(api, "owner_a", testo, {"first_name": "Ernesto", "last_name": "Rivo"})["steps"][0]
    assert [c["id"] for c in titolare["candidates"]["contact"]] == [db["ids"]["ruvolo_collega"]]


def test_c3_nessun_falso_positivo(api, db):  # noqa: F811
    p = _telefonata(api, "agent_a1", "Telefonata con Mario Bianchi domani alle 10",
                    {"first_name": "Mario", "last_name": "Bianchi"})["steps"][0]
    assert db["ids"]["ruvolo"] not in [c["id"] for c in p["candidates"].get("contact", [])]
    p = _telefonata(api, "agent_a1", "Telefonata con Francesco Rivo domani alle 10",
                    {"first_name": "Francesco", "last_name": "Rivo"})["steps"][0]
    assert db["ids"]["solo_francesco"] not in [c["id"] for c in p["candidates"]["contact"]]


def test_c4_venditore_trovato_fra_i_contatti_senza_immobili(api, db):  # noqa: F811
    testo = "Visita domani alle 10 compratore Giorgio Censori venditore Francesco Rivo"
    r = detta(api, "agent_a1", testo, [{"intent": "create_appointment", "quote": testo, "appointment_type": "buyer_visit",
                                        "when": QUANDO, "contact": {"first_name": "Giorgio", "last_name": "Censori"}}])
    p = r.json()["steps"][0]
    messaggi = " ".join(m["message"] for m in p["reasons"])
    assert "Tra i contatti ho trovato Francesco Ruvolo" in messaggi and "quale immobile" in messaggi, messaggi
    assert p["state"] == "pending" and p["needs"] == "needs_answer"


# ---------------------------------------------------------------------------
# Chiarimenti senza il modello
# ---------------------------------------------------------------------------

def _rispondi(api, chi, cid, testo, chiave=None):  # noqa: F811
    return api(chi).post(f"/api/voice/commands/{cid}/reply",
                         json={"client_command_id": str(chiave or uuid.uuid4()), "text": testo})


def _appuntamento(api, testo, quando):  # noqa: F811
    r = detta(api, "agent_a1", testo, [{"intent": "create_appointment", "quote": testo, "appointment_type": "call",
                                        "when": quando, "contact": {"first_name": "Giorgio", "last_name": "Censori"}}])
    assert r.status_code == 200, r.text
    return r.json()


def _codici(vista):
    return {m["code"] for p in vista["steps"] for m in p["reasons"]}


def test_l1_il_giorno_senza_il_modello(api, db):  # noqa: F811
    from voice.dates import ROMA
    vista = _appuntamento(api, "Telefonata con Giorgio Censori prossimamente alle 10:30",
                          {"date_text": "prossimamente", "time_text": "alle 10:30"})
    assert "date_unrecognized" in _codici(vista)
    chiamate = len(api.finto.calls)
    r = _rispondi(api, "agent_a1", vista["command_id"], "domani")
    assert r.status_code == 200, r.text
    assert len(api.finto.calls) == chiamate                                   # nessuna chiamata al modello
    nuovo = r.json()
    assert nuovo["command_id"] != vista["command_id"] and "date_unrecognized" not in _codici(nuovo)
    inizio = datetime.fromisoformat(nuovo["steps"][0]["start_at"]).astimezone(ROMA)
    assert (inizio.date(), inizio.hour, inizio.minute) == ((datetime.now(ROMA) + timedelta(days=1)).date(), 10, 30)
    assert q(db, "SELECT status FROM voice_commands WHERE id = %s", (vista["command_id"],))[0][0] == "cancelled"
    assert nuovo["steps"][0]["summary"]["contact"] == "Giorgio Censori"       # gli altri dati restano


def test_l2_l_ora_e_la_parte_del_giorno_senza_il_modello(api, db):  # noqa: F811
    from voice.dates import ROMA
    vista = _appuntamento(api, "Telefonata con Giorgio Censori domani", {"date_text": "domani"})
    assert "time_unrecognized" in _codici(vista)
    chiamate = len(api.finto.calls)
    nuovo = _rispondi(api, "agent_a1", vista["command_id"], "10 e 30").json()
    inizio = datetime.fromisoformat(nuovo["steps"][0]["start_at"]).astimezone(ROMA)
    assert (inizio.hour, inizio.minute) == (10, 30) and len(api.finto.calls) == chiamate
    vista = _appuntamento(api, "Telefonata con Giorgio Censori domani alle tre", {"date_text": "domani", "time_text": "alle tre"})
    assert "time_ambiguous_hour" in _codici(vista)
    nuovo = _rispondi(api, "agent_a1", vista["command_id"], "pomeriggio").json()
    inizio = datetime.fromisoformat(nuovo["steps"][0]["start_at"]).astimezone(ROMA)
    assert (inizio.hour, inizio.minute) == (15, 0) and len(api.finto.calls) == chiamate + 1   # solo il comando


def test_l3_stessa_risposta_stessa_chiave_un_solo_comando(api, db):  # noqa: F811
    vista = _appuntamento(api, "Telefonata con Giorgio Censori domani", {"date_text": "domani"})
    chiave = uuid.uuid4()
    primo = _rispondi(api, "agent_a1", vista["command_id"], "alle 11", chiave).json()
    prima = q(db, "SELECT count(*) FROM voice_commands")[0][0]
    secondo = _rispondi(api, "agent_a1", vista["command_id"], "alle 11", chiave)
    assert secondo.status_code == 200 and secondo.json()["command_id"] == primo["command_id"]
    assert q(db, "SELECT count(*) FROM voice_commands")[0][0] == prima


def test_l4_durante_la_pausa_dopo_un_429(api, db):  # noqa: F811
    from voice import service
    vista = _appuntamento(api, "Telefonata con Giorgio Censori prossimamente alle 9", {"date_text": "prossimamente",
                                                                                     "time_text": "alle 9"})
    service._fornitore_occupato_fino[0] = datetime.now(service.ROMA) + timedelta(seconds=260)
    chiamate = len(api.finto.calls)
    r = _rispondi(api, "agent_a1", vista["command_id"], "domani")
    assert r.status_code == 200, r.text                                        # capita qui: nessuna attesa
    occupato = _rispondi(api, "agent_a1", r.json()["command_id"], "il cliente è Mario Rossi")
    assert occupato.status_code == 429 and occupato.json()["code"] == "VOICE_PROVIDER_BUSY"
    assert len(api.finto.calls) == chiamate


def test_l5_una_correzione_vera_va_al_modello(api, db):  # noqa: F811
    from voice import service
    testo = "Telefonata con Giorgio Censori domani"
    vista = _appuntamento(api, testo, {"date_text": "domani"})
    risposta = "il cliente è Mario Rossi alle 11"
    api.finto.register(f"{testo}\n{service.REPLY_PREFIX}{risposta}", {"commands": [{
        "intent": "create_appointment", "quote": testo, "appointment_type": "call",
        "when": {"date_text": "domani", "time_text": "alle 11"}, "contact": {"first_name": "Mario", "last_name": "Rossi"}}],
        "clarifications": []})
    chiamate = len(api.finto.calls)
    r = _rispondi(api, "agent_a1", vista["command_id"], risposta)
    assert r.status_code == 200, r.text
    assert len(api.finto.calls) == chiamate + 1


# ---------------------------------------------------------------------------
# Ripresa e sovrapposizioni
# ---------------------------------------------------------------------------

def test_r1_il_comando_in_corso_si_rilegge_senza_il_modello(api, db):  # noqa: F811
    assert api("agent_a1").get("/api/voice/commands/current").json() == {"command": None, "conversation": []}
    testo = "Telefonata con Giorgio Censori domani"
    vista = _appuntamento(api, testo, {"date_text": "domani"})
    chiamate = len(api.finto.calls)
    corrente = api("agent_a1").get("/api/voice/commands/current").json()
    assert corrente["command"]["command_id"] == vista["command_id"] and corrente["conversation"] == [testo]
    assert corrente["command"]["steps"][0]["reasons"] == vista["steps"][0]["reasons"]     # le stesse domande
    assert api("agent_a2").get("/api/voice/commands/current").json()["command"] is None     # solo i propri
    nuovo = _rispondi(api, "agent_a1", vista["command_id"], "alle 10 e 30").json()
    corrente = api("agent_a1").get("/api/voice/commands/current").json()
    assert corrente["command"]["command_id"] == nuovo["command_id"]
    assert corrente["conversation"] == [testo, "alle 10 e 30"]
    assert len(api.finto.calls) == chiamate                                   # ripresa e risposta: zero chiamate
    api("agent_a1").post(f"/api/voice/commands/{nuovo['command_id']}/cancel")
    assert api("agent_a1").get("/api/voice/commands/current").json()["command"] is None


def test_r2_un_comando_nuovo_chiude_quello_lasciato_a_meta(api, db):  # noqa: F811
    primo = _appuntamento(api, "Telefonata con Giorgio Censori domani", {"date_text": "domani"})
    secondo = _appuntamento(api, "Telefonata con Giorgio Censori dopodomani", {"date_text": "dopodomani"})
    assert q(db, "SELECT status FROM voice_commands WHERE id = %s", (primo["command_id"],))[0][0] == "cancelled"
    corrente = api("agent_a1").get("/api/voice/commands/current").json()
    assert corrente["command"]["command_id"] == secondo["command_id"]
    # un comando concluso non si tocca
    terzo = _appuntamento(api, "Telefonata con Giorgio Censori dopodomani alle 17",
                          {"date_text": "dopodomani", "time_text": "alle 17"})
    conferma_tutto(db, "agent_a1", terzo["command_id"], choices=None)
    fatto = api("agent_a1").post(f"/api/voice/commands/{terzo['command_id']}/execute").json()
    assert fatto["status"] == "completed", fatto
    _appuntamento(api, "Telefonata con Giorgio Censori dopodomani alle 18", {"date_text": "dopodomani",
                                                                            "time_text": "alle 18"})
    assert q(db, "SELECT status FROM voice_commands WHERE id = %s", (terzo["command_id"],))[0][0] == "completed"
