"""STIMA Voice Fase 5 - le API su PostgreSQL vero, con il fornitore FINTO.

Il router gira su un'app FastAPI di test (`main.py` non lo monta). La
sessione e' sostituita come negli altri test di router (`require_operator`).
Opt-in `P29_TEST_DSN`, SOLO database locale usa-e-getta; nessun modello reale.

Cosa prova:
  * abilitazione fail-closed su tre livelli (interruttore globale, agenzia,
    fornitore) e platform admin bloccato;
  * i tre scenari della roadmap via API, dal testo all'esecuzione;
  * idempotenza della richiesta (stessa chiave = stesso comando, un'unica
    interpretazione), conflitto di chiave, richieste concorrenti;
  * errori del fornitore e output malformati: nessuna azione, ripetibile;
  * isolamento fra agenzie e fra agenti, corpi chiusi, scelte convalidate;
  * comune fuori catalogo chiesto PRIMA di scrivere; limite giornaliero;
  * annullamento, annullamento del comando, riconciliazione, e il limite
    noto «partial dopo riconciliazione» dichiarato nella risposta.
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from tests.test_voice_2_resolver_postgres import DSN, ctx, db  # noqa: F401  (fixture condivisa)
from tests.test_voice_4_executor_postgres import _voce_accesa, conta, q  # noqa: F401

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare STIMA Voice Fase 5")

ROMA = ZoneInfo("Europe/Rome")


@pytest.fixture
def api(db, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from operator_auth.dependencies import require_operator
    from voice import router as voice_router
    from voice.fake_provider import FakeVoiceProvider

    monkeypatch.setenv("STIMA_VOICE_ENABLED", "1")
    finto = FakeVoiceProvider()
    monkeypatch.setattr(voice_router, "get_provider", lambda: finto)
    app = FastAPI()
    app.include_router(voice_router.router)
    stato = {"ctx": None}
    app.dependency_overrides[require_operator] = lambda: stato["ctx"]
    client = TestClient(app)

    def come(chi, **kw):
        stato["ctx"] = ctx(db, chi, **kw)
        return client
    come.finto = finto
    return come


def detta(api, chi, testo, comandi, *, chiave=None, clarifications=(), **kw):
    api.finto.register(testo, {"commands": comandi, "clarifications": list(clarifications)})
    return api(chi, **kw).post("/api/voice/commands",
                               json={"client_command_id": str(chiave or uuid.uuid4()), "text": testo})


def passo(vista, n):
    return next(s for s in vista["steps"] if s["ordinal"] == n)


def codici(s):
    return {r["code"] for r in s["reasons"]}


def domani(ora: int, minuti: int = 0) -> datetime:
    return datetime.combine(datetime.now(ROMA).date() + timedelta(days=1), time(ora, minuti), tzinfo=ROMA)


# ---------------------------------------------------------------------------
# Abilitazione fail-closed
# ---------------------------------------------------------------------------

def test_e1_global_switch_off_refuses_everything(api, db, monkeypatch):
    monkeypatch.delenv("STIMA_VOICE_ENABLED")
    prima = q(db, "SELECT count(*) FROM voice_commands")[0][0]
    r = detta(api, "agent_a1", "Nota per Mario: richiamare", [])
    assert r.status_code == 403 and r.json()["code"] == "VOICE_GLOBALLY_DISABLED"
    for metodo, percorso in (("get", ""), ("post", "/execute"), ("post", "/undo"), ("post", "/confirm")):
        r = getattr(api("agent_a1"), metodo)(f"/api/voice/commands/1{percorso}",
                                            **({"json": {"all": True}} if percorso == "/confirm" else {}))
        assert r.json()["code"] == "VOICE_GLOBALLY_DISABLED", percorso
    assert api.finto.calls == []
    assert q(db, "SELECT count(*) FROM voice_commands")[0][0] == prima


def test_e2_agency_disabled_and_platform_admin_are_refused(api, db):
    q(db, "UPDATE voice_agency_settings SET enabled = FALSE WHERE agency_id = 2")
    try:
        r = detta(api, "agent_b", "Nota per Sergio: richiamare", [])
        assert r.status_code == 403 and r.json()["code"] == "VOICE_DISABLED"
    finally:
        q(db, "UPDATE voice_agency_settings SET enabled = TRUE WHERE agency_id = 2")
    r = detta(api, "owner_a", "Nota per Mario: richiamare", [], ruolo=None, platform=True)
    assert r.status_code == 403 and r.json()["code"] == "VOICE_PLATFORM_ADMIN_BLOCKED"
    assert api.finto.calls == []


def test_e3_provider_not_configured_is_503_and_creates_nothing(api, db, monkeypatch):
    from voice import provider as voice_provider
    from voice import router as voice_router
    for k in ("STIMA_VOICE_AI_URL", "STIMA_VOICE_AI_MODEL", "STIMA_VOICE_AI_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(voice_router, "get_provider", voice_provider.from_env)
    prima = q(db, "SELECT count(*) FROM voice_commands")[0][0]
    r = api("agent_a1").post("/api/voice/commands", json={"client_command_id": str(uuid.uuid4()), "text": "Nota"})
    assert r.status_code == 503 and r.json()["code"] == "VOICE_PROVIDER_NOT_CONFIGURED"
    assert q(db, "SELECT count(*) FROM voice_commands")[0][0] == prima


def test_e5_without_migration_098_voice_is_off_not_broken(api, db):
    """Come su un database senza la 098 (oggi TEST e PROD): STIMA Voice
    risponde «spento», non un errore. Solo nel database di prova locale."""
    q(db, "ALTER TABLE voice_agency_settings RENAME TO _voice_settings_nascosta")
    try:
        r = detta(api, "agent_a1", "Nota per Mario: senza 098", [])
        assert r.status_code == 403 and r.json()["code"] == "VOICE_DISABLED"
        assert api.finto.calls == []
    finally:
        q(db, "ALTER TABLE _voice_settings_nascosta RENAME TO voice_agency_settings")


def test_e4_router_is_not_mounted_in_main():
    from pathlib import Path
    testo = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    # SENTINELLA AGGIORNATA DA STIMA VOICE FASE 7: il router e' montato UNA volta, come gli altri
    # domini (mount `require_authenticated_operator`, scope da `require_operator` su ogni rotta), e
    # `main.py` non contiene altro di Voice: nessuna logica, nessuna variabile, nessun interruttore.
    righe = [r.strip() for r in testo.splitlines() if "voice" in r.lower() and not r.strip().startswith("#")]
    assert righe == ["from voice.router import router as voice_router",
                     "app.include_router(voice_router, dependencies=[Depends(require_authenticated_operator)])"], righe


# ---------------------------------------------------------------------------
# Scenario A - «Fabio vuole vendere un trilocale in via Duca d'Aosta»
# ---------------------------------------------------------------------------

SCENARIO_A = "Fabio vuole vendere un trilocale in via Duca d'Aosta"
COMANDI_A = [
    {"intent": "create_unit", "quote": "un trilocale in via Duca d'Aosta", "rooms": 3, "address": "Via Duca d'Aosta",
     "owner": {"first_name": "Fabio"}},
    {"intent": "activate_seller", "quote": "vuole vendere", "contact": {"first_name": "Fabio"},
     "property": {"step": 1}},
]


def test_a1_scenario_a_asks_then_executes_through_the_executor(api, db):
    prima = conta(db)
    r = detta(api, "agent_a2", SCENARIO_A, COMANDI_A)
    assert r.status_code == 200, r.text
    vista = r.json()
    assert [s["intent"] for s in vista["steps"]] == ["create_unit", "link_owner", "activate_seller"]
    collegamento = passo(vista, 2)
    assert collegamento["needs"] == "needs_confirmation"
    assert "owner_link_confirmation_required" in codici(collegamento)
    assert [c["id"] for c in collegamento["candidates"]["contact"]] == [db["ids"]["fabio_verdi"]]
    assert collegamento["candidates"]["contact"][0]["label"] == "Fabio Verdi"
    assert conta(db) == prima                                          # niente prima delle conferme
    cid = vista["command_id"]
    vista = api("agent_a2").post(f"/api/voice/commands/{cid}/confirm", json={"all": True}).json()
    assert all(s["needs"] is None for s in vista["steps"])
    vista = api("agent_a2").post(f"/api/voice/commands/{cid}/execute").json()
    assert vista["status"] == "completed", vista
    unita = passo(vista, 1)["result"]["id"]
    assert q(db, "SELECT record_kind, assigned_agent_id FROM properties WHERE id = %s", (unita,))[0] == \
        ["crm", db["ids"]["agent_a2"]]
    assert passo(vista, 3)["result"]["origin"] == "created"
    assert passo(vista, 2)["undo"] == "not_undoable"                   # D-A
    dopo = conta(db)
    assert dopo["properties"] == prima["properties"] + 1 and dopo["contacts"] == prima["contacts"]


# ---------------------------------------------------------------------------
# Scenario B - «Censisci una palazzina con 12 appartamenti»
# ---------------------------------------------------------------------------

def test_b1_building_needs_the_address_and_writes_nothing(api, db):
    prima = conta(db)
    vista = detta(api, "agent_a1", "Censisci una palazzina con 12 appartamenti",
                  [{"intent": "create_building", "quote": "una palazzina con 12 appartamenti",
                    "units_declared": 12}]).json()
    s = passo(vista, 1)
    assert s["needs"] == "needs_answer" and "building_location_missing" in codici(s)
    assert api("agent_a1").post(f"/api/voice/commands/{vista['command_id']}/confirm",
                                json={"all": True}).json()["code"] == "VOICE_CONFIRMATION_NOT_ALLOWED"
    assert conta(db) == prima


def test_b2_twelve_units_are_never_created(api, db):
    prima = conta(db)
    unita = [{"intent": "create_unit", "quote": "appartamento", "record_kind": "census", "building": {"step": 1}}
             for _ in range(11)]
    vista = detta(api, "agent_a1", "Censisci una palazzina in via Marina 3 con 12 appartamenti",
                  [{"intent": "create_building", "quote": "palazzina in via Marina 3", "address": "Via Marina",
                    "civic_number": "3", "units_declared": 12}, *unita]).json()
    assert all(passo(vista, n)["decision"] == "blocked" for n in range(2, 13))
    assert conta(db) == prima


def test_b3_building_with_address_runs_automatically_with_declared_units(api, db):
    prima = conta(db)
    vista = detta(api, "agent_a1", "Censisci una palazzina in via Gramsci 8 a Tortoreto con 12 appartamenti",
                  [{"intent": "create_building", "quote": "palazzina in via Gramsci 8 a Tortoreto",
                    "address": "Via Gramsci", "civic_number": "8", "city": "Tortoreto", "units_declared": 12}]).json()
    assert vista["status"] == "completed", vista                    # chiaro, univoco, annullabile: automatico
    edificio = passo(vista, 1)["result"]["id"]
    assert q(db, "SELECT units_declared FROM buildings WHERE id = %s", (edificio,))[0][0] == 12
    assert conta(db)["properties"] == prima["properties"] and conta(db)["buildings"] == prima["buildings"] + 1


def test_b4_unsupported_municipality_is_asked_before_writing(api, db):
    """Limite 2 della Fase 4: ora si chiede nel piano, con la regola del
    censimento, e nulla si scrive."""
    prima = conta(db)
    vista = detta(api, "agent_a1", "Censisci una palazzina in via Mare 1 a Pineto",
                  [{"intent": "create_building", "quote": "palazzina in via Mare 1 a Pineto", "address": "Via Mare",
                    "civic_number": "1", "city": "Pineto"}]).json()
    s = passo(vista, 1)
    assert s["needs"] == "needs_answer" and "municipality_not_supported" in codici(s)
    assert not next(r for r in s["reasons"] if r["code"] == "municipality_not_supported")["confirmable"]
    api("agent_a1").post(f"/api/voice/commands/{vista['command_id']}/execute")
    assert conta(db) == prima


# ---------------------------------------------------------------------------
# Scenario C - «Domani alle 15 appuntamento con Fabio»
# ---------------------------------------------------------------------------

SCENARIO_C = "Domani alle 15 appuntamento con Fabio"
COMANDI_C = [{"intent": "create_appointment", "quote": SCENARIO_C,
              "when": {"date_text": "domani", "time_text": "alle 15"}, "contact": {"first_name": "Fabio"}}]


def test_c1_appointment_for_the_speaker_rome_60_minutes_other(api, db):
    vista = detta(api, "agent_a2", SCENARIO_C, COMANDI_C).json()
    s = passo(vista, 1)
    assert s["needs"] == "needs_confirmation" and "contact_confirmation_required" in codici(s)
    assert s["start_at"] == domani(15).isoformat()
    cid = vista["command_id"]
    api("agent_a2").post(f"/api/voice/commands/{cid}/confirm", json={"steps": [{"ordinal": 1}]})
    vista = api("agent_a2").post(f"/api/voice/commands/{cid}/execute").json()
    assert vista["status"] == "completed", vista
    riga = q(db, "SELECT appointment_type, start_at, end_at, assigned_user_id, contact_id, timezone "
                 "FROM appointments WHERE id = %s", (passo(vista, 1)["result"]["id"],))[0]
    assert riga[0] == "other" and riga[1] == domani(15) and riga[2] - riga[1] == timedelta(minutes=60)
    assert riga[3] == db["ids"]["agent_a2"] and riga[4] == db["ids"]["fabio_verdi"] and riga[5] == "Europe/Rome"


def test_c2_agenda_conflict_is_a_question_never_forced(api, db):
    from appointments import service as agenda
    from appointments.schemas import AppointmentCreateBody
    agenda.create_appointment_idempotent(ctx(db, "agent_a1"), AppointmentCreateBody(
        appointment_type="call", start_at=domani(14, 30).isoformat(), end_at=domani(15, 30).isoformat(),
        assigned_user_id=db["ids"]["agent_a1"], client_request_id=str(uuid.uuid4())))
    q(db, "UPDATE contacts SET assigned_agent_id = %s WHERE id = %s", (db["ids"]["agent_a1"], db["ids"]["fabio_bianchi"]))
    try:
        prima = conta(db)
        vista = detta(api, "agent_a1", SCENARIO_C, COMANDI_C, chiave=uuid.uuid4()).json()
        s = passo(vista, 1)
        assert s["needs"] == "needs_answer" and "agenda_conflict" in codici(s)
        api("agent_a1").post(f"/api/voice/commands/{vista['command_id']}/execute")
        assert conta(db) == prima
    finally:
        q(db, "UPDATE contacts SET assigned_agent_id = NULL WHERE id = %s", (db["ids"]["fabio_bianchi"],))


# ---------------------------------------------------------------------------
# Idempotenza, retry, concorrenza
# ---------------------------------------------------------------------------

NOTA = "Nota per Mario 333 1111111: cucina da rifare"
COMANDI_NOTA = [{"intent": "add_note", "quote": "cucina da rifare", "text": "Cucina da rifare",
                 "contact": {"phone": "333 1111111"}}]


def test_i1_same_key_same_text_is_one_command_and_one_interpretation(api, db):
    chiave = uuid.uuid4()
    primo = detta(api, "agent_a1", NOTA, COMANDI_NOTA, chiave=chiave).json()
    assert primo["status"] == "completed"                           # nota chiara: automatica
    prima, chiamate = conta(db), len(api.finto.calls)
    for _ in range(5):
        r = api("agent_a1").post("/api/voice/commands", json={"client_command_id": str(chiave), "text": NOTA})
        assert r.status_code == 200 and r.json()["command_id"] == primo["command_id"]
    assert len(api.finto.calls) == chiamate and conta(db) == prima
    r = api("agent_a1").post("/api/voice/commands", json={"client_command_id": str(chiave), "text": "altro testo"})
    assert r.status_code == 409 and r.json()["code"] == "VOICE_IDEMPOTENCY_KEY_REUSED"


def test_i2_concurrent_requests_interpret_once(api, db):
    chiave, testo = uuid.uuid4(), "Nota per Mario 333 1111111: concorrente"
    api.finto.register(testo, {"commands": [{**COMANDI_NOTA[0], "text": "Concorrente"}], "clarifications": []})
    barriera, esiti = threading.Barrier(3), []
    client = api("agent_a1")

    def invia():
        barriera.wait()
        r = client.post("/api/voice/commands", json={"client_command_id": str(chiave), "text": testo})
        esiti.append((r.status_code, r.json().get("command_id") or r.json().get("code")))
    fili = [threading.Thread(target=invia) for _ in range(3)]
    for f in fili:
        f.start()
    for f in fili:
        f.join(60)
    assert len([c for c in api.finto.calls if c[1] == testo]) == 1
    assert q(db, "SELECT count(*) FROM activities WHERE description = 'Concorrente'")[0][0] == 1
    assert {s for s, _ in esiti} <= {200, 409}


def test_i3_provider_failure_leaves_no_effect_and_is_retryable(api, db):
    chiave, testo = uuid.uuid4(), "Nota per Mario 333 1111111: dopo il guasto"
    api.finto.register(testo, {"commands": [{**COMANDI_NOTA[0], "text": "Dopo il guasto"}], "clarifications": []})
    api.finto.fail_next = True
    prima = conta(db)
    r = api("agent_a1").post("/api/voice/commands", json={"client_command_id": str(chiave), "text": testo})
    assert r.status_code == 503 and r.json()["code"] == "VOICE_PROVIDER_UNAVAILABLE"
    assert conta(db) == prima
    r = api("agent_a1").post("/api/voice/commands", json={"client_command_id": str(chiave), "text": testo})
    assert r.status_code == 200 and r.json()["status"] == "completed"


@pytest.mark.parametrize("uscita", ["inventato", "con_id"])
def test_i4_malformed_model_output_executes_nothing(api, db, uscita):
    testo = f"Nota per Mario: output {uscita}"
    if uscita == "inventato":
        api.finto.malformed_next = True
        api.finto.register(testo, {"commands": [], "clarifications": []})
    else:
        api.finto.register(testo, {"commands": [{**COMANDI_NOTA[0], "contact_id": db["ids"]["mario"]}],
                                   "clarifications": []})
    prima = conta(db)
    r = api("agent_a1").post("/api/voice/commands", json={"client_command_id": str(uuid.uuid4()), "text": testo})
    assert r.status_code == 502 and r.json()["code"] == "VOICE_MODEL_OUTPUT_INVALID"
    assert testo not in r.text
    assert conta(db) == prima


# ---------------------------------------------------------------------------
# Sicurezza: isolamento, corpi chiusi, scelte
# ---------------------------------------------------------------------------

def test_s1_other_agents_and_agencies_cannot_see_or_act(api, db):
    vista = detta(api, "agent_a1", "Nota per Mario 333 1111111: privata", [{**COMANDI_NOTA[0], "text": "Privata"}]).json()
    cid = vista["command_id"]
    for altro in ("agent_a2", "owner_a", "agent_b", "owner_b"):
        for metodo, percorso in (("get", ""), ("post", "/execute"), ("post", "/undo"), ("get", "/undo")):
            r = getattr(api(altro), metodo)(f"/api/voice/commands/{cid}{percorso}")
            assert r.status_code == 404, (altro, percorso, r.text)
    # lo stesso telefono non esiste nell'agenzia 2
    vista_b = detta(api, "agent_b", "Nota per Mario 333 1111111", COMANDI_NOTA).json()
    assert "reference_not_found" in codici(passo(vista_b, 1))


def test_s2_bodies_are_closed_and_choices_are_validated(api, db):
    r = api("agent_a1").post("/api/voice/commands", json={"client_command_id": str(uuid.uuid4()), "text": "x",
                                                          "agency_id": 2})
    assert r.status_code == 422
    vista = detta(api, "owner_a", SCENARIO_C, COMANDI_C).json()            # owner: due Fabio visibili
    cid = vista["command_id"]
    assert "reference_ambiguous" in codici(passo(vista, 1))
    r = api("owner_a").post(f"/api/voice/commands/{cid}/confirm", json={"steps": [{"ordinal": 1}]})
    assert r.status_code == 422 and r.json()["code"] == "VOICE_CHOICE_REQUIRED"
    r = api("owner_a").post(f"/api/voice/commands/{cid}/confirm",
                            json={"steps": [{"ordinal": 1, "choices": {"contact": db["ids"]["fabio_b"]}}]})
    assert r.status_code == 422 and r.json()["code"] == "VOICE_CHOICE_INVALID"        # altra agenzia
    r = api("owner_a").post(f"/api/voice/commands/{cid}/confirm",
                            json={"steps": [{"ordinal": 1, "choices": {"contact": db["ids"]["fabio_bianchi"]}}]})
    assert r.status_code == 200


def test_s3_responses_never_carry_the_transcript(api, db):
    vista = detta(api, "agent_a1", "Nota per Mario 333 1111111: riservata", [{**COMANDI_NOTA[0], "text": "Riservata"}])
    assert "transcript" not in vista.text
    assert "Nota per Mario 333 1111111: riservata" not in vista.text


# ---------------------------------------------------------------------------
# Limite giornaliero
# ---------------------------------------------------------------------------

def test_l1_daily_limit_counts_new_commands_only(api, db):
    usati = q(db, "SELECT count(*) FROM voice_commands WHERE agency_id = 2")[0][0]
    q(db, "UPDATE voice_agency_settings SET max_commands_per_day = %s WHERE agency_id = 2", (usati + 1,))
    try:
        chiave = uuid.uuid4()
        assert detta(api, "agent_b", "Nota per Sergio 333 9999999: uno",
                     [{"intent": "add_note", "quote": "uno", "text": "Uno", "contact": {"phone": "333 9999999"}}],
                     chiave=chiave).status_code == 200
        r = detta(api, "agent_b", "Nota per Sergio 333 9999999: due",
                  [{"intent": "add_note", "quote": "due", "text": "Due", "contact": {"phone": "333 9999999"}}])
        assert r.status_code == 429 and r.json()["code"] == "VOICE_DAILY_LIMIT"
        r = api("agent_b").post("/api/voice/commands", json={"client_command_id": str(chiave),
                                                             "text": "Nota per Sergio 333 9999999: uno"})
        assert r.status_code == 200                                  # la ripetizione non conta
    finally:
        q(db, "UPDATE voice_agency_settings SET max_commands_per_day = 50 WHERE agency_id = 2")


# ---------------------------------------------------------------------------
# Annullamento, annullamento del comando, riconciliazione
# ---------------------------------------------------------------------------

def test_u1_undo_via_api(api, db):
    vista = detta(api, "agent_a1", "Nota per Mario 333 1111111: da annullare",
                  [{**COMANDI_NOTA[0], "text": "Da annullare via API"}]).json()
    cid = vista["command_id"]
    assert api("agent_a1").get(f"/api/voice/commands/{cid}/undo").json()[0]["status"] == "undoable"
    assert api("agent_a1").post(f"/api/voice/commands/{cid}/steps/1/undo").json()["outcome"] == "undone"
    assert q(db, "SELECT metadata->>'mistake' FROM activities WHERE description = 'Da annullare via API'")[0][0] == "true"
    assert api("agent_a1").get(f"/api/voice/commands/{cid}").json()["steps"][0]["undo"] == "undone"


def test_u2_cancel_a_waiting_command(api, db):
    vista = detta(api, "agent_a2", SCENARIO_C, COMANDI_C, chiave=uuid.uuid4()).json()
    vista = api("agent_a2").post(f"/api/voice/commands/{vista['command_id']}/cancel").json()
    assert vista["status"] == "cancelled" and vista["steps"][0]["state"] == "skipped"


def test_r1_reconciled_partial_is_declared_not_hidden(api, db, monkeypatch):
    """Limite 1 della Fase 4: il comando resta `partial` anche con tutti i
    passi conclusi; la risposta lo dice."""
    from core import service as core_service

    class Crash(BaseException):
        pass
    originale = core_service.create_contact

    def crash(*a, **k):
        originale(*a, **k)
        raise Crash()
    testo = "Nuovo contatto Rita Valli 333 5151515"
    api.finto.register(testo, {"commands": [{"intent": "create_contact", "quote": testo, "first_name": "Rita",
                                             "last_name": "Valli", "phone": "333 5151515"}], "clarifications": []})
    chiave = uuid.uuid4()
    monkeypatch.setattr(core_service, "create_contact", crash)
    with pytest.raises(Crash):
        api("agent_a1").post("/api/voice/commands", json={"client_command_id": str(chiave), "text": testo})
    monkeypatch.undo()
    monkeypatch.setenv("STIMA_VOICE_ENABLED", "1")
    cid = q(db, "SELECT id FROM voice_commands WHERE client_command_id = %s", (str(chiave),))[0][0]
    vista = api("agent_a1").post(f"/api/voice/commands/{cid}/execute").json()
    assert vista["steps"][0]["state"] == "indeterminate"
    analisi = api("agent_a1").get(f"/api/voice/commands/{cid}/steps/1/reconcile").json()
    assert analisi["outcome"] == "not_provable" and len(analisi["candidates"]) == 1
    vista = api("agent_a1").post(f"/api/voice/commands/{cid}/steps/1/reconcile",
                                 json={"outcome": "present", "record_id": analisi["candidates"][0]["id"]}).json()
    assert vista["status"] == "partial" and vista["all_steps_concluded"] is True
    assert vista["notice"] and "riconciliazione" in vista["notice"]
    r = api("agent_a1").get(f"/api/voice/commands/{cid}/steps/1/reconcile")
    assert r.status_code == 409 and r.json()["code"] == "VOICE_NOT_INDETERMINATE"
