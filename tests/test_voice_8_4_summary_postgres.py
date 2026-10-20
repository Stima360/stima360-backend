"""STIMA Voice V8-4 - la scheda riepilogativa dal server vero (PostgreSQL).

  01  la vista di un comando con una visita porta `summary`: tipo in italiano,
      cliente e incarico (le etichette dei candidati), data e ora, l'agente che
      parla, le note; dopo l'esecuzione il risultato porta `start_at`
  02  un agente nominato (con il permesso) compare come agente della scheda
"""
from __future__ import annotations

import pytest

from tests.test_voice_2_resolver_postgres import DSN, ctx, db  # noqa: F401
from tests.test_voice_4_executor_postgres import abilita, comando, conferma_tutto, esegui, q  # noqa: F401
from tests.test_voice_8_2_visits_postgres import MARIO, _incarichi, visita  # noqa: F401

pytestmark = pytest.mark.skipif(not DSN, reason="P29_TEST_DSN non impostata: nessun PostgreSQL per V8-4")


@pytest.fixture(autouse=True)
def _acceso(monkeypatch):
    monkeypatch.setenv("STIMA_VOICE_ENABLED", "1")


def test_01_la_scheda_dal_server(db):
    from voice import service
    v = {**visita({"address": "via Roma", "civic_number": "10"}, MARIO), "notes": "porta i documenti"}
    cid = comando(db, "agent_a1", v, transcript="Mario Rossi vuole vedere casa in via Roma 10 domani alle 15")
    vista = service.view(ctx(db, "agent_a1"), cid)
    s = vista["steps"][0]["summary"]
    assert s["type"] == "buyer_visit" and s["type_label"] == "Visita immobile" and s["needs_mandate"] is True
    assert s["contact"] == "Mario Rossi" and s["property"].startswith("Via Roma 10") and "VR10" not in s["property"]
    assert s["agent"] == "Anna Neri" and s["notes"] == "porta i documenti"
    assert s["start_at"].startswith("2026-10-11T15:00:00") and s["end_at"].startswith("2026-10-11T16:00:00")
    conferma_tutto(db, "agent_a1", cid)
    report = esegui(db, "agent_a1", cid)
    assert report["status"] == "completed", report
    vista = service.view(ctx(db, "agent_a1"), cid)
    r = vista["steps"][0]["result"]
    from datetime import datetime
    assert r["entity"] == "appointment"
    assert datetime.fromisoformat(r["start_at"]) == datetime.fromisoformat("2026-10-11T15:00:00+02:00")
    assert r["property_id"] == db["ids"]["roma10"] and r["contact_id"] == db["ids"]["mario"]
    # un passo che non e' un appuntamento non ha scheda
    nota = comando(db, "agent_a1", {"intent": "add_note", "quote": "nota", "text": "Richiamare", "contact": MARIO},
                   transcript="Nota per Mario Rossi: richiamare")
    assert service.view(ctx(db, "agent_a1"), nota)["steps"][0]["summary"] is None


def test_02_l_agente_nominato_con_il_permesso(db):
    from voice import service
    v = {**visita({"address": "via Roma", "civic_number": "10"}, MARIO), "agent_name": "Bruno Galli",
         "quote": "Mario Rossi vuole vedere casa in via Roma 10 domani alle 15 con Bruno Galli"}
    cid = comando(db, "owner_a", v, transcript="Mario Rossi vuole vedere casa in via Roma 10 domani alle 15 con Bruno Galli")
    s = service.view(ctx(db, "owner_a"), cid)["steps"][0]["summary"]
    assert s["agent"] == "Bruno Galli"
