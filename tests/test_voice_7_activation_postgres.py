"""STIMA Voice Fase 7 - attivazione controllata: elenco pilota, accesso senza
sessione, spegnimento immediato.

Il router gira su un'app FastAPI di test montato COME lo monterebbe `main.py`
(stessa dipendenza di mount degli altri domini, `require_authenticated_operator`):
`main.py` non lo monta ancora. Opt-in `P29_TEST_DSN`, SOLO database locale
usa-e-getta; fornitore FINTO; valori del fornitore finti.

Cosa prova:
  * `STIMA_VOICE_PILOT_USER_IDS`: solo gli operatori elencati; valore non
    valido = nessuno; assente = vale solo l'agenzia (come prima);
  * nessuna rotta risponde senza sessione (401) e nessuna scrive;
  * lo spegnimento (interruttore globale o agenzia) vale dalla richiesta
    successiva, anche a meta' di un comando: niente esecuzione, niente annulla.
"""
from __future__ import annotations

import uuid

import pytest

from tests.test_voice_2_resolver_postgres import DSN, ctx, db  # noqa: F401  (fixture condivisa)
from tests.test_voice_4_executor_postgres import _voce_accesa, q  # noqa: F401
from tests.test_voice_5_api_postgres import COMANDI_NOTA, api, detta  # noqa: F401

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare STIMA Voice Fase 7")

SPENTO = {"enabled": False, "speech_input": False}
ACCESO = {"enabled": True, "speech_input": False}


@pytest.fixture
def fornitore(monkeypatch):
    monkeypatch.setenv("STIMA_VOICE_AI_URL", "https://fornitore.invalid/v1")
    monkeypatch.setenv("STIMA_VOICE_AI_MODEL", "modello-finto")
    monkeypatch.setenv("STIMA_VOICE_AI_KEY", "chiave-finta")
    monkeypatch.delenv("STIMA_VOICE_BROWSER_SPEECH", raising=False)
    monkeypatch.delenv("STIMA_VOICE_PILOT_USER_IDS", raising=False)


def conta(db):
    return q(db, "SELECT count(*) FROM voice_commands")[0][0]


# ---------------------------------------------------------------------------
# Elenco pilota
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("valore, atteso", [
    ("", None), ("   ", None), ("7", frozenset({7})), (" 7, 9 ", frozenset({7, 9})),
    ("7;9", frozenset()), ("abc", frozenset()), ("7,", frozenset()), ("0", frozenset()), ("-3", frozenset()),
])
def test_p0_pilot_list_parsing_is_fail_closed(valore, atteso):
    from voice import service
    assert service.pilot_user_ids({"STIMA_VOICE_PILOT_USER_IDS": valore}) == atteso
    assert service.pilot_user_ids({}) is None


def test_p1_only_pilot_operators_get_voice(api, db, fornitore, monkeypatch):  # noqa: F811
    a1, a2 = db["ids"]["agent_a1"], db["ids"]["agent_a2"]
    monkeypatch.setenv("STIMA_VOICE_PILOT_USER_IDS", str(a1))
    assert api("agent_a1").get("/api/voice/status").json() == ACCESO
    assert api("agent_a2").get("/api/voice/status").json() == SPENTO
    assert api("owner_a").get("/api/voice/status").json() == SPENTO       # neanche il titolare
    prima = conta(db)
    r = detta(api, "agent_a2", "Nota per Mario: fuori dal pilota", [])
    assert r.status_code == 403 and r.json()["code"] == "VOICE_PILOT_USER_REQUIRED"
    assert conta(db) == prima and api.finto.calls == []
    # un comando del pilota non diventa raggiungibile a un collega fuori elenco
    r = detta(api, "agent_a1", "Nota per Mario 333 1111111: pilota", [{**COMANDI_NOTA[0], "text": "Pilota"}])
    assert r.status_code == 200, r.text
    cid = r.json()["command_id"]
    for metodo, percorso in (("get", ""), ("post", "/execute"), ("post", "/undo"), ("post", "/cancel")):
        r = getattr(api("agent_a2"), metodo)(f"/api/voice/commands/{cid}{percorso}")
        assert r.status_code == 403 and r.json()["code"] == "VOICE_PILOT_USER_REQUIRED", percorso
    assert a2 != a1


def test_p2_invalid_pilot_list_closes_voice_for_everyone(api, db, fornitore, monkeypatch):  # noqa: F811
    monkeypatch.setenv("STIMA_VOICE_PILOT_USER_IDS", f"{db['ids']['agent_a1']};")
    for chi in ("agent_a1", "agent_a2", "owner_a", "agent_b"):
        assert api(chi).get("/api/voice/status").json() == SPENTO, chi
    prima = conta(db)
    r = detta(api, "agent_a1", "Nota per Mario: elenco rotto", [])
    assert r.status_code == 403 and r.json()["code"] == "VOICE_PILOT_USER_REQUIRED"
    assert conta(db) == prima and api.finto.calls == []


def test_p3_pilot_list_never_overrides_agency_or_platform_rules(api, db, fornitore, monkeypatch):  # noqa: F811
    ids = db["ids"]
    monkeypatch.setenv("STIMA_VOICE_PILOT_USER_IDS", f"{ids['agent_a1']},{ids['agent_b']},{ids['owner_a']}")
    q(db, "UPDATE voice_agency_settings SET enabled = FALSE WHERE agency_id = 2")
    try:
        assert api("agent_b").get("/api/voice/status").json() == SPENTO       # agenzia spenta resta spenta
    finally:
        q(db, "UPDATE voice_agency_settings SET enabled = TRUE WHERE agency_id = 2")
    # un platform admin in elenco resta bloccato (D-E)
    assert api("owner_a", ruolo=None, platform=True).get("/api/voice/status").json() == SPENTO
    r = detta(api, "owner_a", "Nota per Mario: platform", [], ruolo=None, platform=True)
    assert r.status_code == 403 and r.json()["code"] == "VOICE_PLATFORM_ADMIN_BLOCKED"


# ---------------------------------------------------------------------------
# Senza sessione: nessuna rotta, nessuna scrittura
# ---------------------------------------------------------------------------

ROTTE = [("get", "/api/voice/status"), ("post", "/api/voice/commands"), ("get", "/api/voice/commands/1"),
         ("post", "/api/voice/commands/1/confirm"), ("post", "/api/voice/commands/1/execute"),
         ("post", "/api/voice/commands/1/cancel"), ("post", "/api/voice/commands/1/steps/1/retry"),
         ("get", "/api/voice/commands/1/undo"), ("post", "/api/voice/commands/1/undo"),
         ("post", "/api/voice/commands/1/steps/1/undo"), ("get", "/api/voice/commands/1/steps/1/reconcile"),
         ("post", "/api/voice/commands/1/steps/1/reconcile"), ("post", "/api/voice/commands/1/reply")]


def test_a1_every_route_requires_a_session_when_mounted_like_main(db, fornitore, monkeypatch):  # noqa: F811
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from operator_auth.dependencies import require_authenticated_operator
    from voice import router as voice_router

    import psycopg2

    from operator_auth import database as auth_database
    monkeypatch.setattr(auth_database, "get_connection", lambda: psycopg2.connect(db["dsn"]))
    monkeypatch.setenv("STIMA_VOICE_ENABLED", "1")
    app = FastAPI()
    app.include_router(voice_router.router, dependencies=[Depends(require_authenticated_operator)])
    client = TestClient(app)
    montate = sorted((m.lower(), r.path.replace("{command_id}", "1").replace("{ordinal}", "1"))
                     for r in voice_router.router.routes for m in r.methods)
    assert montate == sorted(ROTTE)                       # l'elenco qui sotto e' completo
    prima = conta(db)
    for metodo, percorso in ROTTE:
        corpo = {"json": {"client_command_id": str(uuid.uuid4()), "text": "Nota"}} if metodo == "post" else {}
        r = getattr(client, metodo)(percorso, **corpo)
        assert r.status_code == 401, (percorso, r.status_code)
        # un cookie inventato non e' una sessione
        r = getattr(client, metodo)(percorso, cookies={"stima360_operator_session": "inventato"}, **corpo)
        assert r.status_code == 401, (percorso, r.status_code)
    assert conta(db) == prima


# ---------------------------------------------------------------------------
# Spegnimento immediato, anche a meta' di un comando
# ---------------------------------------------------------------------------

def test_k1_kill_switch_stops_a_command_in_flight(api, db, fornitore, monkeypatch):  # noqa: F811
    testo = "Nuovo contatto Rita Spegni 333 7070707"
    r = detta(api, "agent_a1", testo, [{"intent": "create_contact", "quote": testo, "first_name": "Rita",
                                         "last_name": "Spegni", "phone": "333 7070707"}])
    assert r.status_code == 200, r.text
    cid = r.json()["command_id"]
    contatti = q(db, "SELECT count(*) FROM contacts WHERE last_name = 'Spegni'")[0][0]
    # interruttore globale: dalla richiesta successiva tutto e' rifiutato
    monkeypatch.setenv("STIMA_VOICE_ENABLED", "0")
    for metodo, percorso in (("get", ""), ("post", "/execute"), ("post", "/undo"), ("post", "/confirm")):
        r = getattr(api("agent_a1"), metodo)(f"/api/voice/commands/{cid}{percorso}",
                                             **({"json": {"all": True}} if percorso == "/confirm" else {}))
        assert r.status_code == 403 and r.json()["code"] == "VOICE_GLOBALLY_DISABLED", percorso
    assert api("agent_a1").get("/api/voice/status").json() == SPENTO
    monkeypatch.setenv("STIMA_VOICE_ENABLED", "1")
    # spegnimento dell'agenzia: stesso effetto, senza riavvio
    q(db, "UPDATE voice_agency_settings SET enabled = FALSE WHERE agency_id = 1")
    try:
        r = api("agent_a1").post(f"/api/voice/commands/{cid}/execute")
        assert r.status_code == 403 and r.json()["code"] == "VOICE_DISABLED"
        assert api("agent_a1").get("/api/voice/status").json() == SPENTO
    finally:
        q(db, "UPDATE voice_agency_settings SET enabled = TRUE WHERE agency_id = 1")
    # nessuna scrittura CRM durante lo spegnimento
    assert q(db, "SELECT count(*) FROM contacts WHERE last_name = 'Spegni'")[0][0] == contatti
