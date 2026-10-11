"""STIMA Voice Fase 7 - la conversazione: rispondere a una domanda (o
correggere) senza perdere il comando, su PostgreSQL vero, fornitore FINTO.

`POST /api/voice/commands/{id}/reply` non e' una scorciatoia: il testo
originale + «Precisazione: <risposta>» diventa un comando NUOVO che passa per
lo stesso percorso di `POST /commands` (abilitazione, limite, idempotenza,
fornitore, planner, resolver con i permessi di chi parla, policy, conferme). Il
comando precedente, mai eseguito, viene annullato.

Cosa prova:
  * orario ambiguo / mancante: solo un chiarimento del modello, poi la risposta
    completa il comando; il contesto (la domanda) arriva al modello;
  * correzione di orario e persona su un comando gia' pianificato;
  * contatti omonimi: la scelta resta esplicita dopo la risposta;
  * censimento senza indirizzo: la risposta lo completa, un solo edificio,
    nessuna unita' inventata;
  * contatto non visibile (Fabio di un collega): nessuna visibilita' in piu';
    «crea un nuovo contatto» prepara contatto + STESSO appuntamento (data, ora,
    tipo «acquisizione» = seller_meeting), solo dopo la conferma;
  * guardie: comando gia' eseguito, comando altrui, stessa chiave, corpo
    chiuso, trascritto scaduto, Voice spento.
"""
from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from tests.test_voice_2_resolver_postgres import DSN, ctx, db  # noqa: F401  (fixture condivisa)
from tests.test_voice_4_executor_postgres import _voce_accesa, q  # noqa: F401
from tests.test_voice_5_api_postgres import api, codici, detta, passo  # noqa: F401

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare STIMA Voice Fase 7")

ROMA = ZoneInfo("Europe/Rome")


def domani(ora: int, minuti: int = 0) -> datetime:
    return datetime.combine(datetime.now(ROMA).date() + timedelta(days=1), time(ora, minuti), tzinfo=ROMA)


def rispondi(api, chi, cid, testo_originale, risposta, comandi, *, chiave=None, clarifications=()):  # noqa: F811
    unito = f"{testo_originale}\nPrecisazione: {risposta}"
    api.finto.register(unito, {"commands": comandi, "clarifications": list(clarifications)})
    return api(chi).post(f"/api/voice/commands/{cid}/reply",
                         json={"client_command_id": str(chiave or uuid.uuid4()), "text": risposta})


def stato_di(db, cid):  # noqa: F811
    return q(db, "SELECT status FROM voice_commands WHERE id = %s", (cid,))[0][0]


def scritture(db):  # noqa: F811
    return q(db, "SELECT (SELECT count(*) FROM appointments), (SELECT count(*) FROM contacts), "
                 "(SELECT count(*) FROM buildings), (SELECT count(*) FROM properties)")[0]


APP = {"intent": "create_appointment", "contact": {"first_name": "Fabio"}}


# ---------------------------------------------------------------------------
# Orario mancante: solo un chiarimento, poi la risposta
# ---------------------------------------------------------------------------

def test_c1_a_clarification_only_is_a_question_and_the_answer_completes_the_command(api, db):  # noqa: F811
    testo = "Domani appuntamento con Fabio"
    prima = scritture(db)
    vista = detta(api, "owner_a", testo, [], clarifications=["A che ora vuoi fissare l'appuntamento con Fabio?"]).json()
    assert vista["steps"] == [] and vista["clarifications"] == ["A che ora vuoi fissare l'appuntamento con Fabio?"]
    vecchio = vista["command_id"]
    r = rispondi(api, "owner_a", vecchio, testo, "alle 18:30",
                 [{**APP, "quote": testo + " alle 18:30", "when": {"date_text": "domani", "time_text": "alle 18:30"}}])
    assert r.status_code == 200, r.text
    nuova = r.json()
    assert nuova["command_id"] != vecchio and stato_di(db, vecchio) == "cancelled"
    s = passo(nuova, 1)
    assert s["intent"] == "create_appointment" and datetime.fromisoformat(s["start_at"]) == domani(18, 30)
    # il modello ha ricevuto la domanda come contesto, non nel testo dell'agente
    assert api.finto.contexts[-1] == ["A che ora vuoi fissare l'appuntamento con Fabio?"]
    # due Fabio visibili al titolare: scelta esplicita, nessuna scrittura prima
    assert "reference_ambiguous" in codici(s) and len(s["candidates"]["contact"]) == 2
    assert scritture(db) == prima
    scelta = db["ids"]["fabio_bianchi"]
    r = api("owner_a").post(f"/api/voice/commands/{nuova['command_id']}/confirm",
                            json={"steps": [{"ordinal": 1, "choices": {"contact": scelta}}]})
    assert r.status_code == 200, r.text
    fatto = api("owner_a").post(f"/api/voice/commands/{nuova['command_id']}/execute").json()
    assert fatto["status"] == "completed", fatto
    app = q(db, "SELECT contact_id, start_at, assigned_user_id FROM appointments WHERE id = %s",
            (passo(fatto, 1)["result"]["id"],))[0]
    assert app[0] == scelta and app[1] == domani(18, 30) and app[2] == db["ids"]["owner_a"]


# ---------------------------------------------------------------------------
# Correzione di orario e persona su un piano gia' pronto
# ---------------------------------------------------------------------------

def test_c2_time_and_person_are_corrected_without_starting_over(api, db):  # noqa: F811
    testo = "Domani alle 15 appuntamento con Fabio"
    vista = detta(api, "owner_a", testo,
                  [{**APP, "quote": testo, "when": {"date_text": "domani", "time_text": "alle 15"}}]).json()
    assert datetime.fromisoformat(passo(vista, 1)["start_at"]) == domani(15)
    prima = scritture(db)
    # (le 18:30 del titolare sono gia' occupate dal test c1: si corregge alle 17:30)
    r = rispondi(api, "owner_a", vista["command_id"], testo, "no, alle 17:30 con Fabio Bianchi",
                 [{"intent": "create_appointment", "quote": testo, "when": {"date_text": "domani", "time_text": "alle 17:30"},
                   "contact": {"first_name": "Fabio", "last_name": "Bianchi"}}])
    assert r.status_code == 200, r.text
    s = passo(r.json(), 1)
    assert datetime.fromisoformat(s["start_at"]) == domani(17, 30)
    assert [c["id"] for c in s["candidates"]["contact"]] == [db["ids"]["fabio_bianchi"]]
    assert s["needs"] == "needs_confirmation"                     # trovato per nome: si conferma
    assert stato_di(db, vista["command_id"]) == "cancelled" and scritture(db) == prima


# ---------------------------------------------------------------------------
# Censimento senza indirizzo
# ---------------------------------------------------------------------------

def test_c3_census_address_given_in_the_answer_one_building_no_units(api, db):  # noqa: F811
    testo = "Censisci una palazzina con 12 appartamenti"
    vista = detta(api, "agent_a1", testo, [{"intent": "create_building", "quote": testo, "units_declared": 12}]).json()
    s = passo(vista, 1)
    assert s["needs"] == "needs_answer" and "building_location_missing" in codici(s)
    unita_prima = q(db, "SELECT count(*) FROM properties")[0][0]
    r = rispondi(api, "agent_a1", vista["command_id"], testo, "in via del Collaudo 7 a Tortoreto",
                 [{"intent": "create_building", "quote": testo, "address": "Via del Collaudo", "civic_number": "7",
                   "city": "Tortoreto", "units_declared": 12}])
    assert r.status_code == 200, r.text
    nuova = r.json()
    # il modello ha ricevuto la domanda sull'indirizzo
    assert any("palazzina" in d for d in api.finto.contexts[-1])
    if nuova["status"] != "completed":                            # secondo la modalita' dell'agenzia
        api("agent_a1").post(f"/api/voice/commands/{nuova['command_id']}/confirm", json={"all": True})
        nuova = api("agent_a1").post(f"/api/voice/commands/{nuova['command_id']}/execute").json()
    assert nuova["status"] == "completed", nuova
    bid = passo(nuova, 1)["result"]["id"]
    assert q(db, "SELECT units_declared, address, city FROM buildings WHERE id = %s", (bid,))[0] == \
        [12, "Via del Collaudo", "Tortoreto"]
    assert q(db, "SELECT count(*) FROM properties")[0][0] == unita_prima        # nessuna unita' inventata
    assert stato_di(db, vista["command_id"]) == "cancelled"
    # guardia: a un comando gia' eseguito non si risponde
    r = rispondi(api, "agent_a1", nuova["command_id"], testo + "\nPrecisazione: in via del Collaudo 7 a Tortoreto",
                 "anzi 14 appartamenti", [])
    assert r.status_code == 409 and r.json()["code"] == "VOICE_REPLY_NOT_ALLOWED"


# ---------------------------------------------------------------------------
# Fabio non visibile: nessuna visibilita' in piu', poi «crea un nuovo contatto»
# ---------------------------------------------------------------------------

def test_c4_unseen_fabio_then_new_contact_keeps_the_same_appointment(api, db):  # noqa: F811
    testo = "Domani, fissami un appuntamento con Fabio alle 18:30, un appuntamento acquisizione"
    quota = "un appuntamento con Fabio alle 18:30, un appuntamento acquisizione"
    vista = detta(api, "agent_a1", testo,
                  [{**APP, "quote": quota, "when": {"date_text": "domani", "time_text": "alle 18:30"}}]).json()
    s = passo(vista, 1)
    # agent_a1 non vede ne' il Fabio di un collega ne' quello non assegnato: nessuna fuga
    assert "reference_not_found" in codici(s)
    assert "Verdi" not in str(vista) and "Bianchi" not in str(vista)
    assert datetime.fromisoformat(s["start_at"]) == domani(18, 30)
    prima = scritture(db)
    acquisizioni = q(db, "SELECT count(*) FROM acquisitions")[0][0]
    r = rispondi(api, "agent_a1", vista["command_id"], testo, "crea un nuovo contatto Fabio Neri 333 8080808",
                 [{"intent": "create_contact", "quote": "crea un nuovo contatto Fabio Neri 333 8080808",
                   "first_name": "Fabio", "last_name": "Neri", "phone": "333 8080808"},
                  {"intent": "create_appointment", "quote": quota,
                   "when": {"date_text": "domani", "time_text": "alle 18:30"}, "contact": {"step": 1}}])
    assert r.status_code == 200, r.text
    nuova = r.json()
    assert [p["intent"] for p in nuova["steps"]] == ["create_contact", "create_appointment"]
    assert datetime.fromisoformat(passo(nuova, 2)["start_at"]) == domani(18, 30)        # data e ora conservate
    assert nuova["status"] != "completed" and scritture(db) == prima                     # niente senza conferma
    r = api("agent_a1").post(f"/api/voice/commands/{nuova['command_id']}/confirm", json={"all": True})
    assert r.status_code == 200, r.text
    fatto = api("agent_a1").post(f"/api/voice/commands/{nuova['command_id']}/execute").json()
    assert fatto["status"] == "completed", fatto
    contatto = passo(fatto, 1)["result"]["id"]
    app = q(db, "SELECT appointment_type, contact_id, start_at, end_at, assigned_user_id FROM appointments WHERE id = %s",
            (passo(fatto, 2)["result"]["id"],))[0]
    assert app == ["seller_meeting", contatto, domani(18, 30), domani(19, 30), db["ids"]["agent_a1"]]
    assert q(db, "SELECT assigned_agent_id FROM contacts WHERE id = %s", (contatto,))[0][0] == db["ids"]["agent_a1"]
    # l'appuntamento di acquisizione non apre un'Acquisizione da solo
    assert q(db, "SELECT count(*) FROM acquisitions")[0][0] == acquisizioni


def test_c5_acquisition_keyword_maps_to_the_crm_type_used_by_acquisitions():
    from acquisitions.enums import APPOINTMENT_TYPE
    from appointments.enums import APPOINTMENT_TYPES
    from voice.planner import appointment_type_from
    assert appointment_type_from("un appuntamento acquisizione") == APPOINTMENT_TYPE == "seller_meeting"
    assert APPOINTMENT_TYPE in APPOINTMENT_TYPES
    assert appointment_type_from("un appuntamento con fabio") == "other"                # nessuna invenzione


# ---------------------------------------------------------------------------
# Guardie
# ---------------------------------------------------------------------------

def test_g1_reply_guards(api, db, monkeypatch):  # noqa: F811
    testo = "Domani appuntamento con Mario 333 1111111"
    vista = detta(api, "agent_a1", testo, [], clarifications=["A che ora?"]).json()
    cid = vista["command_id"]
    # corpo chiuso, testo vuoto
    r = api("agent_a1").post(f"/api/voice/commands/{cid}/reply",
                             json={"client_command_id": str(uuid.uuid4()), "text": "alle 9", "agency_id": 2})
    assert r.status_code == 422
    r = api("agent_a1").post(f"/api/voice/commands/{cid}/reply", json={"client_command_id": str(uuid.uuid4()), "text": ""})
    assert r.status_code == 422
    # un altro agente, un'altra agenzia: il comando non esiste per loro
    for altro in ("agent_a2", "agent_b"):
        r = rispondi(api, altro, cid, testo, "alle 9", [])
        assert r.status_code == 404, (altro, r.text)
    assert stato_di(db, cid) != "cancelled"
    # stessa chiave due volte: un solo comando nuovo, una sola interpretazione
    comandi = [{"intent": "create_appointment", "quote": testo, "when": {"date_text": "domani", "time_text": "alle 9"},
                "contact": {"phone": "333 1111111"}}]
    chiave = uuid.uuid4()
    primo = rispondi(api, "agent_a1", cid, testo, "alle 9", comandi, chiave=chiave).json()
    chiamate = len(api.finto.calls)
    secondo = rispondi(api, "agent_a1", cid, testo, "alle 9", comandi, chiave=chiave).json()
    assert secondo["command_id"] == primo["command_id"] and len(api.finto.calls) == chiamate
    # un'altra risposta (altra chiave) allo stesso comando, gia' sostituito: rifiutata, un solo piano vivo
    r = rispondi(api, "agent_a1", cid, testo, "alle 11", comandi)
    assert r.status_code == 409 and r.json()["code"] == "VOICE_REPLY_NOT_ALLOWED"
    vivi = q(db, "SELECT count(*) FROM voice_commands WHERE actor_user_id = %s AND status NOT IN ('cancelled') "
                 "AND created_at >= (SELECT created_at FROM voice_commands WHERE id = %s)", (db["ids"]["agent_a1"], cid))[0][0]
    assert vivi == 1
    # Voice spento: rifiuto prima di tutto
    monkeypatch.setenv("STIMA_VOICE_ENABLED", "0")
    r = rispondi(api, "agent_a1", primo["command_id"], testo + "\nPrecisazione: alle 9", "alle 10", [])
    assert r.status_code == 403 and r.json()["code"] == "VOICE_GLOBALLY_DISABLED"
    monkeypatch.setenv("STIMA_VOICE_ENABLED", "1")
    # trascritto scaduto: il contesto non c'e' piu', nessuna invenzione
    q(db, "UPDATE voice_commands SET transcript_expires_at = NOW() - interval '1 minute' WHERE id = %s",
      (primo["command_id"],))
    from voice import ledger
    ledger.purge_expired_transcripts()
    r = rispondi(api, "agent_a1", primo["command_id"], testo + "\nPrecisazione: alle 9", "alle 10", [])
    assert r.status_code == 409 and r.json()["code"] == "VOICE_CONVERSATION_EXPIRED"
    # la risposta non riporta mai il trascritto della conversazione
    assert "Precisazione" not in str(secondo)


@pytest.mark.parametrize("testo, dal_modello, atteso", [
    ("Domani alle 15 appuntamento con Fabio", None, "other"),
    ("Domani alle 15 appuntamento con Fabio", "seller_meeting", "other"),          # il modello non basta
    ("Domani alle 15 appuntamento acquisizione con Fabio", None, "seller_meeting"),
    ("Domani alle 15 appuntamento con Fabio, il proprietario di via Roma", None, "seller_meeting"),
    ("Domani alle 15 appuntamento acquisizione con Fabio", "seller_meeting", "seller_meeting"),
    ("Domani alle 15 sopralluogo con Fabio", None, "inspection"),
    ("Domani alle 15 sopralluogo con Fabio", "seller_meeting", "inspection"),
    ("Domani alle 15 telefonata con Fabio", "call", "call"),
])
def test_c6_seller_meeting_only_when_the_words_say_acquisition_or_owner(testo, dal_modello, atteso):
    from voice.planner import build_plan
    from voice.schemas import parse_plan_output
    comando = {"intent": "create_appointment", "quote": testo, "contact": {"first_name": "Fabio"},
               "when": {"date_text": "domani", "time_text": "alle 15"}}
    if dal_modello:
        comando["appointment_type"] = dal_modello
    piano = build_plan(parse_plan_output({"commands": [comando], "clarifications": []}), testo,
                       datetime(2026, 10, 11, 9, 0, tzinfo=ROMA))
    assert piano.steps[0].payload["appointment_type"] == atteso
