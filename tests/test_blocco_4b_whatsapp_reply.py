"""BLOCCO-4B - prove offline della risposta WhatsApp manuale.

Permessi, finestra, STOP, doppio invio, errori ed esiti incerti sono provati
su PostgreSQL vero in `test_blocco_4b_whatsapp_reply_postgres.py`. Qui:
l'adattatore verso Meta (con una sessione HTTP finta: nessuna chiamata
reale), la classificazione delle risposte, la parola STOP e l'interfaccia.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import requests

from inbound_messages.parser import InboundMessage
from inbound_messages.service import is_stop_request
from message_replies import transport

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "static" / "os_shell" / "assets"


class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeSession:
    def __init__(self, result):
        self.result, self.calls = result, []

    def post(self, url, **kw):
        self.calls.append((url, kw))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


# ---------------------------------------------------------------------------
# adattatore Meta
# ---------------------------------------------------------------------------

def test_sending_is_disabled_unless_explicitly_enabled(monkeypatch):
    monkeypatch.delenv("WHATSAPP_OUTBOUND_MODE", raising=False)
    monkeypatch.setenv("WHATSAPP_TOKEN", "x")
    assert transport.enabled() is False and transport.current() is None
    for value in ("", "1", "true", "on", "simulato"):
        monkeypatch.setenv("WHATSAPP_OUTBOUND_MODE", value)
        assert transport.current() is None
    monkeypatch.setenv("WHATSAPP_OUTBOUND_MODE", "meta")
    monkeypatch.delenv("WHATSAPP_TOKEN")
    assert transport.current() is None                         # senza credenziali nessun trasporto


def test_text_message_request_shape():
    session = FakeSession(FakeResponse(200, {"messages": [{"id": "wamid.ABC"}]}))
    result = transport.MetaCloudTransport("tok", "v18.0", session=session).send("851689118034087", "393331234567", "Ciao")
    assert result.outcome == "accepted" and result.provider_message_id == "wamid.ABC"
    url, kw = session.calls[0]
    assert url == "https://graph.facebook.com/v18.0/851689118034087/messages"
    assert kw["json"] == {"messaging_product": "whatsapp", "recipient_type": "individual", "to": "393331234567",
                          "type": "text", "text": {"preview_url": False, "body": "Ciao"}}
    assert kw["headers"]["Authorization"] == "Bearer tok" and kw["timeout"] == 15


@pytest.mark.parametrize("exc, outcome, code", [
    (requests.exceptions.ConnectTimeout(), "rejected", "provider_unavailable"),   # mai partito
    (requests.exceptions.ReadTimeout(), "indeterminate", "timeout"),             # partito, risposta persa
    (requests.exceptions.ConnectionError(), "indeterminate", "outcome_unknown"),
    (requests.exceptions.SSLError(), "indeterminate", "outcome_unknown"),
])
def test_network_errors_are_definite_only_when_nothing_left(exc, outcome, code):
    result = transport.MetaCloudTransport("tok", session=FakeSession(exc)).send("1", "393331234567", "x")
    assert (result.outcome, result.error_code) == (outcome, code)
    assert "393331234567" not in (result.error_detail or "")


@pytest.mark.parametrize("status, body, outcome, code, detail", [
    (200, {"messages": [{"id": "wamid.1"}]}, "accepted", None, None),
    (200, {}, "indeterminate", "outcome_unknown", "accepted_without_id"),
    (200, ValueError(), "indeterminate", "outcome_unknown", "accepted_without_id"),
    (500, {}, "indeterminate", "outcome_unknown", "http_500"),
    (503, ValueError(), "indeterminate", "outcome_unknown", "http_503"),
    (400, {"error": {"code": 131047}}, "rejected", "provider_rejected", "meta_131047_window_closed"),
    (400, {"error": {"code": 131026}}, "rejected", "invalid_destination", "meta_131026"),
    (401, {"error": {"code": 190}}, "rejected", "missing_credentials", "meta_190"),
    (429, {"error": {"code": 130429}}, "rejected", "provider_unavailable", "meta_130429"),
    (400, {"error": {"code": 999999}}, "rejected", "provider_rejected", "meta_999999"),
])
def test_meta_responses_are_classified(status, body, outcome, code, detail):
    response = FakeResponse(status, body)
    result = transport.MetaCloudTransport("tok", session=FakeSession(response)).send("1", "2", "x")
    assert (result.outcome, result.error_code, result.error_detail) == (outcome, code, detail)


# ---------------------------------------------------------------------------
# STOP
# ---------------------------------------------------------------------------

def _msg(text, kind="text"):
    return InboundMessage("w", "1", None, "393331234567", None, kind, text, {}, None)


@pytest.mark.parametrize("text, stop", [("STOP", True), (" stop. ", True), ("Stop!", True), ("stop per favore", False),
                                        ("non stop", False), ("", False), (None, False)])
def test_only_a_bare_stop_is_a_stop_request(text, stop):
    assert is_stop_request(_msg(text)) is stop
    assert is_stop_request(_msg("STOP", kind="image")) is False


# ---------------------------------------------------------------------------
# confini del codice
# ---------------------------------------------------------------------------

def test_the_meta_adapter_lives_outside_communication_and_inbound():
    assert not (ROOT / "communication" / "providers" / "whatsapp_meta.py").exists()
    for path in (ROOT / "communication").rglob("*.py"):
        assert "WHATSAPP_" not in path.read_text(encoding="utf-8") or path.name == "__init__.py", path
    for path in (ROOT / "inbound_messages").glob("*.py"):
        assert "requests" not in path.read_text(encoding="utf-8"), path
    replies = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "message_replies").glob("*.py"))
    assert "communication_messages" not in replies and "communication_attempts" not in replies
    assert "invia_whatsapp" not in replies and "admin/whatsapp" not in replies


def test_routes_are_mounted_behind_the_operator_session():
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    line = next(r for r in main.splitlines() if "message_replies_router" in r and "include_router" in r)
    assert "require_authenticated_operator" in line


# ---------------------------------------------------------------------------
# interfaccia
# ---------------------------------------------------------------------------

def test_reply_ui_confirms_reuses_the_request_key_and_never_calls_the_old_admin_endpoint():
    js = (APP / "components" / "whatsapp-reply.js").read_text(encoding="utf-8")
    view = (APP / "views" / "messaggi.js").read_text(encoding="utf-8")
    for text in (js, view):
        assert "/api/admin/whatsapp" not in text and "innerHTML" not in text
    assert "/api/message-replies/conversations" in js and "mountWhatsappReply(detail" in view
    assert "Conferma invio" in js and "confirm: true" in js and "client_request_id: requestId" in js
    assert "if (!requestId) requestId = newRequestId();" in js                # stessa chiave sui nuovi tentativi
    assert re.search(r"send\.disabled = true", js)                            # niente doppio clic
    assert "Esito incerto" in js and "Non è stata ritentata" in js
    # il corpo inviato e' SOLO testo, chiave e conferma: destinatario e mittente li decide il server
    assert "{ text: text.value, client_request_id: requestId, confirm: true }" in js
    assert "phone_number_id" not in js
