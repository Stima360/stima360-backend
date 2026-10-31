"""Inoltro preparato per il ricevitore di produzione (`integrations/whatsapp_relay`),
provato qui senza toccare la produzione: configurazione, selezione dei
mittenti, compatibilita' con il webhook del CRM, tentativi e resilienza.

Il ricevitore di produzione e' riprodotto da `_production_like_app` con la
stessa struttura del codice di `main` dopo la patch (`main_webhook.patch`):
firma Meta, inoltro accodato, salvataggio storico, risposta 200. Gira sotto
un uvicorn VERO, cosi' si misura che la risposta non aspetta il CRM.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "integrations" / "whatsapp_relay"))
import forwarder  # noqa: E402

from inbound_messages import relay  # noqa: E402

SECRET = "segreto-sintetico-dell-inoltro-lungo-almeno-32"
META_SECRET = "segreto-meta-sintetico"
ALLOWED = "393000000001"
OTHER = "393999999999"


def _payload():
    return {"object": "whatsapp_business_account", "entry": [{"id": "waba", "changes": [
        {"field": "messages", "value": {
            "messaging_product": "whatsapp", "metadata": {"phone_number_id": "900000000000001"},
            "contacts": [{"wa_id": ALLOWED, "profile": {"name": "Prova"}},
                         {"wa_id": OTHER, "profile": {"name": "Cliente reale"}}],
            "messages": [{"from": ALLOWED, "id": "wamid.a", "timestamp": "1761800000", "type": "text",
                          "text": {"body": "Risposta di prova"}},
                         {"from": OTHER, "id": "wamid.b", "timestamp": "1761800000", "type": "text",
                          "text": {"body": "Testo di un cliente"}}],
            "statuses": [{"id": "wamid.out", "status": "read", "recipient_id": OTHER}]}},
        {"field": "account_update", "value": {"event": "VERIFIED_ACCOUNT"}}]}]}


def _raw():
    return json.dumps(_payload()).encode()


ENV = {forwarder.URL_ENV: "https://crm.example/webhook/whatsapp", forwarder.SECRET_ENV: SECRET,
       forwarder.SENDERS_ENV: f"+{ALLOWED}"}


# ---------------------------------------------------------------------------
# configurazione: spento di partenza
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("change", [
    {forwarder.URL_ENV: ""}, {forwarder.SECRET_ENV: ""}, {forwarder.SECRET_ENV: "corto"},
    {forwarder.SENDERS_ENV: ""}, {forwarder.SENDERS_ENV: "nessun numero"},
    {forwarder.URL_ENV: "http://crm.example/webhook/whatsapp"}, {forwarder.URL_ENV: "ftp://crm.example/x"},
])
def test_config_is_off_unless_complete(change):
    assert forwarder.config({**ENV, **change}) is None


def test_config_on_and_secret_never_in_repr():
    cfg = forwarder.config(ENV)
    assert cfg.senders == {ALLOWED} and cfg.url.startswith("https://")
    assert SECRET not in repr(cfg)
    assert forwarder.config({**ENV, forwarder.SENDERS_ENV: "*"}).senders == forwarder.ALL
    assert forwarder.config({**ENV, forwarder.URL_ENV: "http://127.0.0.1:9/x"}) is not None   # solo locale


def test_config_with_no_environment_is_off(monkeypatch):
    for name in (forwarder.URL_ENV, forwarder.SECRET_ENV, forwarder.SENDERS_ENV):
        monkeypatch.delenv(name, raising=False)
    assert forwarder.config() is None


# ---------------------------------------------------------------------------
# selezione dei mittenti: i clienti reali non escono
# ---------------------------------------------------------------------------

def test_select_keeps_only_allowed_senders():
    body = forwarder.select(_raw(), frozenset({ALLOWED}))
    text = body.decode()
    assert OTHER not in text and "Cliente reale" not in text and "Testo di un cliente" not in text
    assert "account_update" not in text
    out = json.loads(body)
    value = out["entry"][0]["changes"][0]["value"]
    assert [m["id"] for m in value["messages"]] == ["wamid.a"] and value["statuses"] == []
    assert value["metadata"] == {"phone_number_id": "900000000000001"}


def test_select_returns_nothing_without_allowed_content_or_on_bad_json():
    assert forwarder.select(_raw(), frozenset({"393111111111"})) is None
    assert forwarder.select(b"non json", frozenset({ALLOWED})) is None
    assert forwarder.select(b'{"entry": "x"}', frozenset({ALLOWED})) is None


def test_select_all_forwards_the_original_bytes():
    raw = _raw()
    assert forwarder.select(raw, forwarder.ALL) is raw


# ---------------------------------------------------------------------------
# compatibilita' con il webhook del CRM
# ---------------------------------------------------------------------------

def test_the_crm_accepts_the_forwarder_signature_and_filters_nothing_more(monkeypatch):
    monkeypatch.setenv(relay.RELAY_SECRET_ENV, SECRET)
    body = forwarder.select(_raw(), frozenset({ALLOWED}))
    sent = []
    forwarder.deliver(forwarder.config(ENV), body, post=lambda url, **k: sent.append(k) or _R(200))
    headers = sent[0]["headers"]
    assert headers[forwarder.SIGNATURE_HEADER] == relay.signature(SECRET, headers[forwarder.TIMESTAMP_HEADER], body)
    relay.verify(sent[0]["data"], headers)                      # nessuna eccezione
    filtered, dropped = relay.filter_payload(json.loads(body), relay.allowed_senders(ALLOWED))
    assert dropped == 0 and filtered == json.loads(body)
    assert forwarder.SIGNATURE_HEADER == relay.SIGNATURE_HEADER and forwarder.TIMESTAMP_HEADER == relay.TIMESTAMP_HEADER


# ---------------------------------------------------------------------------
# tentativi
# ---------------------------------------------------------------------------

class _R:
    def __init__(self, status_code):
        self.status_code = status_code


def _script(*outcomes):
    calls = []

    def post(url, **kwargs):
        calls.append(kwargs)
        out = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
        if isinstance(out, Exception):
            raise out
        return _R(out)
    return post, calls


@pytest.mark.parametrize("outcomes, expected, attempts", [
    ((200,), "delivered", 1),
    ((500, 200), "delivered", 2),
    ((ConnectionError("giu'"), 503, 200), "delivered", 3),
    ((429, 429, 429), "gave_up", 3),
    ((500,), "gave_up", 3),
    ((403,), "rejected", 1),
    ((400,), "rejected", 1),
])
def test_retries_are_bounded_and_definitive_refusals_are_not_retried(outcomes, expected, attempts):
    post, calls = _script(*outcomes)
    waits = []
    assert forwarder.deliver(forwarder.config(ENV), b"{}", post=post, sleep=waits.append) == expected
    assert len(calls) == attempts and len(waits) == attempts - 1
    assert all(c["timeout"] == forwarder.TIMEOUT for c in calls)
    assert all(c["headers"][forwarder.SIGNATURE_HEADER].startswith("sha256=") for c in calls)


def test_each_attempt_is_signed_with_its_own_time():
    post, calls = _script(500, 500, 200)
    clock = iter([1000, 2000, 3000])
    forwarder.deliver(forwarder.config(ENV), b"{}", post=post, sleep=lambda s: None, now=lambda: next(clock))
    assert [c["headers"][forwarder.TIMESTAMP_HEADER] for c in calls] == ["1000", "2000", "3000"]


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_an_unreachable_crm_gives_up_without_raising():
    cfg = forwarder.config({**ENV, forwarder.URL_ENV: f"http://127.0.0.1:{_free_port()}/webhook/whatsapp"})
    assert forwarder.deliver(cfg, b"{}", sleep=lambda s: None) == "gave_up"


def test_a_hanging_crm_is_cut_by_the_timeout():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)                                  # accetta e non risponde mai
    try:
        cfg = forwarder.config({**ENV, forwarder.URL_ENV: f"http://127.0.0.1:{srv.getsockname()[1]}/x"})
        t0 = time.monotonic()
        assert forwarder.deliver(cfg, b"{}", sleep=lambda s: None, timeout=(0.5, 0.5)) == "gave_up"
        assert time.monotonic() - t0 < 10
    finally:
        srv.close()


def test_log_lines_carry_no_number_text_or_secret(caplog):
    caplog.set_level("INFO", logger="whatsapp_relay")
    post, _ = _script(ConnectionError(ALLOWED), 500, 403)
    forwarder.deliver(forwarder.config(ENV), forwarder.select(_raw(), frozenset({ALLOWED})), post=post,
                      sleep=lambda s: None)
    text = caplog.text
    for secret_or_personal in (ALLOWED, OTHER, SECRET, "Risposta di prova", "Prova"):
        assert secret_or_personal not in text


# ---------------------------------------------------------------------------
# ricevitore di produzione riprodotto: risposta, salvataggio storico, resilienza
# ---------------------------------------------------------------------------

def _production_like_app(stored):
    """Stessa struttura del webhook di `main` dopo `main_webhook.patch`."""
    app = FastAPI()

    @app.post("/webhook/whatsapp")
    async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
        raw_body = await request.body()
        expected = "sha256=" + hmac.new(META_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, request.headers.get("X-Hub-Signature-256") or ""):
            raise HTTPException(status_code=403, detail="Invalid webhook signature")
        forwarder.schedule(background_tasks, raw_body)
        payload = await request.json()
        try:
            msg = payload.get("entry", [])[0].get("changes", [])[0].get("value", {})["messages"][0]
            stored.append((msg.get("from"), msg.get("type")))      # = INSERT INTO whatsapp_incoming
        except Exception:
            pass
        return {"ok": True}

    return app


class _Server:
    def __init__(self, app):
        import uvicorn

        self.port = _free_port()
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("server di prova non partito")

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=10)


def _crm_fake(received, *, delay=0.0, status=200):
    app = FastAPI()

    @app.post("/webhook/whatsapp")
    async def hook(request: Request):
        import asyncio

        await asyncio.sleep(delay)
        received.append((dict(request.headers), await request.body()))
        return Response(status_code=status)

    return app


def _meta_post(port, raw):
    import requests

    sig = "sha256=" + hmac.new(META_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    t0 = time.monotonic()
    r = requests.post(f"http://127.0.0.1:{port}/webhook/whatsapp", data=raw, timeout=20,
                      headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig})
    return r, time.monotonic() - t0


def _wait(cond, seconds=15):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_production_answers_meta_before_a_slow_crm_and_still_forwards(monkeypatch):
    stored, received = [], []
    with _Server(_crm_fake(received, delay=3.0)) as crm, _Server(_production_like_app(stored)) as prod:
        monkeypatch.setenv(forwarder.URL_ENV, f"http://127.0.0.1:{crm.port}/webhook/whatsapp")
        monkeypatch.setenv(forwarder.SECRET_ENV, SECRET)
        monkeypatch.setenv(forwarder.SENDERS_ENV, ALLOWED)
        r, elapsed = _meta_post(prod.port, _raw())
        assert r.status_code == 200 and r.json() == {"ok": True}
        assert elapsed < 2.0, elapsed                         # il CRM impiega 3 s: la risposta non lo aspetta
        assert stored == [(ALLOWED, "text")]                  # salvataggio storico invariato
        assert _wait(lambda: received)
        headers, body = received[0]
        assert OTHER.encode() not in body and b"Testo di un cliente" not in body
        assert {k.lower() for k in headers} >= {forwarder.SIGNATURE_HEADER.lower(), forwarder.TIMESTAMP_HEADER.lower()}


@pytest.mark.parametrize("scenario", ["unreachable", "error_500", "schedule_raises", "off"])
def test_production_keeps_answering_and_storing_whatever_happens_to_the_crm(monkeypatch, scenario):
    stored, received = [], []
    monkeypatch.setattr(forwarder, "BACKOFF", (0.0, 0.0))
    with _Server(_crm_fake(received, status=500)) as crm, _Server(_production_like_app(stored)) as prod:
        url = {"unreachable": f"http://127.0.0.1:{_free_port()}/webhook/whatsapp"}.get(
            scenario, f"http://127.0.0.1:{crm.port}/webhook/whatsapp")
        if scenario != "off":
            monkeypatch.setenv(forwarder.URL_ENV, url)
            monkeypatch.setenv(forwarder.SECRET_ENV, SECRET)
            monkeypatch.setenv(forwarder.SENDERS_ENV, ALLOWED)
        else:
            for name in (forwarder.URL_ENV, forwarder.SECRET_ENV, forwarder.SENDERS_ENV):
                monkeypatch.delenv(name, raising=False)
        if scenario == "schedule_raises":
            monkeypatch.setattr(forwarder, "config", lambda env=None: (_ for _ in ()).throw(RuntimeError("guasto")))
        for _ in range(2):
            r, _ = _meta_post(prod.port, _raw())
            assert r.status_code == 200 and r.json() == {"ok": True}
        assert stored == [(ALLOWED, "text")] * 2
        if scenario == "error_500":
            assert _wait(lambda: len(received) == 2 * forwarder.ATTEMPTS)    # tentativi limitati
        if scenario in ("off", "schedule_raises"):
            time.sleep(0.3)
            assert received == []


def test_production_still_rejects_a_bad_meta_signature_and_forwards_nothing(monkeypatch):
    stored, received = [], []
    with _Server(_crm_fake(received)) as crm, _Server(_production_like_app(stored)) as prod:
        monkeypatch.setenv(forwarder.URL_ENV, f"http://127.0.0.1:{crm.port}/webhook/whatsapp")
        monkeypatch.setenv(forwarder.SECRET_ENV, SECRET)
        monkeypatch.setenv(forwarder.SENDERS_ENV, "*")
        import requests

        r = requests.post(f"http://127.0.0.1:{prod.port}/webhook/whatsapp", data=_raw(), timeout=10,
                          headers={"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=" + "0" * 64})
        assert r.status_code == 403
        time.sleep(0.3)
        assert stored == [] and received == []


def test_the_patch_matches_the_production_receiver_shape():
    """La patch tocca solo import e webhook: tre righe aggiunte nel corpo,
    nessuna riga rimossa oltre la firma della funzione e l'import FastAPI."""
    patch = (ROOT / "integrations" / "whatsapp_relay" / "main_webhook.patch").read_text(encoding="utf-8")
    removed = [l for l in patch.splitlines() if l.startswith("-") and not l.startswith("---")]
    added = [l for l in patch.splitlines() if l.startswith("+") and not l.startswith("+++")]
    assert removed == ["-from fastapi import FastAPI, Request, HTTPException, Depends, Response",
                       "-async def whatsapp_webhook(request: Request):"]
    assert "+    whatsapp_relay.schedule(background_tasks, raw_body)" in added
    code = [l for l in added if not l.lstrip("+ ").startswith("#")]
    assert all("whatsapp_incoming" not in l and "invia_whatsapp" not in l for l in code)
