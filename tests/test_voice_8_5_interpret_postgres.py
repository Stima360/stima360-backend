"""STIMA Voice V8-5 - `service.interpret` sul server vero: nessun comando nel
registro, nessuna scrittura, le stesse regole (risolutore con i permessi,
incarico per la visita, politica)."""
from __future__ import annotations

from datetime import datetime

import pytest

from tests.test_voice_2_resolver_postgres import DSN, ctx, db  # noqa: F401
from tests.test_voice_4_executor_postgres import abilita, q  # noqa: F401
from tests.test_voice_8_2_visits_postgres import MARIO, _incarichi, visita  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL per V8-5")
NOW = datetime.fromisoformat("2026-10-10T16:00:00+02:00")


@pytest.fixture(autouse=True)
def _acceso(monkeypatch):
    monkeypatch.setenv("STIMA_VOICE_ENABLED", "1")


def _finto(testo, comandi):
    from voice.fake_provider import FakeVoiceProvider
    p = FakeVoiceProvider()
    p.register(testo, {"commands": comandi, "clarifications": []})
    return p


def test_01_interpreta_senza_scrivere(db):
    from voice import service
    testo = "Mario Rossi vuole vedere casa in via Roma 10 domani alle 15"
    prima = (q(db, "SELECT count(*) FROM voice_commands")[0][0], q(db, "SELECT count(*) FROM appointments")[0][0])
    esito = service.interpret(ctx(db, "agent_a1"), testo, provider=_finto(testo, [visita({"address": "via Roma", "civic_number": "10"}, MARIO)]), now=NOW)
    assert (q(db, "SELECT count(*) FROM voice_commands")[0][0], q(db, "SELECT count(*) FROM appointments")[0][0]) == prima
    s = esito["steps"][0]
    assert s["appointment_type"] == "buyer_visit" and s["type_label"] == "Visita immobile" and s["needs_mandate"] is True
    assert s["contact"]["id"] == db["ids"]["mario"] and s["property"]["id"] == db["ids"]["roma10"]
    assert s["agent"] is None and s["actor"] == "Anna Neri" and s["questions"] == [] and s["blocked"] == []
    assert s["start_at"].startswith("2026-10-11T15:00:00") and s["duration_minutes"] == 60


def test_02_domande_e_blocchi_come_nel_comando(db):
    from voice import service
    testo = "Visita con Mario Rossi domani alle 15"
    s = service.interpret(ctx(db, "agent_a1"), testo, provider=_finto(testo, [visita(None, MARIO, quote=testo)]), now=NOW)["steps"][0]
    assert s["property"] is None and s["questions"] == ["Quale immobile deve visitare il cliente? Una visita si fissa solo su un incarico di vendita."]
    testo = "Mario Rossi vuole vedere casa in via Roma 12 domani alle 15"
    s = service.interpret(ctx(db, "agent_a1"), testo, provider=_finto(testo, [visita({"address": "via Roma", "civic_number": "12"}, MARIO, quote=testo)]), now=NOW)["steps"][0]
    assert s["decision"] == "blocked" and any("non ha un incarico" in m for m in s["blocked"])
    # un altro agente non vede Mario: il risolutore applica i suoi permessi anche qui
    s = service.interpret(ctx(db, "agent_a2"), testo, provider=_finto(testo, [visita({"address": "via Roma", "civic_number": "10"}, MARIO, quote=testo)]), now=NOW)["steps"][0]
    assert s["contact"] is None and s["contact_candidates"] == []
