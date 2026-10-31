"""BLOCCO-4D - prove offline del motore delle risposte automatiche.

Decisioni, doppioni, concorrenza, agenzie, pausa e arresto generale sono
provati su PostgreSQL vero in `test_blocco_4d_auto_replies_postgres.py`. Qui:
spento per impostazione predefinita, migrazione, confini del codice, interfaccia.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from auto_replies import enums, service

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "static" / "os_shell" / "assets"


@pytest.mark.parametrize("value", [None, "", "off", "send", "meta", "on", "true", "1", "SIMULATE", " simulate x"])
def test_the_engine_is_stopped_unless_explicitly_simulating(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(enums.GLOBAL_ENV, raising=False)
    else:
        monkeypatch.setenv(enums.GLOBAL_ENV, value)
    assert service.global_enabled() is False


def test_only_simulate_starts_it(monkeypatch):
    monkeypatch.setenv(enums.GLOBAL_ENV, " simulate ")
    assert service.global_enabled() is True
    assert enums.MODES == ("off", "simulate")                         # nessun modo d'invio


def test_migration_119_is_valid_additive_and_simulation_only():
    sys.path.insert(0, str(ROOT / "scripts"))
    import p26_migrate as runner

    tutte = runner.discover_migrations()
    runner.verify_contiguous(tutte)
    last = tutte[-1]
    assert last.version == "119_whatsapp_auto_reply_simulation"
    assert last.down_available and runner.validate_migration(last) == []
    up = (ROOT / "migrations" / "119_whatsapp_auto_reply_simulation.sql").read_text(encoding="utf-8")
    executable = "\n".join(r for r in up.splitlines() if not r.lstrip().startswith("--"))
    assert not re.search(r"\b(ALTER TABLE|DROP|DELETE FROM|UPDATE \w+ SET|INSERT INTO)\b", executable)
    assert "UNIQUE (inbound_message_id, rules_version)" in executable
    assert "CHECK (mode = 'simulate')" in executable and "CHECK (mode IN ('off', 'simulate'))" in executable
    down = (ROOT / "migrations" / "119_whatsapp_auto_reply_simulation_down.sql").read_text(encoding="utf-8")
    down_exec = "\n".join(r for r in down.splitlines() if not r.lstrip().startswith("--"))
    assert "RAISE EXCEPTION" in down_exec and "DELETE FROM" not in down_exec and "TRUNCATE" not in down_exec
    assert "whatsapp_inbound_messages" not in down_exec


def test_the_engine_never_sends_queues_or_creates_tasks():
    code = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "auto_replies").glob("*.py"))
    stripped = re.sub(r'"""[\s\S]*?"""', "", code)
    for forbidden in ("enqueue", "claim_message", "claim_due", "transport", "requests", "httpx", "graph.facebook",
                      "send_reply", "create_task", "INSERT INTO tasks", "INSERT INTO communication", "openai",
                      "anthropic", "finalize_"):
        assert forbidden not in stripped, forbidden
    # opera come sistema dell'agenzia: nessun operatore inventato
    assert "SystemAgencyContext(agency_id=agency_id, origin=INBOUND_ORIGIN)" in stripped


def test_the_webhook_does_not_run_the_engine():
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    webhook = main[main.index("/webhook/whatsapp"):][:6000]
    assert "auto_replies" not in webhook and "tick(" not in webhook
    inbound = (ROOT / "inbound_messages" / "service.py").read_text(encoding="utf-8")
    assert "auto_replies" not in inbound


def test_route_is_mounted_behind_the_operator_session():
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    line = next(r for r in main.splitlines() if "auto_replies_router" in r and "include_router" in r)
    assert "require_authenticated_operator" in line


def test_ui_marks_every_decision_as_a_simulation_not_sent():
    js = (APP / "components" / "auto-reply.js").read_text(encoding="utf-8")
    view = (APP / "views" / "messaggi.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "d.label" in js and "data-auto-simulation" in js
    assert "(non inviato)" in js and "attività solo pianificata, non creata" in js
    assert "Metti in pausa" in js and "Riprendi automazione" in js and "arresto generale" in js
    assert "/api/message-replies" not in js and "/tick" not in js                 # l'interfaccia non fa girare il motore
    assert "renderDecision(decisions.get(m.id))" in view and "renderAutoPanel(auto, sender" in view
    assert enums.SIMULATION_LABEL == "Simulazione — non inviato"
