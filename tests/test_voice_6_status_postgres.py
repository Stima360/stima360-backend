"""STIMA Voice Fase 6 - `GET /api/voice/status` su PostgreSQL vero.

L'interfaccia mostra STIMA Voice solo se questa risposta dice `enabled: true`.
Qui si prova che il si' arriva soltanto con TUTTI i livelli accesi e che il
microfono del browser resta spento senza `STIMA_VOICE_BROWSER_SPEECH=1`.
Il router gira su un'app FastAPI di test (`main.py` non lo monta).
Opt-in `P29_TEST_DSN`, SOLO database locale usa-e-getta; valori del
fornitore finti (nessuna chiamata: lo stato non interpella il modello).
"""
from __future__ import annotations

import pytest

from tests.test_voice_2_resolver_postgres import DSN, ctx, db  # noqa: F401  (fixture condivisa)
from tests.test_voice_4_executor_postgres import _voce_accesa, q  # noqa: F401
from tests.test_voice_5_api_postgres import api  # noqa: F401

pytestmark = pytest.mark.skipif(
    not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL su cui provare STIMA Voice Fase 6")

SPENTO = {"enabled": False, "speech_input": False}


@pytest.fixture
def fornitore(monkeypatch):
    """Configurazione del fornitore con valori FINTI: lo stato la legge soltanto."""
    monkeypatch.setenv("STIMA_VOICE_AI_URL", "https://fornitore.invalid/v1")
    monkeypatch.setenv("STIMA_VOICE_AI_MODEL", "modello-finto")
    monkeypatch.setenv("STIMA_VOICE_AI_KEY", "chiave-finta")
    monkeypatch.delenv("STIMA_VOICE_BROWSER_SPEECH", raising=False)


def stato(api, chi="agent_a1", **kw):
    r = api(chi, **kw).get("/api/voice/status")
    assert r.status_code == 200
    return r.json()


def test_st1_all_levels_on_gives_yes_but_no_browser_speech(api, db, fornitore, monkeypatch):  # noqa: F811
    prima = q(db, "SELECT count(*) FROM voice_commands")[0][0]
    assert stato(api) == {"enabled": True, "speech_input": False}
    monkeypatch.setenv("STIMA_VOICE_BROWSER_SPEECH", "1")
    assert stato(api) == {"enabled": True, "speech_input": True}
    for valore in ("0", "true", "yes", ""):
        monkeypatch.setenv("STIMA_VOICE_BROWSER_SPEECH", valore)
        assert stato(api) == {"enabled": True, "speech_input": False}, valore
    # lo stato non crea comandi e non interpella il modello
    assert q(db, "SELECT count(*) FROM voice_commands")[0][0] == prima
    assert api.finto.calls == []


def test_st2_any_level_off_is_a_plain_no(api, db, fornitore, monkeypatch):  # noqa: F811
    monkeypatch.setenv("STIMA_VOICE_BROWSER_SPEECH", "1")
    # interruttore globale
    monkeypatch.setenv("STIMA_VOICE_ENABLED", "0")
    assert stato(api) == SPENTO
    monkeypatch.setenv("STIMA_VOICE_ENABLED", "1")
    # agenzia
    q(db, "UPDATE voice_agency_settings SET enabled = FALSE WHERE agency_id = 2")
    try:
        assert stato(api, "agent_b") == SPENTO
    finally:
        q(db, "UPDATE voice_agency_settings SET enabled = TRUE WHERE agency_id = 2")
    # amministratore della piattaforma (D-E)
    assert stato(api, "owner_a", ruolo=None, platform=True) == SPENTO
    # fornitore non configurato o senza HTTPS: un'interfaccia che porta a un 503 non si mostra
    monkeypatch.setenv("STIMA_VOICE_AI_URL", "http://fornitore.invalid/v1")
    assert stato(api) == SPENTO
    monkeypatch.delenv("STIMA_VOICE_AI_KEY")
    assert stato(api) == SPENTO


def test_st3_without_migration_098_it_is_off_not_an_error(api, db, fornitore):  # noqa: F811
    q(db, "ALTER TABLE voice_agency_settings RENAME TO _voice_settings_nascosta")
    try:
        assert stato(api) == SPENTO
    finally:
        q(db, "ALTER TABLE _voice_settings_nascosta RENAME TO voice_agency_settings")


def test_st4_response_carries_two_booleans_and_nothing_else(api, db, fornitore):  # noqa: F811
    corpo = stato(api)
    assert set(corpo) == {"enabled", "speech_input"} and all(isinstance(v, bool) for v in corpo.values())
