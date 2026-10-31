"""BLOCCO 1 PRE-COLLAUDO - correzioni prima del collaudo reale.

    W  WHATSAPP_SERVICE_URL senza valore predefinito: un ambiente senza la
       variabile non contatta mai il servizio esterno del template WhatsApp
    C  configurazione del runner del cron: segreti fuori dal repr
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_w_no_default_whatsapp_service_url_in_the_code():
    main_py = (ROOT / "main.py").read_text(encoding="utf-8")
    assert 'WHATSAPP_SERVICE_URL = (os.getenv("WHATSAPP_SERVICE_URL") or "").strip()' in main_py
    assert "stima360-whatsapp-webhook" not in main_py


def test_w_without_the_variable_the_url_is_empty():
    env = {k: v for k, v in os.environ.items() if k != "WHATSAPP_SERVICE_URL"}
    out = subprocess.run([sys.executable, "-c", "import main; print(repr(main.WHATSAPP_SERVICE_URL))"],
                         cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-500:]
    assert out.stdout.strip().splitlines()[-1] == "''"


def test_w_the_sender_never_calls_the_service_without_a_url(monkeypatch):
    import requests

    import main

    calls = []
    monkeypatch.setattr(main, "WHATSAPP_SERVICE_URL", "")
    monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append(a) or None)
    assert main.invia_whatsapp("3331234567", "Nome", "Indirizzo", "token") is False
    assert calls == []


def test_w_with_the_variable_the_sender_still_posts_once(monkeypatch):
    """Dove la variabile e' impostata il comportamento resta quello di prima."""
    import requests

    import main

    calls = []

    class Ok:
        status_code = 200

    monkeypatch.setattr(main, "WHATSAPP_SERVICE_URL", "http://127.0.0.1:9/fake")
    monkeypatch.setattr(requests, "post", lambda url, **k: calls.append((url, k["json"])) or Ok())
    assert main.invia_whatsapp("3331234567", "Nome", "Indirizzo", "token") is True
    assert len(calls) == 1 and calls[0][0] == "http://127.0.0.1:9/fake"
    assert calls[0][1] == {"to": main.normalizza_numero_whatsapp("3331234567"), "p1": "Nome", "p2": "Indirizzo",
                           "p3": "token"}


def test_c_runner_config_repr_hides_password_and_token():
    sys.path.insert(0, str(ROOT))
    import run_communication_dispatch_cron as cron

    cfg = cron.Config("https://crm.example", "cron@esempio.invalid", "password-segreta-di-prova", "email", 10,
                      (5.0, 120.0), 500, False, "token-segreto-di-prova-lungo-almeno-32-caratteri", 1)
    text = repr(cfg) + str(cfg)
    assert "password-segreta-di-prova" not in text and "token-segreto-di-prova" not in text
    assert cfg.password == "password-segreta-di-prova" and cfg.token.startswith("token-segreto")
    assert "https://crm.example" in text                      # il resto resta leggibile
