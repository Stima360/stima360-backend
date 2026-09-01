from __future__ import annotations

import importlib


def _module():
    return importlib.import_module("run_followup_p18d_cron")


def test_runner_returns_zero_on_completed(monkeypatch):
    mod = _module()
    monkeypatch.setattr(
        mod,
        "load_config",
        lambda: mod.Config("https://x.test", "u", "p", 10, (1.0, 1.0)),
    )
    monkeypatch.setattr(
        mod,
        "run_once",
        lambda _cfg: {
            "status": "completed",
            "requested_limit": 10,
            "processed": 1,
            "escalated": 1,
            "skipped": 0,
            "failed": 0,
        },
    )
    assert mod.main() == 0


def test_runner_returns_two_on_partial_failure(monkeypatch):
    mod = _module()
    monkeypatch.setattr(
        mod,
        "load_config",
        lambda: mod.Config("https://x.test", "u", "p", 10, (1.0, 1.0)),
    )
    monkeypatch.setattr(
        mod,
        "run_once",
        lambda _cfg: {
            "status": "partial_failure",
            "requested_limit": 10,
            "processed": 1,
            "escalated": 0,
            "skipped": 0,
            "failed": 1,
        },
    )
    assert mod.main() == 2


def test_runner_returns_one_on_configuration_error(monkeypatch):
    mod = _module()
    monkeypatch.setattr(
        mod,
        "load_config",
        lambda: (_ for _ in ()).throw(mod.ConfigurationError("cfg")),
    )
    assert mod.main() == 1
