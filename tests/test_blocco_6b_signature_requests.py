"""BLOCCO-6B - prove offline della preparazione della firma elettronica."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from esign import providers

ROOT = Path(__file__).resolve().parents[1]


def test_only_the_simulated_provider_exists(monkeypatch):
    monkeypatch.delenv(providers.PROVIDER_ENV, raising=False)
    p = providers.current()
    assert p.name == "simulated" and p.is_simulation is True
    for value in ("real", "qualified", "signed", "SIMULATED"):
        monkeypatch.setenv(providers.PROVIDER_ENV, value)
        with pytest.raises(providers.ProviderNotConfigured):
            providers.current()


def test_the_simulated_adapter_is_local_and_recognizable():
    p = providers.SimulatedProvider()
    ref = p.create_request(document=b"%PDF", document_sha256="0" * 64, signer_name="X", signer_contact="x@x.invalid",
                           expires_at=None, idempotency_key="k").reference
    assert ref.startswith("SIM-") and ref == p.create_request(document=b"", document_sha256="", signer_name="",
                                                              signer_contact="", expires_at=None,
                                                              idempotency_key="k").reference
    code = (ROOT / "esign" / "providers.py").read_text(encoding="utf-8")
    for network in ("requests", "httpx", "urllib", "socket"):
        assert network not in code, network


def test_migration_122_forbids_a_signed_simulation():
    sys.path.insert(0, str(ROOT / "scripts"))
    import p26_migrate as runner

    m = next(x for x in runner.discover_migrations() if x.version == "122_signature_requests")
    assert m.down_available and runner.validate_migration(m) == []
    up = (ROOT / "migrations" / "122_signature_requests.sql").read_text(encoding="utf-8")
    assert "CHECK (provider <> 'simulated' OR status <> 'signed')" in up
    assert "append-only" in up and "is closed" in up


def test_ui_marks_every_request_as_a_simulation():
    js = (ROOT / "static" / "os_shell" / "assets" / "components" / "visit-sheets.js").read_text(encoding="utf-8")
    assert "SIMULAZIONE — nessuna firma valida" in js and "Richiedi firma (simulazione)" in js
    assert "nessuna firma valida viene prodotta" in js and "innerHTML" not in js
    assert re.search(r"r\.simulation \? \[\[", js)                                # esiti simulati solo se simulata
